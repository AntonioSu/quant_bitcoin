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
# from ..indicators.cvd_divergence import DivergenceType  # 传统指标模式需要
from utils import logger


# 各参数组对应的 AI 置信度门槛
_AI_CONFIDENCE_THRESHOLDS = {
    ParameterSet.CONSERVATIVE: 75,
    ParameterSet.STANDARD: 65,
    ParameterSet.AGGRESSIVE: 55,
}

_OPEN_ACTIONS = {
    "LONG": "加多",
    "SHORT": "加空",
}

_NO_ENTRY_ACTIONS = {"持仓观望", "等待入场", "减仓", "离场"}

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

    Returns: (bias: str, confidence: int, summary: str, action: str, memory_id: str)
        bias: "LONG" / "SHORT" / "NEUTRAL" / "N/A"(不可用)
    """
    if not market.ai_analysis or not market.ai_analysis.raw:
        return "N/A", 0, "AI 研判未就绪", "", None

    raw = market.ai_analysis.raw
    return (
        raw.get("bias", "NEUTRAL"),
        int(raw.get("confidence", 0)),
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


# ── [已注释] 传统指标辅助函数 ──────────────────────────────────
# def _ai_not_contrary(target_mode: str) -> tuple:
#     """检查 AI 研判是否与目标方向矛盾
#     规则:
#       - AI 方向一致或 NEUTRAL → 通过
#       - AI 方向相反 → 不通过
#       - AI 不可用（未刷新/报错） → 视为 NEUTRAL，通过
#     Returns: (passed: bool, ai_bias: str, ai_confidence: int)
#     """
#     if not market.ai_analysis or not market.ai_analysis.raw:
#         return True, "N/A", 0
#     raw = market.ai_analysis.raw
#     bias = raw.get("bias", "NEUTRAL")
#     confidence = raw.get("confidence", 0)
#     if bias == "NEUTRAL":
#         return True, bias, confidence
#     if target_mode == "LONG":
#         return bias != "SHORT", bias, confidence
#     else:
#         return bias != "LONG", bias, confidence
#
#
# def _build_result(target_mode, conditions, values, ok_reason, fail_prefix):
#     all_green = all(conditions.values())
#     confidence = sum(conditions.values()) / len(conditions)
#     if all_green:
#         logger.info(ok_reason)
#         reason = ok_reason
#     else:
#         failed = [k for k, v in conditions.items() if not v]
#         reason = f"{fail_prefix}: {', '.join(failed)} 条件不满足"
#         logger.debug(reason)
#     return SignalResult(
#         mode=target_mode if all_green else TradingMode.IDLE,
#         conditions=conditions, values=values,
#         confidence=confidence, reason=reason,
#     )
# ── [已注释] 传统指标辅助函数 END ──────────────────────────────


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
    
    def _check_ai_direction(self, target: str) -> SignalResult:
        """检查 AI 是否给出指定方向的信号

        Args:
            target: "LONG" 或 "SHORT"
        """
        ai_bias, ai_conf, ai_summary, ai_action, analysis_id = _get_ai_signal()
        ai_raw = market.ai_analysis.raw if market.ai_analysis and market.ai_analysis.raw else {}

        fg_value = market.fear_greed.value if market.fear_greed else 50
        fr_value = market.funding_rate.value if market.funding_rate else 0
        tt_value = market.top_trader.value if market.top_trader else 1.0
        rsi_signal = market.rsi.signal_type.value if market.rsi else "none"
        cvd_divergence = market.cvd.divergence.value if market.cvd else "none"

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
        }

        mode = TradingMode.LONG if target == "LONG" else TradingMode.SHORT
        direction_match = ai_bias == target
        confidence_enough = ai_conf >= self._confidence_threshold
        action_allows_entry = ai_action == _OPEN_ACTIONS[target]
        no_entry_action = ai_action in _NO_ENTRY_ACTIONS
        no_entry_keyword = _has_directional_no_entry_keyword(ai_raw, target)
        low_confidence_entry = ai_conf < 60
        reversal_risk_short = (
            target == "SHORT"
            and ai_conf < 65
            and (
                fg_value <= 35
                or rsi_signal in ("oversold", "bullish_divergence")
                or cvd_divergence == "bullish"
            )
        )
        entry_guard_ok = (
            action_allows_entry
            and not no_entry_action
            and not no_entry_keyword
            and not low_confidence_entry
            and not reversal_risk_short
        )

        conditions = {
            "ai_direction": direction_match,
            "ai_confidence": confidence_enough,
            "ai_action": action_allows_entry,
            "entry_guard": entry_guard_ok,
        }

        all_green = direction_match and confidence_enough and entry_guard_ok
        if all_green:
            tag = "⚔️ 做多" if target == "LONG" else "🛡️ 做空"
            reason = (
                f"{tag}模式触发: AI 研判 {ai_bias} "
                f"(置信度 {ai_conf}% >= {self._confidence_threshold}%, action={ai_action}) "
                f"| {ai_summary}"
            )
            logger.info(reason)
        else:
            parts = []
            if not direction_match:
                parts.append(f"AI 方向={ai_bias} (需要 {target})")
            if not confidence_enough:
                parts.append(f"置信度 {ai_conf}% < {self._confidence_threshold}%")
            if not action_allows_entry:
                parts.append(f"action={ai_action or '空'} (需要 {_OPEN_ACTIONS[target]})")
            if no_entry_keyword:
                parts.append("研判文本含禁止/等待入场语义")
            if low_confidence_entry:
                parts.append("置信度 < 60，仅允许观望")
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

    # ── [已注释] 传统指标版 check_long_conditions ─────────────
    # def check_long_conditions(self) -> SignalResult:
    #     cfg = self.config.long
    #     fg_value = market.fear_greed.value if market.fear_greed else 50
    #     tt_value = market.top_trader.value if market.top_trader else 1.0
    #     if market.cvd:
    #         cvd_bullish = market.cvd.is_valid_signal and market.cvd.divergence == DivergenceType.BULLISH
    #         cvd_value = market.cvd.cvd_change_pct
    #         price_change = market.cvd.price_change_pct
    #         divergence_strength = market.cvd.strength
    #         divergence_type = "底背离" if cvd_bullish else "无"
    #     else:
    #         cvd_bullish = False
    #         cvd_value = price_change = divergence_strength = 0.0
    #         divergence_type = "无"
    #     cond_tt = tt_value > cfg.top_trader_ratio_threshold
    #     cond_cvd = cvd_bullish
    #     ai_ok, ai_bias, ai_conf = _ai_not_contrary("LONG")
    #     return _build_result(
    #         TradingMode.LONG,
    #         conditions={
    #             "top_trader_ratio": cond_tt,
    #             "cvd_divergence": cond_cvd,
    #             "ai_not_contrary": ai_ok,
    #         },
    #         values={
    #             "fear_greed": fg_value, "top_trader_ratio": tt_value,
    #             "cvd_change_pct": cvd_value, "price_change_pct": price_change,
    #             "divergence_strength": divergence_strength,
    #             "divergence_type": divergence_type,
    #             "ai_bias": ai_bias, "ai_confidence": ai_conf,
    #         },
    #         ok_reason="⚔️ 做多模式触发: 聪明钱看多 + CVD 底背离 + AI 不矛盾",
    #         fail_prefix="做多模式未触发",
    #     )
    # ── [已注释] 传统指标版 check_long_conditions END ─────────

    def check_short_conditions(self) -> SignalResult:
        return self._check_ai_direction("SHORT")

    # ── [已注释] 传统指标版 check_short_conditions ────────────
    # def check_short_conditions(self) -> SignalResult:
    #     cfg = self.config.short
    #     fg_value = market.fear_greed.value if market.fear_greed else 50
    #     fr_value = market.funding_rate.value if market.funding_rate else 0
    #     tt_value = market.top_trader.value if market.top_trader else 1.0
    #     if market.cvd:
    #         cvd_bearish = market.cvd.is_valid_signal and market.cvd.divergence == DivergenceType.BEARISH
    #         cvd_value = market.cvd.cvd_change_pct
    #         price_change = market.cvd.price_change_pct
    #         divergence_strength = market.cvd.strength
    #         divergence_type = "顶背离" if cvd_bearish else "无"
    #     else:
    #         cvd_bearish = False
    #         cvd_value = price_change = divergence_strength = 0.0
    #         divergence_type = "无"
    #     ai_ok, ai_bias, ai_conf = _ai_not_contrary("SHORT")
    #     return _build_result(
    #         TradingMode.SHORT,
    #         conditions={
    #             "top_trader_ratio": tt_value <= cfg.top_trader_ratio_threshold,
    #             "cvd_divergence": cvd_bearish,
    #             "ai_not_contrary": ai_ok,
    #         },
    #         values={
    #             "fear_greed": fg_value, "funding_rate": fr_value,
    #             "top_trader_ratio": tt_value,
    #             "cvd_change_pct": cvd_value, "price_change_pct": price_change,
    #             "divergence_strength": divergence_strength,
    #             "divergence_type": divergence_type,
    #             "ai_bias": ai_bias, "ai_confidence": ai_conf,
    #         },
    #         ok_reason="🛡️ 做空模式触发: 聪明钱看空 + CVD 顶背离 + AI 不矛盾",
    #         fail_prefix="做空模式未触发",
    #     )
    # ── [已注释] 传统指标版 check_short_conditions END ────────

    def evaluate(self, current_mode: TradingMode = TradingMode.IDLE) -> SignalResult:
        """综合评估: 做多优先 > 做空 > 空闲"""
        long_result = self.check_long_conditions()
        if long_result.mode == TradingMode.LONG:
            return long_result

        short_result = self.check_short_conditions()
        if short_result.mode == TradingMode.SHORT:
            return short_result
        
        ai_bias, ai_conf, ai_summary, ai_action, analysis_id = _get_ai_signal()
        return SignalResult(
            mode=TradingMode.IDLE,
            conditions={},
            values={
                "ai_bias": ai_bias,
                "ai_confidence": ai_conf,
                "ai_action": ai_action,
                "analysis_id": analysis_id,
            },
            confidence=0.0,
            reason=f"无交易信号 (AI: {ai_bias} {ai_conf}%)"
                   if ai_bias != "N/A"
                   else "无交易信号 (AI 研判未就绪)",
        )
