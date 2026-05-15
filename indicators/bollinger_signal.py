"""Bollinger Bands (布林带) 信号检测

布林带三线:
- 中轨 = SMA(close, period)
- 上轨 = 中轨 + std_dev * STD(close, period)
- 下轨 = 中轨 - std_dev * STD(close, period)

信号类型:
- 触及下轨 (touch_lower): 价格≤下轨，均值回归做多
- 触及上轨 (touch_upper): 价格≥上轨，均值回归做空
- 突破上轨 (breakout_up): 前一根在带内，本根突破上轨 + 放量确认趋势做多
- 跌破下轨 (breakout_down): 前一根在带内，本根跌破下轨 + 放量确认趋势做空
- 收窄突破 (squeeze_breakout): 带宽收窄后方向性突破，波动率爆发

辅助指标:
- %B = (price - lower) / (upper - lower)，衡量价格在带内位置
- 带宽 (bandwidth) = (upper - lower) / middle，衡量波动率
"""

from typing import List
from dataclasses import dataclass
from enum import Enum

from ..utils import logger


class BollingerSignalType(Enum):
    """布林带信号类型"""
    NONE = "none"
    TOUCH_LOWER = "touch_lower"          # 触及下轨 (均值回归做多)
    TOUCH_UPPER = "touch_upper"          # 触及上轨 (均值回归做空)
    BREAKOUT_UP = "breakout_up"          # 放量突破上轨 (趋势做多)
    BREAKOUT_DOWN = "breakout_down"      # 放量跌破下轨 (趋势做空)
    SQUEEZE_BREAKOUT_UP = "squeeze_up"   # 收窄后向上突破
    SQUEEZE_BREAKOUT_DOWN = "squeeze_down"  # 收窄后向下突破


@dataclass
class BollingerResult:
    """布林带分析结果"""
    signal_type: BollingerSignalType
    price: float                   # 当前价格
    upper: float                   # 上轨
    middle: float                  # 中轨
    lower: float                   # 下轨
    percent_b: float               # %B 位置 (0=下轨, 1=上轨)
    bandwidth: float               # 带宽 (波动率)
    is_squeeze: bool               # 是否处于收窄状态
    strength: float                # 信号强度 (0-1)


