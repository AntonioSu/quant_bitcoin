"""FastAPI 后端服务 - REST API 和 WebSocket 接口

路由:
- /api/indicators - 获取所有指标
- /api/portfolio - 获取资产组合 (?preset=)
- /api/klines - 获取K线数据
- /api/trades - 获取交易记录 (?preset=)
- /api/status - 获取系统状态 (?preset=)
- /api/performance - 获取绩效指标 (?preset=)
- /api/config - 获取所有预设配置
- /api/config/{preset} - 切换当前预设
- /api/account - 获取账户余额
- /ws - WebSocket 实时推送
"""

import asyncio
import time
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import os

from stock_btc.core import market, refresh_market_data_async, refresh_ai_analysis_async
from stock_btc.binance_utils import fetch_price_sync, fetch_klines_sync
from stock_btc.core import TradingMode
from stock_btc.utils import logger
from stock_btc.server.history_store import history_store, HistoryStore
from stock_btc.server.scheduler import app_state, DEFAULT_INITIAL_USDT


AI_MANUAL_COOLDOWN_SEC = 30


# ══════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════

class IndicatorData(BaseModel):
    fear_greed: int
    fear_greed_class: str
    funding_rate: float
    funding_rate_annual: float
    top_trader_ratio: float
    top_trader_sentiment: str
    price_change_pct: float
    cvd_change_pct: float
    has_bullish_divergence: bool
    has_bearish_divergence: bool
    atr_value: float
    taker_buy_btc: float
    taker_sell_btc: float
    taker_total_btc: float
    taker_buy_ratio: float
    macd_signal_type: str
    macd_above_zero: bool
    macd_histogram_rising: bool
    macd_strength: float
    rsi_signal_type: str
    rsi_value: float
    rsi_above_center: bool
    rsi_trend_strength: str
    rsi_strength: float
    bollinger_signal_type: str
    bollinger_percent_b: float
    bollinger_bandwidth: float
    bollinger_is_squeeze: bool
    bollinger_strength: float
    ma_signal_type: str
    ma_trend: str
    ma_fast: float
    ma_slow: float
    ma_price_deviation: float
    ma_strength: float
    volume_signal_type: str
    volume_ratio: float
    volume_obv_trend: str
    volume_price_change: float
    volume_strength: float
    news_score: Optional[float] = None
    news_sentiment: Optional[str] = None
    news_reasoning: Optional[str] = None
    news_bullish_factors: Optional[list] = None
    news_bearish_factors: Optional[list] = None
    news_updated_at: Optional[str] = None
    etf_daily_flow: Optional[float] = None
    etf_flow_3d: Optional[float] = None
    etf_flow_7d: Optional[float] = None
    etf_cum_flow: Optional[float] = None
    etf_total_assets: Optional[float] = None
    etf_streak_days: Optional[int] = None
    etf_date: Optional[str] = None
    oi_value_usd: Optional[float] = None
    oi_contracts: Optional[float] = None
    oi_change_1h: Optional[float] = None
    oi_change_4h: Optional[float] = None
    oi_change_24h: Optional[float] = None
    ai_bias: Optional[str] = None
    ai_confidence: Optional[int] = None
    ai_summary: Optional[str] = None
    ai_action: Optional[str] = None
    ai_key_drivers: Optional[list] = None
    ai_risks: Optional[list] = None
    ai_horizon: Optional[str] = None
    ai_updated_at: Optional[str] = None
    timestamp: str


class PortfolioData(BaseModel):
    spot_btc: float
    futures_btc: float
    total_btc: float
    total_usd: float
    btc_price: float
    position_direction: str
    position_size: float
    unrealized_pnl: float


class SystemStatus(BaseModel):
    mode: str
    short_state: str
    long_state: str
    uptime_hours: float
    last_trade_time: Optional[str]
    total_trades: int
    total_pnl: float


async def _get_btc_price() -> float:
    """获取 BTC 价格 (带缓存 + 异步，不阻塞事件循环)"""
    cached = app_state.cache_get("btc_price")
    if cached is not None:
        return cached
    try:
        # 使用 binance_utils 封装的同步方法
        price = await asyncio.to_thread(fetch_price_sync, "BTCUSDT")
        app_state.cache_set("btc_price", price, ttl=15)
        history_store.add(HistoryStore.BTC_PRICE, price)
        return price
    except Exception as e:
        logger.error(f"获取 BTC 价格失败: {e}")
        stale = app_state._cache.get("btc_price")
        return stale if stale else 65000.0


