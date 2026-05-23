"""币安聪明钱数据源（Top Trader Long/Short Ratio）

聪明钱说明:
- 监控 Top 20% 大户的多空持仓比例
- longAccount: 做多账户占比
- shortAccount: 做空账户占比
- longShortRatio: 多空比率 = longAccount / shortAccount

交易策略:
- 高多空比 (>2.0): 大户看多，市场可能过热，适合做空（神盾模式）
- 低多空比 (<0.5): 大户看空，市场可能超跌，适合做多（长矛模式）
"""

import requests
from datetime import datetime
from typing import Optional, List, Dict

from data_sources.base import DataSourceBase, DataPoint
from utils import logger, retry_request


class TopTraderRatio(DataSourceBase):
    """币安聪明钱（Top Trader）多空比数据源"""
    
    # 币安合约API
    FUTURES_URL = "https://fapi.binance.com/futures/data/topLongShortAccountRatio"
    
    def __init__(self, symbol: str = "BTCUSDT", period: str = "1h"):
        """
        Args:
            symbol: 交易对 (如 BTCUSDT)
            period: 时间周期 (5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d)
        """
        super().__init__("Top Trader Ratio")
        self.symbol = symbol
        self.period = period
        self._cache_ttl = 300  # 缓存5分钟
    
    @retry_request(max_retries=3, delay=1.0)
    def fetch(self) -> DataPoint:
        """获取当前聪明钱多空比"""
        params = {
            "symbol": self.symbol,
            "period": self.period,
            "limit": 1  # 只获取最新数据
        }
        
        response = requests.get(self.FUTURES_URL, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if not data:
            raise ValueError("未获取到数据")
        
        latest = data[0]
        
        long_account = float(latest["longAccount"])
        short_account = float(latest["shortAccount"])
        long_short_ratio = float(latest["longShortRatio"])
        timestamp = int(latest["timestamp"])
        
        logger.info(
            f"💰 聪明钱: 多 {long_account:.2%} / 空 {short_account:.2%}, "
            f"比率 {long_short_ratio:.2f}"
        )
        
        return DataPoint(
            value=long_short_ratio,
            timestamp=datetime.fromtimestamp(timestamp / 1000),
            source=self.name,
            raw={
                "long_account": long_account,
                "short_account": short_account,
                "long_short_ratio": long_short_ratio,
                "symbol": self.symbol,
                "period": self.period,
            }
        )
    
    @retry_request(max_retries=3, delay=1.0)
    def fetch_history(self, limit: int = 100) -> List[Dict]:
        """获取历史聪明钱数据"""
        params = {
            "symbol": self.symbol,
            "period": self.period,
            "limit": min(limit, 500)  # 最大500
        }
        
        response = requests.get(self.FUTURES_URL, params=params, timeout=10)
        response.raise_for_status()
        
        history = []
        for item in response.json():
            history.append({
                "time": datetime.fromtimestamp(int(item["timestamp"]) / 1000),
                "long_account": float(item["longAccount"]),
                "short_account": float(item["shortAccount"]),
                "long_short_ratio": float(item["longShortRatio"]),
            })
        
        return history
    
    def is_bullish_extreme(self, threshold: float = 2.0) -> bool:
        """
        是否过度看多（神盾模式触发条件）
        
        Args:
            threshold: 多空比阈值（如 2.0 表示做多账户是做空的2倍）
        """
        return self.is_above_threshold(threshold)
    
    def is_bearish_extreme(self, threshold: float = 0.5) -> bool:
        """
        是否过度看空（长矛模式触发条件）
        
        Args:
            threshold: 多空比阈值（如 0.5 表示做空账户是做多的2倍）
        """
        return self.is_below_threshold(threshold)
    
    def get_sentiment(self) -> str:
        """获取市场情绪"""
        data = self.get()
        ratio = data.value
        
        if ratio > 2.0:
            return "极度看多（过热）"
        elif ratio > 1.5:
            return "看多"
        elif ratio > 1.0:
            return "偏多"
        elif ratio > 0.67:
            return "偏空"
        elif ratio > 0.5:
            return "看空"
        else:
            return "极度看空（超跌）"


def main():
    """测试"""
    import os
    from pathlib import Path
    
    # 加载 .env 文件
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
    
    tt = TopTraderRatio()
    
    print("=" * 50)
    print("聪明钱数据测试 (Top Trader)")
    print("=" * 50)
    
    data = tt.fetch()
    print(f"\n当前数据:")
    print(f"  多空比: {data.value:.2f}")
    print(f"  做多账户: {data.raw['long_account']:.2%}")
    print(f"  做空账户: {data.raw['short_account']:.2%}")
    print(f"  市场情绪: {tt.get_sentiment()}")
    print(f"  过度看多 (>2.0): {tt.is_bullish_extreme()}")
    print(f"  过度看空 (<0.5): {tt.is_bearish_extreme()}")
    
    print("\n历史数据 (最近5次):")
    for h in tt.fetch_history(5):
        print(f"  {h['time']}: 比率 {h['long_short_ratio']:.2f} "
              f"(多 {h['long_account']:.1%} / 空 {h['short_account']:.1%})")


if __name__ == "__main__":
    main()
