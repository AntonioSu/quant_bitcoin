"""Sequential multi-agent committee for BTC market decisions."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from pydantic import ValidationError

from multi_agent.schemas import CommitteeDecision, DebateCase, RiskReview
from utils import logger
from utils.common_utils import read_file_prompt
from utils.llm_client import LLMClient


class DecisionCommitteeError(Exception):
    """Raised when the final manager output cannot be used."""


class DecisionCommittee:
    """Run bull, bear, risk and manager roles in a conservative sequence."""

    def __init__(
        self,
        llm: LLMClient,
        prompt_dir: Optional[str] = None,
        static_context: str = "",
    ):
        self.llm = llm
        self.prompt_dir = prompt_dir or os.path.join(
            os.path.dirname(__file__), "prompts"
        )
        self.static_context = static_context

    def run(self, snapshot: Dict[str, Any], dynamic_context: str = "") -> Dict[str, Any]:
        """Return a MarketAnalyzer-compatible JSON dict."""
        bull = self._run_debate_role("bull", snapshot, dynamic_context)
        bear = self._run_debate_role("bear", snapshot, dynamic_context)
        risk = self._run_risk_role(snapshot, dynamic_context, bull, bear)
        decision = self._run_manager_role(snapshot, dynamic_context, bull, bear, risk)

        if not decision.committee.bull_case:
            decision.committee.bull_case = bull.thesis
        if not decision.committee.bear_case:
            decision.committee.bear_case = bear.thesis
        if not decision.committee.risk_review:
            decision.committee.risk_review = self._summarize_risk(risk)

        result = decision.to_analysis_dict()
        logger.info(
            "🤖 决策委员会: %s (%s%%), action=%s, entry_ok=%s",
            result.get("bias"),
            result.get("confidence"),
            result.get("action"),
            result.get("entry_ok"),
        )
        return result

    def _run_debate_role(
        self,
        side: str,
        snapshot: Dict[str, Any],
        dynamic_context: str,
    ) -> DebateCase:
        role_name = "bull" if side == "bull" else "bear"
        try:
            resp = self.llm.chat(
                system_prompt=self._system_prompt(f"{role_name}_researcher.md"),
                prompt=self._debate_prompt(role_name, snapshot, dynamic_context),
                usage_tag=f"[{role_name}]",
            )
            data = self._parse_json(resp)
            case = DebateCase.model_validate(data)
            if case.side != role_name:
                case.side = role_name
                for item in case.evidence:
                    item.side = role_name
            return case
        except Exception as e:
            logger.warning(f"🤖 {role_name} researcher 失败，使用保守兜底: {e}")
            return DebateCase.fallback(role_name, str(e))

    def _run_risk_role(
        self,
        snapshot: Dict[str, Any],
        dynamic_context: str,
        bull: DebateCase,
        bear: DebateCase,
    ) -> RiskReview:
        try:
            resp = self.llm.chat(
                system_prompt=self._system_prompt("risk_reviewer.md"),
                prompt=self._risk_prompt(snapshot, dynamic_context, bull, bear),
                usage_tag="[risk]",
            )
            data = self._parse_json(resp)
            return RiskReview.model_validate(data)
        except Exception as e:
            logger.warning(f"🤖 risk reviewer 失败，默认阻断开仓: {e}")
            return RiskReview.fallback(str(e))

    def _run_manager_role(
        self,
        snapshot: Dict[str, Any],
        dynamic_context: str,
        bull: DebateCase,
        bear: DebateCase,
        risk: RiskReview,
    ) -> CommitteeDecision:
        try:
            resp = self.llm.chat(
                system_prompt=self._system_prompt("decision_manager.md"),
                prompt=self._manager_prompt(snapshot, dynamic_context, bull, bear, risk),
                usage_tag="[manager]",
            )
            data = self._parse_json(resp)
            return CommitteeDecision.model_validate(data)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError, ValueError) as e:
            logger.warning(f"🤖 decision manager 输出不可用: {e}")
            raise DecisionCommitteeError(str(e)) from e
        except Exception as e:
            logger.warning(f"🤖 decision manager 调用失败: {e}")
            raise DecisionCommitteeError(str(e)) from e

    def _system_prompt(self, prompt_name: str) -> str:
        prompt = read_file_prompt(os.path.join(self.prompt_dir, prompt_name))
        if self.static_context:
            prompt += "\n\n## 共享交易规则与知识库\n" + self.static_context
        return prompt

    @staticmethod
    def _debate_prompt(
        side: str,
        snapshot: Dict[str, Any],
        dynamic_context: str,
    ) -> str:
        side_cn = "多头" if side == "bull" else "空头"
        return (
            f"请作为 {side_cn} researcher，只构造本方论证，不要输出最终交易结论。\n\n"
            "## 当前 BTC 市场快照\n"
            f"```json\n{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n```\n\n"
            f"{dynamic_context}"
        )

    @staticmethod
    def _risk_prompt(
        snapshot: Dict[str, Any],
        dynamic_context: str,
        bull: DebateCase,
        bear: DebateCase,
    ) -> str:
        debate = {
            "bull": bull.model_dump(mode="json"),
            "bear": bear.model_dump(mode="json"),
        }
        return (
            "请只做风险审查，不判断最终多空方向。\n\n"
            "## 当前 BTC 市场快照\n"
            f"```json\n{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n```\n\n"
            "## 多空双方论证\n"
            f"```json\n{json.dumps(debate, ensure_ascii=False, indent=2)}\n```\n\n"
            f"{dynamic_context}"
        )

    @staticmethod
    def _manager_prompt(
        snapshot: Dict[str, Any],
        dynamic_context: str,
        bull: DebateCase,
        bear: DebateCase,
        risk: RiskReview,
    ) -> str:
        committee_inputs = {
            "bull": bull.model_dump(mode="json"),
            "bear": bear.model_dump(mode="json"),
            "risk": risk.model_dump(mode="json"),
        }
        return (
            "请综合多头、空头和风险审查，输出最终 MarketAnalyzer 兼容 JSON。\n\n"
            "## 当前 BTC 市场快照\n"
            f"```json\n{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n```\n\n"
            "## 决策委员会输入\n"
            f"```json\n{json.dumps(committee_inputs, ensure_ascii=False, indent=2)}\n```\n\n"
            f"{dynamic_context}"
        )

    @staticmethod
    def _summarize_risk(risk: RiskReview) -> str:
        blockers = "；".join(risk.blockers[:2]) if risk.blockers else "无明确阻断项"
        return (
            f"entry_ok={risk.entry_ok}, risk_level={risk.risk_level}, "
            f"position={risk.position_size_hint}, blockers={blockers}"
        )

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if "```json" in raw:
            raw = raw.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in raw:
            raw = raw.split("```", 1)[1].split("```", 1)[0]
        else:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                raw = raw[start:end + 1]

        parsed = json.loads(raw.strip())
        if not isinstance(parsed, dict):
            raise TypeError("LLM output is not a JSON object")
        return parsed
