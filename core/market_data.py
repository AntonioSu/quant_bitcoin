"""全局市场数据管理

统一管理所有数据源，避免重复实例化和重复请求。
所有模块通过 `from core.market_data import market` 获取数据。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from data_sources.base import DataPoint
from data_sources.fear_greed import FearGreedIndex
from data_sources.funding_rate import FundingRate
from data_sources.top_trader import TopTraderRatio
from data_sources.etf_flow import ETFFlow
from data_sources.open_interest import OpenInterest
from data_sources.liquidation import Liquidation
from data_sources.btc_taker_kline import TakerData, taker_analyzer
from data_sources.cvd_orderflow import CVDOrderFlow
from data_sources.exchange_netflow import ExchangeNetflow
from data_sources.macro_data import MacroData
from data_sources.options_data import OptionsData
from data_sources.stablecoin_flow import StablecoinFlow
from data_sources.mvrv_data import MVRVData
from indicators import ATRCalculator, CVDDivergenceDetector, MACDCalculator, RSICalculator, BollingerCalculator, MACalculator, VolumeCalculator, SupportResistanceCalculator
from indicators.atr import ATRResult
from indicators.cvd_divergence import CVDResult
from indicators.macd_signal import MACDResult
from indicators.rsi_signal import RSIResult
from indicators.bollinger_signal import BollingerResult
from indicators.ma_signal import MAResult
from indicators.volume_signal import VolumeResult
from indicators.support_resistance import SupportResistanceResult
from multi_agent.news_analyzer import NewsAnalyzer
from multi_agent.market_analyzer import MarketAnalyzer
from indicators.analysis_memory import AnalysisMemory
from multi_agent.strategy_summarizer import StrategySummarizer
from binance_utils import fetch_klines_sync
from utils import logger


@dataclass
class MarketData:
    """全局市场数据容器"""
    
    fear_greed: Optional[DataPoint] = None
    funding_rate: Optional[DataPoint] = None
    top_trader: Optional[DataPoint] = None
    atr: Optional[ATRResult] = None
    cvd: Optional[CVDResult] = None
    macd: Optional[MACDResult] = None
    rsi: Optional[RSIResult] = None
    bollinger: Optional[BollingerResult] = None
    ma: Optional[MAResult] = None
    volume: Optional[VolumeResult] = None
    support_resistance: Optional[SupportResistanceResult] = None
    taker: Optional[TakerData] = None
    news: Optional[DataPoint] = None
    etf_flow: Optional[DataPoint] = None
    open_interest: Optional[DataPoint] = None
    liquidation: Optional[DataPoint] = None
    ai_analysis: Optional[DataPoint] = None
    cvd_orderflow: Optional[DataPoint] = None
    exchange_netflow: Optional[DataPoint] = None
    macro: Optional[DataPoint] = None
    options: Optional[DataPoint] = None
    stablecoin: Optional[DataPoint] = None
    mvrv: Optional[DataPoint] = None
    klines_4h: List[List] = field(default_factory=list)
    last_update: Optional[datetime] = None
    position_context: Optional[dict] = None
    
    def is_ready(self) -> bool:
        """数据是否已初始化"""
        return self.last_update is not None
    
    def to_dict(self) -> dict:
        """转换为字典 (供 API 返回)"""
        return {
            "fear_greed": {
                "value": self.fear_greed.value if self.fear_greed else None,
                "classification": self.fear_greed.raw.get("classification") if self.fear_greed and self.fear_greed.raw else None,
            },
            "funding_rate": {
                "value": self.funding_rate.value if self.funding_rate else None,
                "annual_yield": self.funding_rate.raw.get("annual_yield") if self.funding_rate and self.funding_rate.raw else None,
            },
            "top_trader": {
                "value": self.top_trader.value if self.top_trader else None,
                "long_account": self.top_trader.raw.get("long_account") if self.top_trader and self.top_trader.raw else None,
                "short_account": self.top_trader.raw.get("short_account") if self.top_trader and self.top_trader.raw else None,
            },
            "atr": {
                "value": self.atr.value if self.atr else None,
                "period": self.atr.period if self.atr else None,
                "timeframe": self.atr.timeframe if self.atr else None,
            },
            "cvd": {
                "price_change_pct": self.cvd.price_change_pct if self.cvd else None,
                "cvd_change_pct": self.cvd.cvd_change_pct if self.cvd else None,
                "divergence": self.cvd.divergence.value if self.cvd else None,
                "is_valid_signal": self.cvd.is_valid_signal if self.cvd else None,
            },
            "macd": {
                "signal_type": self.macd.signal_type.value if self.macd else None,
                "above_zero": self.macd.above_zero if self.macd else None,
                "histogram_rising": self.macd.histogram_rising if self.macd else None,
                "strength": self.macd.strength if self.macd else None,
            },
            "rsi": {
                "signal_type": self.rsi.signal_type.value if self.rsi else None,
                "rsi_value": self.rsi.rsi_value if self.rsi else None,
                "above_center": self.rsi.above_center if self.rsi else None,
                "trend_strength": self.rsi.trend_strength if self.rsi else None,
                "strength": self.rsi.strength if self.rsi else None,
            },
            "bollinger": {
                "signal_type": self.bollinger.signal_type.value if self.bollinger else None,
                "price": self.bollinger.price if self.bollinger else None,
                "upper": self.bollinger.upper if self.bollinger else None,
                "middle": self.bollinger.middle if self.bollinger else None,
                "lower": self.bollinger.lower if self.bollinger else None,
                "percent_b": self.bollinger.percent_b if self.bollinger else None,
                "bandwidth": self.bollinger.bandwidth if self.bollinger else None,
                "is_squeeze": self.bollinger.is_squeeze if self.bollinger else None,
                "strength": self.bollinger.strength if self.bollinger else None,
            },
            "ma": {
                "signal_type": self.ma.signal_type.value if self.ma else None,
                "fast_ma": self.ma.fast_ma if self.ma else None,
                "slow_ma": self.ma.slow_ma if self.ma else None,
                "trend": self.ma.trend if self.ma else None,
                "price_deviation": self.ma.price_deviation if self.ma else None,
                "strength": self.ma.strength if self.ma else None,
            },
            "volume": {
                "signal_type": self.volume.signal_type.value if self.volume else None,
                "vol_ratio": self.volume.vol_ratio if self.volume else None,
                "obv_trend": self.volume.obv_trend if self.volume else None,
                "price_change_pct": self.volume.price_change_pct if self.volume else None,
                "strength": self.volume.strength if self.volume else None,
            },
            "support_resistance": self.support_resistance.to_dict() if self.support_resistance else None,
            "taker": self.taker.to_dict() if self.taker else None,
            "news": {
                "score": self.news.value if self.news else None,
                "sentiment": self.news.raw.get("sentiment") if self.news and self.news.raw else None,
                "reasoning": self.news.raw.get("reasoning") if self.news and self.news.raw else None,
                "bullish_factors": self.news.raw.get("bullish_factors", []) if self.news and self.news.raw else [],
                "bearish_factors": self.news.raw.get("bearish_factors", []) if self.news and self.news.raw else [],
                "updated_at": self.news.timestamp.isoformat() if self.news else None,
            },
            "liquidation": {
                "total_usd": self.liquidation.raw.get("total_usd") if self.liquidation and self.liquidation.raw else None,
                "long_liquidation_usd": self.liquidation.raw.get("long_liquidation_usd") if self.liquidation and self.liquidation.raw else None,
                "short_liquidation_usd": self.liquidation.raw.get("short_liquidation_usd") if self.liquidation and self.liquidation.raw else None,
                "long_short_ratio": self.liquidation.raw.get("long_short_ratio") if self.liquidation and self.liquidation.raw else None,
                "total_count": self.liquidation.raw.get("total_count") if self.liquidation and self.liquidation.raw else None,
            },
            "etf_flow": self.etf_flow.raw if self.etf_flow and self.etf_flow.raw else None,
            "ai_analysis": {
                **(self.ai_analysis.raw or {}),
                "updated_at": self.ai_analysis.timestamp.isoformat() if self.ai_analysis else None,
            } if self.ai_analysis else None,
            "cvd_orderflow": self.cvd_orderflow.raw if self.cvd_orderflow and self.cvd_orderflow.raw else None,
            "exchange_netflow": self.exchange_netflow.raw if self.exchange_netflow and self.exchange_netflow.raw else None,
            "macro": self.macro.raw if self.macro and self.macro.raw else None,
            "options": self.options.raw if self.options and self.options.raw else None,
            "stablecoin": self.stablecoin.raw if self.stablecoin and self.stablecoin.raw else None,
            "mvrv": self.mvrv.raw if self.mvrv and self.mvrv.raw else None,
            "last_update": self.last_update.isoformat() if self.last_update else None,
        }


# ══════════════════════════════════════════════════════════════════════════════
# 模块级单例 (只创建一次)
# ══════════════════════════════════════════════════════════════════════════════

_fear_greed = FearGreedIndex()
_funding_rate = FundingRate()
_top_trader = TopTraderRatio()
_etf_flow = ETFFlow()
_atr_calc = ATRCalculator(period=14, timeframe="4h")
_cvd_detector = CVDDivergenceDetector(lookback_periods=6)
_macd_calc = MACDCalculator(fast_period=12, slow_period=26, signal_period=9, timeframe="4h")
_rsi_calc = RSICalculator(period=14, overbought=70.0, oversold=30.0, timeframe="4h")
_bollinger_calc = BollingerCalculator(period=20, std_dev=2.0, timeframe="4h")
_ma_calc = MACalculator(fast_period=7, slow_period=25, timeframe="4h")
_volume_calc = VolumeCalculator(
    avg_period=20,
    surge_threshold=2.0,
    timeframe="4h",
    time_normalize=True,        # 按当根 K线已走时长摊薄历史均量, 消除 K线进度偏置
    kline_duration_min=240.0,   # 4h = 240 min
    min_elapsed_min=10.0,       # 前 10 分钟视为 warmup, 返回中性量比 1.0
)
_support_resistance_calc = SupportResistanceCalculator(lookback=80, timeframe="4h")
_news_analyzer = NewsAnalyzer()
_analysis_memory = AnalysisMemory()
_strategy_summarizer = StrategySummarizer(memory=_analysis_memory)
_market_analyzer = MarketAnalyzer(memory=_analysis_memory, summarizer=_strategy_summarizer)
_open_interest = OpenInterest(symbol="BTCUSDT", period="4h")
_liquidation = Liquidation(symbol="BTCUSDT", lookback_minutes=60)

# 新增数据源
import os as _os
_cvd_orderflow = CVDOrderFlow(lookback_minutes=240)  # 4h aggTrade CVD 分层
_exchange_netflow = ExchangeNetflow()  # CoinMetrics 免费 API
_macro_data = MacroData(fred_api_key=_os.getenv("FRED_API_KEY", ""))
_options_data = OptionsData()  # Deribit 公开 API
_stablecoin_flow = StablecoinFlow()  # DefiLlama 免费 API
_mvrv_data = MVRVData()  # CoinMetrics 免费 API

# 全局数据实例
market = MarketData()

# 启动时从磁盘恢复上次 AI 研判，避免重启后在 AI 分析完成前无退出保护
if _market_analyzer._last_analysis:
    _seed = _market_analyzer._last_analysis
    market.ai_analysis = DataPoint(
        value=float(_seed.get("confidence", 0)) if _seed.get("bias") != "NEUTRAL" else 0.0,
        timestamp=datetime.now(),
        source="Market Analyzer (restored)",
        raw=_seed,
    )
    logger.info(
        f"📂 启动恢复 AI 研判到 market: "
        f"{_seed.get('bias')} ({_seed.get('confidence')}%) action={_seed.get('action')}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 刷新函数
# ══════════════════════════════════════════════════════════════════════════════

def refresh_market_data() -> MarketData:
    """
    刷新所有市场数据 (同步版本)
    
    Returns:
        更新后的 market 实例
    """
    logger.info("🔄 刷新市场数据...")
    
    # 1. 获取基础数据源
    try:
        market.fear_greed = _fear_greed.fetch()
    except Exception as e:
        logger.warning(f"获取恐惧贪婪指数失败: {e}")
    
    try:
        market.funding_rate = _funding_rate.fetch()
    except Exception as e:
        logger.warning(f"获取资金费率失败: {e}")
    
    try:
        market.top_trader = _top_trader.fetch()
    except Exception as e:
        logger.warning(f"获取聪明钱数据失败: {e}")

    try:
        market.etf_flow = _etf_flow.fetch()
    except Exception as e:
        logger.warning(f"获取 ETF 资金流数据失败: {e}")

    try:
        market.open_interest = _open_interest.fetch()
    except Exception as e:
        logger.warning(f"获取未平仓量失败: {e}")

    try:
        market.liquidation = _liquidation.fetch()
    except Exception as e:
        logger.warning(f"获取爆仓数据失败: {e}")

    try:
        market.cvd_orderflow = _cvd_orderflow.fetch()
    except Exception as e:
        logger.warning(f"获取CVD分层订单流数据失败: {e}")

    try:
        market.exchange_netflow = _exchange_netflow.fetch()
    except Exception as e:
        logger.warning(f"获取交易所净流入数据失败: {e}")

    try:
        market.options = _options_data.fetch()
    except Exception as e:
        logger.warning(f"获取期权数据失败: {e}")

    try:
        market.stablecoin = _stablecoin_flow.fetch()
    except Exception as e:
        logger.warning(f"获取稳定币供应数据失败: {e}")

    try:
        market.mvrv = _mvrv_data.fetch()
    except Exception as e:
        logger.warning(f"获取MVRV数据失败: {e}")

    try:
        market.macro = _macro_data.fetch()
    except Exception as e:
        logger.warning(f"获取宏观经济数据失败: {e}")

    # 2. 获取 K线数据 (供 ATR/CVD/Taker 计算)
    try:
        market.klines_4h = fetch_klines_sync(
            symbol="BTCUSDT",
            interval="4h",
            limit=50
        )
    except Exception as e:
        logger.warning(f"获取K线数据失败: {e}")
    
    # 3. 计算技术指标
    if market.klines_4h and len(market.klines_4h) >= 15:
        try:
            market.atr = _atr_calc.calculate(market.klines_4h)
        except Exception as e:
            logger.warning(f"计算ATR失败: {e}")
        
        try:
            market.cvd = _cvd_detector.detect(market.klines_4h)
        except Exception as e:
            logger.warning(f"计算CVD失败: {e}")
        
        try:
            market.taker = taker_analyzer.calculate(market.klines_4h, periods=1)
        except Exception as e:
            logger.warning(f"计算Taker买卖量失败: {e}")

    # MACD 需要至少 slow_period + signal_period = 35 根K线
    if market.klines_4h and len(market.klines_4h) >= 35:
        try:
            market.macd = _macd_calc.calculate(market.klines_4h)
        except Exception as e:
            logger.warning(f"计算MACD失败: {e}")

    # RSI 需要至少 period + divergence_lookback = 28 根K线
    if market.klines_4h and len(market.klines_4h) >= 28:
        try:
            market.rsi = _rsi_calc.calculate(market.klines_4h)
        except Exception as e:
            logger.warning(f"计算RSI失败: {e}")

    # Bollinger 需要至少 period + squeeze_lookback = 25 根K线
    if market.klines_4h and len(market.klines_4h) >= 25:
        try:
            market.bollinger = _bollinger_calc.calculate(market.klines_4h)
        except Exception as e:
            logger.warning(f"计算布林带失败: {e}")

    # MA 需要至少 slow_period + 2 = 27 根K线
    if market.klines_4h and len(market.klines_4h) >= 27:
        try:
            market.ma = _ma_calc.calculate(market.klines_4h)
        except Exception as e:
            logger.warning(f"计算MA均线失败: {e}")

    # Volume 需要至少 avg_period + 2 = 22 根K线
    if market.klines_4h and len(market.klines_4h) >= 22:
        try:
            market.volume = _volume_calc.calculate(market.klines_4h)
        except Exception as e:
            logger.warning(f"计算成交量指标失败: {e}")

    # 支撑/压力位使用最近 K线局部高低点聚类
    if market.klines_4h and len(market.klines_4h) >= 14:
        try:
            market.support_resistance = _support_resistance_calc.calculate(market.klines_4h)
        except Exception as e:
            logger.warning(f"计算支撑/压力位失败: {e}")

    market.last_update = datetime.now()
    logger.info(f"✅ 市场数据刷新完成 @ {market.last_update.strftime('%H:%M:%S')}")
    
    return market


async def refresh_market_data_async() -> MarketData:
    """
    刷新所有市场数据 (异步版本，用于 FastAPI)
    
    注意: 底层数据源目前是同步的，这里只是包装成 async
    后续可以改造数据源为真正的异步实现
    """
    import asyncio
    return await asyncio.to_thread(refresh_market_data)


async def refresh_news_data_async() -> None:
    """刷新新闻多空分析 (独立于市场数据，30分钟一次)"""
    import asyncio
    try:
        market.news = await asyncio.to_thread(_news_analyzer.fetch)
        logger.info(f"📰 新闻分析完成: score={market.news.value}")
    except Exception as e:
        logger.warning(f"新闻分析失败: {e}")


async def refresh_ai_analysis_async() -> None:
    """刷新 AI 综合多空研判 (独立刷新, 默认 15 分钟一次)

    依赖 market 已经被 refresh_market_data 填充过, 否则跳过本轮.
    失败时保留上一次的结果, 不会清空 market.ai_analysis.
    """
    import asyncio
    if not market.is_ready():
        logger.debug("🤖 market 数据未就绪, 跳过本轮 AI 分析")
        return
    try:
        result = await asyncio.to_thread(_market_analyzer.fetch, market)
        market.ai_analysis = result
        bias = (result.raw or {}).get("bias", "NEUTRAL")
        conf = (result.raw or {}).get("confidence", 0)
        logger.info(f"🤖 AI 综合分析完成: {bias} ({conf}%)")
    except Exception as e:
        logger.warning(f"AI 综合分析失败: {e}")


def get_analysis_memory():
    """获取全局研判记忆实例"""
    return _analysis_memory


def get_strategy_summarizer():
    """获取全局策略备忘录实例"""
    return _strategy_summarizer


def get_sentiment() -> str:
    """获取市场情绪描述"""
    if not market.top_trader:
        return "未知"
    
    ratio = market.top_trader.value
    if ratio > 2.0:
        return "极度看多（过热）"
    elif ratio > 1.5:
        return "看多"
    elif ratio > 1.0:
        return "偏多"
    elif ratio > 0.67:
        return "偏空"
    elif ratio > 0.5:
        return "看空"
    else:
        return "极度看空（超跌）"
