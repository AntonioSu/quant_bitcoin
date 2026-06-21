"""Structured schemas for the BTC decision committee."""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ACTIONS = {"加多", "加空", "持仓观望", "等待入场", "减仓", "离场"}
OPEN_ACTIONS = {"加多", "加空"}
WEIGHTS = {"high", "medium", "low"}
SIDES = {"bull", "bear"}
SOURCES = {
    "technical",
    "flow",
    "sentiment",
    "derivatives",
    "macro",
    "onchain",
    "news",
    "risk",
    "other",
}
POSITION_HINTS = {"0%", "25%", "50%", "75%", "100%"}
LEVERAGE_HINTS = {1, 2, 3, 5, 10, 20}
RISK_LEVELS = {"low", "medium", "high", "extreme"}
TREND_REGIMES = {"UP_TREND", "DOWN_TREND", "RANGE", "UNCLEAR"}
VOL_REGIMES = {
    "LOW_VOL_COMPRESSION",
    "NORMAL_VOL",
    "BREAKOUT_EXPANSION",
    "HIGH_VOL_EXTREME",
}


def _clamp_confidence(value: Any) -> int:
    try:
        confidence = int(value)
    except (TypeError, ValueError):
        confidence = 0
    return max(0, min(99, confidence))


def normalize_action(value: Any, default: str = "持仓观望") -> str:
    action = str(value or "").strip()
    return action if action in ACTIONS else default


def normalize_position_hint(value: Any) -> str:
    text = str(value or "0%").strip()
    if text in POSITION_HINTS:
        return text
    if text.isdigit() and f"{text}%" in POSITION_HINTS:
        return f"{text}%"
    return "0%"


def normalize_leverage_hint(value: Any) -> int:
    try:
        lev = int(value)
    except (TypeError, ValueError):
        return 5
    if lev in LEVERAGE_HINTS:
        return lev
    return min(LEVERAGE_HINTS, key=lambda x: abs(x - lev))


def normalize_driver_list(items: Any, limit: int = 5) -> List["Driver"]:
    if not isinstance(items, list):
        return []

    drivers: List[Driver] = []
    for item in items:
        if len(drivers) >= limit:
            break
        if isinstance(item, str):
            factor = item.strip()
            if factor:
                drivers.append(Driver(factor=factor))
            continue
        if isinstance(item, dict):
            try:
                drivers.append(Driver.model_validate(item))
            except Exception:
                continue
    return drivers


class Driver(BaseModel):
    """A concise evidence item used by committee outputs."""

    factor: str = Field(default="")
    side: Literal["bull", "bear"] = "bull"
    weight: Literal["high", "medium", "low"] = "medium"
    source: str = "other"

    @field_validator("factor", mode="before")
    @classmethod
    def _normalize_factor(cls, value: Any) -> str:
        return str(value or "").strip()[:240]

    @field_validator("side", mode="before")
    @classmethod
    def _normalize_side(cls, value: Any) -> str:
        side = str(value or "bull").strip().lower()
        return side if side in SIDES else "bull"

    @field_validator("weight", mode="before")
    @classmethod
    def _normalize_weight(cls, value: Any) -> str:
        weight = str(value or "medium").strip().lower()
        return weight if weight in WEIGHTS else "medium"

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, value: Any) -> str:
        source = str(value or "other").strip().lower()
        return source if source in SOURCES else "other"


