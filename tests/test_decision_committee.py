#!/usr/bin/env python3
"""Tests for decision committee schemas and entry gate integration."""

import os
from datetime import datetime


from data_sources.base import DataPoint
from multi_agent.decision_committee import DecisionCommittee
from multi_agent.market_analyzer import MarketAnalyzer
from multi_agent.schemas import CommitteeDecision, RiskReview


def test_committee_decision_blocks_open_action_when_entry_not_ok():
    decision = CommitteeDecision.model_validate({
        "bias": "LONG",
        "confidence": 82,
        "action": "加多",
        "entry_ok": False,
        "position_size_hint": "50%",
        "key_drivers": [
            {"factor": "RSI 4H 从 31 回升", "side": "bull", "weight": "high"},
            {"factor": "CVD 出现底背离", "side": "bull", "weight": "high"},
            {"factor": "资金费率降至 0.01%", "side": "bull", "weight": "medium"},
        ],
        "risks": ["风险审查不允许入场"],
    })

    assert decision.action == "等待入场"
    assert decision.entry_ok is False
    assert decision.position_size_hint == "0%"


def test_low_confidence_open_action_is_normalized_to_wait():
    decision = CommitteeDecision.model_validate({
        "bias": "SHORT",
        "confidence": 20,
        "action": "加空",
        "entry_ok": True,
        "position_size_hint": "25%",
    })

    assert decision.action == "等待入场"
    assert decision.entry_ok is False


def test_entry_ok_without_size_hint_derives_size_and_action():
    """Manager 常省略 action/仓位；不可因默认 0% 把 entry_ok 误杀为 false。"""
    decision = CommitteeDecision.model_validate({
        "bias": "LONG",
        "confidence": 55,
        "confidence_level": "MODERATE",
        "summary": "均线多头，允许轻仓",
        "entry_ok": True,
    })

    assert decision.entry_ok is True
    assert decision.action == "加多"
    assert decision.position_size_hint == "50%"


def test_risk_review_zero_position_blocks_entry():
    review = RiskReview.model_validate({
        "entry_ok": True,
        "risk_level": "medium",
        "position_size_hint": "0%",
    })

    assert review.entry_ok is False


def test_risk_review_extreme_level_blocks_entry():
    review = RiskReview.model_validate({
        "entry_ok": True,
        "risk_level": "extreme",
        "position_size_hint": "50%",
    })

    assert review.entry_ok is False


def test_risk_review_high_level_can_still_allow_entry():
    """risk_level 只表达烈度，high 不等于阻断——这是 entry_ok 独立存在的意义。"""
    review = RiskReview.model_validate({
        "entry_ok": True,
        "risk_level": "high",
        "position_size_hint": "25%",
    })

    assert review.entry_ok is True
    assert review.position_size_hint == "25%"


def test_merge_risk_veto_overrides_manager_entry_ok():
    merged = DecisionCommittee._merge_risk_into_manager_payload(
        {"bias": "LONG", "entry_ok": True, "position_size_hint": "50%"},
        RiskReview.model_validate({
            "entry_ok": False,
            "risk_level": "high",
            "position_size_hint": "0%",
            "blockers": ["极端波动"],
        }),
    )
    assert merged["entry_ok"] is False
    assert merged["position_size_hint"] == "0%"
    assert merged["entry_gate"] == "RISK_VETO"


def test_entry_gate_attributes_manager_block():
    """Manager 自己不开：不该记到审查员账上。"""
    merged = DecisionCommittee._merge_risk_into_manager_payload(
        {"bias": "LONG", "entry_ok": False},
        RiskReview.model_validate({
            "entry_ok": False,
            "risk_level": "high",
            "position_size_hint": "0%",
        }),
    )
    assert merged["entry_gate"] == "MANAGER_BLOCK"


def test_entry_gate_attributes_risk_default_when_manager_omits():
    """Manager 省略 entry_ok，采用审查员的否决结论。"""
    merged = DecisionCommittee._merge_risk_into_manager_payload(
        {"bias": "LONG"},
        RiskReview.model_validate({
            "entry_ok": False,
            "risk_level": "high",
            "position_size_hint": "0%",
        }),
    )
    assert merged["entry_ok"] is False
    assert merged["entry_gate"] == "RISK_DEFAULT"


