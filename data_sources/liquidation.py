"""Binance 合约爆仓数据源 (Liquidation / Force Orders)

通过 Binance WebSocket `!forceOrder@arr` 实时采集全市场强平订单，
后台线程持续收集，fetch() 从内存缓冲区聚合统计。

注: Binance REST `/fapi/v1/allForceOrders` 已于 2021 年下线，
    爆仓数据只能通过 WebSocket 获取。

信号逻辑:
- 多头爆仓量 >> 空头 → 杠杆多头被清洗，可能接近底部 (逆向做多信号)
- 空头爆仓量 >> 多头 → 杠杆空头被轧，可能接近顶部 (逆向做空信号)
- 总爆仓量突然暴增 → 剧烈波动，方向待确认，谨慎操作
- 爆仓量持续低迷 → 市场杠杆低，波动率可能上升

WebSocket 数据格式:
- side=SELL → 多头仓位被强平 (多头爆仓)
- side=BUY  → 空头仓位被强平 (空头爆仓)
"""

import asyncio
import json
import os
import ssl
import threading
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import aiohttp

from data_sources.base import DataSourceBase, DataPoint
from utils import logger


# 最大保留 24h 数据
_MAX_BUFFER_SECONDS = 86400

# WebSocket URL
_WS_URL_9443 = "wss://fstream.binance.com:9443/ws/!forceOrder@arr"
_WS_URL_443 = "wss://fstream.binance.com/ws/!forceOrder@arr"


class _LiquidationOrder:
    """单条强平订单"""
    __slots__ = ("timestamp", "symbol", "side", "price", "qty", "usd_value")

    def __init__(self, timestamp: float, symbol: str, side: str,
                 price: float, qty: float):
        self.timestamp = timestamp
        self.symbol = symbol
        self.side = side
        self.price = price
        self.qty = qty
        self.usd_value = price * qty


class _LiquidationCollector:
    """后台 WebSocket 采集器 (单例)

    在独立线程中运行 asyncio 事件循环，持续订阅 Binance 强平流。
    """

    _instance: Optional["_LiquidationCollector"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._buffer: deque[_LiquidationOrder] = deque()
        self._buf_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._connected = False
        self._last_msg_time: float = 0
        self._total_received: int = 0

    @classmethod
    def get_instance(cls) -> "_LiquidationCollector":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def start(self):
        """启动后台采集线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="liquidation-ws", daemon=True,
        )
        self._thread.start()
        logger.info("💥 爆仓采集器已启动 (后台线程)")

    def stop(self):
        """停止采集"""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    def get_orders(self, symbol: Optional[str] = None,
                   lookback_seconds: int = 3600) -> List[_LiquidationOrder]:
        """获取指定时间窗口内的强平订单 (线程安全)"""
        cutoff = time.time() - lookback_seconds
        with self._buf_lock:
            orders = [
                o for o in self._buffer
                if o.timestamp >= cutoff
                and (symbol is None or o.symbol == symbol)
            ]
        return orders

    def _prune_buffer(self):
        """清理超过 24h 的旧数据"""
        cutoff = time.time() - _MAX_BUFFER_SECONDS
        with self._buf_lock:
            while self._buffer and self._buffer[0].timestamp < cutoff:
                self._buffer.popleft()

    def _run_loop(self):
        """线程入口: 创建事件循环并运行 WebSocket"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._ws_loop())
        except Exception as e:
            logger.error(f"💥 爆仓采集器异常退出: {e}")
        finally:
            self._loop.close()
            self._running = False

    async def _ws_loop(self):
        """WebSocket 主循环 (自动重连)"""
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
                            f"💥 爆仓 WebSocket 已连接: {ws_url}"
                            + (f" (代理: {proxy_url})" if proxy_url else "")
                        )

                        prune_counter = 0
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                self._handle_message(msg.data)
                                prune_counter += 1
                                if prune_counter >= 100:
                                    self._prune_buffer()
                                    prune_counter = 0
                            elif msg.type in (
                                aiohttp.WSMsgType.ERROR,
                                aiohttp.WSMsgType.CLOSED,
                            ):
                                logger.warning(f"💥 爆仓 WebSocket 断开: {msg.type}")
                                break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    f"💥 爆仓 WebSocket 连接失败: {e}, "
                    f"{reconnect_delay}s 后重连"
                )
            finally:
                self._connected = False

            if self._running:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)

    def _handle_message(self, raw: str):
        """解析并存储一条强平消息"""
        try:
            data = json.loads(raw)
            order_data = data.get("o", data)

            order = _LiquidationOrder(
                timestamp=int(order_data["T"]) / 1000,
                symbol=order_data["s"],
                side=order_data["S"],
                price=float(order_data["p"]),
                qty=float(order_data["q"]),
            )

            with self._buf_lock:
                self._buffer.append(order)

            self._last_msg_time = time.time()
            self._total_received += 1

        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"💥 解析爆仓消息失败: {e}")


