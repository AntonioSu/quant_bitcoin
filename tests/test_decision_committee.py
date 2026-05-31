#!/usr/bin/env python3
"""Tests for decision committee schemas and entry gate integration."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_sources.base import DataPoint
from multi_agent.decision_committee import DecisionCommittee
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
        "confidence": 55,
        "action": "加空",
        "entry_ok": True,
        "position_size_hint": "25%",
    })

    assert decision.action == "等待入场"
    assert decision.entry_ok is False


def test_risk_review_zero_position_blocks_entry():
    review = RiskReview.model_validate({
        "entry_ok": True,
        "risk_level": "medium",
        "allowed_actions": ["加多", "持仓观望"],
        "position_size_hint": "0%",
    })

    assert review.entry_ok is False
    assert "加多" not in review.allowed_actions


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
                  "allowed_actions": ["持仓观望", "等待入场"],
                  "position_size_hint": "0%",
                  "blockers": ["多空分歧较大，等待确认"],
                  "risk_controls": ["突破后再评估"]
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
    committee = DecisionCommittee(fake, prompt_dir=prompt_dir, static_context="rules")

    result = committee.run(snapshot={"rsi_4h": {"value": 31}}, dynamic_context="")

    assert fake.calls == ["[bull]", "[bear]", "[risk]", "[manager]"]
    assert result["bias"] == "NEUTRAL"
    assert result["entry_ok"] is False
    assert result["committee"]["bull_case"] == "多头有技术修复机会"
    assert result["committee"]["risk_review"].startswith("entry_ok=False")


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
