"""MA (Moving Average) 均线信号检测

均线系统:
- 快线 EMA(7) / 慢线 EMA(25) 双均线交叉
- 价格相对均线位置

信号类型:
- 金叉 (golden_cross): 快线上穿慢线，做多
- 死叉 (death_cross): 快线下穿慢线，做空
- 多头排列 (bullish_alignment): 价格 > 快线 > 慢线
- 空头排列 (bearish_alignment): 价格 < 快线 < 慢线

辅助指标:
- 价格偏离度 = (price - slow_ma) / slow_ma
- 均线斜率: 判断趋势加速/减速
"""

from typing import List
from dataclasses import dataclass
from enum import Enum

from utils import logger


class MASignalType(Enum):
    """MA 信号类型"""
    NONE = "none"
    GOLDEN_CROSS = "golden_cross"          # 金叉
    DEATH_CROSS = "death_cross"            # 死叉
    BULLISH_ALIGNMENT = "bullish_alignment"  # 多头排列形成
    BEARISH_ALIGNMENT = "bearish_alignment"  # 空头排列形成


@dataclass
class MAResult:
    """MA 分析结果"""
    signal_type: MASignalType
    price: float                 # 当前价格
    fast_ma: float               # 快线值
    slow_ma: float               # 慢线值
    trend: str                   # "bullish" / "bearish" / "neutral"
    price_deviation: float       # 价格偏离慢线的百分比
    strength: float              # 信号强度 (0-1)


