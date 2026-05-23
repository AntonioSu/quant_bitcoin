"""Volume (成交量) 信号检测

成交量分析:
- 量比 = 当前成交量 / 近N期平均成交量
- OBV (On-Balance Volume): 累计量能方向

支持两种量比计算模式 (由 time_normalize 控制):
1. candle 模式 (time_normalize=False, 默认)
   分子 = 当根 (可能未走完) K线累计量
   分母 = 过去 N 根完整 K线均量
   缺点: 同一根 K线进度不同时分子大小不同, 刚开盘时量比偏小

2. scaled 模式 (time_normalize=True)
   分子 = 当根 K线累计量 (cur_vol)
   分母 = 过去 N 根均量 × (当根已走时长 / 完整K线时长)
   即把历史均量线性摊薄到"相同时长"做对比, 消除 K线进度偏置

信号类型:
- 放量上涨 (volume_surge_up): 量比 > 阈值 且价格上涨，趋势确认
- 放量下跌 (volume_surge_down): 量比 > 阈值 且价格下跌，抛压增大
- 缩量 (volume_dry): 成交量持续萎缩，变盘前兆
- 量价背离-顶背离 (vol_price_div_top): 价格新高但成交量递减
- 量价背离-底背离 (vol_price_div_bottom): 价格新低但成交量递减

辅助指标:
- 量比: 当前量 / 平均量
- OBV 趋势: OBV 是否与价格同向
"""

import time
from typing import List, Optional
from dataclasses import dataclass
from enum import Enum

from utils import logger


class VolumeSignalType(Enum):
    """成交量信号类型"""
    NONE = "none"
    SURGE_UP = "surge_up"                  # 放量上涨
    SURGE_DOWN = "surge_down"              # 放量下跌
    DRY_UP = "dry_up"                      # 缩量 (变盘前兆)
    DIV_TOP = "divergence_top"             # 量价顶背离
    DIV_BOTTOM = "divergence_bottom"       # 量价底背离


@dataclass
class VolumeResult:
    """成交量分析结果"""
    signal_type: VolumeSignalType
    volume: float                  # 当前成交量
    avg_volume: float              # 平均成交量 (scaled 模式下已按 elapsed/full 摊薄)
    vol_ratio: float               # 量比
    obv_trend: str                 # "up" / "down" / "flat"
    price_change_pct: float        # 最近一根K线价格变化%
    strength: float                # 信号强度 (0-1)
    is_warmup: bool = False        # scaled 模式下 K线刚开盘 elapsed 太小时为 True
    elapsed_min: float = 0.0       # scaled 模式: 当根 K线已走时长 (分钟)


