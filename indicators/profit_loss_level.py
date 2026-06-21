"""止损 / 强平价格计算

基于 ATR 计算止损价位:
  LONG  → stop = entry - ATR × sl_mult
  SHORT → stop = entry + ATR × sl_mult

强平价格计算 (Binance USDT-M 合约):
  LONG  → liquidation = entry × (1 - 1/leverage + maintenance_margin_rate)
  SHORT → liquidation = entry × (1 + 1/leverage - maintenance_margin_rate)
"""

from typing import List

from indicators.atr import ATRCalculator
from utils import logger


def calc_maintenance_margin_rate(notional_value: float) -> float:
    """根据仓位名义价值计算维持保证金率 (Binance USDT-M BTC 合约)"""
    if notional_value <= 50_000:
        return 0.004
    elif notional_value <= 250_000:
        return 0.005
    elif notional_value <= 1_000_000:
        return 0.01
    elif notional_value <= 5_000_000:
        return 0.025
    elif notional_value <= 20_000_000:
        return 0.05
    elif notional_value <= 50_000_000:
        return 0.10
    elif notional_value <= 100_000_000:
        return 0.125
    else:
        return 0.15


def calc_liquidation_price(
    entry_price: float,
    leverage: int,
    is_long: bool,
    notional_value: float = 500.0,
) -> float:
    """计算强平价格"""
    mmr = calc_maintenance_margin_rate(notional_value)

    if is_long:
        liquidation_price = entry_price * (1 - 1 / leverage + mmr)
    else:
        liquidation_price = entry_price * (1 + 1 / leverage - mmr)

    return liquidation_price


class LongLevel:
    """做多止损 / 强平计算"""

    def __init__(self, atr_calc: ATRCalculator):
        self.atr_calc = atr_calc

    def calculate(
        self,
        entry_price: float,
        klines: List[List],
        atr_multiplier: float,
        leverage: int = 10,
        notional_value: float = 500.0,
    ) -> dict:
        atr = self.atr_calc.calculate(klines)
        atr_value = atr.value

        stop_loss = entry_price - atr_value * atr_multiplier
        liquidation_price = calc_liquidation_price(
            entry_price, leverage, is_long=True, notional_value=notional_value
        )

        if stop_loss <= liquidation_price:
            logger.warning(
                f"⚠️ 止损价(${stop_loss:,.0f}) <= 强平价(${liquidation_price:,.0f})，"
                f"调整止损价为强平价上方 0.5%"
            )
            stop_loss = liquidation_price * 1.005

        result = {
            "stop_loss": stop_loss,
            "liquidation_price": liquidation_price,
            "atr": atr_value,
        }

        logger.info(
            f"🗡️ 做多价位: 止损=${stop_loss:,.0f}, "
            f"强平=${liquidation_price:,.0f}, ATR=${atr_value:,.0f}"
        )

        return result

    def fallback(self, entry_price: float, leverage: int = 10, notional_value: float = 500.0) -> dict:
        dist = entry_price * 0.02
        liquidation_price = calc_liquidation_price(
            entry_price, leverage, is_long=True, notional_value=notional_value
        )
        stop_loss = entry_price - dist

        if stop_loss <= liquidation_price:
            stop_loss = liquidation_price * 1.005

        return {
            "stop_loss": stop_loss,
            "liquidation_price": liquidation_price,
            "atr": dist,
        }


class ShortLevel:
    """做空止损 / 强平计算"""

    def __init__(self, atr_calc: ATRCalculator):
        self.atr_calc = atr_calc

    def calculate(
        self,
        entry_price: float,
        klines: List[List],
        atr_multiplier: float,
        leverage: int = 2,
        notional_value: float = 500.0,
    ) -> dict:
        atr = self.atr_calc.calculate(klines)
        atr_value = atr.value

        stop_loss = entry_price + atr_value * atr_multiplier
        liquidation_price = calc_liquidation_price(
            entry_price, leverage, is_long=False, notional_value=notional_value
        )

        if stop_loss >= liquidation_price:
            logger.warning(
                f"⚠️ 止损价(${stop_loss:,.0f}) >= 强平价(${liquidation_price:,.0f})，"
                f"调整止损价为强平价下方 0.5%"
            )
            stop_loss = liquidation_price * 0.995

        result = {
            "stop_loss": stop_loss,
            "liquidation_price": liquidation_price,
            "atr": atr_value,
        }

        logger.info(
            f"🛡️ 做空价位: 止损=${stop_loss:,.0f}, "
            f"强平=${liquidation_price:,.0f}, ATR=${atr_value:,.0f}"
        )

        return result

    def fallback(self, entry_price: float, leverage: int = 2, notional_value: float = 500.0) -> dict:
        dist = entry_price * 0.02
        liquidation_price = calc_liquidation_price(
            entry_price, leverage, is_long=False, notional_value=notional_value
        )
        stop_loss = entry_price + dist

        if stop_loss >= liquidation_price:
            stop_loss = liquidation_price * 0.995

        return {
            "stop_loss": stop_loss,
            "liquidation_price": liquidation_price,
            "atr": dist,
        }
