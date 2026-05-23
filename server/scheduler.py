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
from core import TradingConfig, ParameterSet, TradingMode, refresh_market_data_async, refresh_news_data_async, refresh_ai_analysis_async
from utils import logger

from server.trading_scheduler import SimTradingScheduler, LiveTradingScheduler


# ══════════════════════════════════════════════════════════════
# 全局状态
# ══════════════════════════════════════════════════════════════

DEFAULT_INITIAL_USDT = 1000.0


class AppState:
    """应用状态管理 - 调度器和市场数据的统一入口"""

    PRESET_NAMES = ["conservative", "standard", "aggressive"]
    MARKET_REFRESH_INTERVAL = 300  # 市场数据刷新间隔 (秒)
    NEWS_REFRESH_INTERVAL = 1800   # 新闻分析刷新间隔 (秒)
    AI_REFRESH_INTERVAL = 900      # AI 综合研判刷新间隔 (秒)

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
            except:
                self.ws_connections.remove(ws)
    
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
            while True:
                try:
                    await refresh_news_data_async()
                except Exception as e:
                    logger.error(f"刷新新闻分析失败: {e}")
                await asyncio.sleep(self.NEWS_REFRESH_INTERVAL)

        async def ai_loop():
            # 启动后等市场数据先跑一轮再做 AI 分析
            await asyncio.sleep(30)
            while True:
                try:
                    # 与手动刷新共享同一把锁, 避免并发 LLM 调用
                    async with self.ai_refresh_lock:
                        await refresh_ai_analysis_async()
                        self.ai_last_refresh_ts = time.time()
                except Exception as e:
                    logger.error(f"刷新 AI 综合研判失败: {e}")
                await asyncio.sleep(self.AI_REFRESH_INTERVAL)

        self._market_refresh_task = asyncio.create_task(refresh_loop())
        self._news_refresh_task = asyncio.create_task(news_loop())
        self._ai_refresh_task = asyncio.create_task(ai_loop())
        logger.info(f"📊 市场数据定时刷新已启动 (间隔 {self.MARKET_REFRESH_INTERVAL}s)")
        logger.info(f"📰 新闻分析定时刷新已启动 (间隔 {self.NEWS_REFRESH_INTERVAL}s)")
        logger.info(f"🤖 AI 综合研判定时刷新已启动 (间隔 {self.AI_REFRESH_INTERVAL}s)")
    
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

def _create_sim_scheduler(preset_name: str, config: TradingConfig, app_state) -> Optional[Any]:
    """创建模拟盘调度器 (纯计算，不调用API)
    
    Args:
        preset_name: 预设名称 (conservative/standard/aggressive)
        config: 交易配置
        app_state: 应用状态
    
    Returns:
        SimTradingScheduler 实例
    """
    from server.trading_scheduler import SimTradingScheduler
    
    # 状态文件路径
    state_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", f"trading_state_{preset_name}_sim.json",
    )
    
    scheduler = SimTradingScheduler(
        config=config,
        check_interval=60,
        state_file=state_file,
    )
    
    # 从状态文件恢复
    scheduler.restore_position_state()
    
    scheduler_key = f"{preset_name}_sim"
    
    # 注册回调（仅用于 WebSocket 广播）
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
    
    logger.info(f"🔧 [{preset_name}] 模拟调度器已创建")
    return scheduler


