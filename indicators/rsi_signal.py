"""RSI (Relative Strength Index) 信号检测

RSI 核心逻辑:
- RSI = 100 - 100 / (1 + RS)
- RS = 平均涨幅 / 平均跌幅 (Wilder 平滑)

信号类型:
- 超买 (overbought): RSI > 70，可能回调
- 超卖 (oversold): RSI < 30，可能反弹
- 看涨背离 (bullish divergence): 价格新低但 RSI 未新低
- 看跌背离 (bearish divergence): 价格新高但 RSI 未新高
- 中轴突破 (centerline cross): RSI 穿越 50 线确认趋势
"""

from typing import List
from dataclasses import dataclass
from enum import Enum

from ..utils import logger


class RSISignalType(Enum):
    """RSI 信号类型"""
    NONE = "none"
    OVERBOUGHT = "overbought"              # 超买
    OVERSOLD = "oversold"                  # 超卖
    BULLISH_DIVERGENCE = "bullish_divergence"  # 看涨背离
    BEARISH_DIVERGENCE = "bearish_divergence"  # 看跌背离


@dataclass
class RSIResult:
    """RSI 分析结果"""
    signal_type: RSISignalType
    rsi_value: float               # 当前 RSI 值
    above_center: bool             # RSI 是否在 50 上方
    trend_strength: str            # "strong_bull" / "bull" / "neutral" / "bear" / "strong_bear"
    strength: float                # 信号强度 (0-1)


class RSICalculator:
    """RSI 计算与信号检测"""

    def __init__(
        self,
        period: int = 14,
        overbought: float = 70.0,
        oversold: float = 30.0,
        divergence_lookback: int = 14,
        timeframe: str = "4h",
    ):
        self.period = period
        self.overbought = overbought
        self.oversold = oversold
        self.divergence_lookback = divergence_lookback
        self.timeframe = timeframe

    def _compute_rsi(self, closes: List[float]) -> List[float]:
        """Wilder 平滑法计算 RSI 序列"""
        if len(closes) < self.period + 1:
            return []

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        gains = [max(d, 0) for d in deltas[:self.period]]
        losses = [abs(min(d, 0)) for d in deltas[:self.period]]
        avg_gain = sum(gains) / self.period
        avg_loss = sum(losses) / self.period

        rsi_values = []
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - 100.0 / (1.0 + rs))

        for i in range(self.period, len(deltas)):
            gain = max(deltas[i], 0)
            loss = abs(min(deltas[i], 0))
            avg_gain = (avg_gain * (self.period - 1) + gain) / self.period
            avg_loss = (avg_loss * (self.period - 1) + loss) / self.period

            if avg_loss == 0:
                rsi_values.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_values.append(100.0 - 100.0 / (1.0 + rs))

        return rsi_values

    def _detect_divergence(
        self, closes: List[float], rsi_values: List[float]
    ) -> RSISignalType:
        """检测价格与 RSI 之间的背离"""
        lookback = min(self.divergence_lookback, len(rsi_values) - 1, len(closes) - 1)
        if lookback < 5:
            return RSISignalType.NONE

        recent_closes = closes[-lookback:]
        recent_rsi = rsi_values[-lookback:]

        price_low_idx = recent_closes.index(min(recent_closes))
        price_high_idx = recent_closes.index(max(recent_closes))

        cur_price = recent_closes[-1]
        cur_rsi = recent_rsi[-1]

        if (
            cur_price <= recent_closes[price_low_idx] * 1.005
            and cur_rsi > recent_rsi[price_low_idx] + 2
            and cur_rsi < 45
        ):
            return RSISignalType.BULLISH_DIVERGENCE

        if (
            cur_price >= recent_closes[price_high_idx] * 0.995
            and cur_rsi < recent_rsi[price_high_idx] - 2
            and cur_rsi > 55
        ):
            return RSISignalType.BEARISH_DIVERGENCE

        return RSISignalType.NONE

    def _classify_trend(self, rsi: float) -> str:
        """根据 RSI 值判定趋势强度"""
        if rsi >= 70:
            return "strong_bull"
        elif rsi >= 55:
            return "bull"
        elif rsi >= 45:
            return "neutral"
        elif rsi >= 30:
            return "bear"
        else:
            return "strong_bear"

    def _calc_strength(
        self, signal_type: RSISignalType, rsi: float, rsi_values: List[float]
    ) -> float:
        """
        计算信号强度 (0-1)

        加分项:
        - RSI 越极端 (远离 30/70) 信号越强
        - RSI 在超买/超卖区停留时间长后反转
        - 背离配合极端值
        """
        if signal_type == RSISignalType.NONE:
            return 0.0

        score = 0.4

        if signal_type == RSISignalType.OVERBOUGHT:
            excess = (rsi - self.overbought) / (100 - self.overbought)
            score += min(excess * 0.4, 0.3)
        elif signal_type == RSISignalType.OVERSOLD:
            excess = (self.oversold - rsi) / self.oversold
            score += min(excess * 0.4, 0.3)
        elif signal_type in (
            RSISignalType.BULLISH_DIVERGENCE,
            RSISignalType.BEARISH_DIVERGENCE,
        ):
            score += 0.2

        recent = rsi_values[-5:]
        if signal_type == RSISignalType.OVERBOUGHT:
            time_in_zone = sum(1 for r in recent if r > self.overbought)
            score += min(time_in_zone * 0.05, 0.15)
        elif signal_type == RSISignalType.OVERSOLD:
            time_in_zone = sum(1 for r in recent if r < self.oversold)
            score += min(time_in_zone * 0.05, 0.15)

        if len(rsi_values) >= 2:
            reversal = abs(rsi_values[-1] - rsi_values[-2])
            score += min(reversal / 20.0 * 0.1, 0.1)

        return min(score, 1.0)

    def calculate(self, klines: List[List]) -> RSIResult:
        """
        计算 RSI 并检测信号

        Args:
            klines: K线数据 [[timestamp, open, high, low, close, volume], ...]
                    需要至少 period + divergence_lookback 根K线

        Returns:
            RSIResult
        """
        min_required = self.period + self.divergence_lookback
        if len(klines) < min_required:
            raise ValueError(
                f"需要至少 {min_required} 根K线，当前只有 {len(klines)} 根"
            )

        closes = [k[4] for k in klines]
        rsi_values = self._compute_rsi(closes)

        if not rsi_values:
            return RSIResult(
                signal_type=RSISignalType.NONE,
                rsi_value=50.0,
                above_center=False,
                trend_strength="neutral",
                strength=0.0,
            )

        cur_rsi = rsi_values[-1]

        signal_type = RSISignalType.NONE
        if cur_rsi >= self.overbought:
            signal_type = RSISignalType.OVERBOUGHT
        elif cur_rsi <= self.oversold:
            signal_type = RSISignalType.OVERSOLD

        if signal_type == RSISignalType.NONE:
            signal_type = self._detect_divergence(closes, rsi_values)

        above_center = cur_rsi > 50.0
        trend_strength = self._classify_trend(cur_rsi)
        strength = self._calc_strength(signal_type, cur_rsi, rsi_values)

        if signal_type != RSISignalType.NONE:
            labels = {
                RSISignalType.OVERBOUGHT: "超买",
                RSISignalType.OVERSOLD: "超卖",
                RSISignalType.BULLISH_DIVERGENCE: "看涨背离",
                RSISignalType.BEARISH_DIVERGENCE: "看跌背离",
            }
            logger.info(
                f"🎯 RSI {labels[signal_type]}! "
                f"RSI={cur_rsi:.2f}, 趋势={trend_strength}, "
                f"强度={strength:.2f}"
            )
        else:
            logger.debug(
                f"📊 RSI 无信号: RSI={cur_rsi:.2f}, 趋势={trend_strength}"
            )

        return RSIResult(
            signal_type=signal_type,
            rsi_value=cur_rsi,
            above_center=above_center,
            trend_strength=trend_strength,
            strength=strength,
        )

    def is_overbought(self, klines: List[List]) -> bool:
        """是否超买"""
        result = self.calculate(klines)
        return result.signal_type == RSISignalType.OVERBOUGHT

    def is_oversold(self, klines: List[List]) -> bool:
        """是否超卖"""
        result = self.calculate(klines)
        return result.signal_type == RSISignalType.OVERSOLD

    def is_bullish(self, klines: List[List]) -> bool:
        """是否出现看涨信号 (超卖 或 看涨背离)"""
        result = self.calculate(klines)
        return result.signal_type in (
            RSISignalType.OVERSOLD,
            RSISignalType.BULLISH_DIVERGENCE,
        )

    def is_bearish(self, klines: List[List]) -> bool:
        """是否出现看跌信号 (超买 或 看跌背离)"""
        result = self.calculate(klines)
        return result.signal_type in (
            RSISignalType.OVERBOUGHT,
            RSISignalType.BEARISH_DIVERGENCE,
        )