class Liquidation(DataSourceBase):
    """币安合约爆仓量数据源

    基于后台 WebSocket 采集，fetch() 从内存缓冲区聚合统计。
    首次实例化时自动启动采集器，采集器全局单例复用。
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        lookback_minutes: int = 60,
    ):
        """
        Args:
            symbol: 合约符号 (如 BTCUSDT)
            lookback_minutes: 统计时间窗口 (分钟)，默认 1 小时
        """
        super().__init__("Binance Liquidation")
        self.symbol = symbol
        self.lookback_minutes = lookback_minutes
        self._cache_ttl = 60

        self._collector = _LiquidationCollector.get_instance()
        self._collector.start()

    def fetch(self) -> DataPoint:
        """从缓冲区聚合爆仓统计"""
        orders = self._collector.get_orders(
            symbol=self.symbol,
            lookback_seconds=self.lookback_minutes * 60,
        )

        long_liq_usd, short_liq_usd = 0.0, 0.0
        long_count, short_count = 0, 0
        max_single_usd = 0.0

        for o in orders:
            if o.side == "SELL":
                long_liq_usd += o.usd_value
                long_count += 1
            else:
                short_liq_usd += o.usd_value
                short_count += 1
            max_single_usd = max(max_single_usd, o.usd_value)

        total_usd = long_liq_usd + short_liq_usd
        total_count = long_count + short_count

        if short_liq_usd > 0:
            long_short_ratio = long_liq_usd / short_liq_usd
        elif long_liq_usd > 0:
            long_short_ratio = float("inf")
        else:
            long_short_ratio = 1.0

        status = "connected" if self._collector.is_connected else "disconnected"

        logger.info(
            f"💥 爆仓({self.lookback_minutes}min): "
            f"${total_usd / 1e6:.2f}M "
            f"(多${long_liq_usd / 1e6:.2f}M / 空${short_liq_usd / 1e6:.2f}M) "
            f"共{total_count}笔 [{status}]"
        )

        return DataPoint(
            value=total_usd,
            timestamp=datetime.now(),
            source=self.name,
            raw={
                "total_usd": total_usd,
                "long_liquidation_usd": long_liq_usd,
                "short_liquidation_usd": short_liq_usd,
                "long_count": long_count,
                "short_count": short_count,
                "total_count": total_count,
                "long_short_ratio": long_short_ratio,
                "max_single_usd": max_single_usd,
                "lookback_minutes": self.lookback_minutes,
                "ws_connected": self._collector.is_connected,
                "buffer_size": self._collector.buffer_size,
            },
        )

    def fetch_multi_window(self) -> Dict[str, Dict]:
        """获取多个时间窗口的爆仓统计 (1h / 4h / 24h)"""
        result = {}
        for label, minutes in [("1h", 60), ("4h", 240), ("24h", 1440)]:
            orders = self._collector.get_orders(
                symbol=self.symbol,
                lookback_seconds=minutes * 60,
            )
            long_usd = sum(o.usd_value for o in orders if o.side == "SELL")
            short_usd = sum(o.usd_value for o in orders if o.side == "BUY")
            result[label] = {
                "total_usd": long_usd + short_usd,
                "long_liquidation_usd": long_usd,
                "short_liquidation_usd": short_usd,
                "count": len(orders),
            }
        return result

    def is_long_flush(self, threshold_ratio: float = 3.0) -> bool:
        """多头是否被大规模清洗 (多头爆仓量 / 空头爆仓量 > 阈值)"""
        data = self.get()
        ratio = data.raw.get("long_short_ratio", 1.0) if data.raw else 1.0
        return ratio >= threshold_ratio

    def is_short_squeeze(self, threshold_ratio: float = 3.0) -> bool:
        """空头是否被大规模轧仓 (空头爆仓量 / 多头爆仓量 > 阈值)"""
        data = self.get()
        ratio = data.raw.get("long_short_ratio", 1.0) if data.raw else 1.0
        if ratio == 0:
            return True
        return (1.0 / ratio) >= threshold_ratio

    def is_high_volume(self, threshold_usd: float = 10_000_000) -> bool:
        """爆仓总量是否超过阈值 (默认 1000 万美元)"""
        data = self.get()
        return data.value >= threshold_usd


def main():
    """测试: 启动采集器，等待数据，然后输出统计"""
    import signal

    print("=" * 60)
    print("爆仓数据测试 (Binance WebSocket)")
    print("=" * 60)

    liq = Liquidation(symbol="BTCUSDT", lookback_minutes=60)

    print(f"\n⏳ WebSocket 采集器已启动，等待爆仓数据...")
    print(f"   (市场平静时可能需要等待数分钟才有数据)\n")

    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())

    intervals = [15, 15, 30, 60, 60]
    for i, wait in enumerate(intervals):
        if stop_event.wait(timeout=wait):
            break

        data = liq.fetch()
        raw = data.raw
        elapsed = sum(intervals[:i + 1])
        print(f"\n--- 运行 {elapsed}s 后统计 ---")
        print(f"  WebSocket:  {'已连接' if raw['ws_connected'] else '未连接'}")
        print(f"  缓冲区:     {raw['buffer_size']} 条")
        print(f"  总爆仓量:   ${raw['total_usd']:>14,.0f}  ({raw['total_count']} 笔)")
        print(f"  多头爆仓:   ${raw['long_liquidation_usd']:>14,.0f}  ({raw['long_count']} 笔)")
        print(f"  空头爆仓:   ${raw['short_liquidation_usd']:>14,.0f}  ({raw['short_count']} 笔)")
        print(f"  多空比:     {raw['long_short_ratio']:.2f}")
        if raw['max_single_usd'] > 0:
            print(f"  最大单笔:   ${raw['max_single_usd']:>14,.0f}")

    print(f"\n{'=' * 60}")
    print("多窗口统计:")
    print("=" * 60)
    for label, stats in liq.fetch_multi_window().items():
        print(
            f"  [{label:>3s}] ${stats['total_usd']:>14,.0f}  "
            f"(多${stats['long_liquidation_usd']:>12,.0f} / "
            f"空${stats['short_liquidation_usd']:>12,.0f}) "
            f"{stats['count']}笔"
        )

    print(f"\n👋 测试结束")


if __name__ == "__main__":
    main()
