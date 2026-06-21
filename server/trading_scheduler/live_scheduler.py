"""实盘交易调度器实现"""

import asyncio
import time
from typing import Optional

from core import TradingConfig, TradingMode
from utils import logger

from server.trading_scheduler.base import BaseTradingScheduler

OPEN_COOLDOWN_SEC = 120


class LiveTradingScheduler(BaseTradingScheduler):
    """实盘交易调度器（Binance Demo Trading）

    通过交易所执行真实订单，平仓逻辑与 Sim 共用基类: AI + 止损 + 强平。
    开仓后额外在交易所挂止损单作为离线兜底。
    """

    def __init__(
        self,
        config: Optional[TradingConfig] = None,
        futures_executor=None,
        check_interval: int = 300,
        max_capital: Optional[float] = None,
        state_file: Optional[str] = None,
    ):
        if not futures_executor:
            raise ValueError("实盘模式需要 futures_executor")
        super().__init__(config=config, check_interval=check_interval, state_file=state_file)
        self._futures_executor = futures_executor
        self._max_capital = max_capital
        self._exchange_portfolio = None
        self._last_open_ts: float = 0
        self._consecutive_sync_errors: int = 0

        if state_file:
            self.restore_position_state()

    @property
    def is_live(self) -> bool:
        return True

    @property
    def futures_executor(self):
        return self._futures_executor

    @property
    def max_capital(self) -> Optional[float]:
        return self._max_capital

    def _btc_mark_price(self) -> float:
        """优先从已同步的合约 portfolio 中取标记价，避免额外 HTTP 请求。"""
        p = self._exchange_portfolio
        if p and not p.get("_error"):
            m = float(p.get("mark_price") or 0)
            if m > 0:
                return m
        return 0.0

    # ── 交易所止损单管理 ──────────────────────────────────────

    async def _place_exchange_sl(self):
        """开仓/减仓后在交易所挂止损单（与本地 stop_loss 同价）"""
        pos = self.position
        if not pos.is_active or not self._futures_executor:
            return

        sl_result = await asyncio.to_thread(
            self._futures_executor.place_stop_loss,
            self.FUTURES_SYMBOL, pos.direction, pos.size_btc, pos.stop_loss,
        )
        if sl_result.get("success"):
            pos.sl_order_id = sl_result["order_id"]
        else:
            logger.error("⚠️ 交易所止损单挂单失败，本地轮询仍生效")
        self.save_position_state()

    async def _cancel_exchange_orders(self):
        """取消所有残留交易所挂单"""
        if not self._futures_executor:
            return

        if self.position.sl_order_id:
            await asyncio.to_thread(
                self._futures_executor.cancel_order,
                self.FUTURES_SYMBOL, self.position.sl_order_id,
            )
            self.position.sl_order_id = None
            self.save_position_state()
            return

        await asyncio.to_thread(
            self._futures_executor.cancel_all_orders,
            self.FUTURES_SYMBOL,
        )

    async def _replace_exchange_sl(self):
        """取消旧止损单，按当前仓位重新挂止损单"""
        if not self._futures_executor or not self.position.is_active:
            return

        if self.position.sl_order_id:
            await asyncio.to_thread(
                self._futures_executor.cancel_order,
                self.FUTURES_SYMBOL, self.position.sl_order_id,
            )
            self.position.sl_order_id = None

        await self._place_exchange_sl()

    async def _on_position_opened(self):
        await self._place_exchange_sl()

    async def _on_position_reduced(self):
        await self._replace_exchange_sl()

    def _fallback_to_local_file(self):
        """API 失败且内存无仓位时，尝试从状态文件恢复（最后兜底）"""
        if self.position.is_active:
            return
        if self.restore_position_state():
            logger.warning(
                f"⚠️ 从本地文件兜底恢复: {self.position.direction} "
                f"@ ${self.position.entry_price:,.0f}"
            )

    async def _sync_position(self):
        """从交易所同步仓位状态"""
        try:
            portfolio = await asyncio.to_thread(
                self._futures_executor.get_portfolio, "bitcoin"
            )
            self._exchange_portfolio = portfolio

            if portfolio.get("_error"):
                self._consecutive_sync_errors += 1
                logger.warning(
                    f"⚠️ 交易所 API 返回错误，保留本地状态 "
                    f"(连续失败 {self._consecutive_sync_errors} 次)"
                )
                self._fallback_to_local_file()
                return

            self._consecutive_sync_errors = 0

            total_balance = portfolio.get("total_balance") or portfolio.get("balance", 0)
            if total_balance > 0:
                self.equity = min(total_balance, self._max_capital) if self._max_capital else total_balance

            ex_dir = portfolio.get("direction", "NONE")
            ex_size = portfolio.get("position", 0.0)
            ex_entry = portfolio.get("entry_price", 0.0)
            ex_leverage = portfolio.get("leverage", 1)
            ex_liq_price = portfolio.get("liquidation_price", 0.0)
            ex_mark_price = portfolio.get("mark_price", 0.0)

            if ex_dir != "NONE" and ex_size > 0.0001:
                prev_size = self.position.size_btc

                self.position.direction = ex_dir
                self.position.size_btc = ex_size
                self.position.entry_price = ex_entry
                self.position.leverage = ex_leverage
                if ex_liq_price > 0:
                    self.position.liquidation_price = ex_liq_price

                if self.position.stop_loss == 0 and ex_entry > 0:
                    await self._recalculate_levels(ex_dir, ex_entry, ex_leverage)

                if prev_size > 0 and ex_size < prev_size * 0.75:
                    logger.info(
                        f"📡 检测到交易所仓位减少: "
                        f"{prev_size:.4f} → {ex_size:.4f} BTC"
                    )
                    self.save_position_state()

            elif self.position.is_active:
                logger.warning("⚠️ 交易所无仓位，重置本地状态")
                await self._cancel_exchange_orders()
                self.position.reset()
                self.current_mode = TradingMode.IDLE

            logger.debug(
                f"📡 交易所同步: {ex_dir} {ex_size:.4f} BTC @ ${ex_entry:,.0f}, "
                f"杠杆={ex_leverage}x, 强平=${ex_liq_price:,.0f}, "
                f"标记价=${ex_mark_price:,.0f}, 余额=${total_balance:,.2f}"
            )
        except Exception as e:
            self._consecutive_sync_errors += 1
            logger.error(
                f"交易所同步异常，保留本地状态 "
                f"(连续失败 {self._consecutive_sync_errors} 次): {e}"
            )
            self._fallback_to_local_file()

    async def _recalculate_levels(self, direction: str, entry_price: float, leverage: int):
        """重新计算止损价位（用于重启后恢复）"""
        from binance_utils import fetch_klines

        try:
            klines = await fetch_klines(symbol="BTCUSDT", interval="4h", limit=100, use_cache=True)
            if not klines:
                logger.warning("⚠️ 无法获取K线数据，使用兜底止损")
                klines = []

            if direction == "LONG":
                cfg = self.config.long
                try:
                    levels = self.long_level.calculate(
                        entry_price=entry_price,
                        klines=klines,
                        atr_multiplier=cfg.atr_multiplier,
                        leverage=leverage,
                        notional_value=self.OPEN_NOTIONAL,
                    )
                except Exception:
                    levels = self.long_level.fallback(entry_price, leverage=leverage, notional_value=self.OPEN_NOTIONAL)
            else:
                cfg = self.config.short
                try:
                    levels = self.short_level.calculate(
                        entry_price=entry_price,
                        klines=klines,
                        atr_multiplier=cfg.atr_multiplier,
                        leverage=leverage,
                        notional_value=self.OPEN_NOTIONAL,
                    )
                except Exception:
                    levels = self.short_level.fallback(entry_price, leverage=leverage, notional_value=self.OPEN_NOTIONAL)

            self.position.stop_loss = levels["stop_loss"]
            if self.position.liquidation_price == 0:
                self.position.liquidation_price = levels["liquidation_price"]

            logger.info(
                f"📂 重新计算止损: {direction} @ ${entry_price:,.0f}, "
                f"止损=${levels['stop_loss']:,.0f}"
            )
        except Exception as e:
            logger.error(f"重新计算止损失败: {e}")

    def _check_capital_guard(self, action_label: str,
                             notional: float = 0, leverage: int = 5) -> Optional[str]:
        """检查资金上限，返回拒绝原因；None 表示通过"""
        if self.position.is_active:
            return f"已有 {self.position.direction} 仓位"

        if self._consecutive_sync_errors > 0:
            return f"交易所同步异常 (连续{self._consecutive_sync_errors}次)"

        elapsed = time.time() - self._last_open_ts
        if elapsed < OPEN_COOLDOWN_SEC:
            return f"距上次开仓仅 {elapsed:.0f}s，冷却中({OPEN_COOLDOWN_SEC}s)"

        if self._exchange_portfolio and not self._exchange_portfolio.get("_error"):
            free_balance = self._exchange_portfolio.get("balance", 0)
            effective_notional = notional or self.OPEN_NOTIONAL
            effective_leverage = leverage or 5
            margin_needed = effective_notional / effective_leverage
            cap = self._max_capital or float("inf")
            usable = min(free_balance, cap)
            if usable < margin_needed:
                return (
                    f"可用资金不足: 需要保证金 ${margin_needed:,.0f}, "
                    f"可用 ${usable:,.0f} (余额=${free_balance:,.0f}, 上限=${cap:,.0f})"
                )

        return None

    async def _open_long(self, btc_price: float, klines: list,
                         market_indicators: dict = None, decision=None) -> Optional[dict]:
        notional, leverage = self._resolve_ai_sizing(decision)

        reject = self._check_capital_guard("开多", notional, leverage)
        if reject:
            logger.warning(f"⚠️ 拒绝开多: {reject}")
            return None

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

        result = await asyncio.to_thread(
            self._futures_executor.execute_buy,
            self.FUTURES_SYMBOL, notional, btc_price,
        )
        if not result.get("success"):
            logger.error(f"🗡️ 交易所开多失败: {result.get('message')}")
            return None

        order = result.get("order", {})
        fill_price = float(order.get("average") or btc_price)
        fill_amount = float(order.get("filled") or notional / btc_price)

        self.position.direction = "LONG"
        self.position.entry_price = fill_price
        self.position.size_btc = fill_amount
        self.position.stop_loss = levels["stop_loss"]
        self.position.leverage = leverage
        self.position.liquidation_price = levels["liquidation_price"]
        self.position.analysis_id = sig_meta["analysis_id"]
        self._last_open_ts = time.time()

        logger.info(
            f"🗡️ 实盘开多: {fill_amount:.4f} BTC @ ${fill_price:,.0f} "
            f"(${notional:,.0f}, {leverage}x), "
            f"止损=${levels['stop_loss']:,.0f}, 强平=${levels['liquidation_price']:,.0f}"
        )

        return self._make_trade(
            "LONG", "LONG", fill_price, fill_amount, 0,
            market_indicators=market_indicators,
            trigger_reason=decision.reason if decision else None,
            signal_confidence=sig_meta["confidence"],
            position_levels=levels,
            analysis_id=self.position.analysis_id,
            notional=notional, leverage=leverage,
        )

    async def _open_short(self, btc_price: float, klines: list,
                          market_indicators: dict = None, decision=None) -> Optional[dict]:
        notional, leverage = self._resolve_ai_sizing(decision)

        reject = self._check_capital_guard("开空", notional, leverage)
        if reject:
            logger.warning(f"⚠️ 拒绝开空: {reject}")
            return None

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

        result = await asyncio.to_thread(
            self._futures_executor.execute_short,
            self.FUTURES_SYMBOL, notional, btc_price,
        )
        if not result.get("success"):
            logger.error(f"🛡️ 交易所开空失败: {result.get('message')}")
            return None

        order = result.get("order", {})
        fill_price = float(order.get("average") or btc_price)
        fill_amount = float(order.get("filled") or notional / btc_price)

        self.position.direction = "SHORT"
        self.position.entry_price = fill_price
        self.position.size_btc = fill_amount
        self.position.stop_loss = levels["stop_loss"]
        self.position.leverage = leverage
        self.position.liquidation_price = levels["liquidation_price"]
        self.position.analysis_id = sig_meta["analysis_id"]
        self._last_open_ts = time.time()

        logger.info(
            f"🛡️ 实盘开空: {fill_amount:.4f} BTC @ ${fill_price:,.0f} "
            f"(${notional:,.0f}, {leverage}x), "
            f"止损=${levels['stop_loss']:,.0f}, 强平=${levels['liquidation_price']:,.0f}"
        )

        return self._make_trade(
            "SHORT", "SHORT", fill_price, fill_amount, 0,
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

        await self._cancel_exchange_orders()

        is_long = self.position.direction == "LONG"

        if is_long:
            result = await asyncio.to_thread(
                self._futures_executor.execute_sell,
                self.FUTURES_SYMBOL, close_ratio, btc_price,
            )
        else:
            result = await asyncio.to_thread(
                self._futures_executor.execute_cover,
                self.FUTURES_SYMBOL, close_ratio, btc_price,
            )

        if not result.get("success"):
            logger.error(f"交易所平仓失败: {result.get('message')}")
            return None

        order = result.get("order", {})
        fill_price = float(order.get("average") or btc_price)

        close_btc = self.position.size_btc * close_ratio
        sign = 1 if is_long else -1
        pnl = sign * (fill_price - self.position.entry_price) * close_btc
        close_notional = close_btc * fill_price
        position_leverage = self.position.leverage

        mode_str = "LONG" if is_long else "SHORT"
        action = "REDUCE" if (is_partial and close_ratio < 1.0) else "CLOSE"

        logger.info(
            f"{'🗡️' if mode_str == 'LONG' else '🛡️'} 实盘平仓: "
            f"{close_btc:.4f} BTC @ ${fill_price:,.0f}, "
            f"入场=${self.position.entry_price:,.0f}, "
            f"PnL=${pnl:+,.2f} ({reason})"
        )

        trade = self._make_trade(mode_str, action, fill_price, close_btc, pnl,
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