class BollingerCalculator:
    """布林带计算与信号检测"""

    def __init__(
        self,
        period: int = 20,
        std_dev: float = 2.0,
        squeeze_threshold: float = 0.03,
        squeeze_lookback: int = 5,
        volume_confirm_ratio: float = 1.3,
        timeframe: str = "4h",
    ):
        self.period = period
        self.std_dev = std_dev
        self.squeeze_threshold = squeeze_threshold
        self.squeeze_lookback = squeeze_lookback
        self.volume_confirm_ratio = volume_confirm_ratio
        self.timeframe = timeframe

    def _sma(self, values: List[float], period: int) -> List[float]:
        """简单移动平均"""
        result = []
        for i in range(len(values)):
            if i < period - 1:
                result.append(sum(values[: i + 1]) / (i + 1))
            else:
                result.append(sum(values[i - period + 1: i + 1]) / period)
        return result

    def _std(self, values: List[float], period: int) -> List[float]:
        """滚动标准差"""
        result = []
        for i in range(len(values)):
            if i < period - 1:
                window = values[: i + 1]
            else:
                window = values[i - period + 1: i + 1]
            mean = sum(window) / len(window)
            variance = sum((x - mean) ** 2 for x in window) / len(window)
            result.append(variance ** 0.5)
        return result

    def _compute_bands(
        self, closes: List[float]
    ) -> tuple[List[float], List[float], List[float]]:
        """计算布林带三线"""
        middle = self._sma(closes, self.period)
        std = self._std(closes, self.period)
        upper = [m + self.std_dev * s for m, s in zip(middle, std)]
        lower = [m - self.std_dev * s for m, s in zip(middle, std)]
        return upper, middle, lower

    def _percent_b(self, price: float, upper: float, lower: float) -> float:
        """计算 %B"""
        band_width = upper - lower
        if band_width <= 0:
            return 0.5
        return (price - lower) / band_width

    def _bandwidth(self, upper: float, middle: float, lower: float) -> float:
        """计算带宽"""
        if middle <= 0:
            return 0.0
        return (upper - lower) / middle

    def _detect_squeeze(
        self, upper: List[float], middle: List[float], lower: List[float]
    ) -> tuple[bool, bool]:
        """
        检测收窄状态

        Returns:
            (当前是否收窄, 之前是否收窄但现在已展开)
        """
        if len(upper) < self.squeeze_lookback + 1:
            return False, False

        cur_bw = self._bandwidth(upper[-1], middle[-1], lower[-1])
        prev_bw = self._bandwidth(
            upper[-self.squeeze_lookback], middle[-self.squeeze_lookback],
            lower[-self.squeeze_lookback]
        )

        is_squeeze = cur_bw < self.squeeze_threshold
        was_squeeze_now_expand = prev_bw < self.squeeze_threshold and cur_bw >= self.squeeze_threshold
        return is_squeeze, was_squeeze_now_expand

    def _volume_ratio(self, volumes: List[float]) -> float:
        """当前成交量 / 近期平均成交量"""
        if len(volumes) < 2:
            return 1.0
        cur_vol = volumes[-1]
        avg_vol = sum(volumes[-11:-1]) / min(len(volumes) - 1, 10)
        if avg_vol <= 0:
            return 1.0
        return cur_vol / avg_vol

    def _calc_strength(
        self,
        signal_type: BollingerSignalType,
        percent_b: float,
        bandwidth: float,
        vol_ratio: float,
        was_squeeze: bool,
    ) -> float:
        """
        计算信号强度 (0-1)

        加分项:
        - %B 越极端信号越强
        - 放量确认加分
        - 收窄后突破加分
        - 带宽越大 (波动性突然放大) 突破信号越强
        """
        if signal_type == BollingerSignalType.NONE:
            return 0.0

        score = 0.35

        if signal_type == BollingerSignalType.TOUCH_LOWER:
            excess = max(0, -percent_b) * 0.3
            score += min(excess, 0.15)
        elif signal_type == BollingerSignalType.TOUCH_UPPER:
            excess = max(0, percent_b - 1.0) * 0.3
            score += min(excess, 0.15)
        elif signal_type in (
            BollingerSignalType.BREAKOUT_UP,
            BollingerSignalType.BREAKOUT_DOWN,
        ):
            score += 0.1

        if vol_ratio >= self.volume_confirm_ratio:
            vol_bonus = min((vol_ratio - 1.0) * 0.1, 0.2)
            score += vol_bonus

        if was_squeeze:
            score += 0.15

        if signal_type in (
            BollingerSignalType.BREAKOUT_UP,
            BollingerSignalType.BREAKOUT_DOWN,
            BollingerSignalType.SQUEEZE_BREAKOUT_UP,
            BollingerSignalType.SQUEEZE_BREAKOUT_DOWN,
        ):
            bw_bonus = min(bandwidth * 2.0, 0.1)
            score += bw_bonus

        return min(score, 1.0)

    def calculate(self, klines: List[List]) -> BollingerResult:
        """
        计算布林带并检测信号

        Args:
            klines: K线数据 [[timestamp, open, high, low, close, volume], ...]
                    需要至少 period + squeeze_lookback 根K线

        Returns:
            BollingerResult
        """
        min_required = self.period + self.squeeze_lookback
        if len(klines) < min_required:
            raise ValueError(
                f"需要至少 {min_required} 根K线，当前只有 {len(klines)} 根"
            )

        closes = [k[4] for k in klines]
        volumes = [k[5] for k in klines]

        upper, middle, lower = self._compute_bands(closes)

        cur_price = closes[-1]
        prev_price = closes[-2]
        cur_upper, cur_middle, cur_lower = upper[-1], middle[-1], lower[-1]
        prev_upper, prev_lower = upper[-2], lower[-2]

        pct_b = self._percent_b(cur_price, cur_upper, cur_lower)
        bw = self._bandwidth(cur_upper, cur_middle, cur_lower)
        vol_ratio = self._volume_ratio(volumes)
        is_squeeze, was_squeeze_now_expand = self._detect_squeeze(upper, middle, lower)

        signal_type = BollingerSignalType.NONE

        # 优先级1: 收窄后突破
        if was_squeeze_now_expand:
            if cur_price > cur_upper:
                signal_type = BollingerSignalType.SQUEEZE_BREAKOUT_UP
            elif cur_price < cur_lower:
                signal_type = BollingerSignalType.SQUEEZE_BREAKOUT_DOWN

        # 优先级2: 放量突破 (前一根在带内)
        if signal_type == BollingerSignalType.NONE:
            if (
                prev_price <= prev_upper
                and cur_price > cur_upper
                and vol_ratio >= self.volume_confirm_ratio
            ):
                signal_type = BollingerSignalType.BREAKOUT_UP
            elif (
                prev_price >= prev_lower
                and cur_price < cur_lower
                and vol_ratio >= self.volume_confirm_ratio
            ):
                signal_type = BollingerSignalType.BREAKOUT_DOWN

        # 优先级3: 触及轨道 (均值回归)
        if signal_type == BollingerSignalType.NONE:
            if cur_price <= cur_lower:
                signal_type = BollingerSignalType.TOUCH_LOWER
            elif cur_price >= cur_upper:
                signal_type = BollingerSignalType.TOUCH_UPPER

        strength = self._calc_strength(
            signal_type, pct_b, bw, vol_ratio, was_squeeze_now_expand
        )

        if signal_type != BollingerSignalType.NONE:
            labels = {
                BollingerSignalType.TOUCH_LOWER: "触及下轨",
                BollingerSignalType.TOUCH_UPPER: "触及上轨",
                BollingerSignalType.BREAKOUT_UP: "放量突破上轨",
                BollingerSignalType.BREAKOUT_DOWN: "放量跌破下轨",
                BollingerSignalType.SQUEEZE_BREAKOUT_UP: "收窄后向上突破",
                BollingerSignalType.SQUEEZE_BREAKOUT_DOWN: "收窄后向下突破",
            }
            logger.info(
                f"🎯 布林带 {labels[signal_type]}! "
                f"价格={cur_price:.2f}, %B={pct_b:.2f}, "
                f"带宽={bw:.4f}, 量比={vol_ratio:.2f}, "
                f"强度={strength:.2f}"
            )
        else:
            logger.debug(
                f"📊 布林带无信号: 价格={cur_price:.2f}, %B={pct_b:.2f}, "
                f"带宽={bw:.4f}, 收窄={'是' if is_squeeze else '否'}"
            )

        return BollingerResult(
            signal_type=signal_type,
            price=cur_price,
            upper=cur_upper,
            middle=cur_middle,
            lower=cur_lower,
            percent_b=pct_b,
            bandwidth=bw,
            is_squeeze=is_squeeze,
            strength=strength,
        )

    def is_bullish(self, klines: List[List]) -> bool:
        """是否出现看涨信号 (触下轨 / 突破上轨 / 收窄向上)"""
        result = self.calculate(klines)
        return result.signal_type in (
            BollingerSignalType.TOUCH_LOWER,
            BollingerSignalType.BREAKOUT_UP,
            BollingerSignalType.SQUEEZE_BREAKOUT_UP,
        )

    def is_bearish(self, klines: List[List]) -> bool:
        """是否出现看跌信号 (触上轨 / 跌破下轨 / 收窄向下)"""
        result = self.calculate(klines)
        return result.signal_type in (
            BollingerSignalType.TOUCH_UPPER,
            BollingerSignalType.BREAKOUT_DOWN,
            BollingerSignalType.SQUEEZE_BREAKOUT_DOWN,
        )

    def is_squeeze(self, klines: List[List]) -> bool:
        """当前是否处于收窄状态"""
        result = self.calculate(klines)
        return result.is_squeeze


