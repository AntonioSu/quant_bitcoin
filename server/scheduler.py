"""24小时后台调度器 - 工厂函数和集成

功能:
- 定时获取市场数据
- 评估交易信号
- 执行交易策略
- 推送实时更新到前端
- ATR 动态仓位 + 推土机止盈止损

类层次:
- BaseTradingScheduler:  抽象基类，定义框架和接口 (trading_scheduler/base.py)
- SimTradingScheduler:   纯模拟交易 (trading_scheduler/sim_scheduler.py)
- LiveTradingScheduler:  实盘交易 (trading_scheduler/live_scheduler.py)
"""

import os
import json
import time
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any
from core import TradingConfig, ParameterSet, refresh_market_data_async, refresh_news_data_async, refresh_ai_analysis_async
from utils import logger
from utils.common_utils import seconds_until_next_boundary

from server.trading_scheduler import (
    BaseTradingScheduler,
    SimTradingScheduler,
    LiveTradingScheduler,
    DEFAULT_CHECK_INTERVAL,
)


# ══════════════════════════════════════════════════════════════
# 全局状态
# ══════════════════════════════════════════════════════════════

# 绩效基准权益与调度器初始权益必须一致，统一以基类常量为唯一来源
DEFAULT_INITIAL_USDT = BaseTradingScheduler.DEFAULT_EQUITY

# Demo / 实盘默认资金上限，供 bin/run_server.py 的 --max-capital 复用
DEFAULT_MAX_CAPITAL = 500.0

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


