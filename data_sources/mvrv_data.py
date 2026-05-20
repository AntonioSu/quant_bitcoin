"""MVRV Z-Score 数据源

MVRV (Market Value to Realized Value):
- Market Value (MV): 当前市值 = 价格 × 流通量
- Realized Value (RV): 已实现市值 = 每个UTXO最后移动时的价格总和

MVRV Z-Score = (MV - RV) / std(MV)
- Z-Score > 7: 历史顶部区域 → 极度高估
- Z-Score > 3: 高估区域 → 谨慎做多
- Z-Score 1~3: 正常偏高
- Z-Score 0~1: 正常/低估
- Z-Score < 0: 历史底部区域 → 极好买入机会

数据源:
1. CoinGlass API (免费，有 MVRV)
2. Blockchain.info (免费，可自己算)
3. CryptoQuant (付费)
"""

import requests
from datetime import datetime
from typing import Optional, Dict

from .base import DataSourceBase, DataPoint
from ..utils import logger, retry_request


class MVRVData(DataSourceBase):
    """MVRV Z-Score 数据

    数据源: CoinMetrics Community API (完全免费)
    CapMVRVCur = Market Cap / Realized Cap

    MVRV 解读:
    - > 3.5: 历史顶部区域
    - 2.5~3.5: 高估
    - 1.0~2.5: 正常
    - < 1.0: 低估/底部
    """

    COINMETRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

    def __init__(self, coinglass_api_key: str = ""):
        """
        Args:
            coinglass_api_key: 保留参数，当前使用免费 CoinMetrics
        """
        super().__init__("MVRV Z-Score")
        self._cache_ttl = 14400  # MVRV 链上数据变化慢，缓存4小时

    @retry_request(max_retries=3, delay=2.0)
    def fetch(self) -> DataPoint:
        """从 CoinMetrics 获取 MVRV"""
        params = {
            "assets": "btc",
            "metrics": "CapMVRVCur",
            "frequency": "1d",
            "page_size": 30,
        }

        response = requests.get(self.COINMETRICS_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        records = data.get("data", [])
        if not records:
            raise ValueError("CoinMetrics MVRV 返回空数据")

        latest = records[-1]
        mvrv = float(latest.get("CapMVRVCur", 0))
        date_str = latest.get("time", "")[:10]

        # 计算近似 Z-Score: 基于 30 天数据的标准差
        values = [float(r["CapMVRVCur"]) for r in records if r.get("CapMVRVCur")]
        mean_mvrv = sum(values) / len(values) if values else mvrv
        std_mvrv = (sum((v - mean_mvrv) ** 2 for v in values) / len(values)) ** 0.5 if len(values) > 1 else 0.1
        z_score = (mvrv - mean_mvrv) / std_mvrv if std_mvrv > 0 else 0

        # 更直观的周期判断直接用 MVRV 值
        zone = self._classify_zone_by_mvrv(mvrv)
        signal = self._compute_signal_by_mvrv(mvrv)

        logger.info(f"📈 MVRV: {mvrv:.3f} ({zone}), 30d Z: {z_score:.2f}")

        return DataPoint(
            value=mvrv,
            timestamp=datetime.now(),
            source="CoinMetrics",
            raw={
                "mvrv": round(mvrv, 4),
                "z_score_30d": round(z_score, 2),
                "date": date_str,
                "zone": zone,
                "signal": signal,
            },
        )

    @staticmethod
    def _classify_zone_by_mvrv(mvrv: float) -> str:
        """根据 MVRV 绝对值判断周期"""
        if mvrv > 3.5:
            return "extreme_top"
        elif mvrv > 2.5:
            return "overvalued"
        elif mvrv > 1.5:
            return "fair_high"
        elif mvrv > 1.0:
            return "fair_low"
        elif mvrv > 0.8:
            return "undervalued"
        return "extreme_bottom"

    @staticmethod
    def _compute_signal_by_mvrv(mvrv: float) -> str:
        """MVRV 交易信号"""
        if mvrv > 3.5:
            return "strong_sell"
        elif mvrv > 2.5:
            return "sell"
        elif mvrv > 2.0:
            return "caution"
        elif mvrv < 0.8:
            return "strong_buy"
        elif mvrv < 1.0:
            return "buy"
        return "neutral"

def main():
    """测试"""
    mvrv = MVRVData()
    data = mvrv.fetch()
    print(f"MVRV: {data.value:.4f}")
    print(f"Zone: {data.raw.get('zone')}")
    print(f"Signal: {data.raw.get('signal')}")


if __name__ == "__main__":
    main()
