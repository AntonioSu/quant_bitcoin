"""CVD 按成交规模分层 (Order-Size Cohort CVD)

通过 Binance aggTrade WebSocket 实时采集成交数据，
按 USD 金额将每笔交易划分到不同规模层：

  - retail:  < $10,000        (散户)
  - medium:  $10,000–$100,000 (中等资金)
  - large:   $100,000–$10M    (大户/机构)

对每个层分别计算 CVD (Cumulative Volume Delta):
  CVD = Σ(taker_buy_usd - taker_sell_usd)

正值 → 该层净买入; 负值 → 该层净卖出

典型解读:
- 散户/中等资金 CVD 改善 + 大户卖压收缩 → 底部积累信号
- 大户 CVD 暴涨而散户卖出 → 知情资金先行进场
"""

import asyncio
import json
import os
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp

from data_sources.base import DataSourceBase, DataPoint
from utils import logger


_MAX_BUFFER_SECONDS = 86400  # 24h
_PERSIST_INTERVAL = 300  # 每5分钟落盘一次
_PERSIST_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "cvd_orderflow_buffer.json"
)

_WS_URL_9443 = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
_WS_URL_443 = "wss://stream.binance.com/ws/btcusdt@aggTrade"

# 规模分层阈值 (USD)
COHORT_THRESHOLDS = {
    "retail": (0, 10_000),
    "medium": (10_000, 100_000),
    "large": (100_000, 10_000_000),
}


@dataclass
class CohortCVD:
    """单个层的 CVD 统计"""
    buy_usd: float
    sell_usd: float
    net_usd: float   # buy - sell
    trade_count: int


@dataclass
class CVDOrderFlowData:
    """CVD 分层结果"""
    retail: CohortCVD
    medium: CohortCVD
    large: CohortCVD
    window_minutes: int
    total_trades: int
    ws_connected: bool

    def to_dict(self) -> dict:
        def _cohort_dict(c: CohortCVD) -> dict:
            return {
                "buy_usd": round(c.buy_usd, 2),
                "sell_usd": round(c.sell_usd, 2),
                "net_usd": round(c.net_usd, 2),
                "trade_count": c.trade_count,
            }

        return {
            "retail": _cohort_dict(self.retail),
            "medium": _cohort_dict(self.medium),
            "large": _cohort_dict(self.large),
            "window_minutes": self.window_minutes,
            "total_trades": self.total_trades,
            "ws_connected": self.ws_connected,
        }


class _AggTrade:
    """单条聚合成交"""
    __slots__ = ("timestamp", "price", "qty", "usd_value", "is_buyer_maker")

    def __init__(self, timestamp: float, price: float, qty: float,
                 is_buyer_maker: bool):
        self.timestamp = timestamp
        self.price = price
        self.qty = qty
        self.usd_value = price * qty
        self.is_buyer_maker = is_buyer_maker  # True = taker sell