class AppState:
    """应用状态管理 - 调度器和市场数据的统一入口"""

    PRESET_NAMES = ["conservative", "standard", "aggressive"]
    MARKET_REFRESH_INTERVAL = 300  # 市场数据刷新间隔 (秒)
    NEWS_REFRESH_INTERVAL = 7200   # 新闻分析刷新间隔 (秒) — 2 小时
    AI_REFRESH_INTERVAL = 3600     # AI 综合研判刷新间隔 (秒) — 1 小时

    def __init__(self):
        self.start_time = datetime.now()

        # TTL 缓存 (仅用于 BTC 价格等高频数据)
        self._cache: Dict[str, Any] = {}
        self._cache_expires: Dict[str, float] = {}

        # 预设配置
        self.preset_configs: Dict[str, TradingConfig] = {
            name: TradingConfig.get_preset(ParameterSet(name)) for name in self.PRESET_NAMES
        }

        # 当前选中的预设 (前端详情视图)
        self.selected_preset: str = "aggressive"

        # 运行模式标记
        self.demo_preset: Optional[str] = None  # Demo Trading 预设
        self.live_preset: Optional[str] = None  # 真实主网预设

        # WebSocket 连接 (由 api.py 设置)
        self.ws_connections: List[Any] = []

        # 所有调度器统一管理 (模拟盘、Demo盘、实盘)
        # key 格式: "preset_sim" (sim), "preset_demo" (demo), "preset_live" (mainnet)
        self.schedulers: Dict[str, Any] = {}
        self.scheduler_tasks: Dict[str, asyncio.Task] = {}
        
        # 市场数据刷新任务
        self._market_refresh_task: Optional[asyncio.Task] = None
        self._news_refresh_task: Optional[asyncio.Task] = None
        self._ai_refresh_task: Optional[asyncio.Task] = None

        # AI 综合研判: 共享锁 (定时 + 手动复用), 上次完成时间戳 (用于手动冷却)
        self.ai_refresh_lock: asyncio.Lock = asyncio.Lock()
        self.ai_last_refresh_ts: float = 0.0
        # 由 server.api 在导入时注入, 研判完成后立刻推送仪表盘
        self.push_dashboard = None
    
    def get_scheduler(self, preset: Optional[str] = None):
        """获取指定预设的调度器，优先返回 sim 调度器"""
        name = preset or self.selected_preset
        # 尝试顺序: preset_sim > preset_demo > preset_live
        for suffix in ["_sim", "_demo", "_live", ""]:
            key = f"{name}{suffix}"
            if key in self.schedulers:
                return self.schedulers[key]
        return None

    def cache_get(self, key: str):
        if time.time() < self._cache_expires.get(key, 0):
            return self._cache.get(key)
        return None

    def cache_set(self, key: str, value, ttl: float = 10):
        self._cache[key] = value
        self._cache_expires[key] = time.time() + ttl

    def get_uptime_hours(self) -> float:
        delta = datetime.now() - self.start_time
        return delta.total_seconds() / 3600

    async def broadcast(self, message: dict):
        for ws in self.ws_connections[:]:
            try:
                await ws.send_json(message)
            except Exception:
                self.ws_connections.remove(ws)

    async def notify_frontend(self):
        """AI / 新闻刷新完成后立刻推送当前仪表盘快照。"""
        if not self.push_dashboard:
            return
        try:
            await self.push_dashboard()
        except Exception as e:
            logger.warning(f"推送前端更新失败: {e}")
    
    async def start_market_refresh(self):
        """启动市场数据定时刷新"""
        async def refresh_loop():
            while True:
                try:
                    await refresh_market_data_async()
                except Exception as e:
                    logger.error(f"刷新市场数据失败: {e}")
                await asyncio.sleep(self.MARKET_REFRESH_INTERVAL)
        
        async def news_loop():
            last_ts = 0.0
            min_gap = 600  # 启动立即拉取后, 靠近整点时跳过一次, 避免 10 分钟内连打两次
            while True:
                delay = seconds_until_next_boundary(self.NEWS_REFRESH_INTERVAL)
                if last_ts <= 0 and delay > 60:
                    logger.info("📰 启动后立即拉取新闻，随后对齐 2 小时整点")
                else:
                    next_at = datetime.fromtimestamp(time.time() + delay).strftime("%H:%M:%S")
                    logger.info(f"📰 新闻分析等待 {delay:.0f}s 至整点 {next_at}")
                    await asyncio.sleep(delay)
                    if last_ts and (time.time() - last_ts) < min_gap:
                        continue
                try:
                    await refresh_news_data_async()
                    last_ts = time.time()
                    await self.notify_frontend()
                except Exception as e:
                    logger.error(f"刷新新闻分析失败: {e}")

        async def ai_loop():
            while True:
                delay = seconds_until_next_boundary(self.AI_REFRESH_INTERVAL)
                next_at = datetime.fromtimestamp(time.time() + delay).strftime("%H:%M:%S")
                logger.info(f"🤖 AI 综合研判等待 {delay:.0f}s 至整点 {next_at}")
                await asyncio.sleep(delay)
                try:
                    # 整点刚启动时市场数据可能还没第一轮, 最多再等 30s
                    from core.market_data import market
                    if not market.is_ready():
                        logger.info("🤖 整点到达但市场数据未就绪, 30s 后重试")
                        await asyncio.sleep(30)
                    # 与手动刷新共享同一把锁, 避免并发 LLM 调用
                    async with self.ai_refresh_lock:
                        await refresh_ai_analysis_async()
                        self.ai_last_refresh_ts = time.time()
                    await self.notify_frontend()
                except Exception as e:
                    logger.error(f"刷新 AI 综合研判失败: {e}")

        self._market_refresh_task = asyncio.create_task(refresh_loop())
        self._news_refresh_task = asyncio.create_task(news_loop())
        self._ai_refresh_task = asyncio.create_task(ai_loop())
        logger.info(f"📊 市场数据定时刷新已启动 (间隔 {self.MARKET_REFRESH_INTERVAL}s)")
        logger.info(
            f"📰 新闻分析定时刷新已启动 (整点对齐, 间隔 {self.NEWS_REFRESH_INTERVAL}s)"
        )
        logger.info(
            f"🤖 AI 综合研判定时刷新已启动 (整点对齐, 间隔 {self.AI_REFRESH_INTERVAL}s)"
        )
    
    def stop_market_refresh(self):
        """停止市场数据定时刷新"""
        if self._market_refresh_task:
            self._market_refresh_task.cancel()
            self._market_refresh_task = None
        if self._news_refresh_task:
            self._news_refresh_task.cancel()
            self._news_refresh_task = None
        if self._ai_refresh_task:
            self._ai_refresh_task.cancel()
            self._ai_refresh_task = None


