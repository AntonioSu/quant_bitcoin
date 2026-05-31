"""LLM agent 模块

把所有需要调用大模型的 agent 集中在这里：
- MarketAnalyzer:      多维指标综合研判（每个 4H 周期跑一次）
- NewsAnalyzer:        新闻情绪多空打分
- Reflector:           单笔交易复盘（平仓后触发）
- StrategySummarizer:  多笔复盘聚合成策略备忘录（周期性触发）
- DecisionCommittee:   多空辩论 + 风险审查 + 最终汇总

知识库（角色 / 规则 / regime / 指标手册）放在 knowledge/ 子目录，
统一作为 system_prompt 的一部分被加载。
"""

from multi_agent.decision_committee import DecisionCommittee
from multi_agent.market_analyzer import MarketAnalyzer
from multi_agent.news_analyzer import NewsAnalyzer
from multi_agent.reflector import Reflector
from multi_agent.strategy_summarizer import StrategySummarizer

__all__ = [
    "DecisionCommittee",
    "MarketAnalyzer",
    "NewsAnalyzer",
    "Reflector",
    "StrategySummarizer",
]
