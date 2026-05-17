"""模拟交易调度器实现"""

from typing import Optional

from stock_btc.utils import logger

from .base import BaseTradingScheduler


class SimTradingScheduler(BaseTradingScheduler):
    """纯模拟交易调度器

    使用真实市场价格进行模拟交易，不发送任何订单到交易所。
    状态持久化包含交易记录和余额（因为没有交易所，一切都是本地计算）。
    """

    async def _sync_position(self):
        pass
    
    def _get_position_state(self) -> dict:
        """Sim 需要额外保存交易记录和余额"""
        state = super()._get_position_state()
        state.update({
            "trades": self.trades,
            "total_pnl": self.total_pnl,
            "equity": self.equity,
        })
        return state
    
    def _apply_position_state(self, saved: dict):
        """Sim 需要额外恢复交易记录和余额"""
        super()._apply_position_state(saved)
        self.trades = saved.get("trades", [])
        self.total_pnl = saved.get("total_pnl", 0.0)
        self.equity = saved.get("equity", self.DEFAULT_EQUITY)

    async def _open_long(self, btc_price: float, klines: list,
                         market_indicators: dict = None, signal=None) -> Optional[dict]:
        # 防止重复开仓：已有仓位时直接返回
        if self.position.is_active:
            logger.warning(f"⚠️ 已有 {self.position.direction} 仓位，跳过开多")
            return None
        
        cfg = self.config.long

        try:
            levels = self.long_level.calculate(
                entry_price=btc_price,
                klines=klines,
                atr_multiplier=cfg.atr_multiplier,
                leverage=cfg.leverage,
                notional_value=self.OPEN_NOTIONAL,
            )
        except Exception as e:
            logger.error(f"ATR 计算失败: {e}, 使用兜底价位")
            levels = self.long_level.fallback(btc_price, leverage=cfg.leverage, notional_value=self.OPEN_NOTIONAL)

        pos_btc = self.OPEN_NOTIONAL / btc_price

        self.position.direction = "LONG"
        self.position.entry_price = btc_price
        self.position.size_btc = pos_btc
        self.position.stop_loss = levels["stop_loss"]
        self.position.tp1_price = levels["tp1_price"]
        self.position.tp2_price = levels["tp2_price"]
        self.position.tp1_hit = False
        self.position.leverage = cfg.leverage
        self.position.trailing_atr = levels["atr"]
        self.position.liquidation_price = levels["liquidation_price"]

        logger.info(
            f"🗡️ 模拟开多: {pos_btc:.4f} BTC @ ${btc_price:,.0f}, "
            f"止损=${levels['stop_loss']:,.0f}, TP1=${levels['tp1_price']:,.0f}, "
            f"TP2=${levels['tp2_price']:,.0f}, 强平=${levels['liquidation_price']:,.0f}"
        )

        return self._make_trade(
            "LONG", "LONG", btc_price, pos_btc, 0,
            market_indicators=market_indicators,
            trigger_reason=signal.reason if signal else None,
            signal_confidence=signal.confidence * 100 if signal else None,
            position_levels=levels,
        )

    async def _open_short(self, btc_price: float, klines: list,
                          market_indicators: dict = None, signal=None) -> Optional[dict]:
        # 防止重复开仓：已有仓位时直接返回
        if self.position.is_active:
            logger.warning(f"⚠️ 已有 {self.position.direction} 仓位，跳过开空")
            return None
        
        cfg = self.config.short

        try:
            levels = self.short_level.calculate(
                entry_price=btc_price,
                klines=klines,
                atr_multiplier=cfg.atr_multiplier,
                leverage=cfg.leverage,
                notional_value=self.OPEN_NOTIONAL,
            )
        except Exception as e:
            logger.error(f"ATR 计算失败: {e}, 使用兜底价位")
            levels = self.short_level.fallback(btc_price, leverage=cfg.leverage, notional_value=self.OPEN_NOTIONAL)

        pos_btc = self.OPEN_NOTIONAL / btc_price

        self.position.direction = "SHORT"
        self.position.entry_price = btc_price
        self.position.size_btc = pos_btc
        self.position.stop_loss = levels["stop_loss"]
        self.position.tp1_price = levels["tp1_price"]
        self.position.tp2_price = levels["tp2_price"]
        self.position.tp1_hit = False
        self.position.leverage = cfg.leverage
        self.position.trailing_atr = levels["atr"]
        self.position.liquidation_price = levels["liquidation_price"]

        logger.info(
            f"🛡️ 模拟开空: {pos_btc:.4f} BTC @ ${btc_price:,.0f}, "
            f"止损=${levels['stop_loss']:,.0f}, TP1=${levels['tp1_price']:,.0f}, "
            f"TP2=${levels['tp2_price']:,.0f}, 强平=${levels['liquidation_price']:,.0f}"
        )

        return self._make_trade(
            "SHORT", "SHORT", btc_price, pos_btc, 0,
            market_indicators=market_indicators,
            trigger_reason=signal.reason if signal else None,
            signal_confidence=signal.confidence * 100 if signal else None,
            position_levels=levels,
        )

    async def _close_position(self, btc_price: float, reason: str = "",
                              close_ratio: float = 1.0, is_tp: bool = False) -> Optional[dict]:
        if not self.position.is_active:
            return None

        is_long = self.position.direction == "LONG"
        close_btc = self.position.size_btc * close_ratio
        sign = 1 if is_long else -1
        pnl = sign * (btc_price - self.position.entry_price) * close_btc

        mode_str = "LONG" if is_long else "SHORT"
        action = "TP1_HALF" if (is_tp and close_ratio < 1.0) else "CLOSE"

        logger.info(
            f"{'🗡️' if mode_str == 'LONG' else '🛡️'} 模拟平仓: "
            f"{close_btc:.4f} BTC @ ${btc_price:,.0f}, "
            f"入场=${self.position.entry_price:,.0f}, "
            f"PnL=${pnl:+,.2f} ({reason})"
        )

        trade = self._make_trade(mode_str, action, btc_price, close_btc, pnl,
                                 entry_price=self.position.entry_price,
                                 market_indicators=self._capture_market_indicators(),
                                 trigger_reason=reason or None)

        if close_ratio >= 1.0:
            self.position.reset()
        else:
            self.position.size_btc -= close_btc

        return trade