def _get_indicators_from_market() -> IndicatorData:
    """从全局 market 获取指标数据"""
    from stock_btc.indicators.cvd_divergence import DivergenceType
    
    # 恐惧贪婪指数
    fg_value = int(market.fear_greed.value) if market.fear_greed else 50
    fg_class = market.fear_greed.raw.get("classification", "Unknown") if market.fear_greed and market.fear_greed.raw else "Unknown"
    
    # 资金费率
    fr_value = market.funding_rate.value if market.funding_rate else 0
    fr_annual = (market.funding_rate.raw.get("annual_yield", 0) * 100 
                 if market.funding_rate and market.funding_rate.raw else 0)
    
    # 聪明钱多空比
    tt_value = market.top_trader.value if market.top_trader else 1.0
    if market.top_trader and market.top_trader.raw:
        tt_sentiment = f"{market.top_trader.raw['long_account']:.1%}多/{market.top_trader.raw['short_account']:.1%}空"
    else:
        tt_sentiment = "Unknown"
    
    # CVD 数据
    price_change = market.cvd.price_change_pct if market.cvd else 0.0
    cvd_change = market.cvd.cvd_change_pct if market.cvd else 0.0
    has_bullish = (market.cvd.divergence == DivergenceType.BULLISH 
                   if market.cvd else False)
    has_bearish = (market.cvd.divergence == DivergenceType.BEARISH 
                   if market.cvd else False)
    
    # ATR 数据
    atr_value = market.atr.value if market.atr else 0.0
    
    # Taker 买卖数据
    taker_buy = market.taker.taker_buy_btc if market.taker else 0.0
    taker_sell = market.taker.taker_sell_btc if market.taker else 0.0
    taker_total = market.taker.total_volume_btc if market.taker else 0.0
    taker_ratio = market.taker.buy_ratio_pct if market.taker else 50.0

    # MACD 数据
    macd_signal_type = market.macd.signal_type.value if market.macd else "none"
    macd_above_zero = market.macd.above_zero if market.macd else False
    macd_histogram_rising = market.macd.histogram_rising if market.macd else False
    macd_strength = market.macd.strength if market.macd else 0.0

    # RSI 数据
    rsi_signal_type = market.rsi.signal_type.value if market.rsi else "none"
    rsi_value = market.rsi.rsi_value if market.rsi else 50.0
    rsi_above_center = market.rsi.above_center if market.rsi else False
    rsi_trend_strength = market.rsi.trend_strength if market.rsi else "neutral"
    rsi_strength = market.rsi.strength if market.rsi else 0.0

    # Bollinger 数据
    bollinger_signal_type = market.bollinger.signal_type.value if market.bollinger else "none"
    bollinger_percent_b = market.bollinger.percent_b if market.bollinger else 0.5
    bollinger_bandwidth = market.bollinger.bandwidth if market.bollinger else 0.0
    bollinger_is_squeeze = market.bollinger.is_squeeze if market.bollinger else False
    bollinger_strength = market.bollinger.strength if market.bollinger else 0.0

    # MA 均线数据
    ma_signal_type = market.ma.signal_type.value if market.ma else "none"
    ma_trend = market.ma.trend if market.ma else "neutral"
    ma_fast = market.ma.fast_ma if market.ma else 0.0
    ma_slow = market.ma.slow_ma if market.ma else 0.0
    ma_price_deviation = market.ma.price_deviation if market.ma else 0.0
    ma_strength = market.ma.strength if market.ma else 0.0

    # 成交量数据
    volume_signal_type = market.volume.signal_type.value if market.volume else "none"
    volume_ratio = market.volume.vol_ratio if market.volume else 1.0
    volume_obv_trend = market.volume.obv_trend if market.volume else "flat"
    volume_price_change = market.volume.price_change_pct if market.volume else 0.0
    volume_strength = market.volume.strength if market.volume else 0.0
    
    # 记录历史
    history_store.add(HistoryStore.FEAR_GREED, fg_value, extra={"class": fg_class})
    history_store.add(HistoryStore.FUNDING_RATE, fr_value, extra={"annual": fr_annual})
    history_store.add(HistoryStore.TOP_TRADER_RATIO, tt_value, extra={"sentiment": tt_sentiment})

    # 新闻分析数据
    news_score = market.news.value if market.news else None
    news_sentiment = market.news.raw.get("sentiment") if market.news and market.news.raw else None
    news_reasoning = market.news.raw.get("reasoning") if market.news and market.news.raw else None
    news_bullish_factors = market.news.raw.get("bullish_factors") if market.news and market.news.raw else None
    news_bearish_factors = market.news.raw.get("bearish_factors") if market.news and market.news.raw else None
    news_updated_at = market.news.timestamp.isoformat() if market.news else None

    # ETF 资金流数据
    etf_raw = market.etf_flow.raw if market.etf_flow and market.etf_flow.raw else {}
    etf_daily_flow = etf_raw.get("daily_flow_usd")
    etf_flow_3d = etf_raw.get("flow_3d_usd")
    etf_flow_7d = etf_raw.get("flow_7d_usd")
    etf_cum_flow = etf_raw.get("cum_flow_usd")
    etf_total_assets = etf_raw.get("total_net_assets_usd")
    etf_streak_days = etf_raw.get("streak_days")
    etf_date = etf_raw.get("date")

    # 未平仓量数据
    oi_raw = market.open_interest.raw if market.open_interest and market.open_interest.raw else {}
    oi_value_usd = oi_raw.get("oi_value_usd")
    oi_contracts = oi_raw.get("oi_contracts")
    oi_change_1h = oi_raw.get("change_pct_1h")
    oi_change_4h = oi_raw.get("change_pct_4h")
    oi_change_24h = oi_raw.get("change_pct_24h")

    # AI 综合研判
    ai_raw = market.ai_analysis.raw if market.ai_analysis and market.ai_analysis.raw else {}
    ai_bias = ai_raw.get("bias")
    ai_confidence = ai_raw.get("confidence")
    ai_summary = ai_raw.get("summary")
    ai_action = ai_raw.get("action")
    ai_key_drivers = ai_raw.get("key_drivers")
    ai_risks = ai_raw.get("risks")
    ai_horizon = ai_raw.get("horizon")
    ai_updated_at = market.ai_analysis.timestamp.isoformat() if market.ai_analysis else None

    return IndicatorData(
        fear_greed=fg_value,
        fear_greed_class=fg_class,
        funding_rate=fr_value,
        funding_rate_annual=fr_annual,
        top_trader_ratio=tt_value,
        top_trader_sentiment=tt_sentiment,
        price_change_pct=price_change,
        cvd_change_pct=cvd_change,
        has_bullish_divergence=has_bullish,
        has_bearish_divergence=has_bearish,
        atr_value=atr_value,
        taker_buy_btc=taker_buy,
        taker_sell_btc=taker_sell,
        taker_total_btc=taker_total,
        taker_buy_ratio=taker_ratio,
        macd_signal_type=macd_signal_type,
        macd_above_zero=macd_above_zero,
        macd_histogram_rising=macd_histogram_rising,
        macd_strength=macd_strength,
        rsi_signal_type=rsi_signal_type,
        rsi_value=rsi_value,
        rsi_above_center=rsi_above_center,
        rsi_trend_strength=rsi_trend_strength,
        rsi_strength=rsi_strength,
        bollinger_signal_type=bollinger_signal_type,
        bollinger_percent_b=bollinger_percent_b,
        bollinger_bandwidth=bollinger_bandwidth,
        bollinger_is_squeeze=bollinger_is_squeeze,
        bollinger_strength=bollinger_strength,
        ma_signal_type=ma_signal_type,
        ma_trend=ma_trend,
        ma_fast=ma_fast,
        ma_slow=ma_slow,
        ma_price_deviation=ma_price_deviation,
        ma_strength=ma_strength,
        volume_signal_type=volume_signal_type,
        volume_ratio=volume_ratio,
        volume_obv_trend=volume_obv_trend,
        volume_price_change=volume_price_change,
        volume_strength=volume_strength,
        news_score=news_score,
        news_sentiment=news_sentiment,
        news_reasoning=news_reasoning,
        news_bullish_factors=news_bullish_factors,
        news_bearish_factors=news_bearish_factors,
        news_updated_at=news_updated_at,
        etf_daily_flow=etf_daily_flow,
        etf_flow_3d=etf_flow_3d,
        etf_flow_7d=etf_flow_7d,
        etf_cum_flow=etf_cum_flow,
        etf_total_assets=etf_total_assets,
        etf_streak_days=etf_streak_days,
        etf_date=etf_date,
        oi_value_usd=oi_value_usd,
        oi_contracts=oi_contracts,
        oi_change_1h=oi_change_1h,
        oi_change_4h=oi_change_4h,
        oi_change_24h=oi_change_24h,
        ai_bias=ai_bias,
        ai_confidence=ai_confidence,
        ai_summary=ai_summary,
        ai_action=ai_action,
        ai_key_drivers=ai_key_drivers,
        ai_risks=ai_risks,
        ai_horizon=ai_horizon,
        ai_updated_at=ai_updated_at,
        timestamp=market.last_update.isoformat() if market.last_update else datetime.now().isoformat(),
    )