def test_entry_gate_open_when_both_agree():
    merged = DecisionCommittee._merge_risk_into_manager_payload(
        {"bias": "LONG", "entry_ok": True, "position_size_hint": "50%"},
        RiskReview.model_validate({
            "entry_ok": True,
            "risk_level": "medium",
            "position_size_hint": "50%",
        }),
    )
    assert merged["entry_gate"] == "OPEN"


def test_entry_gate_attributes_schema_level_blocks():
    """schema 三条硬规则各自认领自己挡下的入场。"""
    low_conf = CommitteeDecision.model_validate({
        "bias": "LONG", "confidence": 20, "entry_ok": True,
        "position_size_hint": "50%", "entry_gate": "OPEN",
    })
    assert low_conf.entry_ok is False
    assert low_conf.entry_gate == "LOW_CONFIDENCE"

    neutral = CommitteeDecision.model_validate({
        "bias": "NEUTRAL", "confidence": 70, "entry_ok": True,
        "position_size_hint": "50%", "entry_gate": "OPEN",
    })
    assert neutral.entry_ok is False
    assert neutral.entry_gate == "NEUTRAL_BIAS"

    # RISK_VETO 发生在 schema 之前，不该被后续规则改写归因
    vetoed = CommitteeDecision.model_validate({
        "bias": "LONG", "confidence": 70, "entry_ok": False,
        "position_size_hint": "0%", "entry_gate": "RISK_VETO",
    })
    assert vetoed.entry_gate == "RISK_VETO"


def test_entry_gate_is_open_whenever_entry_allowed():
    decision = CommitteeDecision.model_validate({
        "bias": "LONG", "confidence": 75, "action": "加多",
        "entry_ok": True, "position_size_hint": "50%",
    })
    assert decision.entry_ok is True
    assert decision.entry_gate == "OPEN"
    assert decision.to_analysis_dict()["entry_gate"] == "OPEN"


def test_fallback_decision_is_attributed_to_committee_failure():
    decision = CommitteeDecision.fallback("timeout")
    assert decision.entry_ok is False
    assert decision.entry_gate == "COMMITTEE_FAILED"


def test_merge_risk_fills_missing_size_hint():
    merged = DecisionCommittee._merge_risk_into_manager_payload(
        {"bias": "SHORT", "entry_ok": True},
        RiskReview.model_validate({
            "entry_ok": True,
            "risk_level": "medium",
            "position_size_hint": "25%",
        }),
    )
    assert merged["entry_ok"] is True
    assert merged["position_size_hint"] == "25%"


def _kline(high, low, close):
    """[open_ms, open, high, low, close, volume, ...] 的最小可用形态"""
    return [0, close, high, low, close, 0]


def test_range_position_excludes_unclosed_candle_and_detects_breakout():
    """区间必须只用已收盘 K 线，否则突破永远算不出 >100%。"""
    closed = [_kline(100.0, 90.0, 95.0) for _ in range(12)]
    # 进行中的一根冲到 110：区间上沿仍是 100，位置应 >100%
    klines = closed + [_kline(110.0, 95.0, 110.0)]

    pct = MarketAnalyzer._range_position_pct(klines, lookback_hours=48)
    assert pct is not None and pct > 100.0


def test_range_position_matches_scheduler_guard_math():
    """研判层展示的位置必须和执行层护栏算的一致，否则等于告诉 AI 一套、拦另一套。"""
    from types import SimpleNamespace
    from server.trading_scheduler.base import BaseTradingScheduler

    closed = [_kline(100.0 + i, 90.0 + i, 95.0 + i) for i in range(12)]
    klines = closed + [_kline(103.0, 97.0, 99.0)]

    stub = SimpleNamespace(
        config=SimpleNamespace(risk=SimpleNamespace(range_lookback_hours=48))
    )
    snapshot_pct = MarketAnalyzer._range_position_pct(klines, lookback_hours=48)
    guard_pct = BaseTradingScheduler._entry_range_position(
        stub, "LONG", float(klines[-1][4]), klines
    )
    assert abs(snapshot_pct - guard_pct) < 1e-6


