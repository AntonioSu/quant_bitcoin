"""交易调度器基础类和共享组件"""

import asyncio
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from core import (
    TradingConfig, ParameterSet, TradingMode,
    SignalAggregator,
    get_analysis_memory, get_strategy_summarizer,
)
from core.market_data import market
from indicators import LongLevel, ShortLevel
from binance_utils import fetch_klines, fetch_price
from server.state_store import StateStore
from utils import logger


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
        self.analysis_id = None      # 开仓时对应的 AI 研判记录 ID

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
            "analysis_id": self.analysis_id,
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
        from indicators import ATRCalculator
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
            "analysis_id": self.position.analysis_id,
        }
    
    def restore_position_state(self) -> bool:
        """从文件恢复仓位状态，返回是否成功恢复"""
        if not self._state_store:
            return False
        
        saved = self._state_store.load()
        if not saved:
            return False
        
        self._apply_position_state(saved)
        
        if self.position.is_active:
            logger.info(
                f"📂 恢复仓位状态: {self.position.direction} @ ${self.position.entry_price:,.0f}, "
                f"杠杆={self.position.leverage}x, 强平=${self.position.liquidation_price:,.0f}"
            )
            market.position_context = {
                "is_active": True,
                "direction": self.position.direction,
                "entry_price": self.position.entry_price,
                "current_price": self.position.entry_price,
                "size_btc": self.position.size_btc,
                "leverage": self.position.leverage,
                "liquidation_price": self.position.liquidation_price,
                "holding_duration": "重启恢复",
            }
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
        self.position.analysis_id = saved.get("analysis_id")
        
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

            # 优先使用开仓时绑定的研判 ID，避免平仓附近的新研判污染复盘。
            record_id = trade.get("analysis_id") or self.position.analysis_id
            if not record_id:
                record_id = memory.get_latest_analysis_id()
                logger.warning("📝 平仓记录缺少 analysis_id，回退关联最近研判")

            if record_id:
                memory.attach_trade_result(record_id, trade)
                logger.info(f"📝 交易结果已关联到研判 {record_id}")

                # 异步触发复盘（不阻塞交易主循环）
                asyncio.ensure_future(self._async_reflect(record_id))
        except Exception as e:
            logger.warning(f"📝 关联交易记忆失败: {e}")

    async def _async_reflect(self, record_id: str):
        """异步复盘，不影响交易主流程"""
        try:
            from multi_agent.reflector import Reflector
            memory = get_analysis_memory()
            reflector = Reflector(memory=memory)
            await asyncio.to_thread(reflector.reflect_on_trade, record_id)

            # 检查是否需要更新策略备忘录（每 5 笔有复盘的交易触发一次）
            all_reflections = memory.get_all_reflections(since_days=30)
            if len(all_reflections) >= 3 and len(all_reflections) % 5 == 0:
                summarizer = get_strategy_summarizer()
                if summarizer:
                    from core.performance import PerformanceTracker
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

            # 更新全局持仓上下文（供 AI 分析时使用）
            self._update_position_context(btc_price)

            # AI 驱动平仓检查: 若本周期发生了平仓，标记 just_closed 阻止同周期再开仓
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
                        f"持仓中，等待 AI 研判平仓"
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
                    "news_reasoning": n_raw.get("reasoning"),
                    "news_key_signals": n_raw.get("key_signals", []),
                    "news_bullish_factors": n_raw.get("bullish_factors", []),
                    "news_bearish_factors": n_raw.get("bearish_factors", []),
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

    def _update_position_context(self, btc_price: float):
        """更新全局持仓上下文，供 AI 分析时使用"""
        if self.position.is_active:
            open_time = None
            if self.position.analysis_id:
                try:
                    memory = get_analysis_memory()
                    if memory:
                        record = memory.get_record(self.position.analysis_id)
                        if record and record.get("timestamp"):
                            open_time = record["timestamp"]
                except Exception:
                    pass

            duration = "未知"
            if open_time:
                try:
                    from datetime import datetime as _dt
                    if isinstance(open_time, str):
                        open_dt = _dt.fromisoformat(open_time)
                    else:
                        open_dt = open_time
                    delta = datetime.now() - open_dt
                    hours = delta.total_seconds() / 3600
                    if hours < 1:
                        duration = f"{int(delta.total_seconds() / 60)}分钟"
                    elif hours < 24:
                        duration = f"{hours:.1f}小时"
                    else:
                        duration = f"{hours / 24:.1f}天"
                except Exception:
                    pass

            market.position_context = {
                "is_active": True,
                "direction": self.position.direction,
                "entry_price": self.position.entry_price,
                "current_price": btc_price,
                "size_btc": self.position.size_btc,
                "leverage": self.position.leverage,
                "liquidation_price": self.position.liquidation_price,
                "holding_duration": duration,
            }
        else:
            market.position_context = {"is_active": False}

    def _resolve_ai_sizing(self, signal) -> tuple:
        """从 AI signal 中解析仓位大小和杠杆

        Returns:
            (notional: float, leverage: int)
        """
        values = signal.values if signal else {}

        size_hint = str(values.get("position_size_hint") or "50%")
        pct_map = {"25%": 0.25, "50%": 0.50, "75%": 0.75, "100%": 1.0}
        size_pct = pct_map.get(size_hint, 0.50)
        notional = self.equity * size_pct
        notional = max(50.0, min(notional, self.equity))

        leverage_hint = values.get("leverage_hint")
        if leverage_hint is not None:
            try:
                leverage = max(1, min(20, int(leverage_hint)))
            except (TypeError, ValueError):
                leverage = 5
        else:
            leverage = 5

        logger.info(
            f"📐 AI 仓位: size_hint={size_hint} → ${notional:,.0f}, "
            f"leverage_hint={leverage_hint} → {leverage}x"
        )
        return notional, leverage

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
        """检查平仓条件: 强平安全网 + AI 驱动平仓

        平仓决策完全由 AI 研判驱动（通过 action=离场/减仓），
        仅保留强平价检查作为最终安全网。
        """
        trades = []
        if not self.position.is_active:
            return trades

        is_long = self.position.direction == "LONG"

        # ── 强平检查（最终安全网，始终生效）──
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

        # ── AI 驱动平仓 ──
        exit_signal = self.signal_aggregator.evaluate_exit(self.position.direction)
        if exit_signal.should_exit:
            is_partial = exit_signal.close_ratio < 1.0
            trade = await self._close_position(
                btc_price,
                reason=exit_signal.reason,
                close_ratio=exit_signal.close_ratio,
                is_tp=is_partial,
            )
            if trade:
                trades.append(trade)
            logger.info(
                f"🤖 AI 平仓: {exit_signal.ai_action} "
                f"(比例={exit_signal.close_ratio:.0%}, 置信度={exit_signal.ai_confidence}%)"
            )

        return trades

    # ── 交易所挂单 hooks（子类覆盖）──────────────────────────────

    async def _on_tp1_transition(self):
        """TP1 成交后进入移动止盈阶段。Live 子类覆盖以更新交易所挂单。"""

    async def _on_trailing_stop_moved(self, new_stop: float):
        """移动止盈线更新。Live 子类覆盖以替换交易所止损单。"""

    @staticmethod
    def _analysis_id_from_signal(signal) -> Optional[str]:
        return (signal.values or {}).get("analysis_id") if signal else None

    def _make_trade(self, mode: str, action: str, price: float, amount: float, pnl: float,
                    entry_price: float = None, market_indicators: dict = None,
                    trigger_reason: str = None, signal_confidence: float = None,
                    position_levels: dict = None,
                    analysis_id: Optional[str] = None,
                    notional: float = None, leverage: int = None) -> dict:
        trade = {
            "id": len(self.trades) + 1,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "action": action,
            "price": round(price, 2),
            "amount": round(amount, 6),
            "pnl": round(pnl, 2),
        }
        if notional is not None:
            trade["notional"] = round(notional, 2)
        if leverage is not None:
            trade["leverage"] = leverage
        if entry_price is not None:
            trade["entry_price"] = round(entry_price, 2)
        if market_indicators is not None:
            trade["market_indicators"] = market_indicators
        if trigger_reason is not None:
            trade["trigger_reason"] = trigger_reason
        if signal_confidence is not None:
            trade["signal_confidence"] = round(signal_confidence, 2)
        if analysis_id is not None:
            trade["analysis_id"] = analysis_id
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