async def _get_indicators_cached() -> IndicatorData:
    """获取指标 (从全局 market)"""
    if not market.is_ready():
        # 首次访问时触发刷新
        await refresh_market_data_async()
    
    return _get_indicators_from_market()


# ══════════════════════════════════════════════════════════════
# FastAPI 应用
# ══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 API 服务启动")
    
    # 首次刷新市场数据
    try:
        await refresh_market_data_async()
    except Exception as e:
        logger.warning(f"首次刷新市场数据失败: {e}")
    
    # 启动市场数据定时刷新
    await app_state.start_market_refresh()
    
    # 统一启动所有调度器 (模拟盘、Demo盘、实盘)
    for name, scheduler in app_state.schedulers.items():
        task = asyncio.create_task(scheduler.run())
        app_state.scheduler_tasks[name] = task
        
        # 判断调度器类型
        if "_demo" in name:
            logger.info(f"📊 [{name}] Demo Trading 调度器已启动")
        elif "_live" in name:
            logger.info(f"📊 [{name}] 真实主网调度器已启动")
        else:
            logger.info(f"📊 [{name}] 模拟调度器已启动")

    try:
        yield
    finally:
        # 停止市场数据刷新
        app_state.stop_market_refresh()
        
        # 统一停止所有调度器
        for name, scheduler in app_state.schedulers.items():
            scheduler.stop()
        
        for name, task in app_state.scheduler_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    logger.info("🛑 API 服务关闭")