class VolumeCalculator:
    """成交量计算与信号检测"""

    def __init__(
        self,
        avg_period: int = 20,
        surge_threshold: float = 2.0,
        dry_threshold: float = 0.5,
        dry_consecutive: int = 3,
        price_change_threshold: float = 0.5,
        timeframe: str = "4h",
        time_normalize: bool = False,
        kline_duration_min: float = 240.0,
        min_elapsed_min: float = 10.0,
    ):
        """
        Args:
            avg_period: 历史均量样本数 (取过去 N 根已完成 K线)
            surge_threshold: 放量量比阈值
            dry_threshold: 缩量量比阈值
            dry_consecutive: 连续缩量根数
            price_change_threshold: 放量信号的最小价格变动 (%)
            timeframe: K线周期标签 (用于日志)
            time_normalize: 是否对量比做"等时长摊薄"归一化
            kline_duration_min: 单根 K线完整时长 (分钟), 4h=240
            min_elapsed_min: scaled 模式下, 当根 K线已走时长不足该值则进入 warmup
                             (返回 vol_ratio=1.0, signal_type=NONE), 避免分母过小导致量比爆炸
        """
        self.avg_period = avg_period
        self.surge_threshold = surge_threshold
        self.dry_threshold = dry_threshold
        self.dry_consecutive = dry_consecutive
        self.price_change_threshold = price_change_threshold / 100.0
        self.timeframe = timeframe
        self.time_normalize = time_normalize
        self.kline_duration_min = kline_duration_min
        self.min_elapsed_min = min_elapsed_min

    def _calc_obv(self, closes: List[float], volumes: List[float]) -> List[float]:
        """计算 OBV"""
        obv = [0.0]
        for i in range(1, len(closes)):
            if closes[i] > closes[i - 1]:
                obv.append(obv[-1] + volumes[i])
            elif closes[i] < closes[i - 1]:
                obv.append(obv[-1] - volumes[i])
            else:
                obv.append(obv[-1])
        return obv

    def _obv_trend(self, obv: List[float], lookback: int = 5) -> str:
        """判断 OBV 趋势"""
        if len(obv) < lookback + 1:
            return "flat"
        recent = obv[-lookback:]
        rising = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
        falling = sum(1 for i in range(1, len(recent)) if recent[i] < recent[i - 1])
        if rising >= lookback - 1:
            return "up"
        elif falling >= lookback - 1:
            return "down"
        return "flat"

    def _detect_volume_price_divergence(
        self, closes: List[float], volumes: List[float], lookback: int = 10
    ) -> VolumeSignalType:
        """检测量价背离"""
        if len(closes) < lookback or len(volumes) < lookback:
            return VolumeSignalType.NONE

        rc = closes[-lookback:]
        rv = volumes[-lookback:]

        mid = lookback // 2
        first_half_price_avg = sum(rc[:mid]) / mid
        second_half_price_avg = sum(rc[mid:]) / (lookback - mid)
        first_half_vol_avg = sum(rv[:mid]) / mid
        second_half_vol_avg = sum(rv[mid:]) / (lookback - mid)

        price_rising = second_half_price_avg > first_half_price_avg * 1.005
        price_falling = second_half_price_avg < first_half_price_avg * 0.995
        vol_shrinking = second_half_vol_avg < first_half_vol_avg * 0.8

        if price_rising and vol_shrinking:
            return VolumeSignalType.DIV_TOP
        if price_falling and vol_shrinking:
            return VolumeSignalType.DIV_BOTTOM

        return VolumeSignalType.NONE

    def _calc_strength(
        self,
        signal_type: VolumeSignalType,
        vol_ratio: float,
        price_change_pct: float,
    ) -> float:
        """计算信号强度 (0-1)"""
        if signal_type == VolumeSignalType.NONE:
            return 0.0

        score = 0.35

        if signal_type in (VolumeSignalType.SURGE_UP, VolumeSignalType.SURGE_DOWN):
            vol_bonus = min((vol_ratio - 1.0) * 0.15, 0.3)
            score += vol_bonus
            price_bonus = min(abs(price_change_pct) * 5, 0.15)
            score += price_bonus

        elif signal_type == VolumeSignalType.DRY_UP:
            score += 0.1
            dry_bonus = min((1.0 - vol_ratio) * 0.2, 0.15)
            score += dry_bonus

        elif signal_type in (VolumeSignalType.DIV_TOP, VolumeSignalType.DIV_BOTTOM):
            score += 0.15

        return max(min(score, 1.0), 0.0)

    def calculate(
        self,
        klines: List[List],
        now_ms: Optional[int] = None,
    ) -> VolumeResult:
        """
        计算成交量指标并检测信号

        Args:
            klines: K线数据 [[timestamp, open, high, low, close, volume], ...]
                    需要至少 avg_period + 2 根K线
            now_ms: 当前时间戳 (毫秒); 仅 time_normalize=True 时使用,
                    None 则取系统时间. 用于回测时注入

        Returns:
            VolumeResult
        """
        min_required = self.avg_period + 2
        if len(klines) < min_required:
            raise ValueError(
                f"需要至少 {min_required} 根K线，当前只有 {len(klines)} 根"
            )

        closes = [k[4] for k in klines]
        volumes = [k[5] for k in klines]

        cur_vol = volumes[-1]
        avg_full = sum(volumes[-self.avg_period - 1:-1]) / self.avg_period

        is_warmup = False
        elapsed_min = self.kline_duration_min

        if self.time_normalize:
            cur_open_ms = klines[-1][0]
            if now_ms is None:
                now_ms = int(time.time() * 1000)
            elapsed_min = (now_ms - cur_open_ms) / 60000.0
            elapsed_min = max(0.0, min(elapsed_min, self.kline_duration_min))

            if elapsed_min < self.min_elapsed_min:
                is_warmup = True
                logger.debug(
                    f"📊 成交量 warmup: K线开盘 {elapsed_min:.1f} min < "
                    f"{self.min_elapsed_min:.0f} min, 返回中性量比"
                )
                return VolumeResult(
                    signal_type=VolumeSignalType.NONE,
                    volume=cur_vol,
                    avg_volume=avg_full,
                    vol_ratio=1.0,
                    obv_trend="flat",
                    price_change_pct=0.0,
                    strength=0.0,
                    is_warmup=True,
                    elapsed_min=elapsed_min,
                )

            scale = elapsed_min / self.kline_duration_min
            avg_vol = avg_full * scale
        else:
            avg_vol = avg_full

        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0

        price_change_pct = (closes[-1] - closes[-2]) / closes[-2] if closes[-2] > 0 else 0.0

        obv = self._calc_obv(closes, volumes)
        obv_t = self._obv_trend(obv)

        signal_type = VolumeSignalType.NONE

        # 放量信号
        if vol_ratio >= self.surge_threshold:
            if price_change_pct > self.price_change_threshold:
                signal_type = VolumeSignalType.SURGE_UP
            elif price_change_pct < -self.price_change_threshold:
                signal_type = VolumeSignalType.SURGE_DOWN

        # 缩量信号: 仅在 candle 模式判断 (依赖完整 K线), scaled 模式下当根
        # 时长未走完不适合判定"缩量", 避免在 K线前中段误报
        if signal_type == VolumeSignalType.NONE and not self.time_normalize:
            dry_count = 0
            for i in range(1, self.dry_consecutive + 1):
                if len(volumes) >= self.avg_period + i:
                    period_avg = sum(volumes[-self.avg_period - i:-i]) / self.avg_period
                    if period_avg > 0 and volumes[-i] / period_avg < self.dry_threshold:
                        dry_count += 1
            if dry_count >= self.dry_consecutive:
                signal_type = VolumeSignalType.DRY_UP

        # 量价背离 (基于完整 K线序列, 排除当根未走完的)
        if signal_type == VolumeSignalType.NONE:
            div_closes = closes[:-1] if self.time_normalize else closes
            div_volumes = volumes[:-1] if self.time_normalize else volumes
            signal_type = self._detect_volume_price_divergence(div_closes, div_volumes)

        strength = self._calc_strength(signal_type, vol_ratio, price_change_pct)

        if signal_type != VolumeSignalType.NONE:
            labels = {
                VolumeSignalType.SURGE_UP: "放量上涨",
                VolumeSignalType.SURGE_DOWN: "放量下跌",
                VolumeSignalType.DRY_UP: "缩量",
                VolumeSignalType.DIV_TOP: "量价顶背离",
                VolumeSignalType.DIV_BOTTOM: "量价底背离",
            }
            extra = f", 已走{elapsed_min:.0f}/{self.kline_duration_min:.0f}min" if self.time_normalize else ""
            logger.info(
                f"🎯 成交量 {labels[signal_type]}! "
                f"量比={vol_ratio:.2f}, 价变={price_change_pct:.2%}, "
                f"OBV趋势={obv_t}, 强度={strength:.2f}{extra}"
            )
        else:
            logger.debug(
                f"📊 成交量无信号: 量比={vol_ratio:.2f}, OBV={obv_t}"
            )

        return VolumeResult(
            signal_type=signal_type,
            volume=cur_vol,
            avg_volume=avg_vol,
            vol_ratio=vol_ratio,
            obv_trend=obv_t,
            price_change_pct=price_change_pct * 100,
            strength=strength,
            is_warmup=is_warmup,
            elapsed_min=elapsed_min,
        )

    def is_bullish(self, klines: List[List]) -> bool:
        """是否出现看涨成交量信号"""
        result = self.calculate(klines)
        return result.signal_type in (
            VolumeSignalType.SURGE_UP,
            VolumeSignalType.DIV_BOTTOM,
        )

    def is_bearish(self, klines: List[List]) -> bool:
        """是否出现看跌成交量信号"""
        result = self.calculate(klines)
        return result.signal_type in (
            VolumeSignalType.SURGE_DOWN,
            VolumeSignalType.DIV_TOP,
        )