class DebateCase(BaseModel):
    """Bull or bear argument produced before the final decision."""

    side: Literal["bull", "bear"]
    thesis: str = ""
    confidence: int = 0
    evidence: List[Driver] = Field(default_factory=list)
    invalidations: List[str] = Field(default_factory=list)
    best_action: str = "持仓观望"

    @field_validator("side", mode="before")
    @classmethod
    def _normalize_side(cls, value: Any) -> str:
        side = str(value or "bull").strip().lower()
        return side if side in SIDES else "bull"

    @field_validator("thesis", mode="before")
    @classmethod
    def _normalize_thesis(cls, value: Any) -> str:
        return str(value or "").strip()[:400]

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> int:
        return _clamp_confidence(value)

    @field_validator("evidence", mode="before")
    @classmethod
    def _normalize_evidence(cls, value: Any) -> List[Driver]:
        return normalize_driver_list(value, limit=5)

    @field_validator("invalidations", mode="before")
    @classmethod
    def _normalize_invalidations(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:180] for item in value if str(item).strip()][:5]

    @field_validator("best_action", mode="before")
    @classmethod
    def _normalize_best_action(cls, value: Any) -> str:
        return normalize_action(value)

    @model_validator(mode="after")
    def _align_evidence_side(self) -> "DebateCase":
        for item in self.evidence:
            item.side = self.side
        return self

    @classmethod
    def fallback(cls, side: Literal["bull", "bear"], reason: str) -> "DebateCase":
        return cls(
            side=side,
            thesis=f"{'多头' if side == 'bull' else '空头'}论证失败: {reason}",
            confidence=0,
            evidence=[],
            invalidations=[],
            best_action="持仓观望",
        )


class RiskReview(BaseModel):
    """Risk gate output between debate and final decision."""

    entry_ok: bool = False
    risk_level: Literal["low", "medium", "high", "extreme"] = "high"
    allowed_actions: List[str] = Field(default_factory=lambda: ["持仓观望"])
    position_size_hint: str = "0%"
    max_leverage: int = 5
    blockers: List[str] = Field(default_factory=list)
    risk_controls: List[str] = Field(default_factory=list)

    @field_validator("risk_level", mode="before")
    @classmethod
    def _normalize_risk_level(cls, value: Any) -> str:
        level = str(value or "high").strip().lower()
        return level if level in RISK_LEVELS else "high"

    @field_validator("allowed_actions", mode="before")
    @classmethod
    def _normalize_allowed_actions(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return ["持仓观望"]
        actions = [normalize_action(item) for item in value]
        return list(dict.fromkeys(actions)) or ["持仓观望"]

    @field_validator("position_size_hint", mode="before")
    @classmethod
    def _normalize_position_size_hint(cls, value: Any) -> str:
        return normalize_position_hint(value)

    @field_validator("max_leverage", mode="before")
    @classmethod
    def _normalize_max_leverage(cls, value: Any) -> int:
        try:
            lev = int(value)
        except (TypeError, ValueError):
            return 5
        return max(1, min(20, lev))

    @field_validator("blockers", "risk_controls", mode="before")
    @classmethod
    def _normalize_text_list(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:220] for item in value if str(item).strip()][:5]

    @model_validator(mode="after")
    def _enforce_entry_rules(self) -> "RiskReview":
        if self.risk_level == "extreme" or self.position_size_hint == "0%":
            self.entry_ok = False
        if not self.entry_ok:
            self.allowed_actions = [
                action for action in self.allowed_actions if action not in OPEN_ACTIONS
            ] or ["持仓观望"]
        if self.risk_level == "extreme":
            self.max_leverage = min(self.max_leverage, 2)
        elif self.risk_level == "high":
            self.max_leverage = min(self.max_leverage, 5)
        return self

    @classmethod
    def fallback(cls, reason: str) -> "RiskReview":
        return cls(
            entry_ok=False,
            risk_level="high",
            allowed_actions=["持仓观望", "等待入场"],
            position_size_hint="0%",
            blockers=[f"风险审查失败，默认阻断开仓: {reason}"[:220]],
            risk_controls=[],
        )


class CommitteeSummary(BaseModel):
    bull_case: str = ""
    bear_case: str = ""
    risk_review: str = ""
    manager_rationale: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> str:
        return str(value or "").strip()[:500]


