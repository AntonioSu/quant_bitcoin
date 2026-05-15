"""Binance 合约未平仓量 (Open Interest) 数据源

未平仓量 = 市场上所有未平仓的合约总量，反映资金流入/流出。

信号逻辑:
- OI 上升 + 价格上升 → 新多头入场，看涨趋势确认
- OI 上升 + 价格下降 → 新空头入场，看跌趋势确认
- OI 下降 + 价格上升 → 空头平仓 (轧空)，上涨可能减弱
- OI 下降 + 价格下降 → 多头平仓，下跌可能减弱
- OI 突然暴增 → 大资金入场，关注后续方向
"""

import requests
from datetime import datetime
from typing import List, Dict, Optional

from stock_btc.data_sources.base import DataSourceBase, DataPoint
from stock_btc.utils import logger, retry_request


class OpenInterest(DataSourceBase):
    """币安合约未平仓量"""

    # U本位合约 API
    REALTIME_URL = "https://fapi.binance.com/fapi/v1/openInterest"
    HISTORY_URL = "https://fapi.binance.com/futures/data/openInterestHist"

    def __init__(self, symbol: str = "BTCUSDT", period: str = "4h"):
        """
        Args:
            symbol: 合约符号 (如 BTCUSDT)
            period: 历史数据周期 (5m/15m/30m/1h/2h/4h/6h/12h/1d)
        """
        super().__init__("Binance Open Interest")
        self.symbol = symbol
        self.period = period
        self._cache_ttl = 60

    @retry_request(max_retries=3, delay=1.0)
    def fetch(self) -> DataPoint:
        """获取当前未平仓量，并结合历史数据计算变化率"""
        # 1) 实时未平仓量
        resp = requests.get(
            self.REALTIME_URL,
            params={"symbol": self.symbol},
            timeout=10,
        )
        resp.raise_for_status()
        current = resp.json()
        oi_contracts = float(current["openInterest"])

        # 2) 最近一段历史，用于计算变化率
        hist = self._fetch_history_raw(limit=30)
        oi_value_usd = 0.0
        change_pct_1h = 0.0
        change_pct_4h = 0.0
        change_pct_24h = 0.0

        if hist:
            latest = hist[-1]
            oi_value_usd = float(latest["sumOpenInterestValue"])

            def _pct(newer: float, older: float) -> float:
                return ((newer - older) / older * 100) if older else 0.0

            # 根据 period 换算需要跳过几条记录
            period_map = {"5m": (12, 48, 288), "15m": (4, 16, 96),
                          "30m": (2, 8, 48), "1h": (1, 4, 24),
                          "2h": (1, 2, 12), "4h": (1, 1, 6),
                          "6h": (1, 1, 4), "12h": (1, 1, 2),
                          "1d": (1, 1, 1)}
            steps = period_map.get(self.period, (1, 1, 6))

            for idx, label in zip(steps, ["1h", "4h", "24h"]):
                if len(hist) > idx:
                    older_val = float(hist[-(idx + 1)]["sumOpenInterestValue"])
                    pct = _pct(oi_value_usd, older_val)
                    if label == "1h":
                        change_pct_1h = pct
                    elif label == "4h":
                        change_pct_4h = pct
                    else:
                        change_pct_24h = pct

        logger.info(
            f"📊 OI: {oi_value_usd/1e9:.2f}B USD "
            f"(1h:{change_pct_1h:+.2f}% 4h:{change_pct_4h:+.2f}% 24h:{change_pct_24h:+.2f}%)"
        )

        return DataPoint(
            value=oi_value_usd,
            timestamp=datetime.now(),
            source=self.name,
            raw={
                "oi_contracts": oi_contracts,
                "oi_value_usd": oi_value_usd,
                "change_pct_1h": change_pct_1h,
                "change_pct_4h": change_pct_4h,
                "change_pct_24h": change_pct_24h,
            },
        )

    def _fetch_history_raw(self, limit: int = 30) -> List[Dict]:
        """获取历史未平仓量 (原始数据)"""
        try:
            resp = requests.get(
                self.HISTORY_URL,
                params={
                    "symbol": self.symbol,
                    "period": self.period,
                    "limit": limit,
                },
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"获取历史OI失败: {e}")
            return []

    @retry_request(max_retries=3, delay=1.0)
    def fetch_history(self, limit: int = 30) -> List[Dict]:
        """获取历史未平仓量 (格式化)"""
        raw = self._fetch_history_raw(limit)
        return [
            {
                "time": datetime.fromtimestamp(int(item["timestamp"]) / 1000),
                "oi_contracts": float(item["sumOpenInterest"]),
                "oi_value_usd": float(item["sumOpenInterestValue"]),
            }
            for item in raw
        ]

    def is_rising(self, threshold_pct: float = 1.0) -> bool:
        """OI 是否在上升 (4h 变化率超过阈值)"""
        data = self.get()
        return (data.raw.get("change_pct_4h", 0) >= threshold_pct) if data.raw else False

    def is_dropping(self, threshold_pct: float = -1.0) -> bool:
        """OI 是否在下降 (4h 变化率低于阈值)"""
        data = self.get()
        return (data.raw.get("change_pct_4h", 0) <= threshold_pct) if data.raw else False


def main():
    """测试"""
    oi = OpenInterest(symbol="BTCUSDT", period="4h")

    print("=== 实时未平仓量 ===")
    data = oi.fetch()
    print(f"OI (合约): {data.raw['oi_contracts']:,.2f} BTC")
    print(f"OI (价值): ${data.raw['oi_value_usd']:,.0f}")
    print(f"1h 变化: {data.raw['change_pct_1h']:+.2f}%")
    print(f"4h 变化: {data.raw['change_pct_4h']:+.2f}%")
    print(f"24h 变化: {data.raw['change_pct_24h']:+.2f}%")
    print(f"OI 上升中: {oi.is_rising()}")
    print(f"OI 下降中: {oi.is_dropping()}")

    print("\n=== 历史未平仓量 (最近5条) ===")
    for h in oi.fetch_history(5):
        print(f"  {h['time']}: {h['oi_contracts']:,.2f} BTC / ${h['oi_value_usd']:,.0f}")


if __name__ == "__main__":
    main()
