"""止损 / 止盈 / 强平价格计算

基于 ATR 计算三个价位:
  LONG  → stop = entry - ATR × sl_mult,  tp1 = entry + ATR × tp1_mult,  tp2 = entry + ATR × tp2_mult
  SHORT → stop = entry + ATR × sl_mult,  tp1 = entry - ATR × tp1_mult,  tp2 = entry - ATR × tp2_mult

强平价格计算 (Binance USDT-M 合约):
  LONG  → liquidation = entry × (1 - 1/leverage + maintenance_margin_rate)
  SHORT → liquidation = entry × (1 + 1/leverage - maintenance_margin_rate)

  维持保证金率 (maintenance_margin_rate) 根据仓位大小分档:
    - 0-50k USDT: 0.4%
    - 50k-250k USDT: 0.5%
    - 250k-1M USDT: 1.0%
    - ... (更大仓位有更高费率)
"""

from typing import List

from indicators.atr import ATRCalculator
from utils import logger


def calc_maintenance_margin_rate(notional_value: float) -> float:
    """
    根据仓位名义价值计算维持保证金率 (Binance USDT-M BTC 合约)
    
    参考: https://www.binance.com/zh-CN/futures/trading-rules/perpetual/leverage-margin
    
    Args:
        notional_value: 仓位名义价值 (USDT)
        
    Returns:
        维持保证金率 (小数形式，如 0.004 表示 0.4%)
    """
    if notional_value <= 50_000:
        return 0.004   # 0.4%
    elif notional_value <= 250_000:
        return 0.005   # 0.5%
    elif notional_value <= 1_000_000:
        return 0.01    # 1.0%
    elif notional_value <= 5_000_000:
        return 0.025   # 2.5%
    elif notional_value <= 20_000_000:
        return 0.05    # 5.0%
    elif notional_value <= 50_000_000:
        return 0.10    # 10.0%
    elif notional_value <= 100_000_000:
        return 0.125   # 12.5%
    else:
        return 0.15    # 15.0%


def calc_liquidation_price(
    entry_price: float,
    leverage: int,
    is_long: bool,
    notional_value: float = 500.0,
) -> float:
    """
    计算强平价格
    
    Args:
        entry_price: 入场价格
        leverage: 杠杆倍数
        is_long: 是否做多
        notional_value: 仓位名义价值 (USDT)，用于计算维持保证金率
        
    Returns:
        强平价格
    """
    mmr = calc_maintenance_margin_rate(notional_value)
    
    if is_long:
        # 做多强平价 = 入场价 × (1 - 1/杠杆 + 维持保证金率)
        liquidation_price = entry_price * (1 - 1 / leverage + mmr)
    else:
        # 做空强平价 = 入场价 × (1 + 1/杠杆 - 维持保证金率)
        liquidation_price = entry_price * (1 + 1 / leverage - mmr)
    
    return liquidation_price


class LongLevel:
    """做多止损止盈计算"""

    def __init__(self, atr_calc: ATRCalculator):
        self.atr_calc = atr_calc

    def calculate(
        self,
        entry_price: float,
        klines: List[List],
        atr_multiplier: float,
        tp1_multiplier: float = 1.0,
        tp2_multiplier: float = 2.0,
        leverage: int = 10,
        notional_value: float = 500.0,
    ) -> dict:
        """
        计算做多的止损、TP1、TP2、强平价格

        Args:
            entry_price: 入场价格
            klines: K线数据
            atr_multiplier: ATR 止损倍数
            tp1_multiplier: ATR TP1 倍数 (默认 1.0)
            tp2_multiplier: ATR TP2 倍数 (默认 2.0)
            leverage: 杠杆倍数 (默认 10)
            notional_value: 仓位名义价值 (默认 500 USDT)

        Returns:
            {
                "stop_loss": float,
                "tp1_price": float,
                "tp2_price": float,
                "liquidation_price": float,
                "atr": float,
            }
        """
        atr = self.atr_calc.calculate(klines)
        atr_value = atr.value

        stop_loss = entry_price - atr_value * atr_multiplier
        tp1_price = entry_price + atr_value * tp1_multiplier
        tp2_price = entry_price + atr_value * tp2_multiplier
        liquidation_price = calc_liquidation_price(
            entry_price, leverage, is_long=True, notional_value=notional_value
        )

        # 确保止损价高于强平价，否则会被强平
        if stop_loss <= liquidation_price:
            logger.warning(
                f"⚠️ 止损价(${stop_loss:,.0f}) <= 强平价(${liquidation_price:,.0f})，"
                f"调整止损价为强平价上方 0.5%"
            )
            stop_loss = liquidation_price * 1.005

        result = {
            "stop_loss": stop_loss,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "liquidation_price": liquidation_price,
            "atr": atr_value,
        }

        logger.info(
            f"🗡️ 做多价位: 止损=${stop_loss:,.0f}, "
            f"TP1=${tp1_price:,.0f}, TP2=${tp2_price:,.0f}, "
            f"强平=${liquidation_price:,.0f}, ATR=${atr_value:,.0f}"
        )

        return result

    def fallback(self, entry_price: float, leverage: int = 10, notional_value: float = 500.0) -> dict:
        """ATR 计算失败时的兜底价位"""
        dist = entry_price * 0.02
        liquidation_price = calc_liquidation_price(
            entry_price, leverage, is_long=True, notional_value=notional_value
        )
        stop_loss = entry_price - dist
        
        # 确保止损价高于强平价
        if stop_loss <= liquidation_price:
            stop_loss = liquidation_price * 1.005
            
        return {
            "stop_loss": stop_loss,
            "tp1_price": entry_price + dist,
            "tp2_price": entry_price + dist * 2,
            "liquidation_price": liquidation_price,
            "atr": dist,
        }


