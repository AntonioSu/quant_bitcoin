"""交易调度器基础类和共享组件"""

import asyncio
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from stock_btc.core import (
    TradingConfig, ParameterSet, TradingMode,
    SignalAggregator,
    get_analysis_memory, get_strategy_summarizer,
)
from stock_btc.core.market_data import market
from stock_btc.indicators import LongLevel, ShortLevel
from stock_btc.binance_utils import fetch_klines, fetch_price
from stock_btc.server.state_store import StateStore
from stock_btc.utils import logger


class Position:
    """本地仓位状态（Sim/Live 共用，止损止盈逻辑均在本地维护）"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.direction = "NONE"      # LONG / SHORT / NONE
        self.entry_price = 0.0
        self.size_btc = 0.0          # BTC 仓位大小
        self.stop_loss = 0.0
        self.tp1_price = 0.0
        self.tp2_price = 0.0
        self.tp1_hit = False         # TP1 是否已触发
        self.leverage = 1
        self.highest_since_tp1 = 0.0 # TP1 后追踪的最高价 (LONG) / 最低价 (SHORT)
        self.trailing_atr = 0.0      # TP1 时记录的 ATR，用于计算 trailing 距离
        self.liquidation_price = 0.0 # 强平价格
        self.sl_order_id = None      # 交易所止损挂单 ID
        self.tp1_order_id = None     # 交易所 TP1 挂单 ID

    @property
    def is_active(self) -> bool:
        return self.direction != "NONE" and self.size_btc > 0

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "entry_price": self.entry_price,
            "size_btc": self.size_btc,
            "stop_loss": self.stop_loss,
            "tp1_price": self.tp1_price,
            "tp2_price": self.tp2_price,
            "tp1_hit": self.tp1_hit,
            "leverage": self.leverage,
            "highest_since_tp1": self.highest_since_tp1,
            "liquidation_price": self.liquidation_price,
            "sl_order_id": self.sl_order_id,
            "tp1_order_id": self.tp1_order_id,
        }


class BaseTradingScheduler(ABC):
    """交易调度器抽象基类

    只定义框架（信号评估、止盈止损检查、回调、主循环）和抽象接口。
    所有交易操作（开仓、平仓、同步）由子类实现。
    """
    
    DEFAULT_EQUITY = 1000.0 # 默认权益
    OPEN_NOTIONAL = 500.0 # 开仓金额
    FUTURES_SYMBOL = "BTC/USDT:USDT"

    def __init__(
        self,
        config: Optional[TradingConfig] = None,
        check_interval: int = 300,
        state_file: Optional[str] = None,
    ):
        self.config = config or TradingConfig.get_preset(ParameterSet.STANDARD)
        self.check_interval = check_interval

        self.signal_aggregator = SignalAggregator(config=self.config)
        # ATR 使用全局 market 中的计算结果
        # LongLevel/ShortLevel 仍需要 ATR，但可从 market.atr 获取
        from stock_btc.indicators import ATRCalculator
        self._atr_calc_fallback = ATRCalculator(period=14, timeframe="4h")
        self.long_level = LongLevel(self._atr_calc_fallback)
        self.short_level = ShortLevel(self._atr_calc_fallback)

        self.running = False
        self.current_mode = TradingMode.IDLE
        self.last_check_time: Optional[datetime] = None
        self.trades = []
        self.total_pnl = 0.0

        self.position = Position()
        self.equity = self.DEFAULT_EQUITY
        self._last_klines = []

        self._on_update_callbacks = []
        self._on_signal_callbacks = []
        self._on_trade_callbacks = []
        
        # 状态持久化
        self._state_store = StateStore(filepath=state_file) if state_file else None
        self._state_file = state_file

    def _btc_mark_price(self) -> float:
        """从合约 portfolio 获取标记价；实盘子类覆盖此方法。"""
        return 0.0

    # ── 属性 ──────────────────────────────────────────────────

    @property
    def is_live(self) -> bool:
        return False

    @property
    def dry_run(self) -> bool:
        return not self.is_live

    @property
    def mode_label(self) -> str:
        return "实盘" if self.is_live else "模拟"

    @property
    def futures_executor(self):
        return None

    @property
    def max_capital(self) -> Optional[float]:
        return None

    # ── 回调注册 ──────────────────────────────────────────────

    def on_update(self, callback):
        self._on_update_callbacks.append(callback)

    def on_signal(self, callback):
        self._on_signal_callbacks.append(callback)

    def on_trade(self, callback):
        self._on_trade_callbacks.append(callback)

    async def _emit(self, callbacks: list, payload):
        for cb in callbacks:
            try:
                await cb(payload) if asyncio.iscoroutinefunction(cb) else cb(payload)
            except Exception as e:
                logger.error(f"回调错误: {e}")

    # ── 状态持久化 ──────────────────────────────────────────────
    
    def save_position_state(self):
        """保存仓位状态到文件（只保存止盈止损相关，子类可覆盖扩展）"""
        if not self._state_store:
            return
        
        state = self._get_position_state()
        self._state_store.save(state)
    
    def _get_position_state(self) -> dict:
        """获取需要持久化的仓位状态（子类可覆盖扩展）"""
        return {
            "current_mode": self.current_mode.value,
            "position_direction": self.position.direction,
            "position_size": self.position.size_btc,
            "entry_price": self.position.entry_price,
            "stop_loss": self.position.stop_loss,
            "tp1_price": self.position.tp1_price,
            "tp2_price": self.position.tp2_price,
            "tp1_hit": self.position.tp1_hit,
            "trailing_atr": self.position.trailing_atr,
            "liquidation_price": self.position.liquidation_price,
            "leverage": self.position.leverage,
            "highest_since_tp1": self.position.highest_since_tp1,
            "sl_order_id": self.position.sl_order_id,
            "tp1_order_id": self.position.tp1_order_id,
        }
    
    def restore_position_state(self) -> bool:
        """从文件恢复仓位状态，返回是否成功恢复"""
        if not self._state_store:
            return False
        
        saved = self._state_store.load()
        if not saved:
            return False
        
        self._apply_position_state(saved)
        
        if self.position.stop_loss > 0:
            logger.info(
                f"📂 恢复仓位状态: {self.position.direction} @ ${self.position.entry_price:,.0f}, "
                f"止损=${self.position.stop_loss:,.0f}, TP1=${self.position.tp1_price:,.0f}"
            )
        return True
    
    def _apply_position_state(self, saved: dict):
        """应用恢复的状态（子类可覆盖扩展）"""
        self.position.direction = saved.get("position_direction", "NONE")
        self.position.size_btc = saved.get("position_size", 0.0)
        self.position.entry_price = saved.get("entry_price", 0.0)
        self.position.stop_loss = saved.get("stop_loss", 0.0)
        self.position.tp1_price = saved.get("tp1_price", 0.0)
        self.position.tp2_price = saved.get("tp2_price", 0.0)
        self.position.tp1_hit = saved.get("tp1_hit", False)
        self.position.trailing_atr = saved.get("trailing_atr", 0.0)
        self.position.liquidation_price = saved.get("liquidation_price", 0.0)
        self.position.leverage = saved.get("leverage", 1)
        self.position.highest_since_tp1 = saved.get("highest_since_tp1", 0.0)
        self.position.sl_order_id = saved.get("sl_order_id")
        self.position.tp1_order_id = saved.get("tp1_order_id")
        
        if not self.position.is_active:
            self.current_mode = TradingMode.IDLE
            return
        
        saved_mode = saved.get("current_mode", "idle")
        direction = self.position.direction
        # 修正不一致: 有 SHORT 仓位但 current_mode=idle → 强制为 short
        if saved_mode == "idle" and direction in ("LONG", "SHORT"):
            self.current_mode = TradingMode(direction.lower())
            logger.warning(
                f"⚠️ 状态不一致修正: 有 {direction} 仓位但 mode={saved_mode}, "
                f"强制设为 {self.current_mode.value}"
            )
        else:
            self.current_mode = TradingMode(saved_mode)

    # ── 抽象方法（子类必须实现）────────────────────────────────

    @abstractmethod
    async def _sync_position(self):
        """同步仓位状态"""

    @abstractmethod
    async def _open_long(self, btc_price: float, klines: list,
                         market_indicators: dict = None, signal=None) -> Optional[dict]:
        """开多仓，返回交易记录"""

    @abstractmethod
    async def _open_short(self, btc_price: float, klines: list,
                          market_indicators: dict = None, signal=None) -> Optional[dict]:
        """开空仓，返回交易记录"""

    @abstractmethod
    async def _close_position(self, btc_price: float, reason: str = "",
                              close_ratio: float = 1.0, is_tp: bool = False) -> Optional[dict]:
        """平仓（全部或部分），返回交易记录"""

    # ── 框架方法（共享逻辑）────────────────────────────────────

    async def _record_trades(self, trades: list):
        for trade in trades:
            pnl = trade.get("pnl", 0)
            self.total_pnl += pnl
            self.equity += pnl
            self.trades.append(trade)
            await self._emit(self._on_trade_callbacks, trade)

            # 平仓时：关联交易结果到研判记忆 + 触发异步复盘
            action = trade.get("action", "")
            if action in ("CLOSE", "TP1_HALF"):
                self._link_trade_to_memory(trade)

    def _link_trade_to_memory(self, trade: dict):
        """将平仓结果关联到研判记忆，并异步触发复盘"""
        try:
            memory = get_analysis_memory()
            if not memory:
                return

            # 找到对应的研判记录并关联交易结果
            latest_id = memory.get_latest_analysis_id()
            if latest_id:
                memory.attach_trade_result(latest_id, trade)
                logger.info(f"📝 交易结果已关联到研判 {latest_id}")

                # 异步触发复盘（不阻塞交易主循环）
                asyncio.ensure_future(self._async_reflect(latest_id))
        except Exception as e:
            logger.warning(f"📝 关联交易记忆失败: {e}")

    async def _async_reflect(self, record_id: str):
        """异步复盘，不影响交易主流程"""
        try:
            from stock_btc.indicators.reflector import Reflector
            memory = get_analysis_memory()
            reflector = Reflector(memory=memory)
            await asyncio.to_thread(reflector.reflect_on_trade, record_id)

            # 检查是否需要更新策略备忘录（每 5 笔有复盘的交易触发一次）
            all_reflections = memory.get_all_reflections(since_days=30)
            if len(all_reflections) >= 3 and len(all_reflections) % 5 == 0:
                summarizer = get_strategy_summarizer()
                if summarizer:
                    from stock_btc.core.performance import PerformanceTracker
                    perf = PerformanceTracker().calculate(self.trades, self.equity - self.total_pnl)
                    await asyncio.to_thread(summarizer.generate, perf)
        except Exception as e:
            logger.warning(f"🔍 异步复盘失败: {e}")

    async def check_and_execute(self):
        """检查信号并执行"""
        self.last_check_time = datetime.now()

        try:
            klines = market.klines_4h if market.klines_4h else await fetch_klines(symbol="BTCUSDT", interval="4h", limit=100, use_cache=True)
            if not klines:
                logger.warning("K线数据获取失败，跳过本次检查")
                return

            self._last_klines = klines

            await self._sync_position()

            btc_price = self._btc_mark_price()
            if btc_price <= 0:
                btc_price = await fetch_price(symbol="BTCUSDT")
            if btc_price <= 0:
                logger.warning("BTC 价格获取失败（含回退），跳过本次检查")
                return

            # 止盈止损检查: 若本周期发生了平仓，标记 just_closed 阻止同周期再开仓
            just_closed = False
            if self.position.is_active:
                sl_tp_trades = await self._check_stop_loss_take_profit(btc_price)
                await self._record_trades(sl_tp_trades)
                if not self.position.is_active:
                    self.current_mode = TradingMode.IDLE
                    self.save_position_state()
                    just_closed = bool(sl_tp_trades)

            signal = self.signal_aggregator.evaluate(self.current_mode)

            logger.info(
                f"📊 信号检查: 模式={signal.mode.value}, "
                f"置信度={signal.confidence:.1%}, "
                f"原因={signal.reason}"
            )

            await self._emit(self._on_update_callbacks, {
                "timestamp": datetime.now().isoformat(),
                "btc_price": btc_price,
                "mode": self.current_mode.value,
                "signal": {
                    "mode": signal.mode.value,
                    "confidence": signal.confidence,
                    "reason": signal.reason,
                    "values": signal.values,
                },
                "position": self.position.to_dict(),
                "equity": self.equity,
            })

            if signal.mode != self.current_mode:
                if self.position.is_active:
                    logger.info(
                        f"📊 信号切换 {self.current_mode.value} → {signal.mode.value}，"
                        f"但持仓中，等待止损/止盈平仓"
                    )
                elif just_closed:
                    logger.info(
                        f"📊 本周期刚平仓，跳过开仓，下次再评估"
                    )
                else:
                    await self._emit(self._on_signal_callbacks, signal)
                    trades = await self._execute_mode_change(signal, btc_price, klines)
                    await self._record_trades(trades)
                    self.current_mode = signal.mode
                    self.save_position_state()
            
            # 每次检查后保存（移动止盈可能更新 stop_loss）
            if self.position.is_active:
                self.save_position_state()

        except Exception as e:
            logger.error(f"检查执行错误: {e}")
            import traceback
            traceback.print_exc()

    def _capture_market_indicators(self, signal=None) -> dict:
        """从全局 market 中提取完整的市场指标快照（开仓 / 平仓通用）"""
        try:
            signal_values = (signal.values or {}) if signal else {}
            fear_greed = signal_values.get("fear_greed",
                                           market.fear_greed.value if market.fear_greed else 50)
            funding_rate = signal_values.get("funding_rate",
                                             market.funding_rate.value if market.funding_rate else 0.0)
            top_trader_ratio = signal_values.get("top_trader_ratio",
                                                  market.top_trader.value if market.top_trader else 1.0)

            fg_raw = (market.fear_greed.raw or {}) if market.fear_greed else {}
            fr_raw = (market.funding_rate.raw or {}) if market.funding_rate else {}
            tt_raw = (market.top_trader.raw or {}) if market.top_trader else {}

            result: dict = {
                # ── 情绪 / 资金面 ──
                "fear_greed_index": int(fear_greed),
                "fear_greed_status": fg_raw.get("classification", "Unknown"),
                "funding_rate": round(funding_rate, 5),
                "funding_rate_predicted": round(fr_raw.get("predicted_rate", funding_rate), 5),
                "funding_rate_annual": round(fr_raw.get("annual_yield", 0), 2),
                "top_trader_long_pct": round(tt_raw.get("long_account", 0.5) * 100, 2),
                "top_trader_short_pct": round(tt_raw.get("short_account", 0.5) * 100, 2),
                "long_short_ratio": round(top_trader_ratio, 2),
                "price_change_pct": round(signal_values.get("price_change_pct", 0.0), 2),
                "cvd_change_pct": round(signal_values.get("cvd_change_pct", 0.0), 2),
                "divergence_type": signal_values.get("divergence_type", "无"),
                "divergence_strength": round(signal_values.get("divergence_strength", 0.0), 2),
            }

            # ── 技术指标 (4H) ──
            if market.macd:
                result.update({
                    "macd_signal": market.macd.signal_type.value,
                    "macd_above_zero": market.macd.above_zero,
                    "macd_histogram_rising": market.macd.histogram_rising,
                    "macd_strength": round(market.macd.strength, 3),
                })
            if market.rsi:
                result.update({
                    "rsi_value": round(market.rsi.rsi_value, 2),
                    "rsi_signal": market.rsi.signal_type.value,
                    "rsi_above_center": market.rsi.above_center,
                    "rsi_strength": round(market.rsi.strength, 3),
                })
            if market.bollinger:
                result.update({
                    "boll_signal": market.bollinger.signal_type.value,
                    "boll_percent_b": round(market.bollinger.percent_b, 3),
                    "boll_bandwidth": round(market.bollinger.bandwidth, 4),
                    "boll_is_squeeze": market.bollinger.is_squeeze,
                })
            if market.ma:
                result.update({
                    "ma_signal": market.ma.signal_type.value,
                    "ma_trend": market.ma.trend,
                    "ma_price_deviation": round(market.ma.price_deviation, 4),
                })
            if market.volume:
                result.update({
                    "vol_signal": market.volume.signal_type.value,
                    "vol_ratio": round(market.volume.vol_ratio, 2),
                    "obv_trend": market.volume.obv_trend,
                })

            # ── Taker ──
            if market.taker:
                td = market.taker.to_dict()
                result.update({
                    "taker_buy_ratio": round(td.get("taker_buy_ratio", 0.5), 3),
                })

            # ── ETF ──
            if market.etf_flow:
                ef_raw = market.etf_flow.raw or {}
                result.update({
                    "etf_daily_flow_usd": ef_raw.get("daily_flow"),
                    "etf_streak_days": ef_raw.get("streak_days"),
                })

            # ── 持仓量 ──
            if market.open_interest:
                oi_raw = market.open_interest.raw or {}
                result.update({
                    "oi_change_4h_pct": oi_raw.get("change_4h"),
                    "oi_change_24h_pct": oi_raw.get("change_24h"),
                })

            # ── 爆仓 ──
            if market.liquidation:
                liq_raw = market.liquidation.raw or {}
                result.update({
                    "liq_total_usd": liq_raw.get("total_usd"),
                    "liq_long_short_ratio": liq_raw.get("long_short_ratio"),
                })

            # ── 新闻情绪 ──
            if market.news:
                n_raw = market.news.raw or {}
                result.update({
                    "news_sentiment": n_raw.get("sentiment"),
                    "news_score": market.news.value,
                })

            # ── AI 综合研判 ──
            if market.ai_analysis:
                ai_raw = market.ai_analysis.raw or {}
                result.update({
                    "ai_bias": ai_raw.get("bias"),
                    "ai_confidence": ai_raw.get("confidence"),
                    "ai_summary": ai_raw.get("summary"),
                })

            return result
        except Exception as e:
            logger.error(f"捕获市场指标失败: {e}")
            return {}

    async def _execute_mode_change(self, signal, btc_price: float, klines: list) -> list:
        """模式切换时开新仓（仅在无持仓时调用）"""
        trades = []
        market_indicators = self._capture_market_indicators(signal)

        if signal.mode == TradingMode.LONG:
            open_trade = await self._open_long(btc_price, klines, market_indicators, signal)
            if open_trade:
                trades.append(open_trade)
        elif signal.mode == TradingMode.SHORT:
            open_trade = await self._open_short(btc_price, klines, market_indicators, signal)
            if open_trade:
                trades.append(open_trade)

        return trades

    async def _check_stop_loss_take_profit(self, btc_price: float) -> list:
        """检查止盈止损

        阶段1 (TP1前): 固定止损 + 等待TP1
        阶段2 (TP1后): 移动止盈 trailing_stop = highest - ATR × trailing_multiplier
        """
        trades = []
        if not self.position.is_active:
            return trades

        is_long = self.position.direction == "LONG"

        # ── 强平检查（优先于止损）──
        if self.position.liquidation_price > 0:
            hit_liq = (is_long and btc_price <= self.position.liquidation_price) or \
                      (not is_long and btc_price >= self.position.liquidation_price)
            if hit_liq:
                trade = await self._close_position(btc_price, reason="强平触发")
                if trade:
                    trades.append(trade)
                logger.warning(
                    f"⚠️ 强平触发: 价格=${btc_price:,.0f} 触及强平价=${self.position.liquidation_price:,.0f}"
                )
                return trades

        # ── 止损检查（所有阶段通用）──
        hit_sl = (is_long and btc_price <= self.position.stop_loss) or \
                 (not is_long and btc_price >= self.position.stop_loss)
        if hit_sl:
            reason = "移动止盈触发" if self.position.tp1_hit else "止损触发"
            trade = await self._close_position(btc_price, reason=reason)
            if trade:
                trades.append(trade)
            return trades

        # ── 阶段1: 等待 TP1 ──
        if not self.position.tp1_hit:
            tp1_hit = (is_long and btc_price >= self.position.tp1_price) or \
                      (not is_long and btc_price <= self.position.tp1_price)
            if tp1_hit:
                trade = await self._close_position(btc_price, reason="TP1 半仓止盈", close_ratio=0.5, is_tp=True)
                if trade:
                    trades.append(trade)
                self.position.tp1_hit = True
                self.position.highest_since_tp1 = btc_price
                trailing_dist = self.position.trailing_atr * self.config.long.trailing_atr_multiplier
                if is_long:
                    self.position.stop_loss = max(self.position.entry_price, btc_price - trailing_dist)
                else:
                    self.position.stop_loss = min(self.position.entry_price, btc_price + trailing_dist)
                logger.info(
                    f"📊 TP1 触发: 启动移动止盈, "
                    f"trailing={trailing_dist:,.0f}, "
                    f"当前止盈线=${self.position.stop_loss:,.0f}"
                )
                await self._on_tp1_transition()
            return trades

        # ── 阶段2: TP1 后，更新移动止盈 ──
        trailing_dist = self.position.trailing_atr * self.config.long.trailing_atr_multiplier
        if is_long:
            if btc_price > self.position.highest_since_tp1:
                self.position.highest_since_tp1 = btc_price
                new_stop = max(self.position.stop_loss, btc_price - trailing_dist)
                if new_stop > self.position.stop_loss:
                    self.position.stop_loss = new_stop
                    logger.debug(f"📈 移动止盈上移: ${new_stop:,.0f}")
                    await self._on_trailing_stop_moved(new_stop)
        else:
            if btc_price < self.position.highest_since_tp1:
                self.position.highest_since_tp1 = btc_price
                new_stop = min(self.position.stop_loss, btc_price + trailing_dist)
                if new_stop < self.position.stop_loss:
                    self.position.stop_loss = new_stop
                    logger.debug(f"📉 移动止盈下移: ${new_stop:,.0f}")
                    await self._on_trailing_stop_moved(new_stop)

        return trades

    # ── 交易所挂单 hooks（子类覆盖）──────────────────────────────

    async def _on_tp1_transition(self):
        """TP1 成交后进入移动止盈阶段。Live 子类覆盖以更新交易所挂单。"""

    async def _on_trailing_stop_moved(self, new_stop: float):
        """移动止盈线更新。Live 子类覆盖以替换交易所止损单。"""

    def _make_trade(self, mode: str, action: str, price: float, amount: float, pnl: float,
                    entry_price: float = None, market_indicators: dict = None,
                    trigger_reason: str = None, signal_confidence: float = None,
                    position_levels: dict = None) -> dict:
        trade = {
            "id": len(self.trades) + 1,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "action": action,
            "price": round(price, 2),
            "amount": round(amount, 6),
            "pnl": round(pnl, 2),
        }
        if entry_price is not None:
            trade["entry_price"] = round(entry_price, 2)
        if market_indicators is not None:
            trade["market_indicators"] = market_indicators
        if trigger_reason is not None:
            trade["trigger_reason"] = trigger_reason
        if signal_confidence is not None:
            trade["signal_confidence"] = round(signal_confidence, 2)
        if position_levels is not None:
            trade["levels"] = {
                "stop_loss": round(position_levels.get("stop_loss", 0), 2),
                "tp1_price": round(position_levels.get("tp1_price", 0), 2),
                "tp2_price": round(position_levels.get("tp2_price", 0), 2),
                "liquidation_price": round(position_levels.get("liquidation_price", 0), 2),
                "atr": round(position_levels.get("atr", 0), 2),
            }
        return trade

    # ── 主循环 ────────────────────────────────────────────────

    async def run(self):
        self.running = True
        mode_str = f"{self.mode_label}(Demo Trading)" if self.is_live else self.mode_label
        cap_str = f", 资金上限=${self.max_capital:,.0f}" if self.max_capital else ""
        logger.info(f"🚀 调度器启动 (间隔: {self.check_interval}秒, 模式: {mode_str}{cap_str})")

        while self.running:
            try:
                await self.check_and_execute()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器错误: {e}")
                await asyncio.sleep(60)

        logger.info("🛑 调度器停止")

    def stop(self):
        self.running = False
