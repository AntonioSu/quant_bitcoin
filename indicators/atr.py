"""ATR (Average True Range) 指标计算

ATR 用途: 衡量市场波动性
"""

from typing import List
from dataclasses import dataclass

from utils import logger


@dataclass
class ATRResult:
    """ATR 计算结果"""
    value: float           # ATR 值
    period: int            # 计算周期
    timeframe: str         # K线周期
    true_ranges: List[float]  # 各周期 TR 值


class ATRCalculator:
    """ATR 计算器"""
    
    def __init__(self, period: int = 14, timeframe: str = "4h"):
        """
        Args:
            period: ATR 周期 (默认14)
            timeframe: K线周期 (默认4小时)
        """
        self.period = period
        self.timeframe = timeframe
    
    def calculate(self, klines: List[List]) -> ATRResult:
        """
        计算 ATR
        
        Args:
            klines: K线数据 [[timestamp, open, high, low, close, volume], ...]
                    需要至少 period + 1 根K线
        
        Returns:
            ATRResult
        """
        if len(klines) < self.period + 1:
            raise ValueError(f"需要至少 {self.period + 1} 根K线，当前只有 {len(klines)} 根")
        
        true_ranges = []
        
        for i in range(1, len(klines)):
            high = klines[i][2]
            low = klines[i][3]
            prev_close = klines[i - 1][4]
            
            # True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)
        
        recent_trs = true_ranges[-self.period:]
        atr = sum(recent_trs) / len(recent_trs)
        
        logger.debug(f"📏 ATR({self.period}, {self.timeframe}): ${atr:.2f}")
        
        return ATRResult(
            value=atr,
            period=self.period,
            timeframe=self.timeframe,
            true_ranges=true_ranges
        )
