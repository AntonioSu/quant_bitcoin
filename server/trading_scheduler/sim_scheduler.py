"""模拟交易调度器实现"""

from typing import Optional

from utils import logger

from server.trading_scheduler.base import BaseTradingScheduler


class SimTradingScheduler(BaseTradingScheduler):
    """纯模拟交易调度器

    使用真实市场价格进行模拟交易，不发送任何订单到交易所。
    平仓逻辑与 Live 共用基类: AI + 止损 + 强平。
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
                         market_indicators: dict = None, decision=None) -> Optional[dict]:
        if self.position.is_active:
            logger.warning(f"⚠️ 已有 {self.position.direction} 仓位，跳过开多")
            return None

        notional, leverage = self._resolve_ai_sizing(decision)
        cfg = self.config.long
        sig_meta = self._get_signal_metadata()

        try:
            levels = self.long_level.calculate(
                entry_price=btc_price,
                klines=klines,
                atr_multiplier=cfg.atr_multiplier,
                leverage=leverage,
                notional_value=notional,
            )
        except Exception as e:
            logger.error(f"ATR 计算失败: {e}, 使用兜底价位")
            levels = self.long_level.fallback(btc_price, leverage=leverage, notional_value=notional)

        pos_btc = notional / btc_price

        self.position.direction = "LONG"
        self.position.entry_price = btc_price
        self.position.size_btc = pos_btc
        self.position.stop_loss = levels["stop_loss"]
        self.position.leverage = leverage
        self.position.liquidation_price = levels["liquidation_price"]
        self.position.analysis_id = sig_meta["analysis_id"]

        logger.info(
            f"🗡️ 模拟开多: {pos_btc:.4f} BTC @ ${btc_price:,.0f} "
            f"(${notional:,.0f}, {leverage}x), "
            f"止损=${levels['stop_loss']:,.0f}, 强平=${levels['liquidation_price']:,.0f}"
        )

        return self._make_trade(
            "LONG", "LONG", btc_price, pos_btc, 0,
            market_indicators=market_indicators,
            trigger_reason=decision.reason if decision else None,
            signal_confidence=sig_meta["confidence"],
            position_levels=levels,
            analysis_id=self.position.analysis_id,
            notional=notional, leverage=leverage,
        )

    async def _open_short(self, btc_price: float, klines: list,
                          market_indicators: dict = None, decision=None) -> Optional[dict]:
        if self.position.is_active:
            logger.warning(f"⚠️ 已有 {self.position.direction} 仓位，跳过开空")
            return None

        notional, leverage = self._resolve_ai_sizing(decision)
        cfg = self.config.short
        sig_meta = self._get_signal_metadata()

        try:
            levels = self.short_level.calculate(
                entry_price=btc_price,
                klines=klines,
                atr_multiplier=cfg.atr_multiplier,
                leverage=leverage,
                notional_value=notional,
            )
        except Exception as e:
            logger.error(f"ATR 计算失败: {e}, 使用兜底价位")
            levels = self.short_level.fallback(btc_price, leverage=leverage, notional_value=notional)

        pos_btc = notional / btc_price

        self.position.direction = "SHORT"
        self.position.entry_price = btc_price
        self.position.size_btc = pos_btc
        self.position.stop_loss = levels["stop_loss"]
        self.position.leverage = leverage
        self.position.liquidation_price = levels["liquidation_price"]
        self.position.analysis_id = sig_meta["analysis_id"]

        logger.info(
            f"🛡️ 模拟开空: {pos_btc:.4f} BTC @ ${btc_price:,.0f} "
            f"(${notional:,.0f}, {leverage}x), "
            f"止损=${levels['stop_loss']:,.0f}, 强平=${levels['liquidation_price']:,.0f}"
        )

        return self._make_trade(
            "SHORT", "SHORT", btc_price, pos_btc, 0,
            market_indicators=market_indicators,
            trigger_reason=decision.reason if decision else None,
            signal_confidence=sig_meta["confidence"],
            position_levels=levels,
            analysis_id=self.position.analysis_id,
            notional=notional, leverage=leverage,
        )

    async def _close_position(self, btc_price: float, reason: str = "",
                              close_ratio: float = 1.0, is_partial: bool = False) -> Optional[dict]:
        if not self.position.is_active:
            return None

        is_long = self.position.direction == "LONG"
        close_btc = self.position.size_btc * close_ratio
        sign = 1 if is_long else -1
        pnl = sign * (btc_price - self.position.entry_price) * close_btc
        close_notional = close_btc * btc_price
        position_leverage = self.position.leverage

        mode_str = "LONG" if is_long else "SHORT"
        action = "REDUCE" if (is_partial and close_ratio < 1.0) else "CLOSE"

        logger.info(
            f"{'🗡️' if mode_str == 'LONG' else '🛡️'} 模拟平仓: "
            f"{close_btc:.4f} BTC @ ${btc_price:,.0f}, "
            f"入场=${self.position.entry_price:,.0f}, "
            f"PnL=${pnl:+,.2f} ({reason})"
        )

        trade = self._make_trade(mode_str, action, btc_price, close_btc, pnl,
                                 entry_price=self.position.entry_price,
                                 market_indicators=self._capture_market_indicators(),
                                 trigger_reason=reason or None,
                                 analysis_id=self.position.analysis_id,
                                 notional=close_notional, leverage=position_leverage)

        if close_ratio >= 1.0:
            self.position.reset()
        else:
            self.position.size_btc -= close_btc
            if self.position.size_btc < 0.0001:
                logger.info("📌 剩余仓位过小，视为全平")
                self.position.reset()

        return trade
