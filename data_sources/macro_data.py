"""宏观经济数据源 (DXY / 全球M2)

DXY (美元指数):
- 上升 → 美元走强 → BTC承压
- 下降 → 美元走弱 → BTC利好
- 数据源: FRED (DX-Y.NYB) 或 Alpha Vantage

全球 M2 供应量:
- 增长 → 流动性扩张 → BTC上涨
- 收缩 → 流动性紧缩 → BTC下跌
- 数据源: FRED (WM2NS)

FRED API: 免费，注册获取 Key
https://fred.stlouisfed.org/docs/api/fred/
"""

import requests
from datetime import datetime, timedelta
from typing import Optional, Dict

from .base import DataSourceBase, DataPoint
from ..utils import logger, retry_request


class MacroData(DataSourceBase):
    """宏观经济数据 (DXY + M2)"""

    FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
    DXY_SERIES = "DTWEXBGS"  # Trade Weighted U.S. Dollar Index: Broad, Goods and Services
    M2_SERIES = "WM2NS"  # M2 Money Stock (Weekly, Seasonally Adjusted)

    def __init__(self, fred_api_key: str = ""):
        """
        Args:
            fred_api_key: FRED API Key (免费注册: https://fred.stlouisfed.org/docs/api/api_key.html)
        """
        super().__init__("Macro Data")
        self.fred_api_key = fred_api_key
        self._cache_ttl = 14400  # 宏观数据每周更新，缓存4小时

    @retry_request(max_retries=3, delay=2.0)
    def fetch(self) -> DataPoint:
        """获取 DXY 和 M2 数据"""
        dxy_data = self._fetch_fred_series(self.DXY_SERIES, "DXY")
        m2_data = self._fetch_fred_series(self.M2_SERIES, "M2")

        dxy_value = dxy_data.get("value", 0)
        dxy_change = dxy_data.get("change_pct", 0)
        m2_value = m2_data.get("value", 0)
        m2_change = m2_data.get("change_pct", 0)

        logger.info(
            f"🌍 宏观数据 - DXY: {dxy_value:.2f} ({dxy_change:+.2f}%), "
            f"M2: ${m2_value:.0f}B ({m2_change:+.2f}%)"
        )

        composite_signal = self._compute_signal(dxy_change, m2_change)

        return DataPoint(
            value=composite_signal,
            timestamp=datetime.now(),
            source="FRED",
            raw={
                "dxy": {
                    "value": dxy_value,
                    "change_pct_4w": dxy_change,
                    "trend": "strengthening" if dxy_change > 0 else "weakening",
                },
                "m2": {
                    "value_billion": m2_value,
                    "change_pct_4w": m2_change,
                    "trend": "expanding" if m2_change > 0 else "contracting",
                },
                "signal": "bullish" if composite_signal > 0 else ("bearish" if composite_signal < 0 else "neutral"),
            },
        )

    def _fetch_fred_series(self, series_id: str, label: str) -> Dict:
        """从 FRED 获取时间序列最近两个数据点"""
        if not self.fred_api_key:
            return {"value": 0, "change_pct": 0}

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

        params = {
            "series_id": series_id,
            "api_key": self.fred_api_key,
            "file_type": "json",
            "observation_start": start_date,
            "observation_end": end_date,
            "sort_order": "desc",
            "limit": 10,
        }

        response = requests.get(self.FRED_BASE_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        observations = data.get("observations", [])
        valid_obs = [
            o for o in observations
            if o.get("value") and o["value"] != "."
        ]

        if len(valid_obs) < 2:
            logger.warning(f"FRED {label}: 数据不足")
            return {"value": 0, "change_pct": 0}

        current = float(valid_obs[0]["value"])
        previous = float(valid_obs[-1]["value"])
        change_pct = ((current - previous) / previous) * 100 if previous else 0

        return {"value": current, "change_pct": change_pct}

    @staticmethod
    def _compute_signal(dxy_change: float, m2_change: float) -> float:
        """
        综合信号: -100 ~ +100
        - DXY 下降 + M2 上升 → 看涨BTC
        - DXY 上升 + M2 下降 → 看跌BTC
        """
        dxy_score = -dxy_change * 10  # DXY 涨对BTC利空
        m2_score = m2_change * 10  # M2 涨对BTC利好
        composite = max(-100, min(100, (dxy_score + m2_score) / 2))
        return round(composite, 1)


def main():
    """测试"""
    import os
    fred_key = os.getenv("FRED_API_KEY", "")
    if not fred_key:
        print("需要设置 FRED_API_KEY 环境变量")
        return
    macro = MacroData(fred_api_key=fred_key)
    data = macro.fetch()
    print(f"Macro signal: {data.value}")
    print(f"DXY: {data.raw['dxy']}")
    print(f"M2: {data.raw['m2']}")


if __name__ == "__main__":
    main()
