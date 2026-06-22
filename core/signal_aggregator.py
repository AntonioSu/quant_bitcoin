"""信号聚合器

当前模式: 完全基于 AI 综合研判驱动开仓决策
- AI bias/action/置信度同时允许 → 触发开仓
- AI action=持仓观望/等待入场 或文本含禁止入场语义 → IDLE
- 其余情况 → IDLE
- 做多优先级 > 做空 > 空闲

# [已注释] 传统指标模式:
# - 做空: 聪明钱多空比 < 阈值 + CVD 顶背离
# - 做多: 聪明钱多空比 > 阈值 + CVD 底背离

数据来源: 使用全局 market 实例
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from core.config import TradingConfig, ParameterSet
from core.market_data import market
from multi_agent.schemas import (
    CONFIDENCE_VERY_STRONG_THRESHOLD,
    CONFIDENCE_STRONG_THRESHOLD,
    CONFIDENCE_MODERATE_THRESHOLD,
    CONFIDENCE_CAUTIOUS_THRESHOLD,
)
# from ..indicators.cvd_divergence import DivergenceType  # 传统指标模式需要
from utils import logger


_AI_CONFIDENCE_THRESHOLDS = {
    ParameterSet.CONSERVATIVE: 75,
    ParameterSet.STANDARD: CONFIDENCE_STRONG_THRESHOLD,
    ParameterSet.AGGRESSIVE: CONFIDENCE_MODERATE_THRESHOLD,
}

_OPEN_ACTIONS = {
    "LONG": "加多",
    "SHORT": "加空",
}

_NO_ENTRY_ACTIONS = {"持仓观望", "等待入场", "减仓", "离场"}
_EXIT_ACTIONS = {"离场": 1.0, "减仓": 0.5}

_GENERIC_NO_ENTRY_KEYWORDS = (
    "等待确认",
    "等待入场",
    "持仓观望",
)

_DIRECTIONAL_NO_ENTRY_KEYWORDS = {
    "LONG": (
        "禁止抄底",
        "禁止开多",
        "禁止开新多",
    ),
    "SHORT": (
        "禁止追空",
        "禁止开空",
        "禁止开新空",
        "不可追空",
        "不宜追空",
    ),
}

def _get_ai_signal() -> tuple:
    """获取当前 AI 研判信号

    Returns: (bias, confidence, confidence_level, summary, action, memory_id)
        bias: "LONG" / "SHORT" / "NEUTRAL" / "N/A"(不可用)
        confidence_level: "STRONG" / "MODERATE" / "WEAK"
    """
    if not market.ai_analysis or not market.ai_analysis.raw:
        return "N/A", 0, "WEAK", "AI 研判未就绪", "", None

    raw = market.ai_analysis.raw
    conf = int(raw.get("confidence", 0))
    from multi_agent.schemas import confidence_to_level
    level = raw.get("confidence_level", confidence_to_level(conf))
    return (
        raw.get("bias", "NEUTRAL"),
        conf,
        level,
        raw.get("summary", ""),
        raw.get("action", ""),
        raw.get("_memory_id"),
    )


def _analysis_text(raw: Dict) -> str:
    """Flatten current AI analysis text for entry guard keyword checks."""
    if not raw:
        return ""

    parts = [
        str(raw.get("summary", "")),
        str(raw.get("action", "")),
        " ".join(str(r) for r in raw.get("risks", []) if r),
    ]
    for driver in raw.get("key_drivers", []):
        if isinstance(driver, dict):
            parts.append(str(driver.get("factor", "")))
        else:
            parts.append(str(driver))
    return " ".join(parts)


def _has_directional_no_entry_keyword(raw: Dict, target: str) -> bool:
    text = _analysis_text(raw)
    keywords = (
        *_GENERIC_NO_ENTRY_KEYWORDS,
        *_DIRECTIONAL_NO_ENTRY_KEYWORDS[target],
    )
    return any(keyword in text for keyword in keywords)




class TradingMode(Enum):
    """交易模式"""
    IDLE = "idle"          # 空闲
    SHORT = "short"        # 开仓模式 (做空收租)
    LONG = "long"        # 平仓模式 (抄底做多)


@dataclass
class SignalResult:
    """信号判断结果"""
    mode: TradingMode
    conditions: Dict[str, bool]   # 各条件是否满足
    values: Dict[str, float]      # 各指标实际值
    confidence: float             # 置信度 (0-1)
    reason: str                   # 触发/未触发原因


@dataclass
class ExitSignal:
    """AI 驱动的平仓信号"""
    should_exit: bool
    close_ratio: float            # 1.0=全平, 0.5=减半仓
    reason: str
    ai_action: str                # 原始 AI action
    ai_confidence: int


class SignalAggregator:
    """信号聚合器 — 完全由 AI 综合研判驱动"""
    
    def __init__(
        self, 
        config: Optional[TradingConfig] = None,
    ):
        self.config = config or TradingConfig.get_preset(ParameterSet.STANDARD)
        self._confidence_threshold = _AI_CONFIDENCE_THRESHOLDS.get(
            self.config.preset, 65
        )
        self._last_partial_close_analysis_id: Optional[str] = None
    
    def _check_ai_direction(self, target: str) -> SignalResult:
        """检查 AI 是否给出指定方向的信号

        Args:
            target: "LONG" 或 "SHORT"
        """
        ai_bias, ai_conf, ai_level, ai_summary, ai_action, analysis_id = _get_ai_signal()
        ai_raw = market.ai_analysis.raw if market.ai_analysis and market.ai_analysis.raw else {}

        fg_value = market.fear_greed.value if market.fear_greed else 50
        fr_value = market.funding_rate.value if market.funding_rate else 0
        tt_value = market.top_trader.value if market.top_trader else 1.0
        rsi_signal = market.rsi.signal_type.value if market.rsi else "none"
        cvd_divergence = market.cvd.divergence.value if market.cvd else "none"
        has_committee_gate = "entry_ok" in ai_raw
        committee_entry_ok = bool(ai_raw.get("entry_ok")) if has_committee_gate else True
        position_size_hint = ai_raw.get("position_size_hint")
        leverage_hint = ai_raw.get("leverage_hint")

        values = {
            "fear_greed": fg_value,
            "funding_rate": fr_value,
            "top_trader_ratio": tt_value,
            "ai_bias": ai_bias,
            "ai_confidence": ai_conf,
            "ai_action": ai_action,
            "analysis_id": analysis_id,
            "rsi_signal": rsi_signal,
            "cvd_divergence": cvd_divergence,
            "entry_ok": committee_entry_ok if has_committee_gate else None,
            "position_size_hint": position_size_hint,
            "leverage_hint": leverage_hint,
        }

        mode = TradingMode.LONG if target == "LONG" else TradingMode.SHORT
        direction_match = ai_bias == target
        confidence_enough = ai_conf >= self._confidence_threshold
        action_allows_entry = ai_action == _OPEN_ACTIONS[target]
        no_entry_action = ai_action in _NO_ENTRY_ACTIONS
        no_entry_keyword = _has_directional_no_entry_keyword(ai_raw, target)
        low_confidence_entry = ai_conf < CONFIDENCE_CAUTIOUS_THRESHOLD
        reversal_risk_short = (
            target == "SHORT"
            and ai_conf < CONFIDENCE_STRONG_THRESHOLD
            and (
                fg_value <= 35
                or rsi_signal in ("oversold", "bullish_divergence")
                or cvd_divergence == "bullish"
            )
        )
        entry_guard_ok = (
            action_allows_entry
            and committee_entry_ok
            and not no_entry_action
            and not no_entry_keyword
            and not low_confidence_entry
            and not reversal_risk_short
        )

        conditions = {
            "ai_direction": direction_match,
            "ai_confidence": confidence_enough,
            "ai_action": action_allows_entry,
            "committee_entry_ok": committee_entry_ok,
            "entry_guard": entry_guard_ok,
        }

        all_green = direction_match and confidence_enough and entry_guard_ok
        if all_green:
            tag = "⚔️ 做多" if target == "LONG" else "🛡️ 做空"
            reason = (
                f"{tag}模式触发: AI 研判 {ai_bias} {ai_level} "
                f"({ai_conf}%, action={ai_action}) "
                f"| {ai_summary}"
            )
            logger.info(reason)
        else:
            parts = []
            if not direction_match:
                parts.append(f"AI 方向={ai_bias} (需要 {target})")
            if not confidence_enough:
                parts.append(f"置信度 {ai_level} ({ai_conf}%) < 阈值")
            if not action_allows_entry:
                parts.append(f"action={ai_action or '空'} (需要 {_OPEN_ACTIONS[target]})")
            if has_committee_gate and not committee_entry_ok:
                parts.append(f"决策委员会 entry_ok=false (仓位建议={position_size_hint or '0%'})")
            if no_entry_keyword:
                parts.append("研判文本含禁止/等待入场语义")
            if low_confidence_entry:
                parts.append(f"置信度 < {CONFIDENCE_MODERATE_THRESHOLD}，仅允许观望")
            if reversal_risk_short:
                parts.append("低置信空单遇恐惧/反转风险，禁止追空")
            reason = f"{'做多' if target == 'LONG' else '做空'}模式未触发: {', '.join(parts)}"

        return SignalResult(
            mode=mode if all_green else TradingMode.IDLE,
            conditions=conditions,
            values=values,
            confidence=ai_conf / 100.0 if all_green else 0.0,
            reason=reason,
        )

    def check_long_conditions(self) -> SignalResult:
        return self._check_ai_direction("LONG")

    def check_short_conditions(self) -> SignalResult:
        return self._check_ai_direction("SHORT")

    def evaluate_exit(self, current_direction: str) -> ExitSignal:
        """检查 AI 是否建议平仓/减仓（持仓时调用）

        Args:
            current_direction: "LONG" 或 "SHORT"
        Returns:
            ExitSignal
        """
        ai_bias, ai_conf, ai_level, ai_summary, ai_action, analysis_id = _get_ai_signal()

        if ai_bias == "N/A":
            return ExitSignal(False, 0.0, "AI 研判未就绪", "", 0)

        close_ratio = _EXIT_ACTIONS.get(ai_action, 0.0)
        if close_ratio > 0:
            guard_id = analysis_id or "_neutral_no_id"
            if close_ratio < 1.0 and guard_id == self._last_partial_close_analysis_id:
                return ExitSignal(
                    False, 0.0,
                    f"减仓已执行，等待下次 AI 刷新 (analysis_id={guard_id})",
                    ai_action, ai_conf,
                )
            reason = (
                f"AI 建议{ai_action}: {ai_summary} "
                f"(bias={ai_bias}, {ai_level})"
            )
            logger.info(f"🚪 {reason}")
            if close_ratio < 1.0:
                self._last_partial_close_analysis_id = guard_id
            else:
                self._last_partial_close_analysis_id = None
            return ExitSignal(True, close_ratio, reason, ai_action, ai_conf)

        opposite = "SHORT" if current_direction == "LONG" else "LONG"
        if ai_bias == opposite and ai_conf >= CONFIDENCE_STRONG_THRESHOLD:
            self._last_partial_close_analysis_id = None
            reason = (
                f"AI 方向反转 {current_direction}→{ai_bias} "
                f"({ai_level}, action={ai_action}): {ai_summary}"
            )
            logger.info(f"🔄 {reason}")
            return ExitSignal(True, 1.0, reason, ai_action, ai_conf)

        return ExitSignal(
            False, 0.0,
            f"AI 未建议平仓 (bias={ai_bias}, action={ai_action}, {ai_level})",
            ai_action, ai_conf,
        )

    def evaluate(self, current_mode: TradingMode = TradingMode.IDLE) -> SignalResult:
        """综合评估: 做多优先 > 做空 > 空闲"""
        long_result = self.check_long_conditions()
        if long_result.mode == TradingMode.LONG:
            return long_result

        short_result = self.check_short_conditions()
        if short_result.mode == TradingMode.SHORT:
            return short_result
        
        ai_bias, ai_conf, ai_level, ai_summary, ai_action, analysis_id = _get_ai_signal()
        ai_raw = market.ai_analysis.raw if market.ai_analysis and market.ai_analysis.raw else {}
        return SignalResult(
            mode=TradingMode.IDLE,
            conditions={},
            values={
                "ai_bias": ai_bias,
                "ai_confidence": ai_conf,
                "ai_confidence_level": ai_level,
                "ai_action": ai_action,
                "analysis_id": analysis_id,
                "entry_ok": ai_raw.get("entry_ok"),
                "position_size_hint": ai_raw.get("position_size_hint"),
            },
            confidence=0.0,
            reason=f"无交易信号 (AI: {ai_bias} {ai_level})"
                   if ai_bias != "N/A"
                   else "无交易信号 (AI 研判未就绪)",
        )
