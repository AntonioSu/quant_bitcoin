"""Sequential multi-agent committee for BTC market decisions."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Sequence

from pydantic import ValidationError

from multi_agent.schemas import CommitteeDecision, DebateCase, RiskReview
from utils import logger
from utils.common_utils import parse_llm_json, read_file_prompt
from utils.llm_client import LLMClient


_ROLE_KNOWLEDGE: Dict[str, Optional[Sequence[str]]] = {
    "bull_researcher.md": ("indicators/indicator_guide.md",),
    "bear_researcher.md": ("indicators/indicator_guide.md",),
    "risk_reviewer.md": (
        "regimes/volatility_regime.md",
        "regimes/regime_matrix.md",
    ),
    "decision_manager.md": None,  # all files
}


class DecisionCommitteeError(Exception):
    """Raised when the final manager output cannot be used."""


_ROLE_PROMPTS = (
    "bull_researcher.md",
    "bear_researcher.md",
    "risk_reviewer.md",
    "decision_manager.md",
)


class DecisionCommittee:
    """Run bull, bear, risk and manager roles in a conservative sequence."""

    def __init__(
        self,
        llm: LLMClient,
        prompt_dir: Optional[str] = None,
        knowledge_files: Optional[Dict[str, str]] = None,
    ):
        self.llm = llm
        self.prompt_dir = prompt_dir or os.path.join(
            os.path.dirname(__file__), "prompts"
        )
        self._knowledge_files = knowledge_files or {}
        self._system_prompts = self._build_system_prompts()

    def run(self, snapshot: Dict[str, Any], dynamic_context: str = "") -> Dict[str, Any]:
        """Return a MarketAnalyzer-compatible JSON dict (pure signal, no position awareness)."""
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
            "🤖 决策委员会: %s %s (%s%%), entry_ok=%s",
            result.get("bias"),
            result.get("confidence_level"),
            result.get("confidence"),
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
            data = self._merge_risk_into_manager_payload(data, risk)
            return CommitteeDecision.model_validate(data)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError, ValueError) as e:
            logger.warning(f"🤖 decision manager 输出不可用: {e}")
            raise DecisionCommitteeError(str(e)) from e
        except Exception as e:
            logger.warning(f"🤖 decision manager 调用失败: {e}")
            raise DecisionCommitteeError(str(e)) from e

    def _system_prompt(self, prompt_name: str) -> str:
        cached = self._system_prompts.get(prompt_name)
        if cached is not None:
            return cached
        return self._compose_system_prompt(prompt_name)

    def _build_system_prompts(self) -> Dict[str, str]:
        """Pre-compose stable system prompts with knowledge prefix first."""
        return {
            prompt_name: self._compose_system_prompt(prompt_name)
            for prompt_name in _ROLE_PROMPTS
        }

    @staticmethod
    def _join_system_prompt(*, knowledge: str, role_prompt: str) -> str:
        """Knowledge first so identical prefixes can hit LLM prompt cache."""
        if knowledge:
            return knowledge + "\n\n## 角色指令\n" + role_prompt
        return role_prompt

    def _compose_system_prompt(self, prompt_name: str) -> str:
        knowledge = self._knowledge_for_role(prompt_name)
        role_prompt = read_file_prompt(os.path.join(self.prompt_dir, prompt_name))
        return self._join_system_prompt(knowledge=knowledge, role_prompt=role_prompt)

    def _knowledge_for_role(self, prompt_name: str) -> str:
        if not self._knowledge_files:
            return ""
        needed = _ROLE_KNOWLEDGE.get(prompt_name)
        if needed is None:
            keys = sorted(self._knowledge_files)
        else:
            keys = sorted(k for k in needed if k in self._knowledge_files)
        return "\n\n".join(self._knowledge_files[k] for k in keys)

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
    def _merge_risk_into_manager_payload(
        data: Dict[str, Any],
        risk: RiskReview,
    ) -> Dict[str, Any]:
        """补全 Manager 省略字段，并落实 Risk Reviewer 的否决权。"""
        payload = dict(data or {})

        if "entry_ok" not in payload:
            payload["entry_ok"] = risk.entry_ok
        elif not risk.entry_ok:
            # 风险审查否决优先：不允许 Manager 覆盖为可入场
            payload["entry_ok"] = False

        size = str(payload.get("position_size_hint") or "").strip()
        if payload.get("entry_ok") and (not size or size == "0%"):
            if risk.entry_ok and risk.position_size_hint != "0%":
                payload["position_size_hint"] = risk.position_size_hint

        if not payload.get("entry_ok"):
            payload["position_size_hint"] = "0%"

        return payload

    @staticmethod
    def _summarize_risk(risk: RiskReview) -> str:
        blockers = "；".join(risk.blockers[:2]) if risk.blockers else "无明确阻断项"
        return (
            f"entry_ok={risk.entry_ok}, risk_level={risk.risk_level}, "
            f"position={risk.position_size_hint}, blockers={blockers}"
        )

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        result = parse_llm_json(text, strict=True)
        assert result is not None
        return result