class ShortLevel:
    """做空止损止盈计算"""

    def __init__(self, atr_calc: ATRCalculator):
        self.atr_calc = atr_calc

    def calculate(
        self,
        entry_price: float,
        klines: List[List],
        atr_multiplier: float,
        tp1_multiplier: float = 1.0,
        tp2_multiplier: float = 2.0,
        leverage: int = 2,
        notional_value: float = 500.0,
    ) -> dict:
        """
        计算做空的止损、TP1、TP2、强平价格

        Args:
            entry_price: 入场价格
            klines: K线数据
            atr_multiplier: ATR 止损倍数
            tp1_multiplier: ATR TP1 倍数 (默认 1.0)
            tp2_multiplier: ATR TP2 倍数 (默认 2.0)
            leverage: 杠杆倍数 (默认 2)
            notional_value: 仓位名义价值 (默认 500 USDT)

        Returns:
            {
                "stop_loss": float,
                "tp1_price": float,
                "tp2_price": float,
                "liquidation_price": float,
                "atr": float,
            }
        """
        atr = self.atr_calc.calculate(klines)
        atr_value = atr.value

        stop_loss = entry_price + atr_value * atr_multiplier
        tp1_price = entry_price - atr_value * tp1_multiplier
        tp2_price = entry_price - atr_value * tp2_multiplier
        liquidation_price = calc_liquidation_price(
            entry_price, leverage, is_long=False, notional_value=notional_value
        )

        # 确保止损价低于强平价，否则会被强平
        if stop_loss >= liquidation_price:
            logger.warning(
                f"⚠️ 止损价(${stop_loss:,.0f}) >= 强平价(${liquidation_price:,.0f})，"
                f"调整止损价为强平价下方 0.5%"
            )
            stop_loss = liquidation_price * 0.995

        result = {
            "stop_loss": stop_loss,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "liquidation_price": liquidation_price,
            "atr": atr_value,
        }

        logger.info(
            f"🛡️ 做空价位: 止损=${stop_loss:,.0f}, "
            f"TP1=${tp1_price:,.0f}, TP2=${tp2_price:,.0f}, "
            f"强平=${liquidation_price:,.0f}, ATR=${atr_value:,.0f}"
        )

        return result

    def fallback(self, entry_price: float, leverage: int = 2, notional_value: float = 500.0) -> dict:
        """ATR 计算失败时的兜底价位"""
        dist = entry_price * 0.02
        liquidation_price = calc_liquidation_price(
            entry_price, leverage, is_long=False, notional_value=notional_value
        )
        stop_loss = entry_price + dist
        
        # 确保止损价低于强平价
        if stop_loss >= liquidation_price:
            stop_loss = liquidation_price * 0.995
            
        return {
            "stop_loss": stop_loss,
            "tp1_price": entry_price - dist,
            "tp2_price": entry_price - dist * 2,
            "liquidation_price": liquidation_price,
            "atr": dist,
        }