def _create_demo_scheduler(preset_name: str, config: TradingConfig, 
                          max_capital: float, app_state) -> Optional[Any]:
    """创建 Demo Trading 调度器 (Binance Demo API, 虚拟资金)
    
    Args:
        preset_name: 预设名称 (conservative/standard/aggressive)
        config: 交易配置
        max_capital: 资金上限
        app_state: 应用状态
    
    Returns:
        LiveTradingScheduler 实例
    """
    from server.trading_scheduler import LiveTradingScheduler
    from binance_utils import create_futures_executor
    
    try:
        # 加载 Demo 配置，使用 demo_api_key
        binance_cfg = _load_binance_config("binance_demo", use_demo_key=True)
        
        demo_executor = create_futures_executor(
            binance_cfg=binance_cfg,
            assets_config={"bitcoin": {"symbol": "BTC/USDT:USDT", "coin": "BTC", "precision": 3}},
            demo=True,  # Demo Trading 模式
            leverage=config.long.leverage,
        )
        
        # 状态文件路径
        state_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", f"trading_state_{preset_name}_demo.json",
        )
        
        demo_sched = LiveTradingScheduler(
            config=config,
            futures_executor=demo_executor,
            check_interval=60,
            max_capital=max_capital,
            state_file=state_file,
        )
        
        scheduler_key = f"{preset_name}_demo"
        
        # 注册回调（仅用于 WebSocket 广播）
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
        
        demo_sched.on_update(on_update)
        demo_sched.on_trade(on_trade)
        
        logger.info(
            f"🟡 [{preset_name}] Demo Trading 调度器已创建 "
            f"(虚拟资金, 上限=${max_capital:,.0f})"
        )
        return demo_sched
        
    except Exception as e:
        logger.error(f"❌ Demo Trading 调度器创建失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def _create_live_scheduler(preset_name: str, config: TradingConfig,
                          max_capital: float, app_state) -> Optional[Any]:
    """创建真实主网调度器 (Binance Mainnet API, 真实资金)
    
    Args:
        preset_name: 预设名称 (conservative/standard/aggressive)
        config: 交易配置
        max_capital: 资金上限
        app_state: 应用状态
    
    Returns:
        LiveTradingScheduler 实例
    """
    from server.trading_scheduler import LiveTradingScheduler
    from binance_utils import create_futures_executor
    
    try:
        # 加载主网配置
        binance_cfg = _load_binance_config("binance_mainnet")
        
        live_executor = create_futures_executor(
            binance_cfg=binance_cfg,
            assets_config={"bitcoin": {"symbol": "BTC/USDT:USDT", "coin": "BTC", "precision": 3}},
            demo=False,  # 真实主网模式
            leverage=config.long.leverage,
        )
        
        live_sched = LiveTradingScheduler(
            config=config,
            futures_executor=live_executor,
            check_interval=60,
            max_capital=max_capital,
        )
        
        scheduler_key = f"{preset_name}_live"
        
        # 注册回调
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
        
        live_sched.on_update(on_update)
        live_sched.on_trade(on_trade)
        
        logger.info(
            f"🔴 [{preset_name}] 真实主网调度器已创建 "
            f"(⚠️ 真实资金!, 上限=${max_capital:,.0f})"
        )
        return live_sched
        
    except Exception as e:
        logger.error(f"❌ 真实主网调度器创建失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def create_integrated_app(use_sim=True, use_demo=False, use_live=False, 
                         demo_preset="aggressive", live_preset="aggressive",
                         max_capital=500.0):
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

    preset_configs = {
        "conservative": TradingConfig.get_preset(ParameterSet.CONSERVATIVE),
        "standard": TradingConfig.get_preset(ParameterSet.STANDARD),
        "aggressive": TradingConfig.get_preset(ParameterSet.AGGRESSIVE),
    }

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
        
        scheduler = _create_demo_scheduler(
            demo_preset, preset_configs[demo_preset], max_capital, app_state
        )
        if scheduler:
            app_state.schedulers[f"{demo_preset}_demo"] = scheduler
            app_state.demo_preset = demo_preset
        else:
            logger.error(f"❌ Demo Trading 调度器创建失败")

    # ══════════════════════════════════════════════════════════════
    # 3. 创建真实主网调度器 (开关控制，使用 binance_mainnet 配置)
    # ══════════════════════════════════════════════════════════════
    if use_live:
        if live_preset not in preset_configs:
            logger.error(f"❌ 无效的实盘预设: {live_preset}，使用默认 aggressive")
            live_preset = "aggressive"
        
        scheduler = _create_live_scheduler(
            live_preset, preset_configs[live_preset], max_capital, app_state
        )
        if scheduler:
            app_state.schedulers[f"{live_preset}_live"] = scheduler
            app_state.live_preset = live_preset
        else:
            logger.error(f"❌ 真实主网调度器创建失败")

    return app
