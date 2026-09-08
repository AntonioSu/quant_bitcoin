"""Trading Advisor — AI 驱动的交易决策层

职责分离：
  Signal AI (MarketAnalyzer)  → 纯市场方向判断 (bias + confidence)
  Trading AI (TradingAdvisor) → 仓位管理决策 (开仓/平仓/减仓/持仓/推进止损)

TradingAdvisor 由调度器每个 tick（默认 5 分钟）调用一次。决策缓存只在
「同一研判 + 同一仓位 + 未超过 decision_ttl_sec」时命中，因此正常情况下
每个 tick 都会真正问一次 LLM。持仓期间的保本 / 移动止损 / 落袋不再由代码
自动执行，而是由这一层用 action（平仓 / 减仓）和 stop_r（推进硬止损）表达。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from multi_agent.risk_tools import SIZE_PCT_MAP, RiskLevelTool
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

# ── 防抖护栏（不替 AI 做方向判断，只防止它在 5 分钟节拍下来回折腾）──
_MIN_REDUCE_RATIO = 0.10        # 减仓比例下限：低于此值没有意义
_MAX_REDUCE_RATIO = 0.90        # 减仓比例上限：再多就该直接平仓
_MAX_REDUCE_COUNT = 2           # 每仓最多减仓次数，第三次减仓按平仓执行
_MIN_REMAIN_RATIO = 0.20        # 减仓后剩余不足初始仓位的 20% → 直接平仓
REENTRY_COOLDOWN_SEC = 15 * 60  # 平仓后同方向重开的最短间隔
DEFAULT_DECISION_TTL_SEC = 300  # 决策缓存有效期（与调度器节拍对齐）


@dataclass
class TradingDecision:
    """Trading AI 输出"""
    action: str = "等待入场"
    close_ratio: float = 1.0
    position_size_hint: str = "50%"
    leverage_hint: int = 5
    reason: str = ""
    # AI 自定风控（相对量，None 表示不改）
    stop_atr_mult: Optional[float] = None   # 止损距离 = ATR × 该倍数（仅开仓时有效）
    stop_r: Optional[float] = None          # 持仓中：把硬止损推进到入场价 + stop_r × R
    _from_cache: bool = field(default=False, repr=False)

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

    def __init__(
        self,
        model_name: Optional[str] = None,
        decision_ttl_sec: float = DEFAULT_DECISION_TTL_SEC,
    ):
        self.llm = LLMClient(
            model_name=model_name or os.getenv("LLM_MODEL_NAME"),
            key=os.getenv("LLM_API_KEY"),
            api_url=os.getenv("LLM_API_URL"),
            timeout=60,
            max_tokens=1024,
            extra_body={"thinking": {"type": "disabled"}},
        )
        self._system_prompt: Optional[str] = None
        self._decision_ttl_sec = float(decision_ttl_sec)
        self._last_signal_id: Optional[str] = None
        self._last_position_hash: Optional[str] = None
        self._last_decision_ts: float = 0.0
        self._cached_decision: Optional[TradingDecision] = None
        self._reduce_count: int = 0
        self._initial_position_size: float = 0.0
        # 最近一次全平：方向 / 当时的研判 ID / 时间，用于拦截同研判下立刻重开
        self._last_close: Optional[Dict[str, Any]] = None

    def _cache_valid(self, signal_id: str, position_hash: str) -> bool:
        """缓存只在「同研判 + 同仓位 + 未过期」时命中。

        ttl <= 0 表示不做时间缓存：每次 decide() 都问 LLM。
        """
        if self._cached_decision is None or not signal_id:
            return False
        if signal_id != self._last_signal_id or position_hash != self._last_position_hash:
            return False
        if self._decision_ttl_sec <= 0:
            return False
        return (time.monotonic() - self._last_decision_ts) < self._decision_ttl_sec

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
        position_risk: Optional[Dict[str, Any]] = None,
    ) -> TradingDecision:
        """做一次交易决策（缓存有效期内、且信号/仓位不变时直接返回缓存）"""

        signal_id = str(signal.get("_memory_id", ""))
        position_hash = f"{position_direction}:{position_size_btc:.6f}"

        if position_direction == "NONE" and self._initial_position_size > 0:
            self._reset_reduce_state()

        if self._cache_valid(signal_id, position_hash):
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

        # 只有空仓要定 R（止损宽度/仓位/杠杆）才需要试算工具；持仓中 R 已冻结，
        # AI 直接在 R 坐标里输出 stop_r / 平仓 / 减仓 即可，不需要工具。
        is_flat = position_direction == "NONE"
        tool = risk_tool if is_flat else None
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
                logger.info("🔧 风控试算: %s", tool.summary())
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

        # 护栏会重新构造 TradingDecision，途中会丢掉 stop_r。护栏管的是动作
        # （开/平/减），不该连带否掉 AI 对止损的推进，所以事后补回来。
        stop_r = decision.stop_r

        decision = self._apply_policy(
            decision,
            signal=signal,
            position_direction=position_direction,
            position_entry=position_entry,
            position_size_btc=position_size_btc,
            btc_price=btc_price,
            signal_id=signal_id,
        )

        if decision.stop_r is None and not decision.is_open:
            decision.stop_r = stop_r

        self._last_signal_id = signal_id
        self._last_position_hash = position_hash
        self._last_decision_ts = time.monotonic()
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

    def note_position_closed(self, direction: str, signal_id: str):
        """调度器在全平后调用（无论是 AI 平仓还是硬止损 / 强平）。

        记录方向和当时的研判 ID：同一份研判下不允许立刻同方向重开，
        且至少间隔 REENTRY_COOLDOWN_SEC —— 否则「止损出局 → 2 分钟后原样开回」
        这种抖动会反复发生。
        """
        if direction not in ("LONG", "SHORT"):
            return
        self._last_close = {
            "direction": direction,
            "signal_id": str(signal_id or ""),
            "ts": time.monotonic(),
        }
        self._reset_reduce_state()

    def _reentry_block_reason(self, direction: str, signal_id: str) -> Optional[str]:
        """同方向重开是否应被拦下；返回原因，None 表示放行"""
        last = self._last_close
        if not last or last["direction"] != direction:
            return None
        elapsed = time.monotonic() - last["ts"]
        if elapsed < REENTRY_COOLDOWN_SEC:
            return f"刚平掉 {direction} 仅 {elapsed / 60:.0f} 分钟，冷却中"
        if signal_id and last["signal_id"] and signal_id == last["signal_id"]:
            return f"平掉 {direction} 后研判未更新，不在同一研判下重开"
        return None

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
        """护栏只管两件事：开仓门槛（entry_ok / 等级 / 方向一致 / 重开冷却）
        和减仓防抖（比例区间 / 次数 / 残仓）。持仓中怎么出、什么时候出，
        全部由 AI 决定，这里不再拦平仓、也不再替 AI 强制平仓。"""
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

            reentry_block = self._reentry_block_reason(decision.direction, signal_id)
            if reentry_block:
                reason = f"护栏拦截重开: {reentry_block}"
                logger.info("🛡️ %s", reason)
                return TradingDecision(
                    action="等待入场",
                    position_size_hint="0%",
                    reason=reason[:80],
                )

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

        # ── 有持仓：平仓照单执行；减仓只做防抖 ──
        if self._initial_position_size <= 0:
            self._initial_position_size = position_size_btc

        if decision.action == "平仓":
            self._reset_reduce_state()
            return decision

        if decision.action == "减仓":
            position_ratio = (
                position_size_btc / self._initial_position_size
                if self._initial_position_size > 0 else 1.0
            )
            ratio = min(max(decision.close_ratio, _MIN_REDUCE_RATIO), _MAX_REDUCE_RATIO)
            remain_ratio = position_ratio * (1 - ratio)

            escalate = None
            if self._reduce_count >= _MAX_REDUCE_COUNT:
                escalate = f"已减仓{self._reduce_count}次，第{self._reduce_count + 1}次按全平执行"
            elif remain_ratio < _MIN_REMAIN_RATIO:
                escalate = f"减仓后仅剩初始仓位 {remain_ratio:.0%}，残仓无意义，按全平执行"

            if escalate:
                logger.info("🛡️ 护栏: %s", escalate)
                self._reset_reduce_state()
                return TradingDecision(
                    action="平仓",
                    close_ratio=1.0,
                    reason=(decision.reason or escalate)[:80],
                )

            self._reduce_count += 1
            logger.info(
                "📉 减仓 %.0f%% (第 %d/%d 次, 减前仓位比例 %.0f%%)",
                ratio * 100, self._reduce_count, _MAX_REDUCE_COUNT, position_ratio * 100,
            )
            return TradingDecision(
                action="减仓",
                close_ratio=ratio,
                reason=decision.reason[:80],
                stop_r=decision.stop_r,
            )

        return decision

    def _reset_reduce_state(self):
        """全平或新仓位时重置减仓追踪状态"""
        self._reduce_count = 0
        self._initial_position_size = 0.0

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

        模型要自己决定何时保本 / 移动止损 / 落袋，就必须能在 R 空间里推理，
        而不是只拿到美元和绝对价格。峰值（peak_r）必须给：
        「冲到 1.4R 又跌回 0.3R」和「一路磨到 0.3R」当前浮盈完全一样，
        但前者是突破失败该收紧、后者是缓慢推进该给空间。
        """
        lines = [
            "## 本仓风险坐标（R 单位）",
            f"- 1R = ${pr['r_unit_usd']:,.0f}（占入场价 {pr['r_unit_pct']:.2f}%）"
            "，开仓时已冻结，无法再改",
            f"- 初始止损: ${pr['initial_stop']:,.0f}（-1.00R，硬止损兜底）",
            f"- 当前浮盈: {pr['profit_r']:+.2f}R",
            f"- 峰值浮盈: {pr['peak_r']:+.2f}R"
            f"（最有利价 ${pr['mfe_price']:,.0f}）",
            f"- 自峰值回撤: {pr['drawdown_from_peak_r']:.2f}R",
            f"- 当前硬止损: ${pr['stop_price']:,.0f} = {pr['stop_r']:+.2f}R"
            f"（0 = 成本价）"
            + ("，已由你推进过" if pr.get("stop_moved_by_ai") else "，仍在开仓位置"),
            f"- 距强平: {pr['dist_to_liq_r']:.2f}R",
            "- 要推进止损就输出 stop_r（只能比当前值更靠有利方向，且不能越过现价）",
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
                    "更容易被噪声扫到，止损不要贴得太近"
                )
            elif ratio <= 0.75:
                lines.append(
                    f"- 波动已收缩 {(1 - ratio) * 100:.0f}%，可考虑把止损"
                    "推得更近以锁定利润"
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
                close_ratio = float(data.get("close_ratio", 0.5))
                close_ratio = max(_MIN_REDUCE_RATIO, min(_MAX_REDUCE_RATIO, close_ratio))
            except (TypeError, ValueError):
                close_ratio = 0.5
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

        # 只做类型解析：非数字 / 非有限值一律视为「未指定」—— 解析层不替模型做判断。
        def _opt_number(key: str, positive_only: bool) -> Optional[float]:
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
            if positive_only and val <= 0:
                logger.warning(f"🤖 Trading AI {key} 非正数，忽略: {val}")
                return None
            return val

        # stop_r 只在持仓中有意义；0 = 保本、正 = 锁定利润、负 = 仍在亏损侧
        # （负值只要比当前止损更靠有利方向也算推进，由调度器判断棘轮）
        stop_r = _opt_number("stop_r", positive_only=False) if has_position else None

        return TradingDecision(
            action=action,
            close_ratio=close_ratio,
            position_size_hint=size_hint,
            leverage_hint=leverage,
            reason=reason,
            stop_atr_mult=_opt_number("stop_atr_mult", positive_only=True),
            stop_r=stop_r,
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
