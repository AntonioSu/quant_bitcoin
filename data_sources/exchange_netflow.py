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
from typing import Dict, List

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
        records = self._fetch_records(limit=2)
        if not records:
            raise ValueError("CoinMetrics 返回空数据")

        latest = records[-1]
        item = self._format_record(latest)

        logger.info(
            f"🏦 交易所净流入({item['date']}): {item['netflow_btc']:+.1f} BTC "
            f"(in:{item['inflow_btc']:.0f}, out:{item['outflow_btc']:.0f})"
        )

        return DataPoint(
            value=item["netflow_btc"],
            timestamp=datetime.now(),
            source="CoinMetrics",
            raw=item,
        )

    def _fetch_records(self, limit: int = 30) -> List[Dict]:
        """获取 CoinMetrics 原始日频记录。"""
        params = {
            "assets": "btc",
            "metrics": "FlowInExNtv,FlowOutExNtv",
            "frequency": "1d",
            "page_size": max(1, limit),
        }

        response = requests.get(self.COINMETRICS_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])

    def _format_record(self, record: Dict) -> Dict:
        """格式化单条 CoinMetrics 记录，统一正负号语义。"""
        inflow = float(record.get("FlowInExNtv", 0))
        outflow = float(record.get("FlowOutExNtv", 0))
        netflow = inflow - outflow
        return {
            "date": record.get("time", "")[:10],
            "netflow_btc": round(netflow, 2),
            "inflow_btc": round(inflow, 2),
            "outflow_btc": round(outflow, 2),
            "signal": self._interpret(netflow),
        }

    @retry_request(max_retries=3, delay=2.0)
    def fetch_history(self, limit: int = 180) -> List[Dict]:
        """获取交易所每日流入/流出历史，按日期升序返回。"""
        records = self._fetch_records(limit=limit)
        result = []
        seen = set()
        for record in records:
            item = self._format_record(record)
            date = item.get("date")
            if not date or date in seen:
                continue
            seen.add(date)
            result.append(item)
        return result

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
