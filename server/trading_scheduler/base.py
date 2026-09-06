"""交易调度器基础类和共享组件"""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from core import (
    TradingConfig, ParameterSet, TradingMode,
    get_analysis_memory, get_strategy_summarizer,
)
from core.market_data import market
from indicators import PositionLevel
from multi_agent.trading_advisor import TradingAdvisor, TradingDecision
from binance_utils import fetch_klines, fetch_price
from server.state_store import StateStore
from utils import logger

# 调度器主循环节拍（秒）。工厂函数与类默认值共用此常量，避免两处漂移。
DEFAULT_CHECK_INTERVAL = 60

# 日志中标识持仓方向：长矛做多 / 神盾做空
DIRECTION_ICON = {"LONG": "🗡️", "SHORT": "🛡️"}


def _humanize_duration_since(start) -> str:
    """把开仓时间转成 "35分钟" / "4.2小时" / "3.1天"，供 AI 提示词使用。"""
    if not start:
        return "未知"
    try:
        start_dt = datetime.fromisoformat(start) if isinstance(start, str) else start
        seconds = (datetime.now() - start_dt).total_seconds()
    except (TypeError, ValueError) as e:
        logger.debug(f"持仓时长解析失败 ({start!r}): {e}")
        return "未知"

    hours = seconds / 3600
    if hours < 1:
        return f"{int(seconds / 60)}分钟"
    if hours < 24:
        return f"{hours:.1f}小时"
    return f"{hours / 24:.1f}天"


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
        self.initial_stop = 0.0      # 开仓时的原始止损，用于计算 1R
        self.mfe_price = 0.0         # 开仓以来最有利价格 (LONG 最高 / SHORT 最低)
        self.stop_stage = "INIT"     # INIT → BREAKEVEN → TRAILING

    @property
    def is_active(self) -> bool:
        return self.direction != "NONE" and self.size_btc > 0

    @property
    def risk_unit(self) -> float:
        """1R = 入场价到初始止损的距离"""
        if self.entry_price <= 0 or self.initial_stop <= 0:
            return 0.0
        return abs(self.entry_price - self.initial_stop)

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
            "initial_stop": self.initial_stop,
            "mfe_price": self.mfe_price,
            "stop_stage": self.stop_stage,
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
        check_interval: int = DEFAULT_CHECK_INTERVAL,
        state_file: Optional[str] = None,
    ):
        self.config = config or TradingConfig.get_preset(ParameterSet.STANDARD)
        self.check_interval = check_interval

        self.trading_advisor = TradingAdvisor()
        from indicators import ATRCalculator
        self._atr_calc_fallback = ATRCalculator(period=14, timeframe="4h")
        self.long_level = PositionLevel(self._atr_calc_fallback, is_long=True)
        self.short_level = PositionLevel(self._atr_calc_fallback, is_long=False)

        self.running = False
        self.current_mode = TradingMode.IDLE
        self.last_check_time: Optional[datetime] = None
        self.trades = []
        self.total_pnl = 0.0

        self.position = Position()
        self.equity = self.DEFAULT_EQUITY
        self._last_klines = []

        self._on_update_callbacks = []
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
    def initial_equity(self) -> float:
        """本轮交易开始前的权益，供绩效模块作为权益曲线起点。

        实盘权益同步自交易所，因此不能假定它等于 DEFAULT_EQUITY。
        """
        return self.equity - self.total_pnl

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
        state = {
            "current_mode": self.current_mode.value,
            "position_direction": self.position.direction,
            "position_size": self.position.size_btc,
            "entry_price": self.position.entry_price,
            "stop_loss": self.position.stop_loss,
            "liquidation_price": self.position.liquidation_price,
            "leverage": self.position.leverage,
            "sl_order_id": self.position.sl_order_id,
            "analysis_id": self.position.analysis_id,
            "initial_stop": self.position.initial_stop,
            "mfe_price": self.position.mfe_price,
            "stop_stage": self.position.stop_stage,
        }
        state.update(self.trading_advisor.get_reduce_state())
        return state
    
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
        # 旧状态文件没有这三个字段，回退到"刚开仓"的等价值
        self.position.initial_stop = saved.get("initial_stop") or self.position.stop_loss
        self.position.mfe_price = saved.get("mfe_price") or self.position.entry_price
        self.position.stop_stage = saved.get("stop_stage") or "INIT"
        self.trading_advisor.restore_reduce_state(saved)
        
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
    async def _execute_open(self, direction: str, notional: float,
                            btc_price: float) -> Optional[tuple]:
        """真正建仓（Sim 记账 / Live 下单）

        Returns:
            (成交价, 成交 BTC 数量)，失败返回 None
        """

    @abstractmethod
    async def _execute_close(self, is_long: bool, close_ratio: float,
                             btc_price: float) -> Optional[float]:
        """真正平仓（Sim 记账 / Live 下单）

        Returns:
            成交价，失败返回 None
        """

    def _reject_open(self, direction: str, notional: float, leverage: int) -> Optional[str]:
        """开仓前置检查，返回拒绝原因；None 表示放行。子类可加更多护栏。"""
        if self.position.is_active:
            return f"已有 {self.position.direction} 仓位"
        return None

    # ── 开平仓模板（Sim / Live 共用，差异只在上面的钩子）──────────

    async def _open_position(self, direction: str, btc_price: float, klines: list,
                             market_indicators: dict = None,
                             decision: TradingDecision = None) -> Optional[dict]:
        """开仓统一流程：AI 仓位 → 护栏 → 止损价位 → 建仓 → 落账"""
        is_long = direction == "LONG"
        notional, leverage = self._resolve_ai_sizing(decision)

        reject = self._reject_open(direction, notional, leverage)
        if reject:
            logger.warning(f"⚠️ 拒绝开{'多' if is_long else '空'}: {reject}")
            return None

        cfg = self.config.long if is_long else self.config.short
        level = self.long_level if is_long else self.short_level
        sig_meta = self._get_signal_metadata()

        try:
            levels = level.calculate(
                entry_price=btc_price,
                klines=klines,
                atr_multiplier=cfg.atr_multiplier,
                leverage=leverage,
                notional_value=notional,
            )
        except Exception as e:
            logger.error(f"ATR 计算失败: {e}, 使用兜底价位")
            levels = level.fallback(btc_price, leverage=leverage, notional_value=notional)

        fill = await self._execute_open(direction, notional, btc_price)
        if not fill:
            return None
        fill_price, fill_amount = fill

        self.position.direction = direction
        self.position.entry_price = fill_price
        self.position.size_btc = fill_amount
        self.position.stop_loss = levels["stop_loss"]
        self.position.leverage = leverage
        self.position.liquidation_price = levels["liquidation_price"]
        self.position.analysis_id = sig_meta["analysis_id"]

        logger.info(
            f"{DIRECTION_ICON[direction]} {self.mode_label}开{'多' if is_long else '空'}: "
            f"{fill_amount:.4f} BTC @ ${fill_price:,.0f} "
            f"(${notional:,.0f}, {leverage}x), "
            f"止损=${levels['stop_loss']:,.0f}, 强平=${levels['liquidation_price']:,.0f}"
        )

        return self._make_trade(
            direction, direction, fill_price, fill_amount, 0,
            market_indicators=market_indicators,
            trigger_reason=decision.reason if decision else None,
            signal_confidence=sig_meta["confidence"],
            position_levels=levels,
            analysis_id=self.position.analysis_id,
            notional=notional, leverage=leverage,
        )

    async def _open_long(self, btc_price: float, klines: list,
                         market_indicators: dict = None,
                         decision: TradingDecision = None) -> Optional[dict]:
        return await self._open_position("LONG", btc_price, klines, market_indicators, decision)

    async def _open_short(self, btc_price: float, klines: list,
                          market_indicators: dict = None,
                          decision: TradingDecision = None) -> Optional[dict]:
        return await self._open_position("SHORT", btc_price, klines, market_indicators, decision)

    async def _close_position(self, btc_price: float, reason: str = "",
                              close_ratio: float = 1.0, is_partial: bool = False) -> Optional[dict]:
        """平仓统一流程（全平或减仓），返回交易记录"""
        if not self.position.is_active:
            return None

        is_long = self.position.direction == "LONG"
        fill_price = await self._execute_close(is_long, close_ratio, btc_price)
        if fill_price is None:
            return None

        close_btc = self.position.size_btc * close_ratio
        sign = 1 if is_long else -1
        pnl = sign * (fill_price - self.position.entry_price) * close_btc

        mode_str = "LONG" if is_long else "SHORT"
        action = "REDUCE" if (is_partial and close_ratio < 1.0) else "CLOSE"

        logger.info(
            f"{DIRECTION_ICON[mode_str]} {self.mode_label}平仓: "
            f"{close_btc:.4f} BTC @ ${fill_price:,.0f}, "
            f"入场=${self.position.entry_price:,.0f}, "
            f"PnL=${pnl:+,.2f} ({reason})"
        )

        trade = self._make_trade(mode_str, action, fill_price, close_btc, pnl,
                                 entry_price=self.position.entry_price,
                                 market_indicators=self._capture_market_indicators(),
                                 trigger_reason=reason or None,
                                 analysis_id=self.position.analysis_id,
                                 notional=close_btc * fill_price,
                                 leverage=self.position.leverage)

        if close_ratio >= 1.0:
            self.position.reset()
        else:
            self.position.size_btc -= close_btc
            if self.position.size_btc < 0.0001:
                logger.info("📌 剩余仓位过小，视为全平")
                self.position.reset()

        return trade

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

        except Exception:
            logger.exception("检查执行错误")

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

    def _position_open_time(self):
        """从 AI 研判记忆里取当前仓位的开仓时间，取不到返回 None。"""
        if not self.position.analysis_id:
            return None
        try:
            memory = get_analysis_memory()
            record = memory.get_record(self.position.analysis_id) if memory else None
            return (record or {}).get("timestamp")
        except Exception as e:
            logger.debug(f"读取开仓时间失败 (analysis_id={self.position.analysis_id}): {e}")
            return None

    def _update_position_context(self, btc_price: float):
        """更新全局持仓上下文，供 AI 分析时使用"""
        if self.position.is_active:
            duration = _humanize_duration_since(self._position_open_time())

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

    def _arm_protective_stop(self):
        """开仓后记录风险基准，供保本 / 移动止损使用"""
        self.position.initial_stop = self.position.stop_loss
        self.position.mfe_price = self.position.entry_price
        self.position.stop_stage = "INIT"

    async def _update_protective_stop(self, btc_price: float):
        """保本 + 移动止损（棘轮：止损只朝有利方向移动）

        R = 开仓时的止损距离。
          浮盈 >= breakeven_trigger_r × R → 止损上移到成本价
          峰值浮盈 >= trailing_trigger_r × R → 止损跟在峰值回撤 trailing_distance_r × R 处
        """
        pos = self.position
        risk = self.config.risk
        R = pos.risk_unit
        if R <= 0 or btc_price <= 0:
            return

        is_long = pos.direction == "LONG"

        if pos.mfe_price <= 0:
            pos.mfe_price = pos.entry_price
        pos.mfe_price = max(pos.mfe_price, btc_price) if is_long else min(pos.mfe_price, btc_price)

        peak_profit = (pos.mfe_price - pos.entry_price) if is_long else (pos.entry_price - pos.mfe_price)

        candidate = None
        stage = pos.stop_stage
        if peak_profit >= risk.trailing_trigger_r * R:
            offset = risk.trailing_distance_r * R
            candidate = (pos.mfe_price - offset) if is_long else (pos.mfe_price + offset)
            stage = "TRAILING"
        elif peak_profit >= risk.breakeven_trigger_r * R:
            candidate = pos.entry_price
            stage = "BREAKEVEN"

        if candidate is None:
            return

        improved = (candidate > pos.stop_loss) if is_long else (candidate < pos.stop_loss)
        if not improved:
            return

        old_stop = pos.stop_loss
        pos.stop_loss = candidate
        pos.stop_stage = stage
        logger.info(
            f"🔒 {'保本' if stage == 'BREAKEVEN' else '移动'}止损: "
            f"${old_stop:,.0f} → ${candidate:,.0f} "
            f"(峰值=${pos.mfe_price:,.0f}, 浮盈={peak_profit / R:.2f}R)"
        )
        await self._on_stop_loss_moved()
        self.save_position_state()

    async def _on_stop_loss_moved(self):
        """止损价被抬高后的钩子（Live 覆盖以替换交易所止损单）"""

    async def _check_safety_exits(self, btc_price: float) -> list:
        """硬安全网: 强平 + 保本/移动止损 + 止损（每 tick 检查，不依赖 AI）"""
        trades = []
        if not self.position.is_active:
            return trades

        await self._update_protective_stop(btc_price)

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
                reason = {
                    "BREAKEVEN": "保本止损触发",
                    "TRAILING": "移动止盈触发",
                }.get(self.position.stop_stage, "止损触发")
                trade = await self._close_position(btc_price, reason=reason)
                if trade:
                    trades.append(trade)
                logger.warning(
                    f"🛑 {reason}: 价格=${btc_price:,.0f} 触及止损价=${sl_price:,.0f}"
                )
                return trades

        return trades

    def _entry_range_position(
        self, direction: str, btc_price: float, klines: list,
    ) -> Optional[float]:
        """开仓价在最近 range_lookback_hours 区间中的"顺方向位置"(%)

        以已收盘 K 线构造区间（排除进行中的当前根），因此突破时可以 >100%。
          LONG  → 0% 贴区间底部（支撑位进场），100% 贴区间顶部（追高）
          SHORT → 数值镜像，0% 贴区间顶部（高位做空），100% 贴区间底部（杀跌）
        K 线不足时返回 None，表示无法判断、不拦截。
        """
        bars = max(2, int(self.config.risk.range_lookback_hours / 4))
        window = klines[-(bars + 1):-1] if len(klines) >= 3 else []
        if len(window) < 2:
            return None

        high = max(k[2] for k in window)
        low = min(k[3] for k in window)
        if high <= low:
            return None

        pct = (btc_price - low) / (high - low) * 100
        return pct if direction == "LONG" else 100.0 - pct

    def _check_range_guard(
        self, direction: str, btc_price: float, klines: list,
    ) -> Optional[str]:
        """追高护栏：拒绝在区间顺方向另一端开仓，返回拒绝原因；None 表示放行。

        历史数据显示这一档（顺方向 60~100%）胜率仅 19%，是主要亏损来源。
        """
        risk = self.config.risk
        pos_pct = self._entry_range_position(direction, btc_price, klines)
        if pos_pct is None:
            return None

        if pos_pct >= risk.breakout_range_pct:
            logger.info(
                f"📈 突破放行: {direction} 开仓价已突破近 "
                f"{risk.range_lookback_hours}h 区间 ({pos_pct:.0f}%)"
            )
            return None

        if pos_pct >= risk.max_entry_range_pct:
            return (
                f"追高护栏: 开仓价处于近{risk.range_lookback_hours}h区间 "
                f"{pos_pct:.0f}% (阈值{risk.max_entry_range_pct:.0f}%)"
            )
        return None

    async def _execute_trading_decision(
        self, decision: TradingDecision, btc_price: float, klines: list,
    ) -> list:
        """执行 Trading AI 的决策"""
        trades = []

        if decision.is_open and not self.position.is_active:
            reject = self._check_range_guard(decision.direction, btc_price, klines)
            if reject:
                logger.warning(f"⚠️ 拒绝{decision.action}: {reject}")
                return trades

            market_indicators = self._capture_market_indicators()
            if decision.action == "开多":
                trade = await self._open_long(btc_price, klines, market_indicators, decision)
                if trade:
                    trades.append(trade)
                    self._arm_protective_stop()
                    await self._on_position_opened()
                    self.current_mode = TradingMode.LONG
            elif decision.action == "开空":
                trade = await self._open_short(btc_price, klines, market_indicators, decision)
                if trade:
                    trades.append(trade)
                    self._arm_protective_stop()
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
