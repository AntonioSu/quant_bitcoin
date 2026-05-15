"""数据源基类"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class DataPoint:
    """数据点"""
    value: float
    timestamp: datetime
    source: str
    raw: Optional[Dict] = None


class DataSourceBase(ABC):
    """数据源基类"""
    
    def __init__(self, name: str):
        self.name = name
        self._cache: Optional[DataPoint] = None
        self._cache_ttl: int = 300  # 缓存有效期(秒)
    
    @abstractmethod
    def fetch(self) -> DataPoint:
        """获取最新数据"""
        pass
    
    def get(self, force_refresh: bool = False) -> DataPoint:
        """
        获取数据(带缓存)
        
        Args:
            force_refresh: 强制刷新缓存
        """
        now = datetime.now()
        
        if not force_refresh and self._cache:
            age = (now - self._cache.timestamp).total_seconds()
            if age < self._cache_ttl:
                return self._cache
        
        self._cache = self.fetch()
        return self._cache
    
    def is_above_threshold(self, threshold: float) -> bool:
        """判断当前值是否 >= 阈值"""
        return self.get().value >= threshold
    
    def is_below_threshold(self, threshold: float) -> bool:
        """判断当前值是否 <= 阈值"""
        return self.get().value <= threshold
