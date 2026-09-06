"""模拟交易调度器实现"""

from typing import Optional


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

    async def _execute_open(self, direction: str, notional: float,
                            btc_price: float) -> Optional[tuple]:
        """模拟成交：按当前标记价全额成交，无滑点无手续费"""
        return btc_price, notional / btc_price

    async def _execute_close(self, is_long: bool, close_ratio: float,
                             btc_price: float) -> Optional[float]:
        """模拟平仓：按当前标记价成交"""
        return btc_price
