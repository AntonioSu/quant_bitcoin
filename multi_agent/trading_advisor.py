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

from multi_agent.risk_tools import SIZE_PCT_MAP, LadderTool, RiskLevelTool
from utils import logger
from utils.common_utils import read_file_prompt
from utils.llm_client import LLMClient

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), 'prompts')

VALID_ACTIONS = {"开多", "开空", "平仓", "减仓", "持仓观望", "等待入场"}
_SIZE_HINTS = set(SIZE_PCT_MAP)
_LEVEL_RANK = {
    "WEAK": 0,
    "CAUTIOUS": 1,
    "MODERATE": 2,
    "STRONG": 3,
    "VERY_STRONG": 4,
}
_MIN_OPEN_LEVEL = "MODERATE"
_MAX_REDUCE_RATIO = 0.25
_MAX_REDUCE_COUNT = 2
_MIN_POSITION_RATIO = 0.50


@dataclass
class TradingDecision:
    """Trading AI 输出"""
    action: str = "等待入场"
    close_ratio: float = 1.0
    position_size_hint: str = "50%"
    leverage_hint: int = 5
    reason: str = ""
    # AI 自定风控（相对量，None 表示沿用当前生效值）
    stop_atr_mult: Optional[float] = None   # 止损距离 = ATR × 该倍数（仅开仓时有效）
    tp_trigger_r: Optional[float] = None    # 部分止盈触发的 R 倍数
    # 持仓期间可整组重调的阶梯参数（每小时随新研判刷新一次）
    tp_fraction: Optional[float] = None         # 落袋比例，0 = 本仓不落袋
    breakeven_trigger_r: Optional[float] = None # 保本触发线
    trailing_trigger_r: Optional[float] = None  # 移动止损启动线
    trailing_distance_r: Optional[float] = None # 移动止损回撤距离
    _from_cache: bool = field(default=False, repr=False)

    # 可在持仓期间下发的阶梯字段（与 Position.LADDER_FIELDS 对应）
    LADDER_KEYS = (
        "tp_trigger_r",
        "tp_fraction",
        "breakeven_trigger_r",
        "trailing_trigger_r",
        "trailing_distance_r",
    )

    def ladder_overrides(self) -> Dict[str, float]:
        """AI 本次真正给了值的阶梯参数"""
        return {
            k: getattr(self, k)
            for k in self.LADDER_KEYS
            if getattr(self, k) is not None
        }

    @property
    def is_open(self) -> bool:
        return self.action in ("开多", "开空")

    @property
    def is_close(self) -> bool:
        return self.action in ("平仓", "减仓")

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
        self._reduce_count: int = 0
        self._initial_position_size: float = 0.0

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
        risk_tool: Optional[RiskLevelTool] = None,
        ladder_tool: Optional[LadderTool] = None,
        position_risk: Optional[Dict[str, Any]] = None,
    ) -> TradingDecision:
        """做一次交易决策（有缓存，信号/仓位不变时直接返回缓存）"""

        signal_id = str(signal.get("_memory_id", ""))
        position_hash = f"{position_direction}:{position_size_btc:.6f}"

        if position_direction == "NONE" and self._initial_position_size > 0:
            self._reset_reduce_state()

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
            position_risk=position_risk,
        )

        # 空仓要定 R（止损宽度/仓位/杠杆），持仓的 R 已冻结、只能移动各级触发线，
        # 两种场景的入参完全不同，所以挂两个不同的工具。
        is_flat = position_direction == "NONE"
        tool = risk_tool if is_flat else ladder_tool
        try:
            if tool is not None:
                resp = self.llm.chat_with_tools(
                    system_prompt=self._load_system_prompt(),
                    prompt=prompt,
                    tools=tool.schema,
                    dispatch=tool.dispatch,
                    max_rounds=3,
                    usage_tag="[trading]",
                )
                logger.info(
                    "🔧 %s试算: %s",
                    "风控" if is_flat else "阶梯",
                    tool.summary(),
                )
            else:
                resp = self.llm.chat(
                    system_prompt=self._load_system_prompt(),
                    prompt=prompt,
                    usage_tag="[trading]",
                )
            decision = self._parse_response(resp, position_direction)
        except Exception as e:
            logger.error(f"🤖 交易决策 LLM 调用失败: {e}")
            decision = self._safe_default(position_direction)

        # 护栏会重新构造 TradingDecision，途中会丢掉阶梯字段。护栏管的是动作
        # （开/平/减），不该连带否掉 AI 对阶梯的调整，所以事后补回来。
        ladder_overrides = decision.ladder_overrides()

        decision = self._apply_policy(
            decision,
            signal=signal,
            position_direction=position_direction,
            position_entry=position_entry,
            position_size_btc=position_size_btc,
            btc_price=btc_price,
            signal_id=signal_id,
        )

        for key, value in ladder_overrides.items():
            if getattr(decision, key) is None:
                setattr(decision, key, value)

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

    def invalidate_cache(self):
        """强制下次 decide() 重新调用 LLM（例如仓位被外部修改后）"""
        self._last_signal_id = None
        self._last_position_hash = None
        self._cached_decision = None

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

            # 逐条列出真正不满足的条件：以前无论哪一项失败都打印
            # "{level}<{_MIN_OPEN_LEVEL}"，等级明明够时也这么写，误导排查。
            blockers = []
            if not entry_ok:
                blockers.append("entry_ok=false")
            if bias not in ("LONG", "SHORT"):
                blockers.append(f"bias={bias}")
            if level_rank < min_open_rank:
                blockers.append(f"{level}<{_MIN_OPEN_LEVEL}")
            if not blockers:
                blockers.append(f"方向不符({decision.action} vs bias={bias})")
            reason = f"护栏拦截开仓: {', '.join(blockers)}"
            logger.info("🛡️ %s", reason)
            return TradingDecision(
                action="等待入场",
                position_size_hint="0%",
                reason=reason[:80],
            )

        # ── 有持仓 ──
        if self._initial_position_size <= 0:
            self._initial_position_size = position_size_btc

        opposite = "SHORT" if position_direction == "LONG" else "LONG"
        pnl_pct = self._unrealized_pct(
            position_direction, position_entry, position_size_btc, btc_price
        )
        hard_loss_exit = pnl_pct < -5.0
        strong_reversal = bias == opposite and level_rank >= _LEVEL_RANK["STRONG"]
        moderate_reversal = bias == opposite and level == "MODERATE"

        position_ratio = (
            position_size_btc / self._initial_position_size
            if self._initial_position_size > 0 else 1.0
        )
        reduce_exhausted = self._reduce_count >= _MAX_REDUCE_COUNT
        position_too_small = position_ratio < _MIN_POSITION_RATIO

        if reduce_exhausted or position_too_small:
            if moderate_reversal or strong_reversal or hard_loss_exit:
                reason = (
                    f"已减仓{self._reduce_count}次(剩余{position_ratio:.0%})，"
                    f"信号仍反向，全平离场"
                )
                logger.info("🛡️ 护栏: %s", reason)
                self._reset_reduce_state()
                return TradingDecision(
                    action="平仓",
                    close_ratio=1.0,
                    reason=reason[:80],
                )

        if hard_loss_exit or strong_reversal:
            reason = decision.reason or (
                "未实现亏损>5%，止损离场" if hard_loss_exit
                else f"{level} 反向，果断平仓"
            )
            self._reset_reduce_state()
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
            self._reduce_count += 1
            logger.info(
                "📉 减仓计数: %d/%d (仓位比例: %.0f%%)",
                self._reduce_count, _MAX_REDUCE_COUNT, position_ratio * 100,
            )
            return decision

        return decision

    def _reset_reduce_state(self):
        """全平或新仓位时重置减仓追踪状态"""
        self._reduce_count = 0
        self._initial_position_size = 0.0
        self._last_partial_close_signal_id = None

    def get_reduce_state(self) -> dict:
        """导出减仓追踪状态（用于持久化）"""
        return {
            "reduce_count": self._reduce_count,
            "initial_position_size": self._initial_position_size,
        }

    def restore_reduce_state(self, state: dict):
        """从持久化数据恢复减仓追踪状态"""
        self._reduce_count = state.get("reduce_count", 0)
        self._initial_position_size = state.get("initial_position_size", 0.0)

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
        position_risk: Optional[Dict[str, Any]] = None,
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
        # 趋势/波动状态是决定阶梯松紧的核心依据，信号里一直有，之前没传给这一层
        for key, label in (
            ("trend_regime", "趋势状态"),
            ("volatility_regime", "波动状态"),
        ):
            if signal.get(key):
                sig_section += f"- {label}: {signal[key]}\n"
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
            if position_risk:
                parts.append(self._format_position_risk(position_risk))
        else:
            parts.append("## 当前持仓\n- 无持仓（空仓）")

        parts.append(
            f"## 账户状态\n"
            f"- 权益: ${equity:,.2f}\n"
            f"- BTC 价格: ${btc_price:,.0f}"
        )

        parts.append("请根据以上信息输出交易决策 JSON。")
        return "\n\n".join(parts)

    @staticmethod
    def _format_position_risk(pr: Dict[str, Any]) -> str:
        """把持仓的 R 坐标系摊给模型。

        阶梯的每一级都是用 R 定义的，而模型此前只拿到美元和绝对价格 ——
        它根本算不出 R 是多少，也就无法用系统自己的单位表达判断。
        另外峰值（peak_r）必须给：整个棘轮是峰值驱动的，只看当前浮盈的话，
        「冲到 1.4R 又跌回 0.3R」和「一路磨到 0.3R」看起来完全一样，
        而这两种情况需要相反的处理。
        """
        lines = [
            "## 本仓风险坐标（R 单位）",
            f"- 1R = ${pr['r_unit_usd']:,.0f}（占入场价 {pr['r_unit_pct']:.2f}%）"
            "，开仓时已冻结，无法再改",
            f"- 初始止损: ${pr['initial_stop']:,.0f}",
            f"- 当前浮盈: {pr['profit_r']:+.2f}R",
            f"- 峰值浮盈: {pr['peak_r']:+.2f}R"
            f"（最有利价 ${pr['mfe_price']:,.0f}）",
            f"- 自峰值回撤: {pr['drawdown_from_peak_r']:.2f}R",
            f"- 当前止损位置: {pr['stop_r']:+.2f}R"
            f"（0 = 成本价），阶段 {pr['stop_stage']}",
            f"- 距强平: {pr['dist_to_liq_r']:.2f}R",
            f"- 部分止盈已落袋: {'是' if pr['tp_taken'] else '否'}",
        ]

        lad = pr.get("ladder") or {}
        if lad:
            lines += [
                "",
                "### 当前生效的阶梯参数",
                f"- breakeven_trigger_r = {lad['breakeven_trigger_r']}",
                f"- trailing_trigger_r  = {lad['trailing_trigger_r']}",
                f"- trailing_distance_r = {lad['trailing_distance_r']}",
                f"- tp_trigger_r        = {lad['tp_trigger_r']}",
                f"- tp_fraction         = {lad['tp_fraction']}",
            ]

        atr_now = pr.get("atr_now") or 0
        atr_open = pr.get("atr_at_open") or 0
        if atr_now > 0 and atr_open > 0:
            ratio = atr_now / atr_open
            lines += [
                "",
                "### 波动是否已重标定",
                f"- 开仓时 ATR ${atr_open:,.0f} → 现在 ${atr_now:,.0f}"
                f"（{ratio:.2f}×）",
            ]
            if ratio >= 1.3:
                lines.append(
                    f"- 波动已放大 {(ratio - 1) * 100:.0f}%，同样的 R 现在"
                    "更容易被噪声扫到，可考虑放宽 trailing_distance_r"
                )
            elif ratio <= 0.75:
                lines.append(
                    f"- 波动已收缩 {(1 - ratio) * 100:.0f}%，可考虑收紧"
                    " trailing_distance_r 锁定更多利润"
                )

        path = pr.get("path_r") or []
        if len(path) >= 2:
            lines += [
                "",
                "### 开仓以来的路径（4H 收盘，R 单位）",
                "- " + " → ".join(f"{v:+.2f}" for v in path),
            ]

        entry_sig = pr.get("entry_signal") or {}
        if entry_sig.get("bias"):
            lines += [
                "",
                "### 开仓时的研判基准（用于对比论点是否被削弱）",
                f"- 当时: {entry_sig.get('bias')} "
                f"{entry_sig.get('confidence', '?')}%"
                + (f" / {entry_sig['trend_regime']}"
                   if entry_sig.get("trend_regime") else ""),
            ]

        return "\n".join(lines)

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

        # 只做类型解析，不做区间钳制：阶梯参数由 AI 全权决定。非数字或非法值
        # 一律视为「未指定」，回落当前生效值 —— 解析层不替模型做判断。
        def _opt_positive(key: str, allow_zero: bool = False) -> Optional[float]:
            raw = data.get(key)
            if raw is None or raw == "":
                return None
            try:
                val = float(raw)
            except (TypeError, ValueError):
                logger.warning(f"🤖 Trading AI {key} 非数字，忽略: {raw!r}")
                return None
            if val != val or val in (float("inf"), float("-inf")):
                logger.warning(f"🤖 Trading AI {key} 非有限数值，忽略: {raw!r}")
                return None
            if val < 0 or (val == 0 and not allow_zero):
                logger.warning(f"🤖 Trading AI {key} 非正数，忽略: {val}")
                return None
            return val

        return TradingDecision(
            action=action,
            close_ratio=close_ratio,
            position_size_hint=size_hint,
            leverage_hint=leverage,
            reason=reason,
            stop_atr_mult=_opt_positive("stop_atr_mult"),
            tp_trigger_r=_opt_positive("tp_trigger_r"),
            # tp_fraction=0 是合法意图：本仓不落袋，全交给移动止损
            tp_fraction=_opt_positive("tp_fraction", allow_zero=True),
            breakeven_trigger_r=_opt_positive("breakeven_trigger_r"),
            trailing_trigger_r=_opt_positive("trailing_trigger_r"),
            trailing_distance_r=_opt_positive("trailing_distance_r"),
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