def test_range_position_returns_none_without_enough_klines():
    assert MarketAnalyzer._range_position_pct([], lookback_hours=48) is None
    assert MarketAnalyzer._range_position_pct([_kline(1, 1, 1)] * 2) is None


def test_market_analyzer_normalize_keeps_action_and_size():
    normalized = MarketAnalyzer._normalize({
        "bias": "LONG",
        "confidence": 55,
        "confidence_level": "MODERATE",
        "summary": "测试透传",
        "action": "加多",
        "entry_ok": True,
        "position_size_hint": "50%",
        "leverage_hint": 5,
        "key_drivers": [],
        "risks": [],
    })
    assert normalized["action"] == "加多"
    assert normalized["entry_ok"] is True
    assert normalized["position_size_hint"] == "50%"
    assert normalized["leverage_hint"] == 5


def test_decision_committee_runs_roles_with_fake_llm():
    class FakeLLM:
        def __init__(self):
            self.calls = []

        def chat(self, system_prompt=None, prompt=None, usage_tag=""):
            self.calls.append(usage_tag)
            if usage_tag == "[bull]":
                return """
                {
                  "side": "bull",
                  "thesis": "多头有技术修复机会",
                  "confidence": 62,
                  "evidence": [
                    {"factor": "RSI 4H=31 接近超卖", "weight": "high", "source": "technical"},
                    {"factor": "资金费率 0.01% 未过热", "weight": "medium", "source": "derivatives"}
                  ],
                  "invalidations": ["RSI 跌破 30 后继续走弱"],
                  "best_action": "等待入场"
                }
                """
            if usage_tag == "[bear]":
                return """
                {
                  "side": "bear",
                  "thesis": "空头仍有趋势压力",
                  "confidence": 58,
                  "evidence": [
                    {"factor": "MACD 4H=dead_cross", "weight": "high", "source": "technical"},
                    {"factor": "ETF 3d flow=-120000000", "weight": "medium", "source": "flow"}
                  ],
                  "invalidations": ["MACD 重新金叉"],
                  "best_action": "持仓观望"
                }
                """
            if usage_tag == "[risk]":
                return """
                {
                  "entry_ok": false,
                  "risk_level": "high",
                  "position_size_hint": "0%",
                  "blockers": ["多空分歧较大，等待确认"]
                }
                """
            return """
            {
              "trend_regime": "RANGE",
              "volatility_regime": "NORMAL_VOL",
              "bias": "NEUTRAL",
              "confidence": 52,
              "summary": "多空分歧，等待确认",
              "action": "等待入场",
              "entry_ok": false,
              "position_size_hint": "0%",
              "key_drivers": [
                {"factor": "RSI 4H=31 接近超卖", "side": "bull", "weight": "high"},
                {"factor": "MACD 4H=dead_cross", "side": "bear", "weight": "high"},
                {"factor": "ETF 3d flow=-120000000", "side": "bear", "weight": "medium"}
              ],
              "risks": ["若 MACD 重新金叉，空头压力失效"],
              "invalidations": ["突破区间上沿"],
              "horizon": "4H~24H",
              "committee": {"manager_rationale": "风险不允许入场"}
            }
            """

    fake = FakeLLM()
    prompt_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "multi_agent",
        "prompts",
    )
    committee = DecisionCommittee(fake, prompt_dir=prompt_dir, knowledge_files={"test": "rules"})

    result = committee.run(snapshot={"rsi_4h": {"value": 31}}, dynamic_context="")

    assert fake.calls == ["[bull]", "[bear]", "[risk]", "[manager]"]
    assert result["bias"] == "NEUTRAL"
    assert result["entry_ok"] is False
    assert result["committee"]["bull_case"] == "多头有技术修复机会"
    assert result["committee"]["risk_review"].startswith("entry_ok=False")


