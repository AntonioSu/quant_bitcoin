"""稳定币供应与流入数据源

核心逻辑:
- 稳定币总供应量增长 → 新资金入场 → BTC看涨
- 稳定币总供应量下降 → 资金外流 → BTC看跌
- 稳定币交易所余额增加 → 购买力就位 → 短期看涨

监控对象: USDT + USDC (占稳定币市场 90%+)

数据源: DefiLlama API (完全免费，无需Key)
https://stablecoins.llama.fi/
"""

import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

from data_sources.base import DataSourceBase, DataPoint
from utils import logger, retry_request


class StablecoinFlow(DataSourceBase):
    """稳定币供应量与流动性"""

    DEFILLAMA_BASE = "https://stablecoins.llama.fi"
    STABLECOINS_URL = f"{DEFILLAMA_BASE}/stablecoins"
    STABLECOIN_CHART_URL = f"{DEFILLAMA_BASE}/stablecoincharts/all"
    STABLECOIN_HISTORY_URL = f"{DEFILLAMA_BASE}/stablecoin"

    # 主要稳定币 ID (DefiLlama)
    USDT_ID = 1
    USDC_ID = 2

    def __init__(self):
        super().__init__("Stablecoin Flow")
        self._cache_ttl = 3600  # 稳定币供应量每日更新，缓存1小时

    @retry_request(max_retries=3, delay=2.0)
    def fetch(self) -> DataPoint:
        """获取稳定币供应量及变化"""
        total_supply, supply_details = self._fetch_total_supply()
        change_7d_pct = self._calculate_supply_change(7)
        change_30d_pct = self._calculate_supply_change(30)

        signal = self._compute_signal(change_7d_pct, change_30d_pct)

        logger.info(
            f"💵 稳定币总供应: ${total_supply / 1e9:.1f}B "
            f"(7d: {change_7d_pct:+.2f}%, 30d: {change_30d_pct:+.2f}%)"
        )

        return DataPoint(
            value=change_7d_pct,
            timestamp=datetime.now(),
            source="DefiLlama",
            raw={
                "total_supply_usd": total_supply,
                "total_supply_b": round(total_supply / 1e9, 2),
                "usdt_supply": supply_details.get("usdt", 0),
                "usdc_supply": supply_details.get("usdc", 0),
                "change_7d_pct": round(change_7d_pct, 3),
                "change_30d_pct": round(change_30d_pct, 3),
                "signal": signal,
            },
        )

    def _fetch_total_supply(self) -> Tuple[float, Dict]:
        """获取当前稳定币总供应量"""
        response = requests.get(
            self.STABLECOINS_URL,
            params={"includePrices": "false"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        stablecoins = data.get("peggedAssets", [])
        total_supply = 0.0
        usdt_supply = 0.0
        usdc_supply = 0.0

        for coin in stablecoins:
            circulating = coin.get("circulating", {})
            peg_usd = float(circulating.get("peggedUSD", 0) or 0)
            total_supply += peg_usd

            coin_id = coin.get("id")
            if coin_id == self.USDT_ID:
                usdt_supply = peg_usd
            elif coin_id == self.USDC_ID:
                usdc_supply = peg_usd

        return total_supply, {
            "usdt": usdt_supply,
            "usdc": usdc_supply,
        }

    def _calculate_supply_change(self, days: int) -> float:
        """计算N天供应量变化百分比"""
        try:
            response = requests.get(self.STABLECOIN_CHART_URL, timeout=15)
            response.raise_for_status()
            data = response.json()

            if not data or len(data) < days + 1:
                return 0.0

            current = self._extract_total(data[-1])
            past = self._extract_total(data[-(days + 1)])

            if past == 0:
                return 0.0

            return ((current - past) / past) * 100

        except Exception as e:
            logger.warning(f"计算稳定币{days}d变化失败: {e}")
            return 0.0

    @staticmethod
    def _extract_total(entry: Dict) -> float:
        """从chart数据点提取总供应量"""
        total = entry.get("totalCirculating", {})
        return float(total.get("peggedUSD", 0) or 0)

    @staticmethod
    def _compute_signal(change_7d: float, change_30d: float) -> str:
        """综合判断资金流向"""
        if change_7d > 1.0 and change_30d > 2.0:
            return "strong_inflow"
        elif change_7d > 0.3:
            return "moderate_inflow"
        elif change_7d < -1.0 and change_30d < -2.0:
            return "strong_outflow"
        elif change_7d < -0.3:
            return "moderate_outflow"
        return "neutral"


def main():
    """测试"""
    sc = StablecoinFlow()
    data = sc.fetch()
    print(f"Stablecoin 7d change: {data.value:+.3f}%")
    print(f"Total supply: ${data.raw['total_supply_b']}B")
    print(f"USDT: ${data.raw['usdt_supply']/1e9:.1f}B")
    print(f"USDC: ${data.raw['usdc_supply']/1e9:.1f}B")
    print(f"Signal: {data.raw['signal']}")


if __name__ == "__main__":
    main()