app = FastAPI(
    title="BTC 神盾-长矛监控系统 (多策略实验)",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════
# REST API 路由
# ══════════════════════════════════════════════════════════════

@app.get("/api/indicators", response_model=IndicatorData)
async def get_indicators():
    """获取所有指标数据 (缓存 + 异步，不阻塞)"""
    return await _get_indicators_cached()


@app.get("/api/portfolio", response_model=PortfolioData)
async def get_portfolio(preset: Optional[str] = None):
    """获取资产组合 (从调度器读取)"""
    scheduler = app_state.get_scheduler(preset)
    btc_price = await _get_btc_price()

    if scheduler:
        position = scheduler.position
        equity = scheduler.equity
        position_direction = position.direction
        position_size = position.size_btc
        entry_price = position.entry_price
    else:
        equity = DEFAULT_INITIAL_USDT
        position_direction = "NONE"
        position_size = 0.0
        entry_price = 0.0

    futures_btc = equity / btc_price if btc_price > 0 else 0
    total_btc = futures_btc
    total_usd = equity

    unrealized_pnl = 0.0
    if position_size > 0 and entry_price > 0 and position_direction != "NONE":
        sign = 1 if position_direction == "LONG" else -1
        unrealized_pnl = sign * (btc_price - entry_price) * position_size

    return PortfolioData(
        spot_btc=0.0,
        futures_btc=futures_btc,
        total_btc=total_btc,
        total_usd=total_usd,
        btc_price=btc_price,
        position_direction=position_direction,
        position_size=position_size,
        unrealized_pnl=unrealized_pnl,
    )


@app.get("/api/status", response_model=SystemStatus)
async def get_status(preset: Optional[str] = None):
    """获取系统状态 (从调度器读取)"""
    scheduler = app_state.get_scheduler(preset)
    
    if scheduler:
        trades = scheduler.trades
        current_mode = scheduler.current_mode
        total_pnl = scheduler.total_pnl
    else:
        trades = []
        current_mode = TradingMode.IDLE
        total_pnl = 0.0
    
    last_trade = trades[-1]["time"] if trades else None

    return SystemStatus(
        mode=current_mode.value,
        short_state="idle",
        long_state="idle",
        uptime_hours=app_state.get_uptime_hours(),
        last_trade_time=last_trade,
        total_trades=len(trades),
        total_pnl=total_pnl,
    )


@app.get("/api/trades")
async def get_trades(
    preset: Optional[str] = None,
    limit: int = Query(default=50, le=100),
):
    """获取交易记录 (从调度器读取)"""
    scheduler = app_state.get_scheduler(preset)
    if scheduler:
        return scheduler.trades[-limit:]
    return []


@app.get("/api/performance")
async def get_performance(preset: Optional[str] = None):
    """获取策略绩效指标 (从调度器读取)"""
    scheduler = app_state.get_scheduler(preset)
    from ..core.performance import PerformanceTracker
    tracker = PerformanceTracker()
    if scheduler:
        return tracker.calculate(scheduler.trades, DEFAULT_INITIAL_USDT)
    return tracker.calculate([], DEFAULT_INITIAL_USDT)


@app.get("/api/klines")
async def get_klines(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    limit: int = Query(default=100, le=500),
):
    """获取K线数据 (共享，异步不阻塞)"""
    # 对于常用的 4H K线，优先使用全局 market 缓存
    if symbol == "BTCUSDT" and interval == "4h" and market.klines_4h and limit <= len(market.klines_4h):
        return [
            {
                "time": int(k[0]),
                "open": k[1],
                "high": k[2],
                "low": k[3],
                "close": k[4],
                "volume": k[5],
            }
            for k in market.klines_4h[:limit]
        ]
    
    try:
        # 使用 binance_utils 封装的同步方法
        raw = await asyncio.to_thread(
            fetch_klines_sync,
            symbol, interval, limit, False  # use_cache=False，不使用全局缓存
        )
        return [
            {
                "time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in raw
        ]
    except Exception as e:
        logger.error(f"获取K线失败: {e}")
        return []

@app.get("/api/etf-flow")
async def get_etf_flow(limit: int = Query(default=30, le=60)):
    """获取 BTC ETF 历史资金流数据"""
    from stock_btc.data_sources.etf_flow import ETFFlow
    try:
        etf = ETFFlow()
        history = await asyncio.to_thread(etf.fetch_history, limit)
        return history
    except Exception as e:
        logger.error(f"获取 ETF 资金流数据失败: {e}")
        return []


def _ai_analysis_payload() -> dict:
    """读取 market.ai_analysis 当前值, 用于手动刷新接口返回"""
    if not market.ai_analysis:
        return {}
    raw = market.ai_analysis.raw or {}
    return {
        "ai_bias": raw.get("bias"),
        "ai_confidence": raw.get("confidence"),
        "ai_summary": raw.get("summary"),
        "ai_action": raw.get("action"),
        "ai_key_drivers": raw.get("key_drivers"),
        "ai_risks": raw.get("risks"),
        "ai_horizon": raw.get("horizon"),
        "ai_updated_at": market.ai_analysis.timestamp.isoformat(),
    }


@app.post("/api/ai-analysis/refresh")
async def refresh_ai_analysis_manual():
    """手动触发一次 AI 综合研判

    - 锁:    与后台定时任务共享 app_state.ai_refresh_lock, 避免并发 LLM 调用
    - 冷却:  距上次完成不足 AI_MANUAL_COOLDOWN_SEC 秒时拒绝, 防止狂点烧 token
    - 成功:  返回最新的 ai_* 字段, 前端可立即更新, 不必等下一个 WebSocket tick
    """
    now = time.time()

    if app_state.ai_refresh_lock.locked():
        return {"status": "running", "message": "AI 分析正在进行中"}

    elapsed = now - app_state.ai_last_refresh_ts
    if app_state.ai_last_refresh_ts > 0 and elapsed < AI_MANUAL_COOLDOWN_SEC:
        retry_after = int(AI_MANUAL_COOLDOWN_SEC - elapsed) + 1
        return {
            "status": "cooldown",
            "retry_after": retry_after,
            "message": f"请 {retry_after}s 后重试",
        }

    if not market.is_ready():
        return {"status": "error", "message": "市场数据尚未就绪"}

    try:
        async with app_state.ai_refresh_lock:
            await refresh_ai_analysis_async()
            app_state.ai_last_refresh_ts = time.time()
        logger.info("🤖 手动触发的 AI 综合研判已完成")
        return {"status": "ok", **_ai_analysis_payload()}
    except Exception as e:
        logger.error(f"手动 AI 分析失败: {e}")
        return {"status": "error", "message": str(e)}


# ══════════════════════════════════════════════════════════════
# 实验管理 API
# ══════════════════════════════════════════════════════════════

@app.get("/api/config")
async def get_config():
    """获取所有预设配置"""
    return {
        name: config.to_dict()
        for name, config in app_state.preset_configs.items()
    }


@app.post("/api/config/{preset}")
async def set_config(preset: str):
    """切换当前查看的预设 (不影响运行中的调度器)"""
    if preset not in app_state.preset_configs:
        return {"success": False, "error": f"无效预设: {preset}"}
    app_state.selected_preset = preset
    return {"success": True, "preset": preset}


@app.get("/api/account")
async def get_account():
    """获取 Binance 账户余额 (Demo Trading 或 真实主网)"""
    try:
        from stock_btc.binance_utils.binance_client import BinanceClient
        from stock_btc.server.scheduler import _load_binance_config
        
        if app_state.live_preset:
            binance_cfg = _load_binance_config("binance_mainnet")
            client = BinanceClient(binance_cfg, market_type='future', demo=False)
            mode = 'mainnet'
        elif app_state.demo_preset:
            binance_cfg = _load_binance_config("binance_demo", use_demo_key=True)
            client = BinanceClient(binance_cfg, market_type='future', demo=True)
            mode = 'demo'
        else:
            binance_cfg = _load_binance_config("binance_demo")
            client = BinanceClient(binance_cfg, market_type='spot', testnet=True)
            mode = 'testnet'
        balance = await asyncio.to_thread(client.exchange.fetch_balance)

        usdt_total = balance['total'].get('USDT', 0)
        usdt_free = balance['free'].get('USDT', 0)
        usdt_used = balance['used'].get('USDT', 0)

        holdings = []
        for currency, amount in balance['total'].items():
            if amount > 0:
                holdings.append({
                    'currency': currency,
                    'total': amount,
                    'free': balance['free'].get(currency, 0),
                    'used': balance['used'].get(currency, 0),
                })
        holdings.sort(key=lambda x: -x['total'])

        return {
            'usdt_total': usdt_total,
            'usdt_free': usdt_free,
            'usdt_used': usdt_used,
            'holdings': holdings[:20],
            'mode': mode,
        }
    except Exception as e:
        logger.error(f"获取账户余额失败: {e}")
        return {
            'usdt_total': 0,
            'usdt_free': 0,
            'usdt_used': 0,
            'holdings': [],
            'mode': 'error',
            'error': str(e),
        }


# ══════════════════════════════════════════════════════════════
# WebSocket
# ══════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 实时推送"""
    await websocket.accept()
    app_state.ws_connections.append(websocket)
    logger.info(f"WebSocket 连接: {len(app_state.ws_connections)} 个客户端")

    try:
        while True:
            try:
                data = {
                    "type": "update",
                    "selected_preset": app_state.selected_preset,
                    "indicators": (await _get_indicators_cached()).model_dump(),
                    "portfolio": (await get_portfolio()).model_dump(),
                    "status": (await get_status()).model_dump(),
                    "timestamp": datetime.now().isoformat(),
                }
                await websocket.send_json(data)
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.debug(f"WebSocket 推送数据获取失败: {e}")
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        app_state.ws_connections.remove(websocket)
        logger.info(f"WebSocket 断开: {len(app_state.ws_connections)} 个客户端")


# ══════════════════════════════════════════════════════════════
# 静态文件
# ══════════════════════════════════════════════════════════════

WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")


@app.get("/")
async def index():
    """返回前端页面"""
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


if os.path.exists(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
