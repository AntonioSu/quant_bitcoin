"""交易所净流入/流出 (Exchange Netflow) 数据源

监控 BTC 在交易所的净流入流出:
- 净流入 > 0: 巨鲸将BTC转入交易所，准备抛售 → 看跌
- 净流出 < 0: 巨鲸将BTC从交易所提出，囤币 → 看涨

数据源: CoinGlass API (免费)
备选: CryptoQuant API (需 Key)

典型阈值:
- 净流入 > 5000 BTC/日: 强卖压
- 净流出 > 5000 BTC/日: 强囤币信号
"""

import requests
from datetime import datetime
from typing import Optional, Dict

from data_sources.base import DataSourceBase, DataPoint
from utils import logger, retry_request


class ExchangeNetflow(DataSourceBase):
    """交易所BTC净流入/流出

    数据源: CoinMetrics Community API (完全免费，无需Key)
    https://docs.coinmetrics.io/api/v4
    """

    COINMETRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

    def __init__(self, api_key: str = ""):
        """
        Args:
            api_key: 保留参数，当前使用免费 CoinMetrics API 无需 Key
        """
        super().__init__("Exchange Netflow")
        self._cache_ttl = 1800  # 链上数据30分钟刷新

    @retry_request(max_retries=3, delay=2.0)
    def fetch(self) -> DataPoint:
        """获取交易所BTC净流入/流出数据 (CoinMetrics)"""
        params = {
            "assets": "btc",
            "metrics": "FlowInExNtv,FlowOutExNtv",
            "frequency": "1d",
            "page_size": 2,
        }

        response = requests.get(self.COINMETRICS_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        records = data.get("data", [])
        if not records:
            raise ValueError("CoinMetrics 返回空数据")

        latest = records[-1]
        inflow = float(latest.get("FlowInExNtv", 0))
        outflow = float(latest.get("FlowOutExNtv", 0))
        netflow = inflow - outflow
        date_str = latest.get("time", "")[:10]

        logger.info(
            f"🏦 交易所净流入({date_str}): {netflow:+.1f} BTC "
            f"(in:{inflow:.0f}, out:{outflow:.0f})"
        )

        return DataPoint(
            value=netflow,
            timestamp=datetime.now(),
            source="CoinMetrics",
            raw={
                "netflow_btc": round(netflow, 2),
                "inflow_btc": round(inflow, 2),
                "outflow_btc": round(outflow, 2),
                "date": date_str,
                "signal": self._interpret(netflow),
            },
        )

    @staticmethod
    def _interpret(netflow: float) -> str:
        """解读净流入信号"""
        if netflow > 5000:
            return "strong_sell_pressure"
        elif netflow > 2000:
            return "moderate_sell_pressure"
        elif netflow < -5000:
            return "strong_accumulation"
        elif netflow < -2000:
            return "moderate_accumulation"
        return "neutral"


def main():
    """测试"""
    en = ExchangeNetflow()
    data = en.fetch()
    print(f"Exchange Netflow: {data.value:+.2f} BTC")
    print(f"Inflow: {data.raw.get('inflow_btc'):.0f} BTC")
    print(f"Outflow: {data.raw.get('outflow_btc'):.0f} BTC")
    print(f"Signal: {data.raw.get('signal')}")


if __name__ == "__main__":
    main()
