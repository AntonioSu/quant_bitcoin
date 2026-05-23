"""MACD (Moving Average Convergence Divergence) 信号检测

MACD 三要素:
- MACD 线 = EMA(fast) - EMA(slow)
- Signal 线 = EMA(MACD线, signal_period)
- 柱状图 (Histogram) = MACD线 - Signal线

信号类型:
- 金叉 (bullish crossover): MACD 线上穿 Signal 线
- 死叉 (bearish crossover): MACD 线下穿 Signal 线
- 零轴上方金叉: 强势做多信号
- 零轴下方死叉: 强势做空信号
- 柱状图由负转正 / 由正转负: 动能切换
"""

from typing import List
from dataclasses import dataclass
from enum import Enum

from utils import logger


class MACDSignalType(Enum):
    """MACD 信号类型"""
    NONE = "none"
    BULLISH_CROSS = "bullish_cross"    # 金叉
    BEARISH_CROSS = "bearish_cross"    # 死叉


@dataclass
class MACDResult:
    """MACD 分析结果"""
    signal_type: MACDSignalType
    above_zero: bool              # MACD 线是否在零轴上方
    histogram_rising: bool        # 柱状图是否在放大 (动能增强)
    strength: float               # 信号强度 (0-1)


def _ema(values: List[float], period: int) -> List[float]:
    """指数移动平均"""
    if not values:
        return []
    multiplier = 2.0 / (period + 1)
    result = [values[0]]
    for i in range(1, len(values)):
        result.append(values[i] * multiplier + result[-1] * (1 - multiplier))
    return result


class MACDCalculator:
    """MACD 计算与信号检测"""

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        timeframe: str = "4h",
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        self.timeframe = timeframe

    def calculate(self, klines: List[List]) -> MACDResult:
        """
        计算 MACD 并检测交叉信号

        Args:
            klines: K线数据 [[timestamp, open, high, low, close, volume], ...]
                    需要至少 slow_period + signal_period 根K线

        Returns:
            MACDResult
        """
        min_required = self.slow_period + self.signal_period
        if len(klines) < min_required:
            raise ValueError(
                f"需要至少 {min_required} 根K线，当前只有 {len(klines)} 根"
            )

        closes = [k[4] for k in klines]

        ema_fast = _ema(closes, self.fast_period)
        ema_slow = _ema(closes, self.slow_period)

        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]

        signal_line = _ema(macd_line, self.signal_period)

        histogram = [m - s for m, s in zip(macd_line, signal_line)]

        cur_macd = macd_line[-1]
        cur_signal = signal_line[-1]
        cur_hist = histogram[-1]
        prev_hist = histogram[-2]

        signal_type = MACDSignalType.NONE
        prev_diff = macd_line[-2] - signal_line[-2]
        curr_diff = cur_macd - cur_signal

        if prev_diff <= 0 < curr_diff:
            signal_type = MACDSignalType.BULLISH_CROSS
        elif prev_diff >= 0 > curr_diff:
            signal_type = MACDSignalType.BEARISH_CROSS

        if signal_type == MACDSignalType.NONE:
            lookback = min(4, len(macd_line) - 1)
            for i in range(2, lookback + 1):
                d_prev = macd_line[-(i + 1)] - signal_line[-(i + 1)]
                d_curr = macd_line[-i] - signal_line[-i]
                if d_prev <= 0 < d_curr and curr_diff > 0:
                    signal_type = MACDSignalType.BULLISH_CROSS
                    break
                elif d_prev >= 0 > d_curr and curr_diff < 0:
                    signal_type = MACDSignalType.BEARISH_CROSS
                    break

        above_zero = cur_macd > 0
        histogram_rising = cur_hist > prev_hist

        strength = self._calc_strength(
            signal_type, cur_hist, prev_hist, above_zero, histogram
        )

        if signal_type != MACDSignalType.NONE:
            label = "金叉" if signal_type == MACDSignalType.BULLISH_CROSS else "死叉"
            zone = "零轴上方" if above_zero else "零轴下方"
            logger.info(
                f"🎯 MACD {label} ({zone})! "
                f"MACD={cur_macd:.4f}, Signal={cur_signal:.4f}, "
                f"强度={strength:.2f}"
            )
        else:
            logger.debug(
                f"📊 MACD 无交叉: MACD={cur_macd:.4f}, "
                f"Signal={cur_signal:.4f}, Hist={cur_hist:.4f}"
            )

        return MACDResult(
            signal_type=signal_type,
            above_zero=above_zero,
            histogram_rising=histogram_rising,
            strength=strength,
        )

    def _calc_strength(
        self,
        signal_type: MACDSignalType,
        cur_hist: float,
        prev_hist: float,
        above_zero: bool,
        histogram: List[float],
    ) -> float:
        """
        计算信号强度 (0-1)

        加分项:
        - 金叉发生在零轴上方 / 死叉发生在零轴下方 (顺势)
        - 柱状图连续缩小后反转 (动能转换确认)
        - 交叉角度大 (MACD线与Signal线差距扩大快)
        """
        if signal_type == MACDSignalType.NONE:
            return 0.0

        score = 0.5

        is_trend_aligned = (
            (signal_type == MACDSignalType.BULLISH_CROSS and above_zero)
            or (signal_type == MACDSignalType.BEARISH_CROSS and not above_zero)
        )
        if is_trend_aligned:
            score += 0.2

        cross_speed = abs(cur_hist - prev_hist)
        avg_hist = sum(abs(h) for h in histogram[-20:]) / min(20, len(histogram))
        if avg_hist > 0:
            speed_ratio = min(cross_speed / avg_hist, 2.0)
            score += speed_ratio * 0.1

        recent_hist = histogram[-6:-1]
        if len(recent_hist) >= 3:
            if signal_type == MACDSignalType.BULLISH_CROSS:
                shrinking = all(
                    recent_hist[i] > recent_hist[i - 1]
                    for i in range(1, len(recent_hist))
                )
            else:
                shrinking = all(
                    recent_hist[i] < recent_hist[i - 1]
                    for i in range(1, len(recent_hist))
                )
            if shrinking:
                score += 0.1

        return min(score, 1.0)

    def is_bullish(self, klines: List[List]) -> bool:
        """是否出现看涨信号 (金叉)"""
        result = self.calculate(klines)
        return result.signal_type == MACDSignalType.BULLISH_CROSS

    def is_bearish(self, klines: List[List]) -> bool:
        """是否出现看跌信号 (死叉)"""
        result = self.calculate(klines)
        return result.signal_type == MACDSignalType.BEARISH_CROSS