class _AggTradeCollector:
    """后台 WebSocket aggTrade 采集器 (全局单例)

    持续订阅 btcusdt@aggTrade，按时间保留最近 24h 数据。
    """

    _instance: Optional["_AggTradeCollector"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._buffer: deque[_AggTrade] = deque()
        self._buf_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._connected = False
        self._last_msg_time: float = 0
        self._total_received: int = 0
        self._last_persist_time: float = 0
        self._load_from_disk()

    @classmethod
    def get_instance(cls) -> "_AggTradeCollector":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="aggtrade-cvd-ws", daemon=True,
        )
        self._thread.start()
        logger.info("📊 CVD OrderFlow 采集器已启动 (aggTrade WebSocket)")

    def _load_from_disk(self):
        """启动时从磁盘加载上次保存的缓冲区"""
        if not os.path.exists(_PERSIST_FILE):
            return
        try:
            with open(_PERSIST_FILE, "r") as f:
                records = json.load(f)
            cutoff = time.time() - _MAX_BUFFER_SECONDS
            loaded = 0
            for r in records:
                if r[0] >= cutoff:
                    self._buffer.append(_AggTrade(
                        timestamp=r[0], price=r[1], qty=r[2],
                        is_buyer_maker=bool(r[3]),
                    ))
                    loaded += 1
            if loaded:
                logger.info(
                    f"📊 CVD OrderFlow 从磁盘恢复 {loaded} 条成交 "
                    f"(丢弃 {len(records) - loaded} 条过期)"
                )
        except Exception as e:
            logger.warning(f"📊 CVD OrderFlow 加载磁盘缓存失败: {e}")

    def _save_to_disk(self):
        """将缓冲区保存到磁盘 (紧凑数组格式)"""
        try:
            with self._buf_lock:
                records = [
                    [t.timestamp, t.price, t.qty, int(t.is_buyer_maker)]
                    for t in self._buffer
                ]
            os.makedirs(os.path.dirname(_PERSIST_FILE), exist_ok=True)
            tmp_file = _PERSIST_FILE + ".tmp"
            with open(tmp_file, "w") as f:
                json.dump(records, f)
            os.replace(tmp_file, _PERSIST_FILE)
            self._last_persist_time = time.time()
            logger.debug(f"📊 CVD OrderFlow 已落盘 {len(records)} 条")
        except Exception as e:
            logger.warning(f"📊 CVD OrderFlow 落盘失败: {e}")

    def stop(self):
        self._running = False
        self._save_to_disk()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    def get_trades(self, lookback_seconds: int = 3600) -> List[_AggTrade]:
        """获取指定窗口内的成交 (线程安全)"""
        cutoff = time.time() - lookback_seconds
        with self._buf_lock:
            return [t for t in self._buffer if t.timestamp >= cutoff]

    def aggregate_cohorts(self, lookback_seconds: int = 3600) -> Dict[str, CohortCVD]:
        """按规模分层聚合 CVD"""
        trades = self.get_trades(lookback_seconds)
        result = {}
        for name, (lo, hi) in COHORT_THRESHOLDS.items():
            buy_usd = 0.0
            sell_usd = 0.0
            count = 0
            for t in trades:
                if lo <= t.usd_value < hi:
                    count += 1
                    if t.is_buyer_maker:
                        sell_usd += t.usd_value
                    else:
                        buy_usd += t.usd_value
            result[name] = CohortCVD(
                buy_usd=buy_usd,
                sell_usd=sell_usd,
                net_usd=buy_usd - sell_usd,
                trade_count=count,
            )
        return result

    def _prune_buffer(self):
        cutoff = time.time() - _MAX_BUFFER_SECONDS
        with self._buf_lock:
            while self._buffer and self._buffer[0].timestamp < cutoff:
                self._buffer.popleft()
        if time.time() - self._last_persist_time >= _PERSIST_INTERVAL:
            self._save_to_disk()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._ws_loop())
        except Exception as e:
            logger.error(f"📊 CVD OrderFlow 采集器异常退出: {e}")
        finally:
            self._loop.close()
            self._running = False

    async def _ws_loop(self):
        reconnect_delay = 3
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")

        if proxy_url:
            ws_url = _WS_URL_443
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        else:
            ws_url = _WS_URL_9443
            ssl_context = None

        while self._running:
            try:
                timeout = aiohttp.ClientTimeout(total=30, connect=10)
                connector = (
                    aiohttp.TCPConnector(ssl=ssl_context)
                    if ssl_context else None
                )
                async with aiohttp.ClientSession(
                    timeout=timeout, connector=connector
                ) as session:
                    async with session.ws_connect(
                        ws_url,
                        proxy=proxy_url,
                        ssl=ssl_context,
                        heartbeat=20,
                    ) as ws:
                        reconnect_delay = 3
                        self._connected = True
                        logger.info(
                            f"📊 aggTrade WebSocket 已连接: {ws_url}"
                            + (f" (代理: {proxy_url})" if proxy_url else "")
                        )

                        prune_counter = 0
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                self._handle_message(msg.data)
                                prune_counter += 1
                                if prune_counter >= 500:
                                    self._prune_buffer()
                                    prune_counter = 0
                            elif msg.type in (
                                aiohttp.WSMsgType.ERROR,
                                aiohttp.WSMsgType.CLOSED,
                            ):
                                logger.warning(
                                    f"📊 aggTrade WebSocket 断开: {msg.type}"
                                )
                                break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    f"📊 aggTrade WebSocket 连接失败: {e}, "
                    f"{reconnect_delay}s 后重连"
                )
            finally:
                self._connected = False

            if self._running:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)

    def _handle_message(self, raw: str):
        try:
            data = json.loads(raw)
            trade = _AggTrade(
                timestamp=int(data["T"]) / 1000,
                price=float(data["p"]),
                qty=float(data["q"]),
                is_buyer_maker=bool(data["m"]),
            )
            with self._buf_lock:
                self._buffer.append(trade)
            self._last_msg_time = time.time()
            self._total_received += 1
        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"📊 解析 aggTrade 失败: {e}")


