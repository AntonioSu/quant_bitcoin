"""CVD (Cumulative Volume Delta) 底背离检测

CVD 说明:
- Volume Delta = 主动买量 - 主动卖量
- CVD = Volume Delta 的累计值
- CVD 上升表示买方力量强，CVD 下降表示卖方力量强

底背离信号:
- 价格横盘或微跌
- 但 CVD 断崖式暴跌 (说明卖方力量耗尽)
- 常出现在市场投降底部

长矛模式使用:
- 检测 6 根 4H K线的底背离
- 价格下跌 < 3%, CVD 下跌 > 20%
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from utils import logger


class DivergenceType(Enum):
    """背离类型"""
    NONE = "none"
    BULLISH = "bullish"      # 底背离 (看涨)
    BEARISH = "bearish"      # 顶背离 (看跌)


@dataclass
class CVDResult:
    """CVD 分析结果"""
    cvd_values: List[float]       # CVD 序列
    price_change_pct: float       # 价格变化百分比
    cvd_change_pct: float         # CVD 变化百分比
    divergence: DivergenceType    # 背离类型
    strength: float               # 背离强度 (0-1)
    is_valid_signal: bool         # 是否有效信号


class CVDDivergenceDetector:
    """CVD 底背离检测器"""
    
    def __init__(
        self,
        lookback_periods: int = 6,
        timeframe: str = "4h",
        price_threshold_pct: float = 3.0,   # 价格变化阈值
        cvd_threshold_pct: float = 20.0     # CVD 变化阈值
    ):
        """
        Args:
            lookback_periods: 回看周期数 (默认6根K线)
            timeframe: K线周期
            price_threshold_pct: 价格变化阈值 (低于此值视为横盘/微跌)
            cvd_threshold_pct: CVD 下跌阈值 (高于此值视为断崖暴跌)
        """
        self.lookback_periods = lookback_periods
        self.timeframe = timeframe
        self.price_threshold = price_threshold_pct
        self.cvd_threshold = cvd_threshold_pct
    
    def calculate_cvd(self, klines: List[List]) -> List[float]:
        """
        计算 CVD 序列
        
        简化计算方法:
        - 如果 close > open, 视为买方主导, delta = volume
        - 如果 close < open, 视为卖方主导, delta = -volume
        - 如果 close == open, 视为中性, delta = 0
        
        注意: 真实 CVD 需要逐笔成交数据，这里是简化版本
        
        Args:
            klines: [[timestamp, open, high, low, close, volume], ...]
        """
        cvd = []
        cumulative = 0.0
        
        for k in klines:
            open_p, close_p, volume = k[1], k[4], k[5]
            
            if close_p > open_p:
                delta = volume
            elif close_p < open_p:
                delta = -volume
            else:
                delta = 0
            
            cumulative += delta
            cvd.append(cumulative)
        
        return cvd
    
    def detect(self, klines: List[List]) -> CVDResult:
        """
        检测底背离
        
        Args:
            klines: K线数据 (需要至少 lookback_periods + 1 根)
        
        Returns:
            CVDResult
        """
        if len(klines) < self.lookback_periods + 1:
            raise ValueError(
                f"需要至少 {self.lookback_periods + 1} 根K线，"
                f"当前只有 {len(klines)} 根"
            )
        
        # 取最近的 K线
        recent = klines[-self.lookback_periods:]
        
        # 计算 CVD
        cvd_full = self.calculate_cvd(klines)
        cvd_recent = cvd_full[-self.lookback_periods:]
        
        # 计算价格变化
        start_price = recent[0][4]  # 第一根收盘价
        end_price = recent[-1][4]   # 最后一根收盘价
        price_change = end_price - start_price
        price_change_pct = (price_change / start_price) * 100 if start_price > 0 else 0
        
        # 计算 CVD 变化
        start_cvd = cvd_recent[0]
        end_cvd = cvd_recent[-1]
        cvd_change = end_cvd - start_cvd
        cvd_change_pct = (cvd_change / abs(start_cvd)) * 100 if start_cvd != 0 else 0
        
        # 检测背离
        divergence = DivergenceType.NONE
        strength = 0.0
        is_valid = False
        
        # 底背离条件: 价格微跌/横盘 + CVD 暴跌
        if price_change_pct > -self.price_threshold and cvd_change_pct < -self.cvd_threshold:
            divergence = DivergenceType.BULLISH
            # 强度 = CVD 下跌幅度 / 阈值 (标准化到 0-1)
            strength = min(abs(cvd_change_pct) / (self.cvd_threshold * 2), 1.0)
            is_valid = True
            logger.info(
                f"🎯 检测到底背离! 价格变化={price_change_pct:+.2f}%, "
                f"CVD变化={cvd_change_pct:+.2f}%, 强度={strength:.2f}"
            )
        
        # 顶背离条件: 价格微涨/横盘 + CVD 暴涨 (用于神盾模式参考)
        elif price_change_pct < self.price_threshold and cvd_change_pct > self.cvd_threshold:
            divergence = DivergenceType.BEARISH
            strength = min(abs(cvd_change_pct) / (self.cvd_threshold * 2), 1.0)
            is_valid = True
            logger.info(
                f"🎯 检测到顶背离! 价格变化={price_change_pct:+.2f}%, "
                f"CVD变化={cvd_change_pct:+.2f}%, 强度={strength:.2f}"
            )
        else:
            logger.debug(
                f"📊 无背离: 价格变化={price_change_pct:+.2f}%, "
                f"CVD变化={cvd_change_pct:+.2f}%"
            )
        
        return CVDResult(
            cvd_values=cvd_recent,
            price_change_pct=price_change_pct,
            cvd_change_pct=cvd_change_pct,
            divergence=divergence,
            strength=strength,
            is_valid_signal=is_valid
        )
    
    def is_bullish_divergence(self, klines: List[List]) -> bool:
        """
        是否存在底背离 (长矛模式触发条件之一)
        """
        result = self.detect(klines)
        return result.divergence == DivergenceType.BULLISH
    
    def is_bearish_divergence(self, klines: List[List]) -> bool:
        """
        是否存在顶背离 (可作为神盾模式辅助信号)
        """
        result = self.detect(klines)
        return result.divergence == DivergenceType.BEARISH


def main():
    """测试"""
    import random
    
    # 模拟底背离数据: 价格横盘但成交量大幅下降
    print("=" * 60)
    print("CVD 底背离检测测试")
    print("=" * 60)
    
    # 场景1: 底背离 (价格微跌，卖方力量耗尽)
    print("\n[场景1] 模拟底背离:")
    klines_bullish = []
    base_price = 65000
    
    for i in range(10):
        # 价格微跌 (每根跌约0.3%)
        open_p = base_price
        close_p = base_price - base_price * 0.003
        high = max(open_p, close_p) + 100
        low = min(open_p, close_p) - 100
        
        # 成交量递减 (卖方力量耗尽)
        volume = 5000 - i * 400
        
        klines_bullish.append([
            1700000000000 + i * 14400000,
            open_p, high, low, close_p, volume
        ])
        base_price = close_p
    
    detector = CVDDivergenceDetector(lookback_periods=6)
    result = detector.detect(klines_bullish)
    
    print(f"  价格变化: {result.price_change_pct:+.2f}%")
    print(f"  CVD 变化: {result.cvd_change_pct:+.2f}%")
    print(f"  背离类型: {result.divergence.value}")
    print(f"  信号强度: {result.strength:.2f}")
    print(f"  有效信号: {result.is_valid_signal}")
    
    # 场景2: 无背离 (价格和CVD同向)
    print("\n[场景2] 无背离 (正常趋势):")
    klines_normal = []
    base_price = 65000
    
    for i in range(10):
        open_p = base_price
        close_p = base_price + random.uniform(-200, 200)
        high = max(open_p, close_p) + 100
        low = min(open_p, close_p) - 100
        volume = random.uniform(2000, 4000)
        
        klines_normal.append([
            1700000000000 + i * 14400000,
            open_p, high, low, close_p, volume
        ])
        base_price = close_p
    
    result2 = detector.detect(klines_normal)
    print(f"  价格变化: {result2.price_change_pct:+.2f}%")
    print(f"  CVD 变化: {result2.cvd_change_pct:+.2f}%")
    print(f"  背离类型: {result2.divergence.value}")
    print(f"  有效信号: {result2.is_valid_signal}")


if __name__ == "__main__":
    main()
