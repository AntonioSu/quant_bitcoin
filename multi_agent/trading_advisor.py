"""Trading Advisor — AI 驱动的交易决策层

职责分离：
  Signal AI (MarketAnalyzer)  → 纯市场方向判断 (bias + confidence)
  Trading AI (TradingAdvisor) → 仓位管理决策 (开仓/平仓/持仓)

TradingAdvisor 在以下时机被调用（事件驱动，节省 token）：
  1. 市场信号更新（新的 AI 研判 _memory_id）
  2. 仓位状态变化（开仓/平仓/减仓）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from utils import logger
from utils.common_utils import read_file_prompt
from utils.llm_client import LLMClient

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), 'prompts')

VALID_ACTIONS = {"开多", "开空", "平仓", "减仓", "持仓观望", "等待入场"}
_SIZE_HINTS = {"0%", "25%", "50%", "75%", "100%"}
_LEVEL_RANK = {
    "WEAK": 0,
    "CAUTIOUS": 1,
    "MODERATE": 2,
    "STRONG": 3,
    "VERY_STRONG": 4,
}
_MIN_OPEN_LEVEL = "MODERATE"
_MAX_REDUCE_RATIO = 0.25


@dataclass
class TradingDecision:
    """Trading AI 输出"""
    action: str = "等待入场"
    close_ratio: float = 1.0
    position_size_hint: str = "50%"
    leverage_hint: int = 5
    reason: str = ""
    _from_cache: bool = field(default=False, repr=False)

    @property
    def is_open(self) -> bool:
        return self.action in ("开多", "开空")

    @property
    def is_close(self) -> bool:
        return self.action in ("平仓", "减仓")

    @property
    def is_hold(self) -> bool:
        return self.action in ("持仓观望", "等待入场")

    @property
    def direction(self) -> str:
        if self.action == "开多":
            return "LONG"
        if self.action == "开空":
            return "SHORT"
        return "NONE"


class TradingAdvisor:
    """AI 交易决策层 — 根据信号 + 仓位 + 资金决定交易动作"""

    def __init__(self, model_name: Optional[str] = None):
        self.llm = LLMClient(
            model_name=model_name or os.getenv("LLM_MODEL_NAME"),
            key=os.getenv("LLM_API_KEY"),
            api_url=os.getenv("LLM_API_URL"),
            timeout=60,
            max_tokens=1024,
            extra_body={"thinking": {"type": "disabled"}},
        )
        self._system_prompt: Optional[str] = None
        self._last_signal_id: Optional[str] = None
        self._last_position_hash: Optional[str] = None
        self._cached_decision: Optional[TradingDecision] = None
        self._last_partial_close_signal_id: Optional[str] = None

    def decide(
        self,
        signal: Dict[str, Any],
        position_direction: str,
        position_entry: float,
        position_size_btc: float,
        position_leverage: int,
        position_stop_loss: float,
        position_liquidation: float,
        btc_price: float,
        equity: float,
        holding_duration: str = "未知",
    ) -> TradingDecision:
        """做一次交易决策（有缓存，信号/仓位不变时直接返回缓存）"""

        signal_id = str(signal.get("_memory_id", ""))
        position_hash = f"{position_direction}:{position_size_btc:.6f}"

        if (
            signal_id
            and signal_id == self._last_signal_id
            and position_hash == self._last_position_hash
            and self._cached_decision is not None
        ):
            self._cached_decision._from_cache = True
            return self._cached_decision

        prompt = self._build_prompt(
            signal=signal,
            position_direction=position_direction,
            position_entry=position_entry,
            position_size_btc=position_size_btc,
            position_leverage=position_leverage,
            position_stop_loss=position_stop_loss,
            position_liquidation=position_liquidation,
            btc_price=btc_price,
            equity=equity,
            holding_duration=holding_duration,
        )

        try:
            resp = self.llm.chat(
                system_prompt=self._load_system_prompt(),
                prompt=prompt,
                usage_tag="[trading]",
            )
            decision = self._parse_response(resp, position_direction)
        except Exception as e:
            logger.error(f"🤖 交易决策 LLM 调用失败: {e}")
            decision = self._safe_default(position_direction)

        decision = self._apply_policy(
            decision,
            signal=signal,
            position_direction=position_direction,
            position_entry=position_entry,
            position_size_btc=position_size_btc,
            btc_price=btc_price,
            signal_id=signal_id,
        )

        self._last_signal_id = signal_id
        self._last_position_hash = position_hash
        self._cached_decision = decision
        decision._from_cache = False

        logger.info(
            "🤖 交易决策: action=%s, reason=%s (signal=%s)",
            decision.action,
            decision.reason,
            signal_id[:8] if signal_id else "none",
        )
        return decision

    def get_cached_or_none(self) -> TradingDecision | None:
        """返回缓存的决策（不调用 LLM），用于状态显示"""
        return self._cached_decision

    def invalidate_cache(self):
        """强制下次 decide() 重新调用 LLM（例如仓位被外部修改后）"""
        self._last_signal_id = None
        self._last_position_hash = None
        self._cached_decision = None

    def reload_prompt(self):
        """重新加载系统提示词（热更新）"""
        self._system_prompt = None

    def _load_system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = read_file_prompt(
                os.path.join(_PROMPT_DIR, "trading_advisor.md")
            )
        return self._system_prompt

    @staticmethod
    def _signal_level(signal: Dict[str, Any]) -> str:
        from multi_agent.schemas import confidence_to_level

        level = str(signal.get("confidence_level") or "").strip().upper()
        if level in _LEVEL_RANK:
            return level
        try:
            conf = int(signal.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0
        return confidence_to_level(conf)

    @staticmethod
    def _unrealized_pct(
        position_direction: str,
        position_entry: float,
        position_size_btc: float,
        btc_price: float,
    ) -> float:
        if position_direction == "NONE" or position_size_btc <= 0 or position_entry <= 0:
            return 0.0
        sign = 1 if position_direction == "LONG" else -1
        unrealized = sign * (btc_price - position_entry) * position_size_btc
        notional = position_entry * position_size_btc
        return (unrealized / notional * 100) if notional > 0 else 0.0

    def _apply_policy(
        self,
        decision: TradingDecision,
        signal: Dict[str, Any],
        position_direction: str,
        position_entry: float,
        position_size_btc: float,
        btc_price: float,
        signal_id: str,
    ) -> TradingDecision:
        """硬性护栏：防止小赚就跑 / CAUTIOUS 滥开 / entry_ok 误平仓。"""
        bias = str(signal.get("bias", "NEUTRAL") or "NEUTRAL").strip().upper()
        level = self._signal_level(signal)
        entry_ok = signal.get("entry_ok", True)
        if entry_ok is None:
            entry_ok = True
        else:
            entry_ok = bool(entry_ok)

        has_position = position_direction != "NONE" and position_size_btc > 0
        level_rank = _LEVEL_RANK.get(level, 0)
        min_open_rank = _LEVEL_RANK[_MIN_OPEN_LEVEL]

        if not has_position:
            if not decision.is_open:
                return decision

            allow_open = (
                bool(entry_ok)
                and bias in ("LONG", "SHORT")
                and level_rank >= min_open_rank
                and (
                    (decision.action == "开多" and bias == "LONG")
                    or (decision.action == "开空" and bias == "SHORT")
                )
            )
            if allow_open:
                return decision

            reason = (
                f"护栏拦截开仓: entry_ok={entry_ok}, bias={bias}, "
                f"{level}<{_MIN_OPEN_LEVEL}"
            )
            logger.info("🛡️ %s", reason)
            return TradingDecision(
                action="等待入场",
                position_size_hint="0%",
                reason=reason[:80],
            )

        # ── 有持仓 ──
        opposite = "SHORT" if position_direction == "LONG" else "LONG"
        pnl_pct = self._unrealized_pct(
            position_direction, position_entry, position_size_btc, btc_price
        )
        hard_loss_exit = pnl_pct < -5.0
        strong_reversal = bias == opposite and level_rank >= _LEVEL_RANK["STRONG"]
        moderate_reversal = bias == opposite and level == "MODERATE"

        if hard_loss_exit or strong_reversal:
            reason = decision.reason or (
                "未实现亏损>5%，止损离场" if hard_loss_exit
                else f"{level} 反向，果断平仓"
            )
            self._last_partial_close_signal_id = None
            return TradingDecision(
                action="平仓",
                close_ratio=1.0,
                reason=reason[:80],
            )

        if decision.action == "平仓":
            if moderate_reversal:
                logger.info("🛡️ 护栏: 仅 MODERATE 反转，平仓降级为减仓 25%%")
                decision = TradingDecision(
                    action="减仓",
                    close_ratio=_MAX_REDUCE_RATIO,
                    reason=(decision.reason or "MODERATE 反转，轻减仓")[:80],
                )
            else:
                logger.info(
                    "🛡️ 护栏: 拦截平仓 (bias=%s, %s, entry_ok=%s) → 持仓观望",
                    bias, level, entry_ok,
                )
                return TradingDecision(
                    action="持仓观望",
                    reason=(
                        decision.reason
                        or "信号未强反转，entry_ok/NEUTRAL 不构成离场"
                    )[:80],
                )

        if decision.action == "减仓":
            # 仅允许：中等强度反向；同向 / NEUTRAL / 弱反向一律继续持仓
            if not moderate_reversal:
                logger.info(
                    "🛡️ 护栏: 拦截减仓 (bias=%s, %s) → 持仓观望",
                    bias, level,
                )
                return TradingDecision(
                    action="持仓观望",
                    reason="仅 MODERATE 反向才允许轻减仓，其余继续持仓",
                )

            decision = TradingDecision(
                action="减仓",
                close_ratio=min(max(decision.close_ratio, 0.1), _MAX_REDUCE_RATIO),
                reason=(decision.reason or "MODERATE 反转，轻减仓 25%")[:80],
            )

            if signal_id and signal_id == self._last_partial_close_signal_id:
                logger.info("🛡️ 护栏: 同信号已减仓，等待下次研判刷新")
                return TradingDecision(
                    action="持仓观望",
                    reason="同信号已减仓，等待下次研判",
                )
            if signal_id:
                self._last_partial_close_signal_id = signal_id
            return decision

        return decision

    def _build_prompt(
        self,
        signal: Dict[str, Any],
        position_direction: str,
        position_entry: float,
        position_size_btc: float,
        position_leverage: int,
        position_stop_loss: float,
        position_liquidation: float,
        btc_price: float,
        equity: float,
        holding_duration: str,
    ) -> str:
        parts: list[str] = []

        from multi_agent.schemas import confidence_to_level

        bias = signal.get("bias", "NEUTRAL")
        confidence = signal.get("confidence", 0)
        confidence_level = signal.get(
            "confidence_level", confidence_to_level(confidence)
        )
        summary = signal.get("summary", "")
        entry_ok = signal.get("entry_ok", True)
        drivers = signal.get("key_drivers", [])
        risks = signal.get("risks", [])

        drivers_text = ""
        if drivers:
            lines = []
            for d in drivers:
                if isinstance(d, dict):
                    lines.append(
                        f"  - [{d.get('side','?')}/{d.get('weight','?')}] "
                        f"{d.get('factor','')}"
                    )
                else:
                    lines.append(f"  - {d}")
            drivers_text = "\n".join(lines)

        risks_text = ""
        if risks:
            risks_text = "\n".join(f"  - {r}" for r in risks if r)

        size_hint = signal.get("position_size_hint")
        lev_hint = signal.get("leverage_hint")
        sig_section = (
            f"## 市场信号\n"
            f"- 方向: {bias}\n"
            f"- 置信度: {confidence_level} ({confidence}%)\n"
            f"- 研判: {summary}\n"
            f"- entry_ok: {entry_ok}\n"
        )
        if size_hint is not None:
            sig_section += f"- 信号仓位建议: {size_hint}\n"
        if lev_hint is not None:
            sig_section += f"- 信号杠杆上限: {lev_hint}x\n"
        if drivers_text:
            sig_section += f"- 关键驱动:\n{drivers_text}\n"
        if risks_text:
            sig_section += f"- 风险:\n{risks_text}\n"
        parts.append(sig_section)

        has_position = position_direction != "NONE" and position_size_btc > 0
        if has_position:
            sign = 1 if position_direction == "LONG" else -1
            unrealized = sign * (btc_price - position_entry) * position_size_btc
            notional = position_entry * position_size_btc
            unrealized_pct = (unrealized / notional * 100) if notional > 0 else 0

            parts.append(
                f"## 当前持仓\n"
                f"- 方向: {position_direction}\n"
                f"- 入场价: ${position_entry:,.0f}\n"
                f"- 当前价: ${btc_price:,.0f}\n"
                f"- 仓位: {position_size_btc:.4f} BTC "
                f"(${position_size_btc * btc_price:,.0f})\n"
                f"- 杠杆: {position_leverage}x\n"
                f"- 未实现盈亏: ${unrealized:+,.2f} ({unrealized_pct:+.2f}%)\n"
                f"- 止损价: ${position_stop_loss:,.0f}\n"
                f"- 强平价: ${position_liquidation:,.0f}\n"
                f"- 持仓时长: {holding_duration}"
            )
        else:
            parts.append("## 当前持仓\n- 无持仓（空仓）")

        parts.append(
            f"## 账户状态\n"
            f"- 权益: ${equity:,.2f}\n"
            f"- BTC 价格: ${btc_price:,.0f}"
        )

        parts.append("请根据以上信息输出交易决策 JSON。")
        return "\n\n".join(parts)

    def _parse_response(self, text: str, position_direction: str) -> TradingDecision:
        raw = str(text or "").strip()
        if "```json" in raw:
            raw = raw.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in raw:
            raw = raw.split("```", 1)[1].split("```", 1)[0]
        else:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                raw = raw[start : end + 1]

        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            logger.warning("🤖 交易决策 JSON 解析失败: %s", raw[:120])
            return self._safe_default(position_direction)

        action = str(data.get("action", "")).strip()
        if action not in VALID_ACTIONS:
            logger.warning("🤖 无效 action '%s'，回退默认", action)
            return self._safe_default(position_direction)

        has_position = position_direction != "NONE"
        if has_position and action in ("开多", "开空"):
            action = "持仓观望"
        if not has_position and action in ("平仓", "减仓", "持仓观望"):
            action = "等待入场"

        close_ratio = 1.0
        if action == "减仓":
            try:
                close_ratio = float(data.get("close_ratio", _MAX_REDUCE_RATIO))
                close_ratio = max(0.1, min(_MAX_REDUCE_RATIO, close_ratio))
            except (TypeError, ValueError):
                close_ratio = _MAX_REDUCE_RATIO
        elif action == "平仓":
            close_ratio = 1.0

        size_hint = str(data.get("position_size_hint", "50%")).strip()
        if size_hint not in _SIZE_HINTS:
            size_hint = "50%"
        if action == "等待入场":
            size_hint = "0%"

        try:
            leverage = max(1, min(20, int(data.get("leverage_hint", 5))))
        except (TypeError, ValueError):
            leverage = 5

        reason = str(data.get("reason", "")).strip()[:80]

        return TradingDecision(
            action=action,
            close_ratio=close_ratio,
            position_size_hint=size_hint,
            leverage_hint=leverage,
            reason=reason,
        )

    @staticmethod
    def _safe_default(position_direction: str) -> TradingDecision:
        if position_direction != "NONE":
            return TradingDecision(
                action="持仓观望",
                reason="LLM 调用失败，保守持仓",
            )
        return TradingDecision(
            action="等待入场",
            position_size_hint="0%",
            reason="LLM 调用失败，等待下次信号",
        )
