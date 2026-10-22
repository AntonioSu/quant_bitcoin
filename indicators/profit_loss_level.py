"""止损 / 强平价格计算

基于 ATR 计算止损价位:
  LONG  → stop = entry - ATR × sl_mult
  SHORT → stop = entry + ATR × sl_mult

强平价格计算 (Binance USDT-M 合约):
  LONG  → liquidation = entry × (1 - 1/leverage + maintenance_margin_rate)
  SHORT → liquidation = entry × (1 + 1/leverage - maintenance_margin_rate)

多空只差一个方向符号，因此共用 PositionLevel，由 is_long 决定符号。
"""

from typing import List

from indicators.atr import ATRCalculator
from utils import logger

# 止损被强平价穿透时，回退到强平价外侧的安全缓冲
_LIQUIDATION_BUFFER = 0.005

# 无法取得 ATR 时的兜底止损距离（入场价百分比）
_FALLBACK_STOP_PCT = 0.02

# Binance USDT-M BTC 合约维持保证金率阶梯: (名义价值上限, 费率)
_MMR_TIERS = [
    (50_000, 0.004),
    (250_000, 0.005),
    (1_000_000, 0.01),
    (5_000_000, 0.025),
    (20_000_000, 0.05),
    (50_000_000, 0.10),
    (100_000_000, 0.125),
]
_MMR_TOP_TIER = 0.15


def calc_maintenance_margin_rate(notional_value: float) -> float:
    """根据仓位名义价值计算维持保证金率 (Binance USDT-M BTC 合约)"""
    for limit, rate in _MMR_TIERS:
        if notional_value <= limit:
            return rate
    return _MMR_TOP_TIER


def calc_liquidation_price(
    entry_price: float,
    leverage: int,
    is_long: bool,
    notional_value: float,
) -> float:
    """计算强平价格

    多空公式不做代数合并：浮点结合律不成立，合并会在 stop == liquidation 的
    临界点上改变止损是否被判定为穿透。
    """
    mmr = calc_maintenance_margin_rate(notional_value)
    if is_long:
        return entry_price * (1 - 1 / leverage + mmr)
    return entry_price * (1 + 1 / leverage - mmr)


class PositionLevel:
    """止损 / 强平价位计算（多空共用，方向由 is_long 决定）"""

    def __init__(self, atr_calc: ATRCalculator, is_long: bool):
        self.atr_calc = atr_calc
        self.is_long = is_long

    @property
    def _sign(self) -> int:
        """多头止损在入场价下方，空头在上方"""
        return 1 if self.is_long else -1

    def _build(self, entry_price: float, stop_distance: float, atr_value: float,
               leverage: int, notional_value: float, warn: bool) -> dict:
        """由止损距离推导止损价，并保证止损始终停在强平价内侧"""
        liquidation_price = calc_liquidation_price(
            entry_price, leverage, is_long=self.is_long, notional_value=notional_value
        )
        stop_loss = entry_price - self._sign * stop_distance

        # 止损被强平价穿透 → 拉回强平价外侧，否则还没触发止损就先爆仓
        if self._sign * (stop_loss - liquidation_price) <= 0:
            if warn:
                logger.warning(
                    f"⚠️ 止损价(${stop_loss:,.0f}) 已穿透强平价(${liquidation_price:,.0f})，"
                    f"调整到强平价外侧 {_LIQUIDATION_BUFFER:.1%}"
                )
            stop_loss = liquidation_price * (
                1 + _LIQUIDATION_BUFFER if self.is_long else 1 - _LIQUIDATION_BUFFER
            )

        return {
            "stop_loss": stop_loss,
            "liquidation_price": liquidation_price,
            "atr": atr_value,
        }

    def calculate(
        self,
        entry_price: float,
        klines: List[List],
        atr_multiplier: float,
        leverage: int,
        notional_value: float,
    ) -> dict:
        atr_value = self.atr_calc.calculate(klines).value
        result = self._build(
            entry_price, atr_value * atr_multiplier, atr_value,
            leverage, notional_value, warn=True,
        )

        label = "🗡️ 做多" if self.is_long else "🛡️ 做空"
        logger.info(
            f"{label}价位: 止损=${result['stop_loss']:,.0f}, "
            f"强平=${result['liquidation_price']:,.0f}, ATR=${atr_value:,.0f}"
        )
        return result

    def preview(
        self,
        entry_price: float,
        atr_value: float,
        atr_multiplier: float,
        leverage: int,
        notional_value: float,
    ) -> dict:
        """用已知 ATR 试算价位，不写日志

        供 Trading AI 的风控工具反复试算：ATR 由调用方算一次后复用，
        且试算不是真实开仓，穿透强平价时不该刷 warning 日志。
        """
        return self._build(
            entry_price, atr_value * atr_multiplier, atr_value,
            leverage, notional_value, warn=False,
        )

    def fallback(self, entry_price: float, leverage: int, notional_value: float) -> dict:
        """ATR 不可用时按入场价固定百分比兜底"""
        dist = entry_price * _FALLBACK_STOP_PCT
        return self._build(
            entry_price, dist, dist, leverage, notional_value, warn=False
        )