class CommitteeDecision(BaseModel):
    """Final output kept compatible with MarketAnalyzer raw payload."""

    trend_regime: str = "UNCLEAR"
    volatility_regime: str = "NORMAL_VOL"
    bias: Literal["LONG", "SHORT", "NEUTRAL"] = "NEUTRAL"
    confidence: int = 0
    summary: str = ""
    action: str = "持仓观望"
    entry_ok: bool = False
    position_size_hint: str = "0%"
    leverage_hint: int = 5
    key_drivers: List[Driver] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    invalidations: List[str] = Field(default_factory=list)
    horizon: str = "4H~24H"
    committee: CommitteeSummary = Field(default_factory=CommitteeSummary)

    @field_validator("trend_regime", mode="before")
    @classmethod
    def _normalize_trend(cls, value: Any) -> str:
        trend = str(value or "UNCLEAR").strip().upper()
        return trend if trend in TREND_REGIMES else "UNCLEAR"

    @field_validator("volatility_regime", mode="before")
    @classmethod
    def _normalize_volatility(cls, value: Any) -> str:
        vol = str(value or "NORMAL_VOL").strip().upper()
        return vol if vol in VOL_REGIMES else "NORMAL_VOL"

    @field_validator("bias", mode="before")
    @classmethod
    def _normalize_bias(cls, value: Any) -> str:
        bias = str(value or "NEUTRAL").strip().upper()
        return bias if bias in {"LONG", "SHORT", "NEUTRAL"} else "NEUTRAL"

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> int:
        return _clamp_confidence(value)

    @field_validator("summary", mode="before")
    @classmethod
    def _normalize_summary(cls, value: Any) -> str:
        return str(value or "").strip()[:80]

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, value: Any) -> str:
        return normalize_action(value)

    @field_validator("position_size_hint", mode="before")
    @classmethod
    def _normalize_position_size_hint(cls, value: Any) -> str:
        return normalize_position_hint(value)

    @field_validator("leverage_hint", mode="before")
    @classmethod
    def _normalize_leverage_hint(cls, value: Any) -> int:
        return normalize_leverage_hint(value)

    @field_validator("key_drivers", mode="before")
    @classmethod
    def _normalize_key_drivers(cls, value: Any) -> List[Driver]:
        return normalize_driver_list(value, limit=5)

    @field_validator("risks", "invalidations", mode="before")
    @classmethod
    def _normalize_text_list(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:220] for item in value if str(item).strip()][:5]

    @field_validator("horizon", mode="before")
    @classmethod
    def _normalize_horizon(cls, value: Any) -> str:
        return str(value or "4H~24H").strip()[:40] or "4H~24H"

    @model_validator(mode="after")
    def _enforce_trade_rules(self) -> "CommitteeDecision":
        if self.confidence < 60 and self.action in OPEN_ACTIONS:
            self.action = "等待入场"
            self.entry_ok = False
            self.position_size_hint = "0%"

        if not self.entry_ok and self.action in OPEN_ACTIONS:
            self.action = "等待入场"
            self.position_size_hint = "0%"

        if self.position_size_hint == "0%":
            self.entry_ok = False

        if self.action == "加多":
            self.bias = "LONG"
        elif self.action == "加空":
            self.bias = "SHORT"

        if self.bias == "NEUTRAL" and self.action in OPEN_ACTIONS:
            self.action = "持仓观望"
            self.entry_ok = False
            self.position_size_hint = "0%"

        if self.confidence < 55:
            self.leverage_hint = min(self.leverage_hint, 2)
        elif self.confidence < 65:
            self.leverage_hint = min(self.leverage_hint, 5)

        if self.volatility_regime == "HIGH_VOL_EXTREME":
            self.leverage_hint = min(self.leverage_hint, 3)

        return self

    def to_analysis_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def fallback(cls, reason: str) -> "CommitteeDecision":
        return cls(
            bias="NEUTRAL",
            confidence=0,
            summary=f"委员会研判失败: {reason}"[:80],
            action="持仓观望",
            entry_ok=False,
            position_size_hint="0%",
            key_drivers=[],
            risks=[f"委员会输出异常，已按中性处理: {reason}"[:220]],
            invalidations=[],
            horizon="4H~24H",
            trend_regime="UNCLEAR",
            volatility_regime="NORMAL_VOL",
            committee=CommitteeSummary(manager_rationale="fallback to neutral"),
        )