class CVDOrderFlow(DataSourceBase):
    """CVD 分层订单流数据源

    基于后台 aggTrade WebSocket 采集，fetch() 从缓冲区按规模分层聚合。
    首次实例化自动启动采集器，全局单例复用。
    """

    def __init__(self, lookback_minutes: int = 240):
        """
        Args:
            lookback_minutes: 统计时间窗口 (分钟)，默认 4h
        """
        super().__init__("CVD OrderFlow")
        self.lookback_minutes = lookback_minutes
        self._cache_ttl = 60
        self._collector = _AggTradeCollector.get_instance()
        self._collector.start()

    def fetch(self) -> DataPoint:
        """从缓冲区聚合分层 CVD"""
        cohorts = self._collector.aggregate_cohorts(
            lookback_seconds=self.lookback_minutes * 60,
        )
        trades = self._collector.get_trades(
            lookback_seconds=self.lookback_minutes * 60,
        )

        retail = cohorts["retail"]
        medium = cohorts["medium"]
        large = cohorts["large"]

        flow_data = CVDOrderFlowData(
            retail=retail,
            medium=medium,
            large=large,
            window_minutes=self.lookback_minutes,
            total_trades=len(trades),
            ws_connected=self._collector.is_connected,
        )

        total_net = retail.net_usd + medium.net_usd + large.net_usd
        signal = self._compute_signal(retail, medium, large)

        logger.info(
            f"📊 CVD分层({self.lookback_minutes}min): "
            f"散户${retail.net_usd / 1e6:+.2f}M | "
            f"中等${medium.net_usd / 1e6:+.2f}M | "
            f"大户${large.net_usd / 1e6:+.2f}M | "
            f"总净流${total_net / 1e6:+.2f}M "
            f"[{len(trades)}笔, "
            f"{'已连接' if self._collector.is_connected else '未连接'}]"
        )

        raw = flow_data.to_dict()
        raw["signal"] = signal

        return DataPoint(
            value=total_net,
            timestamp=datetime.now(),
            source=self.name,
            raw=raw,
        )

    def fetch_multi_window(self) -> Dict[str, Dict]:
        """获取多个时间窗口的分层 CVD (1h / 4h / 24h)"""
        result = {}
        for label, minutes in [("1h", 60), ("4h", 240), ("24h", 1440)]:
            cohorts = self._collector.aggregate_cohorts(
                lookback_seconds=minutes * 60,
            )
            result[label] = {
                "retail": {
                    "net_usd": round(cohorts["retail"].net_usd, 2),
                    "trade_count": cohorts["retail"].trade_count,
                },
                "medium": {
                    "net_usd": round(cohorts["medium"].net_usd, 2),
                    "trade_count": cohorts["medium"].trade_count,
                },
                "large": {
                    "net_usd": round(cohorts["large"].net_usd, 2),
                    "trade_count": cohorts["large"].trade_count,
                },
            }
        return result

    @staticmethod
    def _compute_signal(retail: CohortCVD, medium: CohortCVD,
                        large: CohortCVD) -> str:
        """根据分层 CVD 判断信号方向"""
        retail_net = retail.net_usd
        medium_net = medium.net_usd
        large_net = large.net_usd

        if large_net > 100_000_000:
            return "smart_money_buying"
        if large_net < -200_000_000:
            return "distribution"
        if retail_net > 0 and medium_net > 0 and large_net > -50_000_000:
            return "bullish_accumulation"
        if retail_net < 0 and medium_net < 0 and large_net < 0:
            return "broad_selling"
        return "neutral"

    def get_signal(self) -> str:
        """根据分层 CVD 判断信号方向"""
        cohorts = self._collector.aggregate_cohorts(
            lookback_seconds=self.lookback_minutes * 60,
        )
        return self._compute_signal(
            cohorts["retail"], cohorts["medium"], cohorts["large"]
        )


def main():
    """测试: 启动采集器，等待数据积累"""
    import signal as sig

    print("=" * 70)
    print("  CVD 分层订单流测试 (Binance aggTrade WebSocket)")
    print("=" * 70)

    cvd = CVDOrderFlow(lookback_minutes=60)

    print(f"\n⏳ 采集器已启动，等待数据...\n")

    stop_event = threading.Event()
    sig.signal(sig.SIGINT, lambda *_: stop_event.set())

    intervals = [30, 30, 60, 60, 120]
    for i, wait in enumerate(intervals):
        if stop_event.wait(timeout=wait):
            break

        data = cvd.fetch()
        raw = data.raw
        elapsed = sum(intervals[:i + 1])
        print(f"\n--- 运行 {elapsed}s ---")
        print(f"  连接状态:  {'已连接' if raw['ws_connected'] else '未连接'}")
        print(f"  总成交笔数: {raw['total_trades']:,}")
        print(f"  窗口: {raw['window_minutes']} min")
        print(f"  ┌─────────────────────────────────────────────")
        for name in ["retail", "medium", "large"]:
            c = raw[name]
            label = {"retail": "散户(<$10K)", "medium": "中等($10K-$100K)",
                     "large": "大户($100K-$10M)"}[name]
            print(
                f"  │ {label:20s}  "
                f"净${c['net_usd'] / 1e6:+8.2f}M  "
                f"(买${c['buy_usd'] / 1e6:.2f}M / 卖${c['sell_usd'] / 1e6:.2f}M)  "
                f"{c['trade_count']:,}笔"
            )
        print(f"  └─────────────────────────────────────────────")
        print(f"  信号: {cvd.get_signal()}")

    print(f"\n多窗口统计:")
    for label, stats in cvd.fetch_multi_window().items():
        print(f"  [{label}]", stats)

    print(f"\n👋 测试结束")


if __name__ == "__main__":
    main()
