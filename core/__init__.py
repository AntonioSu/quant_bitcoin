"""核心模块"""

from .config import TradingConfig, ParameterSet
from .signal_aggregator import SignalAggregator, TradingMode, SignalResult
from .performance import PerformanceTracker
from .market_data import (
    market,
    MarketData,
    refresh_market_data,
    refresh_market_data_async,
    refresh_news_data_async,
    refresh_ai_analysis_async,
    get_sentiment,
)

__all__ = [
    "TradingConfig",
    "ParameterSet",
    "SignalAggregator",
    "TradingMode",
    "SignalResult",
    "PerformanceTracker",
    "market",
    "MarketData",
    "refresh_market_data",
    "refresh_market_data_async",
    "refresh_news_data_async",
    "refresh_ai_analysis_async",
    "get_sentiment",
]
