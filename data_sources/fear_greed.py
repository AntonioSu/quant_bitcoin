"""Fear & Greed Index 数据源

数据来源: https://alternative.me/crypto/fear-and-greed-index/
API: https://api.alternative.me/fng/

指数范围:
- 0-24: 极度恐惧 (Extreme Fear)
- 25-49: 恐惧 (Fear)
- 50-74: 贪婪 (Greed)
- 75-100: 极度贪婪 (Extreme Greed)
"""

import requests
from datetime import datetime
from typing import Optional

from data_sources.base import DataSourceBase, DataPoint
from utils import logger, retry_request


class FearGreedIndex(DataSourceBase):
    """加密货币恐惧与贪婪指数"""
    
    API_URL = "https://api.alternative.me/fng/"
    
    def __init__(self):
        super().__init__("Fear & Greed Index")
        self._cache_ttl = 3600  # F&G 指数每日更新，缓存1小时
    
    @retry_request(max_retries=3, delay=2.0)
    def fetch(self) -> DataPoint:
        """获取最新 F&G 指数"""
        response = requests.get(self.API_URL, params={"limit": 1}, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if data.get("metadata", {}).get("error"):
            raise ValueError(f"API Error: {data['metadata']['error']}")
        
        fng_data = data["data"][0]
        value = int(fng_data["value"])
        ts = int(fng_data["timestamp"])
        classification = fng_data["value_classification"]
        
        logger.info(f"📊 F&G 指数: {value} ({classification})")
        
        return DataPoint(
            value=value,
            timestamp=datetime.fromtimestamp(ts),
            source=self.name,
            raw={
                "classification": classification,
                "time_until_update": fng_data.get("time_until_update"),
            }
        )
    
    def get_classification(self) -> str:
        """获取当前分类"""
        data = self.get()
        return data.raw.get("classification", "Unknown") if data.raw else "Unknown"
    
    def is_extreme_greed(self, threshold: int = 75) -> bool:
        """是否处于极度贪婪 (神盾模式触发条件之一)"""
        return self.is_above_threshold(threshold)
    
    def is_extreme_fear(self, threshold: int = 25) -> bool:
        """是否处于极度恐惧 (长矛模式触发条件之一)"""
        return self.is_below_threshold(threshold)


def main():
    """测试"""
    fng = FearGreedIndex()
    data = fng.fetch()
    print(f"F&G Index: {data.value}")
    print(f"Classification: {fng.get_classification()}")
    print(f"Extreme Greed (>=75): {fng.is_extreme_greed()}")
    print(f"Extreme Fear (<=25): {fng.is_extreme_fear()}")


if __name__ == "__main__":
    main()