def main():
    """测试"""
    import random

    print("=" * 60)
    print("布林带信号检测测试")
    print("=" * 60)

    calc = BollingerCalculator()

    # 场景1: 连续上涨突破上轨
    print("\n[场景1] 连续上涨突破上轨:")
    klines_up = []
    base_price = 65000
    for i in range(40):
        change = random.uniform(200, 600) if i > 25 else random.uniform(-100, 150)
        open_p = base_price
        close_p = base_price + change
        high = max(open_p, close_p) + random.uniform(50, 200)
        low = min(open_p, close_p) - random.uniform(50, 100)
        volume = random.uniform(2000, 8000) if i > 25 else random.uniform(1000, 3000)
        klines_up.append([
            1700000000000 + i * 14400000,
            open_p, high, low, close_p, volume,
        ])
        base_price = close_p

    r1 = calc.calculate(klines_up)
    print(f"  信号类型: {r1.signal_type.value}")
    print(f"  价格:     {r1.price:.2f}")
    print(f"  上轨:     {r1.upper:.2f}")
    print(f"  中轨:     {r1.middle:.2f}")
    print(f"  下轨:     {r1.lower:.2f}")
    print(f"  %B:       {r1.percent_b:.3f}")
    print(f"  带宽:     {r1.bandwidth:.4f}")
    print(f"  信号强度: {r1.strength:.2f}")

    # 场景2: 连续下跌触及下轨
    print("\n[场景2] 连续下跌触及下轨:")
    klines_down = []
    base_price = 70000
    for i in range(40):
        change = random.uniform(-600, -200) if i > 25 else random.uniform(-100, 100)
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
    print(f"  信号类型: {r2.signal_type.value}")
    print(f"  价格:     {r2.price:.2f}")
    print(f"  %B:       {r2.percent_b:.3f}")
    print(f"  带宽:     {r2.bandwidth:.4f}")
    print(f"  信号强度: {r2.strength:.2f}")

    # 场景3: 横盘收窄
    print("\n[场景3] 横盘收窄:")
    klines_flat = []
    base_price = 65000
    for i in range(40):
        shrink = max(0.3, 1.0 - i * 0.02)
        change = random.uniform(-50, 50) * shrink
        open_p = base_price
        close_p = base_price + change
        high = max(open_p, close_p) + random.uniform(10, 30) * shrink
        low = min(open_p, close_p) - random.uniform(10, 30) * shrink
        volume = random.uniform(500, 1500)
        klines_flat.append([
            1700000000000 + i * 14400000,
            open_p, high, low, close_p, volume,
        ])
        base_price = close_p

    r3 = calc.calculate(klines_flat)
    print(f"  信号类型: {r3.signal_type.value}")
    print(f"  价格:     {r3.price:.2f}")
    print(f"  %B:       {r3.percent_b:.3f}")
    print(f"  带宽:     {r3.bandwidth:.4f}")
    print(f"  收窄:     {r3.is_squeeze}")
    print(f"  信号强度: {r3.strength:.2f}")


if __name__ == "__main__":
    main()