def main():
    """测试"""
    import random

    print("=" * 60)
    print("MACD 信号检测测试")
    print("=" * 60)

    # 场景1: 模拟上涨趋势 → 金叉
    print("\n[场景1] 模拟上涨趋势 (期望金叉):")
    klines_up = []
    base_price = 65000

    for i in range(50):
        if i < 30:
            change = random.uniform(-150, 100)
        else:
            change = random.uniform(50, 300)

        open_p = base_price
        close_p = base_price + change
        high = max(open_p, close_p) + random.uniform(50, 200)
        low = min(open_p, close_p) - random.uniform(50, 200)
        volume = random.uniform(1000, 5000)

        klines_up.append([
            1700000000000 + i * 14400000,
            open_p, high, low, close_p, volume,
        ])
        base_price = close_p

    calc = MACDCalculator()
    r1 = calc.calculate(klines_up)
    print(f"  MACD:     {r1.macd_line:+.2f}")
    print(f"  Signal:   {r1.signal_line:+.2f}")
    print(f"  Hist:     {r1.histogram:+.2f}")
    print(f"  信号类型: {r1.signal_type.value}")
    print(f"  零轴上方: {r1.above_zero}")
    print(f"  柱状图增: {r1.histogram_rising}")
    print(f"  信号强度: {r1.strength:.2f}")

    # 场景2: 模拟下跌趋势 → 死叉
    print("\n[场景2] 模拟下跌趋势 (期望死叉):")
    klines_down = []
    base_price = 70000

    for i in range(50):
        if i < 30:
            change = random.uniform(-100, 150)
        else:
            change = random.uniform(-300, -50)

        open_p = base_price
        close_p = base_price + change
        high = max(open_p, close_p) + random.uniform(50, 200)
        low = min(open_p, close_p) - random.uniform(50, 200)
        volume = random.uniform(1000, 5000)

        klines_down.append([
            1700000000000 + i * 14400000,
            open_p, high, low, close_p, volume,
        ])
        base_price = close_p

    r2 = calc.calculate(klines_down)
    print(f"  信号类型: {r2.signal_type.value}")
    print(f"  零轴上方: {r2.above_zero}")
    print(f"  柱状图增: {r2.histogram_rising}")
    print(f"  信号强度: {r2.strength:.2f}")

    # 场景3: 横盘震荡
    print("\n[场景3] 横盘震荡:")
    klines_flat = []
    base_price = 65000

    for i in range(50):
        change = random.uniform(-100, 100)
        open_p = base_price
        close_p = base_price + change
        high = max(open_p, close_p) + random.uniform(50, 150)
        low = min(open_p, close_p) - random.uniform(50, 150)
        volume = random.uniform(1000, 3000)

        klines_flat.append([
            1700000000000 + i * 14400000,
            open_p, high, low, close_p, volume,
        ])
        base_price = close_p

    r3 = calc.calculate(klines_flat)
    print(f"  MACD:     {r3.macd_line:+.2f}")
    print(f"  Signal:   {r3.signal_line:+.2f}")
    print(f"  Hist:     {r3.histogram:+.2f}")
    print(f"  信号类型: {r3.signal_type.value}")
    print(f"  零轴上方: {r3.above_zero}")


if __name__ == "__main__":
    main()
