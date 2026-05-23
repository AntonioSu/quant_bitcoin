#!/usr/bin/env python3
"""
Binance 合约执行器适配器
"""

import time
from typing import Dict, Optional

from utils.log_util import logger


class BinanceFuturesExecutorAdapter:
    """合约执行器适配器"""

    def __init__(self, binance_client, assets_config: Dict, leverage: int = 5):
        self.client = binance_client
        self.assets = assets_config
        self.default_leverage = leverage
        self._setup_futures()

    def _setup_futures(self):
        """设置合约配置，leverage: 杠杆倍数"""
        try:
            self.client.exchange.options["defaultType"] = "future"
            for conf in self.assets.values():
                symbol = conf["symbol"]
                try:
                    self.client.exchange.set_position_mode(True, symbol)
                    logger.info(f"✅ {symbol} 设置为双向持仓模式")
                except Exception as e:
                    logger.warning(f"{symbol} 设置双向持仓模式失败（可能已配置）: {e}")
                try:
                    self.client.exchange.set_leverage(self.default_leverage, symbol)
                    logger.info(f"✅ {symbol} 设置杠杆为 {self.default_leverage}x")
                except Exception as e:
                    logger.warning(f"{symbol} 设置杠杆失败（可能已配置）: {e}")
        except Exception as e:
            logger.warning(f"合约初始化配置失败: {e}")

    def _symbol_to_asset_id(self, symbol: str) -> Optional[str]:
        """将交易对转换为资产ID"""
        for asset_id, conf in self.assets.items():
            if conf.get("symbol") == symbol:
                return asset_id
        return None

    def get_portfolio(self, asset_id: str) -> Dict:
        """获取合约持仓，asset_id: 资产ID
        
        Returns:
            {
                "position": float,          # 仓位大小 (BTC)
                "direction": str,           # LONG / SHORT / NONE
                "entry_price": float,       # 入场价格
                "balance": float,           # 可用余额
                "total_balance": float,     # 总余额
                "unrealized_pnl": float,    # 未实现盈亏
                "leverage": int,            # 杠杆倍数 (交易所实际值)
                "margin": float,            # 已用保证金
                "liquidation_price": float, # 强平价格 (交易所实际值)
                "mark_price": float,        # 标记价格
            }
        """
        try:
            asset_conf = self.assets.get(asset_id, {})
            symbol = asset_conf.get("symbol")
            if not symbol:
                raise ValueError(f"Asset {asset_id} not found in config")

            positions = self.client.exchange.fetch_positions([symbol])

            long_pos = short_pos = long_entry = short_entry = 0.0
            long_pnl = short_pnl = 0.0
            long_leverage = short_leverage = self.default_leverage
            long_liq = short_liq = 0.0
            long_mark = short_mark = 0.0
            long_margin = short_margin = 0.0
            
            for pos in positions:
                if pos["symbol"] == symbol:
                    contracts = float(pos.get("contracts") or 0)
                    side = pos.get("side", "")
                    pnl_val = float(pos.get("unrealizedPnl") or 0)
                    leverage_val = int(pos.get("leverage") or self.default_leverage)
                    liq_price = float(pos.get("liquidationPrice") or 0)
                    mark_price = float(pos.get("markPrice") or 0)
                    margin_val = float(pos.get("initialMargin") or pos.get("collateral") or 0)
                    
                    if side == "long" and contracts > 0:
                        long_pos = contracts
                        long_entry = float(pos.get("entryPrice") or 0)
                        long_pnl = pnl_val
                        long_leverage = leverage_val
                        long_liq = liq_price
                        long_mark = mark_price
                        long_margin = margin_val
                    elif side == "short" and contracts > 0:
                        short_pos = contracts
                        short_entry = float(pos.get("entryPrice") or 0)
                        short_pnl = pnl_val
                        short_leverage = leverage_val
                        short_liq = liq_price
                        short_mark = mark_price
                        short_margin = margin_val

            # 确定主要持仓方向
            unrealized_pnl = 0.0
            leverage = self.default_leverage
            liquidation_price = 0.0
            mark_price = 0.0
            margin = 0.0
            
            if long_pos > 0.0001 and short_pos < 0.0001:
                direction, position, entry_price = "LONG", long_pos, long_entry
                unrealized_pnl, leverage, liquidation_price = long_pnl, long_leverage, long_liq
                mark_price, margin = long_mark, long_margin
            elif short_pos > 0.0001 and long_pos < 0.0001:
                direction, position, entry_price = "SHORT", short_pos, short_entry
                unrealized_pnl, leverage, liquidation_price = short_pnl, short_leverage, short_liq
                mark_price, margin = short_mark, short_margin
            elif long_pos > 0.0001 and short_pos > 0.0001:
                if long_pos > short_pos:
                    direction, position, entry_price = "LONG", long_pos, long_entry
                    unrealized_pnl, leverage, liquidation_price = long_pnl, long_leverage, long_liq
                    mark_price, margin = long_mark, long_margin
                else:
                    direction, position, entry_price = "SHORT", short_pos, short_entry
                    unrealized_pnl, leverage, liquidation_price = short_pnl, short_leverage, short_liq
                    mark_price, margin = short_mark, short_margin
            else:
                direction, position, entry_price = "NONE", 0.0, 0.0

            # 如果交易所没返回 margin，本地计算
            if margin == 0 and position > 0 and entry_price > 0:
                margin = (position * entry_price) / leverage

            balance_info = self.client.exchange.fetch_balance()
            usdt_free = float(balance_info.get("USDT", {}).get("free", 0) or 0)
            usdt_total = float(balance_info.get("USDT", {}).get("total", 0) or 0)

            return {
                "position": position,
                "direction": direction,
                "entry_price": entry_price,
                "balance": usdt_free,
                "total_balance": usdt_total,
                "unrealized_pnl": unrealized_pnl,
                "leverage": leverage,
                "margin": margin,
                "liquidation_price": liquidation_price,
                "mark_price": mark_price,
            }
        except Exception as e:
            logger.error(f"获取持仓失败 ({asset_id}): {e}")
            return {
                "position": 0.0,
                "direction": "NONE",
                "entry_price": 0.0,
                "balance": 0.0,
                "total_balance": 0.0,
                "unrealized_pnl": 0.0,
                "leverage": self.default_leverage,
                "margin": 0.0,
                "liquidation_price": 0.0,
                "mark_price": 0.0,
                "_error": True,
            }

    def execute_buy(self, symbol: str, amount_usdt: float, price: float) -> Dict:
        """开多仓，市价单"""
        try:
            amount = self.client.exchange.amount_to_precision(symbol, amount_usdt / price)
            logger.info(f"🟢 开多仓: {symbol} {amount} @ ${price:.2f} ({self.default_leverage}x)")
            order = self.client.exchange.create_market_buy_order(symbol, amount, params={"reduceOnly": False})
            return {"success": True, "order": order, "message": f"开多成功: {amount} @ ${price:.2f}"}
        except Exception as e:
            logger.error(f"开多失败: {e}")
            return {"success": False, "order": None, "message": f"开多失败: {e}"}

    def execute_sell(self, symbol: str, sell_ratio: float, price: float) -> Dict:
        """平多仓，市价单"""
        try:
            asset_id = self._symbol_to_asset_id(symbol)
            portfolio = self.get_portfolio(asset_id)
            if portfolio["direction"] != "LONG":
                return {"success": False, "order": None, "message": "无多头仓位可平"}
            sell_amount = portfolio["position"] * sell_ratio
            if sell_amount < 0.0001:
                return {"success": False, "order": None, "message": "平仓数量过小"}
            sell_amount = self.client.exchange.amount_to_precision(symbol, sell_amount)
            logger.info(f"🔴 平多仓: {symbol} {sell_amount} ({sell_ratio:.1%}) @ ${price:.2f}")
            order = self.client.exchange.create_market_sell_order(symbol, sell_amount, params={"reduceOnly": True})
            return {"success": True, "order": order, "message": f"平多成功: {sell_amount} ({sell_ratio:.1%})"}
        except Exception as e:
            logger.error(f"平多失败: {e}")
            return {"success": False, "order": None, "message": f"平多失败: {e}"}

    def execute_short(self, symbol: str, amount_usdt: float, price: float) -> Dict:
        """开空仓，市价单"""
        try:
            amount = self.client.exchange.amount_to_precision(symbol, amount_usdt / price)
            logger.info(f"🟠 开空仓: {symbol} {amount} @ ${price:.2f} ({self.default_leverage}x)")
            order = self.client.exchange.create_market_sell_order(symbol, amount, params={"reduceOnly": False})
            return {"success": True, "order": order, "message": f"开空成功: {amount} @ ${price:.2f}"}
        except Exception as e:
            logger.error(f"开空失败: {e}")
            return {"success": False, "order": None, "message": f"开空失败: {e}"}

    def execute_cover(self, symbol: str, cover_ratio: float, price: float) -> Dict:
        """平空仓，市价单"""
        try:
            asset_id = self._symbol_to_asset_id(symbol)
            portfolio = self.get_portfolio(asset_id)
            if portfolio["direction"] != "SHORT":
                return {"success": False, "order": None, "message": "无空头仓位可平"}
            cover_amount = portfolio["position"] * cover_ratio
            if cover_amount < 0.0001:
                return {"success": False, "order": None, "message": "平仓数量过小"}
            cover_amount = self.client.exchange.amount_to_precision(symbol, cover_amount)
            logger.info(f"🟣 平空仓: {symbol} {cover_amount} ({cover_ratio:.1%}) @ ${price:.2f}")
            order = self.client.exchange.create_market_buy_order(symbol, cover_amount, params={"reduceOnly": True})
            return {"success": True, "order": order, "message": f"平空成功: {cover_amount} ({cover_ratio:.1%})"}
        except Exception as e:
            logger.error(f"平空失败: {e}")
            return {"success": False, "order": None, "message": f"平空失败: {e}"}

    # ── 条件单（交易所侧止损止盈）──────────────────────────────

    _NO_RETRY_CODES = {-2021, -2022, -1116, -4003}

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """业务错误（如 Order would immediately trigger）不重试，只重试网络/临时错误"""
        msg = str(exc)
        for code in BinanceFuturesExecutorAdapter._NO_RETRY_CODES:
            if f'"code":{code}' in msg:
                return False
        return True

    def place_stop_loss(self, symbol: str, direction: str,
                        amount: float, stop_price: float,
                        max_retries: int = 3, retry_delay: float = 1.0) -> Dict:
        """挂止损单 (STOP_MARKET)，交易所侧监控触发

        Args:
            direction: 持仓方向 LONG / SHORT（决定平仓方向）
            amount: 平仓数量 (BTC)
            stop_price: 触发价
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒），每次翻倍
        """
        close_side = "sell" if direction == "LONG" else "buy"
        amount = self.client.exchange.amount_to_precision(symbol, amount)
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                order = self.client.exchange.create_order(
                    symbol, "STOP_MARKET", close_side, amount, None,
                    params={"stopPrice": stop_price, "reduceOnly": True},
                )
                order_id = order.get("id", "")
                logger.info(
                    f"📌 挂止损单: {close_side} {amount} @ trigger ${stop_price:,.0f}, "
                    f"order_id={order_id}"
                )
                return {"success": True, "order": order, "order_id": order_id}
            except Exception as e:
                last_err = e
                if not self._is_retryable(e) or attempt >= max_retries:
                    break
                delay = retry_delay * (2 ** (attempt - 1))
                logger.warning(f"挂止损单失败 (第{attempt}次), {delay:.0f}s 后重试: {e}")
                time.sleep(delay)
        logger.error(f"挂止损单失败 (已重试{max_retries}次): {last_err}")
        return {"success": False, "order": None, "order_id": None, "message": str(last_err)}

    def place_take_profit(self, symbol: str, direction: str,
                          amount: float, trigger_price: float,
                          max_retries: int = 3, retry_delay: float = 1.0) -> Dict:
        """挂止盈单 (TAKE_PROFIT_MARKET)，交易所侧监控触发

        Args:
            direction: 持仓方向 LONG / SHORT
            amount: 平仓数量 (BTC)
            trigger_price: 触发价
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒），每次翻倍
        """
        close_side = "sell" if direction == "LONG" else "buy"
        amount = self.client.exchange.amount_to_precision(symbol, amount)
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                order = self.client.exchange.create_order(
                    symbol, "TAKE_PROFIT_MARKET", close_side, amount, None,
                    params={"stopPrice": trigger_price, "reduceOnly": True},
                )
                order_id = order.get("id", "")
                logger.info(
                    f"📌 挂止盈单: {close_side} {amount} @ trigger ${trigger_price:,.0f}, "
                    f"order_id={order_id}"
                )
                return {"success": True, "order": order, "order_id": order_id}
            except Exception as e:
                last_err = e
                if not self._is_retryable(e) or attempt >= max_retries:
                    break
                delay = retry_delay * (2 ** (attempt - 1))
                logger.warning(f"挂止盈单失败 (第{attempt}次), {delay:.0f}s 后重试: {e}")
                time.sleep(delay)
        logger.error(f"挂止盈单失败 (已重试{max_retries}次): {last_err}")
        return {"success": False, "order": None, "order_id": None, "message": str(last_err)}

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """取消指定挂单"""
        try:
            self.client.exchange.cancel_order(order_id, symbol)
            logger.info(f"❌ 已取消挂单: {order_id}")
            return True
        except Exception as e:
            logger.warning(f"取消挂单失败 ({order_id}): {e}")
            return False

    def cancel_all_orders(self, symbol: str) -> bool:
        """取消该交易对的所有挂单（兜底清理）"""
        try:
            self.client.exchange.cancel_all_orders(symbol)
            logger.info(f"❌ 已取消所有挂单: {symbol}")
            return True
        except Exception as e:
            logger.warning(f"取消所有挂单失败 ({symbol}): {e}")
            return False


# ══════════════════════════════════════════════════════════════════
# 工厂函数
# ══════════════════════════════════════════════════════════════════

def create_futures_executor(
    binance_cfg: dict, assets_config: Dict, demo: bool = True, leverage: int = 5,
) -> BinanceFuturesExecutorAdapter:
    """创建合约执行器
    
    Args:
        binance_cfg: Binance 配置字典
        assets_config: 资产配置
        demo: 是否使用 Demo Trading
        leverage: 杠杆倍数
    """
    from binance_utils.binance_client import BinanceClient
    client = BinanceClient(
        binance_cfg=binance_cfg,
        market_type="future",
        demo=demo,
        testnet=False,
    )
    return BinanceFuturesExecutorAdapter(client, assets_config, leverage)
