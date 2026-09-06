"""实盘交易调度器实现"""

import asyncio
import time
from typing import Optional

from core import TradingConfig, TradingMode
from utils import logger

from server.trading_scheduler.base import (
    BaseTradingScheduler,
    DEFAULT_CHECK_INTERVAL,
    DIRECTION_ICON,
)

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
        check_interval: int = DEFAULT_CHECK_INTERVAL,
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

    async def _on_stop_loss_moved(self):
        """保本 / 移动止损抬高后，同步替换交易所止损单"""
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

            is_long = direction == "LONG"
            cfg = self.config.long if is_long else self.config.short
            level = self.long_level if is_long else self.short_level
            try:
                levels = level.calculate(
                    entry_price=entry_price,
                    klines=klines,
                    atr_multiplier=cfg.atr_multiplier,
                    leverage=leverage,
                    notional_value=self.OPEN_NOTIONAL,
                )
            except Exception:
                levels = level.fallback(
                    entry_price, leverage=leverage, notional_value=self.OPEN_NOTIONAL
                )

            self.position.stop_loss = levels["stop_loss"]
            if self.position.liquidation_price == 0:
                self.position.liquidation_price = levels["liquidation_price"]
            # 该分支只在本地无止损时触发，此时保本/移动止损基准也一并重建
            self.position.initial_stop = levels["stop_loss"]
            self.position.mfe_price = entry_price
            self.position.stop_stage = "INIT"

            logger.info(
                f"📂 重新计算止损: {direction} @ ${entry_price:,.0f}, "
                f"止损=${levels['stop_loss']:,.0f}"
            )
        except Exception as e:
            logger.error(f"重新计算止损失败: {e}")

    def _reject_open(self, direction: str, notional: float = 0,
                     leverage: int = 5) -> Optional[str]:
        """实盘开仓四层护栏：已有仓位 / 同步异常 / 开仓冷却 / 资金上限"""
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

    async def _execute_open(self, direction: str, notional: float,
                            btc_price: float) -> Optional[tuple]:
        """在交易所建仓，返回 (成交价, 成交数量)"""
        is_long = direction == "LONG"
        execute = (
            self._futures_executor.execute_buy if is_long
            else self._futures_executor.execute_short
        )
        result = await asyncio.to_thread(
            execute, self.FUTURES_SYMBOL, notional, btc_price
        )
        if not result.get("success"):
            logger.error(
                f"{DIRECTION_ICON[direction]} 交易所开{'多' if is_long else '空'}失败: "
                f"{result.get('message')}"
            )
            return None

        order = result.get("order", {})
        self._last_open_ts = time.time()
        return (
            float(order.get("average") or btc_price),
            float(order.get("filled") or notional / btc_price),
        )

    async def _execute_close(self, is_long: bool, close_ratio: float,
                             btc_price: float) -> Optional[float]:
        """在交易所平仓，返回成交价"""
        # 先撤掉挂在交易所的止损单，避免平仓后残留孤儿单
        await self._cancel_exchange_orders()

        execute = (
            self._futures_executor.execute_sell if is_long
            else self._futures_executor.execute_cover
        )
        result = await asyncio.to_thread(
            execute, self.FUTURES_SYMBOL, close_ratio, btc_price
        )
        if not result.get("success"):
            logger.error(f"交易所平仓失败: {result.get('message')}")
            return None

        order = result.get("order", {})
        return float(order.get("average") or btc_price)