def test_decision_committee_allows_entry_when_manager_omits_size():
    class FakeLLM:
        def chat(self, system_prompt=None, prompt=None, usage_tag=""):
            if usage_tag == "[bull]":
                return """
                {
                  "side": "bull",
                  "thesis": "趋势多头",
                  "confidence": 70,
                  "evidence": [
                    {"factor": "EMA7>EMA25", "weight": "high", "source": "technical"},
                    {"factor": "恐惧贪婪=25", "weight": "high", "source": "sentiment"}
                  ],
                  "invalidations": ["跌破 EMA25"],
                  "best_action": "加多"
                }
                """
            if usage_tag == "[bear]":
                return """
                {
                  "side": "bear",
                  "thesis": "上方抛压",
                  "confidence": 40,
                  "evidence": [
                    {"factor": "稳定币流出", "weight": "medium", "source": "onchain"}
                  ],
                  "invalidations": ["稳定币转正"],
                  "best_action": "持仓观望"
                }
                """
            if usage_tag == "[risk]":
                return """
                {
                  "entry_ok": true,
                  "risk_level": "medium",
                  "position_size_hint": "25%",
                  "blockers": []
                }
                """
            # Manager 省略 action / position_size_hint（线上真实形态）
            return """
            {
              "trend_regime": "UP_TREND",
              "volatility_regime": "LOW_VOL_COMPRESSION",
              "bias": "LONG",
              "confidence_level": "MODERATE",
              "summary": "均线多头+极度恐惧，允许轻仓",
              "entry_ok": true,
              "key_drivers": [
                {"factor": "EMA7>EMA25", "side": "bull", "weight": "high"},
                {"factor": "恐惧贪婪=25", "side": "bull", "weight": "high"},
                {"factor": "稳定币流出", "side": "bear", "weight": "medium"}
              ],
              "risks": ["缩量收窄后向下突破"],
              "invalidations": ["跌破 EMA25"],
              "horizon": "4H~24H",
              "committee": {"manager_rationale": "顺势轻仓"}
            }
            """

    prompt_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "multi_agent",
        "prompts",
    )
    committee = DecisionCommittee(
        FakeLLM(), prompt_dir=prompt_dir, knowledge_files={"test": "rules"}
    )
    result = committee.run(snapshot={"ma_4h": {"trend": "bullish"}}, dynamic_context="")

    assert result["bias"] == "LONG"
    assert result["entry_ok"] is True
    assert result["action"] == "加多"
    assert result["position_size_hint"] == "25%"


def test_signal_aggregator_respects_committee_entry_gate():
    from core.market_data import market
    from core.signal_aggregator import SignalAggregator, TradingMode

    market.ai_analysis = DataPoint(
        value=80,
        timestamp=datetime.now(),
        source="test",
        raw={
            "bias": "LONG",
            "confidence": 80,
            "action": "加多",
            "summary": "测试多头",
            "entry_ok": False,
            "position_size_hint": "0%",
        },
    )

    result = SignalAggregator().check_long_conditions()

    assert result.mode == TradingMode.IDLE
    assert result.conditions["committee_entry_ok"] is False
    assert "entry_ok=false" in result.reason


def test_signal_aggregator_keeps_legacy_behavior_without_entry_gate():
    from core.market_data import market
    from core.signal_aggregator import SignalAggregator, TradingMode

    market.ai_analysis = DataPoint(
        value=80,
        timestamp=datetime.now(),
        source="test",
        raw={
            "bias": "LONG",
            "confidence": 80,
            "action": "加多",
            "summary": "测试旧版多头",
        },
    )

    result = SignalAggregator().check_long_conditions()

    assert result.mode == TradingMode.LONG
    assert result.conditions["committee_entry_ok"] is True


def test_signal_aggregator_allows_entry_ok_without_action_text():
    from core.market_data import market
    from core.signal_aggregator import SignalAggregator, TradingMode

    market.ai_analysis = DataPoint(
        value=70,
        timestamp=datetime.now(),
        source="test",
        raw={
            "bias": "LONG",
            "confidence": 70,
            "summary": "趋势多头，等待放量突破确认",
            "entry_ok": True,
            "position_size_hint": "50%",
        },
    )

    result = SignalAggregator().check_long_conditions()

    assert result.mode == TradingMode.LONG
    assert result.conditions["entry_guard"] is True