class MACalculator:
    """均线计算与信号检测"""

    def __init__(
        self,
        fast_period: int = 7,
        slow_period: int = 25,
        timeframe: str = "4h",
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.timeframe = timeframe

    def _ema(self, values: List[float], period: int) -> List[float]:
        """指数移动平均"""
        if not values:
            return []
        multiplier = 2.0 / (period + 1)
        result = [values[0]]
        for i in range(1, len(values)):
            result.append(values[i] * multiplier + result[-1] * (1 - multiplier))
        return result

    def _slope(self, series: List[float], lookback: int = 3) -> float:
        """计算均线斜率 (百分比变化/周期)"""
        if len(series) < lookback + 1:
            return 0.0
        old = series[-lookback - 1]
        if old == 0:
            return 0.0
        return (series[-1] - old) / old / lookback

    def _calc_strength(
        self,
        signal_type: MASignalType,
        price_deviation: float,
        fast_slope: float,
        slow_slope: float,
        vol_ratio: float,
    ) -> float:
        """
        计算信号强度 (0-1)

        加分项:
        - 交叉刚发生 (基础分)
        - 均线斜率越陡信号越强
        - 价格偏离度适中 (非极端偏离)
        - 放量确认
        """
        if signal_type == MASignalType.NONE:
            return 0.0

        score = 0.35

        slope_strength = abs(fast_slope - slow_slope)
        score += min(slope_strength * 500, 0.2)

        dev = abs(price_deviation)
        if dev < 0.05:
            score += 0.1
        elif dev > 0.1:
            score -= 0.05

        if vol_ratio >= 1.3:
            score += min((vol_ratio - 1.0) * 0.1, 0.15)

        if signal_type in (MASignalType.BULLISH_ALIGNMENT, MASignalType.BEARISH_ALIGNMENT):
            score += 0.1

        return max(min(score, 1.0), 0.0)

    def calculate(self, klines: List[List]) -> MAResult:
        """
        计算均线并检测信号

        Args:
            klines: K线数据 [[timestamp, open, high, low, close, volume], ...]
                    需要至少 slow_period + 2 根K线

        Returns:
            MAResult
        """
        min_required = self.slow_period + 2
        if len(klines) < min_required:
            raise ValueError(
                f"需要至少 {min_required} 根K线，当前只有 {len(klines)} 根"
            )

        closes = [k[4] for k in klines]
        volumes = [k[5] for k in klines]

        fast = self._ema(closes, self.fast_period)
        slow = self._ema(closes, self.slow_period)

        cur_price = closes[-1]
        cur_fast, prev_fast = fast[-1], fast[-2]
        cur_slow, prev_slow = slow[-1], slow[-2]

        price_deviation = (cur_price - cur_slow) / cur_slow if cur_slow > 0 else 0.0

        if cur_fast > cur_slow:
            trend = "bullish"
        elif cur_fast < cur_slow:
            trend = "bearish"
        else:
            trend = "neutral"

        vol_ratio = 1.0
        if len(volumes) >= 11:
            avg_vol = sum(volumes[-11:-1]) / 10
            if avg_vol > 0:
                vol_ratio = volumes[-1] / avg_vol

        fast_slope = self._slope(fast)
        slow_slope = self._slope(slow)

        signal_type = MASignalType.NONE

        # 金叉/死叉
        if prev_fast <= prev_slow and cur_fast > cur_slow:
            signal_type = MASignalType.GOLDEN_CROSS
        elif prev_fast >= prev_slow and cur_fast < cur_slow:
            signal_type = MASignalType.DEATH_CROSS

        # 多头/空头排列形成 (本根刚形成，前一根还不是)
        if signal_type == MASignalType.NONE:
            bullish_align = cur_price > cur_fast > cur_slow
            prev_bullish_align = closes[-2] > prev_fast > prev_slow
            bearish_align = cur_price < cur_fast < cur_slow
            prev_bearish_align = closes[-2] < prev_fast < prev_slow

            if bullish_align and not prev_bullish_align:
                signal_type = MASignalType.BULLISH_ALIGNMENT
            elif bearish_align and not prev_bearish_align:
                signal_type = MASignalType.BEARISH_ALIGNMENT

        strength = self._calc_strength(
            signal_type, price_deviation, fast_slope, slow_slope, vol_ratio
        )

        if signal_type != MASignalType.NONE:
            labels = {
                MASignalType.GOLDEN_CROSS: "金叉",
                MASignalType.DEATH_CROSS: "死叉",
                MASignalType.BULLISH_ALIGNMENT: "多头排列",
                MASignalType.BEARISH_ALIGNMENT: "空头排列",
            }
            logger.info(
                f"🎯 MA {labels[signal_type]}! "
                f"EMA{self.fast_period}={cur_fast:.2f}, "
                f"EMA{self.slow_period}={cur_slow:.2f}, "
                f"偏离={price_deviation:.2%}, 强度={strength:.2f}"
            )
        else:
            logger.debug(
                f"📊 MA 无信号: 趋势={trend}, 偏离={price_deviation:.2%}"
            )

        return MAResult(
            signal_type=signal_type,
            price=cur_price,
            fast_ma=cur_fast,
            slow_ma=cur_slow,
            trend=trend,
            price_deviation=price_deviation,
            strength=strength,
        )

    def is_bullish(self, klines: List[List]) -> bool:
        """是否出现看涨信号"""
        result = self.calculate(klines)
        return result.signal_type in (
            MASignalType.GOLDEN_CROSS,
            MASignalType.BULLISH_ALIGNMENT,
        )

    def is_bearish(self, klines: List[List]) -> bool:
        """是否出现看跌信号"""
        result = self.calculate(klines)
        return result.signal_type in (
            MASignalType.DEATH_CROSS,
            MASignalType.BEARISH_ALIGNMENT,
        )


def main():
    """测试"""
    import random

    print("=" * 60)
    print("MA 均线信号检测测试")
    print("=" * 60)

    calc = MACalculator()

    # 场景1: 连续上涨 → 金叉/多头排列
    print("\n[场景1] 连续上涨:")
    klines = []
    base = 65000
    for i in range(40):
        change = random.uniform(100, 400) if i > 15 else random.uniform(-100, 150)
        o = base
        c = base + change
        h = max(o, c) + random.uniform(50, 200)
        l = min(o, c) - random.uniform(50, 100)
        v = random.uniform(1000, 5000)
        klines.append([1700000000000 + i * 14400000, o, h, l, c, v])
        base = c

    r = calc.calculate(klines)
    print(f"  信号: {r.signal_type.value}, 趋势: {r.trend}")
    print(f"  EMA7={r.fast_ma:.0f}, EMA25={r.slow_ma:.0f}, 偏离={r.price_deviation:.2%}")
    print(f"  强度: {r.strength:.2f}")

    # 场景2: 连续下跌
    print("\n[场景2] 连续下跌:")
    klines2 = []
    base = 70000
    for i in range(40):
        change = random.uniform(-400, -100) if i > 15 else random.uniform(-100, 100)
        o = base
        c = base + change
        h = max(o, c) + random.uniform(50, 100)
        l = min(o, c) - random.uniform(50, 200)
        v = random.uniform(1000, 5000)
        klines2.append([1700000000000 + i * 14400000, o, h, l, c, v])
        base = c

    r2 = calc.calculate(klines2)
    print(f"  信号: {r2.signal_type.value}, 趋势: {r2.trend}")
    print(f"  EMA7={r2.fast_ma:.0f}, EMA25={r2.slow_ma:.0f}, 偏离={r2.price_deviation:.2%}")
    print(f"  强度: {r2.strength:.2f}")


if __name__ == "__main__":
    main()
