"""技术指标模块"""

from indicators.atr import ATRCalculator
from indicators.profit_loss_level import LongLevel, ShortLevel
from indicators.cvd_divergence import CVDDivergenceDetector
from indicators.macd_signal import MACDCalculator
from indicators.rsi_signal import RSICalculator
from indicators.bollinger_signal import BollingerCalculator
from indicators.ma_signal import MACalculator
from indicators.volume_signal import VolumeCalculator
from indicators.support_resistance import SupportResistanceCalculator
__all__ = [
    "ATRCalculator",
    "LongLevel",
    "ShortLevel",
    "CVDDivergenceDetector",
    "MACDCalculator",
    "RSICalculator",
    "BollingerCalculator",
    "MACalculator",
    "VolumeCalculator",
    "SupportResistanceCalculator",
    # 以下三类实际定义在 multi_agent/，通过下方 __getattr__ 懒加载，
    # 保留旧路径 re-export 是为了不破坏 `from indicators import NewsAnalyzer` 这种老代码。
    "NewsAnalyzer",
    "Reflector",
    "StrategySummarizer",
]


# 懒加载兼容入口（避免与 multi_agent/ 形成循环依赖）：
# - multi_agent.reflector 依赖 indicators.analysis_memory
# - 如果在 indicators/__init__.py 里直接 import multi_agent.reflector
#   会触发 indicators 二次初始化，造成 partially initialized module 错误
def __getattr__(name):
    if name in ("NewsAnalyzer", "Reflector", "StrategySummarizer"):
        from importlib import import_module
        module_map = {
            "NewsAnalyzer": "multi_agent.news_analyzer",
            "Reflector": "multi_agent.reflector",
            "StrategySummarizer": "multi_agent.strategy_summarizer",
        }
        mod = import_module(module_map[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