def main():
    """测试"""
    import random

    print("=" * 60)
    print("RSI 信号检测测试")
    print("=" * 60)

    calc = RSICalculator()

    # 场景1: 连续上涨 → 超买
    print("\n[场景1] 连续上涨 (期望超买):")
    klines_up = []
    base_price = 65000
    for i in range(50):
        change = random.uniform(100, 500) if i > 20 else random.uniform(-100, 200)
        open_p = base_price
        close_p = base_price + change
        high = max(open_p, close_p) + random.uniform(50, 200)
        low = min(open_p, close_p) - random.uniform(50, 100)
        volume = random.uniform(1000, 5000)
        klines_up.append([
            1700000000000 + i * 14400000,
            open_p, high, low, close_p, volume,
        ])
        base_price = close_p

    r1 = calc.calculate(klines_up)
    print(f"  RSI:      {r1.rsi_value:.2f}")
    print(f"  信号类型: {r1.signal_type.value}")
    print(f"  趋势强度: {r1.trend_strength}")
    print(f"  50上方:   {r1.above_center}")
    print(f"  信号强度: {r1.strength:.2f}")

    # 场景2: 连续下跌 → 超卖
    print("\n[场景2] 连续下跌 (期望超卖):")
    klines_down = []
    base_price = 70000
    for i in range(50):
        change = random.uniform(-500, -100) if i > 20 else random.uniform(-200, 100)
        open_p = base_price
        close_p = base_price + change
        high = max(open_p, close_p) + random.uniform(50, 100)
        low = min(open_p, close_p) - random.uniform(50, 200)
        volume = random.uniform(1000, 5000)
        klines_down.append([
            1700000000000 + i * 14400000,
            open_p, high, low, close_p, volume,
        ])
        base_price = close_p

    r2 = calc.calculate(klines_down)
    print(f"  RSI:      {r2.rsi_value:.2f}")
    print(f"  信号类型: {r2.signal_type.value}")
    print(f"  趋势强度: {r2.trend_strength}")
    print(f"  信号强度: {r2.strength:.2f}")

    # 场景3: 横盘震荡
    print("\n[场景3] 横盘震荡:")
    klines_flat = []
    base_price = 65000
    for i in range(50):
        change = random.uniform(-150, 150)
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
    print(f"  RSI:      {r3.rsi_value:.2f}")
    print(f"  信号类型: {r3.signal_type.value}")
    print(f"  趋势强度: {r3.trend_strength}")
    print(f"  信号强度: {r3.strength:.2f}")


if __name__ == "__main__":
    main()