def main():
    """测试"""
    import random

    print("=" * 60)
    print("成交量信号检测测试")
    print("=" * 60)

    calc = VolumeCalculator()

    # 场景1: 放量上涨
    print("\n[场景1] 放量上涨:")
    klines = []
    base = 65000
    for i in range(30):
        change = random.uniform(-100, 150)
        vol = random.uniform(1000, 3000)
        if i == 29:
            change = 800
            vol = 12000
        o = base
        c = base + change
        h = max(o, c) + random.uniform(50, 200)
        l = min(o, c) - random.uniform(50, 100)
        klines.append([1700000000000 + i * 14400000, o, h, l, c, vol])
        base = c

    r = calc.calculate(klines)
    print(f"  信号: {r.signal_type.value}")
    print(f"  量比: {r.vol_ratio:.2f}, OBV趋势: {r.obv_trend}")
    print(f"  价变: {r.price_change_pct:.2f}%")
    print(f"  强度: {r.strength:.2f}")

    # 场景2: 缩量
    print("\n[场景2] 缩量震荡:")
    klines2 = []
    base = 65000
    for i in range(30):
        change = random.uniform(-50, 50)
        vol = random.uniform(100, 300) if i >= 27 else random.uniform(2000, 5000)
        o = base
        c = base + change
        h = max(o, c) + random.uniform(10, 50)
        l = min(o, c) - random.uniform(10, 50)
        klines2.append([1700000000000 + i * 14400000, o, h, l, c, vol])
        base = c

    r2 = calc.calculate(klines2)
    print(f"  信号: {r2.signal_type.value}")
    print(f"  量比: {r2.vol_ratio:.2f}, OBV趋势: {r2.obv_trend}")
    print(f"  强度: {r2.strength:.2f}")


if __name__ == "__main__":
    main()