app_state = AppState()


def _load_binance_config(config_key: str = "binance_demo", use_demo_key: bool = False) -> dict:
    """加载 Binance 配置，返回包含 api_key/secret_key 的字典
    
    Args:
        config_key: 配置文件中的 key ("binance_demo", "binance_mainnet")
        use_demo_key: 是否使用 demo_api_key 替换 api_key
    
    Returns:
        Binance 配置字典 (api_key, secret_key, proxy)
    """
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "config.json",
    )
    
    with open(config_path, "r") as f:
        config = json.load(f)
    
    binance_cfg = config.get(config_key, {})
    
    if not binance_cfg:
        logger.warning(f"⚠️  配置 '{config_key}' 不存在，尝试回退到 'binance'")
        binance_cfg = config.get("binance", {})
    
    if not binance_cfg:
        raise ValueError(f"无法找到有效的 Binance 配置 ('{config_key}' 或 'binance')")
    
    # Demo 模式：将 demo_api_key 提升为 api_key
    if use_demo_key and binance_cfg.get("demo_api_key"):
        binance_cfg = {
            **binance_cfg,
            "api_key": binance_cfg["demo_api_key"],
            "secret_key": binance_cfg["demo_secret_key"],
        }
    
    logger.info(f"📝 使用配置: {config_key}" + (" (demo key)" if use_demo_key else ""))
    return binance_cfg


def _state_file(preset_name: str, kind: str) -> str:
    """调度器状态文件路径（三种模式共用命名规则，重启后可恢复仓位）"""
    return os.path.join(DATA_DIR, f"trading_state_{preset_name}_{kind}.json")


def _register_broadcast_callbacks(scheduler, scheduler_key: str, app_state) -> None:
    """把调度器的信号/成交事件转发到 WebSocket 广播

    回调必须是 async def：BaseTradingScheduler._emit 用 iscoroutinefunction 判断是否 await，
    返回协程的普通 lambda 会被直接调用而永不执行。
    """

    async def on_update(data):
        await app_state.broadcast({
            "type": "signal",
            "preset": scheduler_key,
            "data": data,
        })

    async def on_trade(trade):
        await app_state.broadcast({
            "type": "trade",
            "preset": scheduler_key,
            "data": trade,
        })

    scheduler.on_update(on_update)
    scheduler.on_trade(on_trade)


def _create_sim_scheduler(preset_name: str, config: TradingConfig, app_state) -> Optional[Any]:
    """创建模拟盘调度器 (纯计算，不调用 API)"""
    scheduler = SimTradingScheduler(
        config=config,
        check_interval=DEFAULT_CHECK_INTERVAL,
        state_file=_state_file(preset_name, "sim"),
    )
    scheduler.restore_position_state()

    _register_broadcast_callbacks(scheduler, f"{preset_name}_sim", app_state)

    logger.info(f"🔧 [{preset_name}] 模拟调度器已创建")
    return scheduler


# 交易所调度器的模式差异：配置来源、是否使用 demo key、日志措辞
_EXCHANGE_MODES = {
    "demo": {
        "config_key": "binance_demo",
        "use_demo_key": True,
        "label": "Demo Trading",
        "icon": "🟡",
        "capital_note": "虚拟资金",
    },
    "live": {
        "config_key": "binance_mainnet",
        "use_demo_key": False,
        "label": "真实主网",
        "icon": "🔴",
        "capital_note": "⚠️ 真实资金!",
    },
}


