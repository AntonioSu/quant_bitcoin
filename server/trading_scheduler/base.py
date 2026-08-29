"""交易调度器基础类和共享组件"""

import asyncio
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from core import (
    TradingConfig, ParameterSet, TradingMode,
    get_analysis_memory, get_strategy_summarizer,
)
from core.market_data import market
from indicators import LongLevel, ShortLevel
from multi_agent.trading_advisor import TradingAdvisor, TradingDecision
from binance_utils import fetch_klines, fetch_price
from server.state_store import StateStore
from utils import logger


class Position:
    """本地仓位状态（Sim/Live 共用）"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.direction = "NONE"      # LONG / SHORT / NONE
        self.entry_price = 0.0
        self.size_btc = 0.0          # BTC 仓位大小
        self.stop_loss = 0.0
        self.leverage = 1
        self.liquidation_price = 0.0 # 强平价格
        self.sl_order_id = None      # 交易所止损挂单 ID (Live)
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
            "leverage": self.leverage,
            "liquidation_price": self.liquidation_price,
            "sl_order_id": self.sl_order_id,
            "analysis_id": self.analysis_id,
        }


class BaseTradingScheduler(ABC):
    """交易调度器抽象基类

    只定义框架（信号评估、止盈止损检查、回调、主循环）和抽象接口。
    所有交易操作（开仓、平仓、同步）由子类实现。
    """
    
    DEFAULT_EQUITY = 1000.0 # 默认权益
    OPEN_NOTIONAL = 500.0 # 兜底名义本金（止损重算 / 资金检查）
    MIN_NOTIONAL = 50.0
    FUTURES_SYMBOL = "BTC/USDT:USDT"

    def __init__(
        self,
        config: Optional[TradingConfig] = None,
        check_interval: int = 300,
        state_file: Optional[str] = None,
    ):
        self.config = config or TradingConfig.get_preset(ParameterSet.STANDARD)
        self.check_interval = check_interval

        self.trading_advisor = TradingAdvisor()
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
            "liquidation_price": self.position.liquidation_price,
            "leverage": self.position.leverage,
            "sl_order_id": self.position.sl_order_id,
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
        self.position.liquidation_price = saved.get("liquidation_price", 0.0)
        self.position.leverage = saved.get("leverage", 1)
        self.position.sl_order_id = saved.get("sl_order_id")
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
                         market_indicators: dict = None,
                         decision: TradingDecision = None) -> Optional[dict]:
        """开多仓，返回交易记录"""

    @abstractmethod
    async def _open_short(self, btc_price: float, klines: list,
                          market_indicators: dict = None,
                          decision: TradingDecision = None) -> Optional[dict]:
        """开空仓，返回交易记录"""

    @abstractmethod
    async def _close_position(self, btc_price: float, reason: str = "",
                              close_ratio: float = 1.0, is_partial: bool = False) -> Optional[dict]:
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
            if action in ("CLOSE", "REDUCE"):
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
        """检查信号并执行（两层 AI 架构）

        1. 硬安全网: 止损 + 强平（每 tick，不依赖 AI）
        2. Trading AI: 根据 Signal AI 输出 + 仓位状态做交易决策
           （事件驱动：信号或仓位变化时才调 LLM，否则用缓存）
        """
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

            self._update_position_context(btc_price)

            # ── 1. 硬安全网（每 tick 检查，不等 AI）──
            just_closed = False
            if self.position.is_active:
                safety_trades = await self._check_safety_exits(btc_price)
                await self._record_trades(safety_trades)
                if not self.position.is_active:
                    self.current_mode = TradingMode.IDLE
                    self.save_position_state()
                    just_closed = bool(safety_trades)
                    self.trading_advisor.invalidate_cache()

            # ── 2. Trading AI 决策（信号/仓位变化时调 LLM）──
            signal_raw = market.ai_analysis.raw if market.ai_analysis and market.ai_analysis.raw else {}
            pos_ctx = market.position_context or {}

            decision = self.trading_advisor.decide(
                signal=signal_raw,
                position_direction=self.position.direction,
                position_entry=self.position.entry_price,
                position_size_btc=self.position.size_btc,
                position_leverage=self.position.leverage,
                position_stop_loss=self.position.stop_loss,
                position_liquidation=self.position.liquidation_price,
                btc_price=btc_price,
                equity=self.equity,
                holding_duration=pos_ctx.get("holding_duration", "未知"),
            )

            # ── 3. 执行交易决策 ──
            if not just_closed:
                trades = await self._execute_trading_decision(decision, btc_price, klines)
                await self._record_trades(trades)
                if trades:
                    self.save_position_state()
                    self.trading_advisor.invalidate_cache()

            # ── 4. 广播状态 ──
            await self._emit(self._on_update_callbacks, {
                "timestamp": datetime.now().isoformat(),
                "btc_price": btc_price,
                "mode": self.current_mode.value,
                "signal": {
                    "bias": signal_raw.get("bias", "NEUTRAL"),
                    "confidence": signal_raw.get("confidence", 0),
                    "summary": signal_raw.get("summary", ""),
                },
                "trading_decision": {
                    "action": decision.action,
                    "reason": decision.reason,
                    "from_cache": decision._from_cache,
                },
                "position": self.position.to_dict(),
                "equity": self.equity,
            })

            if self.position.is_active:
                self.save_position_state()

        except Exception as e:
            logger.error(f"检查执行错误: {e}")
            import traceback
            traceback.print_exc()

    def _capture_market_indicators(self) -> dict:
        """从全局 market 中提取完整的市场指标快照（开仓 / 平仓通用）"""
        try:
            fear_greed = market.fear_greed.value if market.fear_greed else 50
            funding_rate = market.funding_rate.value if market.funding_rate else 0.0
            top_trader_ratio = market.top_trader.value if market.top_trader else 1.0

            fg_raw = (market.fear_greed.raw or {}) if market.fear_greed else {}
            fr_raw = (market.funding_rate.raw or {}) if market.funding_rate else {}
            tt_raw = (market.top_trader.raw or {}) if market.top_trader else {}

            cvd = market.cvd
            cvd_change = cvd.cvd_change_pct if cvd else 0.0
            price_change = cvd.price_change_pct if cvd else 0.0
            div_type = "无"
            div_strength = 0.0
            if cvd and cvd.is_valid_signal:
                div_type = "底背离" if cvd.divergence.value == "bullish" else \
                           "顶背离" if cvd.divergence.value == "bearish" else "无"
                div_strength = cvd.strength

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
                "price_change_pct": round(price_change, 2),
                "cvd_change_pct": round(cvd_change, 2),
                "divergence_type": div_type,
                "divergence_strength": round(div_strength, 2),
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
                    "ai_confidence_level": ai_raw.get("confidence_level"),
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

    def _resolve_ai_sizing(self, decision: TradingDecision) -> tuple:
        """从 Trading AI 决策中解析仓位大小和杠杆。

        position_size_hint 是保证金占权益的比例，名义本金 = 保证金 × 杠杆。
        例如权益 $500、50%、5x → 保证金 $250，名义 $1250。

        Returns:
            (notional: float, leverage: int)
        """
        size_hint = decision.position_size_hint if decision else "50%"
        pct_map = {"0%": 0.0, "25%": 0.25, "50%": 0.50, "75%": 0.75, "100%": 1.0}
        size_pct = pct_map.get(size_hint, 0.50)

        leverage = decision.leverage_hint if decision else 5
        try:
            leverage = max(1, min(20, int(leverage)))
        except (TypeError, ValueError):
            leverage = 5

        usable = max(float(self.equity or 0), 0.0)
        margin = min(usable * size_pct, usable)
        notional = margin * leverage
        if size_pct > 0:
            notional = max(self.MIN_NOTIONAL, notional)

        logger.info(
            f"📐 Trading AI 仓位: size_hint={size_hint} → "
            f"保证金=${margin:,.0f}, 名义=${notional:,.0f}, leverage={leverage}x"
        )
        return notional, leverage

    def _get_signal_metadata(self) -> dict:
        """从当前 Signal AI 输出中获取 confidence 和 analysis_id"""
        if market.ai_analysis and market.ai_analysis.raw:
            raw = market.ai_analysis.raw
            return {
                "confidence": raw.get("confidence", 0),
                "analysis_id": raw.get("_memory_id"),
            }
        return {"confidence": 0, "analysis_id": None}

    async def _check_safety_exits(self, btc_price: float) -> list:
        """硬安全网: 强平 + 止损（每 tick 检查，不依赖 AI）"""
        trades = []
        if not self.position.is_active:
            return trades

        is_long = self.position.direction == "LONG"

        if self.position.liquidation_price > 0:
            hit_liq = (is_long and btc_price <= self.position.liquidation_price) or \
                      (not is_long and btc_price >= self.position.liquidation_price)
            if hit_liq:
                liq_price = self.position.liquidation_price
                trade = await self._close_position(btc_price, reason="强平触发")
                if trade:
                    trades.append(trade)
                logger.warning(
                    f"⚠️ 强平触发: 价格=${btc_price:,.0f} 触及强平价=${liq_price:,.0f}"
                )
                return trades

        if self.position.stop_loss > 0:
            hit_sl = (is_long and btc_price <= self.position.stop_loss) or \
                     (not is_long and btc_price >= self.position.stop_loss)
            if hit_sl:
                sl_price = self.position.stop_loss
                trade = await self._close_position(btc_price, reason="止损触发")
                if trade:
                    trades.append(trade)
                logger.warning(
                    f"🛑 止损触发: 价格=${btc_price:,.0f} 触及止损价=${sl_price:,.0f}"
                )
                return trades

        return trades

    async def _execute_trading_decision(
        self, decision: TradingDecision, btc_price: float, klines: list,
    ) -> list:
        """执行 Trading AI 的决策"""
        trades = []

        if decision.is_open and not self.position.is_active:
            market_indicators = self._capture_market_indicators()
            if decision.action == "开多":
                trade = await self._open_long(btc_price, klines, market_indicators, decision)
                if trade:
                    trades.append(trade)
                    await self._on_position_opened()
                    self.current_mode = TradingMode.LONG
            elif decision.action == "开空":
                trade = await self._open_short(btc_price, klines, market_indicators, decision)
                if trade:
                    trades.append(trade)
                    await self._on_position_opened()
                    self.current_mode = TradingMode.SHORT

        elif decision.is_close and self.position.is_active:
            is_partial = decision.action == "减仓"
            trade = await self._close_position(
                btc_price,
                reason=decision.reason,
                close_ratio=decision.close_ratio,
                is_partial=is_partial,
            )
            if trade:
                trades.append(trade)
            logger.info(
                f"🤖 Trading AI {decision.action}: "
                f"(比例={decision.close_ratio:.0%}) {decision.reason}"
            )
            if is_partial and self.position.is_active:
                await self._on_position_reduced()
            elif not self.position.is_active:
                self.current_mode = TradingMode.IDLE

        return trades

    # ── 仓位生命周期 hooks（子类覆盖）────────────────────────────

    async def _on_position_opened(self):
        """开仓后的钩子（Live 可挂交易所止损单）"""

    async def _on_position_reduced(self):
        """AI 减仓后的钩子（Live 可更新交易所止损单）"""

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