def _create_exchange_scheduler(kind: str, preset_name: str, config: TradingConfig,
                               max_capital: float, app_state) -> Optional[Any]:
    """创建走交易所 API 的调度器 (Demo Trading 或真实主网)

    Demo 与实盘的流程完全一致，只有配置来源和风险提示不同，差异集中在 _EXCHANGE_MODES。

    Args:
        kind: "demo" 或 "live"
        preset_name: 预设名称 (conservative/standard/aggressive)
        config: 交易配置
        max_capital: 资金上限
        app_state: 应用状态

    Returns:
        LiveTradingScheduler 实例，创建失败返回 None
    """
    mode = _EXCHANGE_MODES[kind]
    from binance_utils import create_futures_executor

    try:
        binance_cfg = _load_binance_config(
            mode["config_key"], use_demo_key=mode["use_demo_key"]
        )

        executor = create_futures_executor(
            binance_cfg=binance_cfg,
            assets_config={
                "bitcoin": {
                    "symbol": BaseTradingScheduler.FUTURES_SYMBOL,
                    "coin": "BTC",
                    "precision": 3,
                }
            },
            demo=(kind == "demo"),
            # 交易所侧的兜底杠杆；每笔实际杠杆由 AI 决策在开仓时覆盖
            leverage=config.long.leverage,
        )

        scheduler = LiveTradingScheduler(
            config=config,
            futures_executor=executor,
            check_interval=DEFAULT_CHECK_INTERVAL,
            max_capital=max_capital,
            state_file=_state_file(preset_name, kind),
        )

        _register_broadcast_callbacks(scheduler, f"{preset_name}_{kind}", app_state)

        logger.info(
            f"{mode['icon']} [{preset_name}] {mode['label']}调度器已创建 "
            f"({mode['capital_note']}, 上限=${max_capital:,.0f})"
        )
        return scheduler

    except Exception:
        logger.exception(f"❌ {mode['label']}调度器创建失败")
        return None


def create_integrated_app(use_sim=True, use_demo=False, use_live=False,
                         demo_preset="aggressive", live_preset="aggressive",
                         max_capital=DEFAULT_MAX_CAPITAL):
    """创建集成了模拟盘、Demo盘、实盘三种独立调度器的应用
    
    三种模式完全独立，互不影响:
    - use_sim:     是否启用模拟盘 (离线计算PnL，不调用API，默认启用)
    - use_demo:    是否启用 Demo Trading (Binance Demo API，虚拟资金)
    - use_live:    是否启用真实主网 (Binance Mainnet API，真实资金)

    Args:
        use_sim: 是否启用模拟盘 (默认 True)
        use_demo: 是否启用 Demo Trading (默认 False)
        use_live: 是否启用真实主网 (默认 False)
        demo_preset: Demo Trading 使用的预设 (默认 aggressive)
        live_preset: 真实主网使用的预设 (默认 aggressive)
        max_capital: 实盘/Demo盘资金上限
    """
    from server.api import app

    preset_configs = app_state.preset_configs

    # ══════════════════════════════════════════════════════════════
    # 1. 创建模拟盘调度器 (开关控制，默认启用)
    # ══════════════════════════════════════════════════════════════
    if use_sim:
        for preset_name, config in preset_configs.items():
            scheduler = _create_sim_scheduler(preset_name, config, app_state)
            if scheduler:
                app_state.schedulers[f"{preset_name}_sim"] = scheduler

    # ══════════════════════════════════════════════════════════════
    # 2. 创建 Demo Trading 调度器 (开关控制，使用 binance_demo 配置)
    # ══════════════════════════════════════════════════════════════
    if use_demo:
        if demo_preset not in preset_configs:
            logger.error(f"❌ 无效的 Demo 预设: {demo_preset}，使用默认 aggressive")
            demo_preset = "aggressive"

        scheduler = _create_exchange_scheduler(
            "demo", demo_preset, preset_configs[demo_preset], max_capital, app_state
        )
        if scheduler:
            app_state.schedulers[f"{demo_preset}_demo"] = scheduler
            app_state.demo_preset = demo_preset

    # ══════════════════════════════════════════════════════════════
    # 3. 创建真实主网调度器 (开关控制，使用 binance_mainnet 配置)
    # ══════════════════════════════════════════════════════════════
    if use_live:
        if live_preset not in preset_configs:
            logger.error(f"❌ 无效的实盘预设: {live_preset}，使用默认 aggressive")
            live_preset = "aggressive"

        scheduler = _create_exchange_scheduler(
            "live", live_preset, preset_configs[live_preset], max_capital, app_state
        )
        if scheduler:
            app_state.schedulers[f"{live_preset}_live"] = scheduler
            app_state.live_preset = live_preset

    return app
