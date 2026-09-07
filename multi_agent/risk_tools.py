"""Trading AI 的风控计算工具（function calling）

Trading AI 需要自己定止损宽度和止盈线，但它看不到带标签的绝对价格，也不该看到
——一旦让模型直接输出价位，它可能幻觉出一个贴着强平价的数字。折中方案是让它
只输出「相对量」（止损 = ATR × n、止盈 = n × R），再通过本工具把相对量换算成
真实价位和真实亏损金额反馈给它，由它确认或调整。

工具复用 PositionLevel.preview 与 SIZE_PCT_MAP 的同一套公式，保证模型看到
的数字和真正下单时用的数字一致；如果两边算法漂移，模型的判断就失去意义。
"""

import json
from typing import Any, Dict, List, Optional

from indicators.profit_loss_level import PositionLevel
from utils import logger

TOOL_NAME = "compute_risk_levels"
LADDER_TOOL_NAME = "compute_ladder_levels"

# 名义本金 = 权益 × 该比例 × 杠杆
SIZE_PCT_MAP = {"0%": 0.0, "25%": 0.25, "50%": 0.50, "75%": 0.75, "100%": 1.0}


def _args_key(args: Dict[str, Any]) -> str:
    """归一化工具入参，用于识别重复试算"""
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(sorted(args.items())) if isinstance(args, dict) else repr(args)


def _repeat_guard(call_log: List[Dict[str, Any]], args: Dict[str, Any]):
    """同一组参数重复试算时，直接回上次结果并要求收敛。

    实测模型会用一模一样的参数反复调用直到耗尽轮数 —— 它从重复调用里得不到
    任何新信息，只是没有停止条件。光靠提示词约束不可靠，这里做硬兜底。
    """
    key = _args_key(args)
    for call in call_log:
        if call.get("_key") == key:
            repeated = dict(call["result"])
            repeated["note"] = (
                "这组参数你已经试算过，结果完全相同。不要再重复调用："
                "若可接受就立即输出最终 JSON，否则换一组不同的参数。"
            )
            return repeated
    return None


def resolve_ladder_stop(
    is_long: bool,
    entry_price: float,
    mfe_price: float,
    current_stop: float,
    r_unit: float,
    breakeven_trigger_r: float,
    trailing_trigger_r: float,
    trailing_distance_r: float,
) -> Dict[str, Any]:
    """由阶梯参数推导止损应该在哪 —— 调度器和 AI 试算工具共用的唯一实现。

    两边必须走同一份代码：AI 每小时按工具返回的价位做判断，一旦工具的算法和
    真正执行的算法漂移，模型看到的就是假数字，它的决策也就失去意义。

    棘轮性质在此处结构性保证：只返回比现有止损更靠有利方向的候选值，
    所以即使 AI 把 trailing_distance_r 调宽，已经推进的止损也不会退回去。
    """
    peak_profit = (mfe_price - entry_price) if is_long else (entry_price - mfe_price)

    candidate = None
    stage = None
    if r_unit > 0 and peak_profit >= trailing_trigger_r * r_unit:
        offset = trailing_distance_r * r_unit
        candidate = (mfe_price - offset) if is_long else (mfe_price + offset)
        stage = "TRAILING"
    elif r_unit > 0 and peak_profit >= breakeven_trigger_r * r_unit:
        candidate = entry_price
        stage = "BREAKEVEN"

    improved = False
    if candidate is not None:
        improved = (candidate > current_stop) if is_long else (candidate < current_stop)

    return {
        "peak_profit": peak_profit,
        "peak_r": (peak_profit / r_unit) if r_unit > 0 else 0.0,
        "candidate": candidate,
        "stage": stage,
        "improved": improved,
        "resulting_stop": candidate if improved else current_stop,
    }


def build_tool_schema(
    stop_mult_min: float,
    stop_mult_max: float,
    tp_r_min: float,
    tp_r_max: float,
) -> List[Dict[str, Any]]:
    """OpenAI function calling 格式的工具定义"""
    return [{
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "把止损/止盈的相对倍数换算成真实价位和真实亏损金额。"
                "在决定 stop_atr_mult 和 tp_trigger_r 之前必须调用，"
                "用返回的 loss_at_stop_usd / loss_pct_of_equity 判断这组参数能否接受；"
                "不满意就换一组倍数再调用一次。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["LONG", "SHORT"],
                        "description": "打算开的方向",
                    },
                    "stop_atr_mult": {
                        "type": "number",
                        "description": (
                            f"止损距离 = ATR × 该倍数，允许 {stop_mult_min}~{stop_mult_max}。"
                            "越小越容易被噪声扫掉，越大单笔亏损越多。"
                        ),
                    },
                    "tp_trigger_r": {
                        "type": "number",
                        "description": (
                            f"浮盈达到该 R 倍数时部分止盈，允许 {tp_r_min}~{tp_r_max}。"
                            "R = |入场价 - 初始止损|。"
                        ),
                    },
                    "position_size_hint": {
                        "type": "string",
                        "enum": list(SIZE_PCT_MAP.keys()),
                        "description": "保证金占权益比例，缺省沿用你本次决策的取值",
                    },
                    "leverage": {
                        "type": "integer",
                        "description": "杠杆倍数 1~20，缺省沿用你本次决策的取值",
                    },
                },
                "required": ["direction", "stop_atr_mult", "tp_trigger_r"],
            },
        },
    }]


def build_ladder_tool_schema() -> List[Dict[str, Any]]:
    """持仓期间重调阶梯参数的工具定义"""
    return [{
        "type": "function",
        "function": {
            "name": LADDER_TOOL_NAME,
            "description": (
                "把持仓阶梯的 R 倍数换算成这一仓的真实价位和真实金额。"
                "R 在开仓时已冻结，无法再改，你只能改各级触发线。"
                "调整任何一项之前必须调用，用返回的 resulting_stop_price / "
                "exits_immediately / tp_fires_immediately 确认后果。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "breakeven_trigger_r": {
                        "type": "number",
                        "description": "峰值浮盈达到该 R 倍数时，止损抬到成本价",
                    },
                    "trailing_trigger_r": {
                        "type": "number",
                        "description": "峰值浮盈达到该 R 倍数时，启动移动止损",
                    },
                    "trailing_distance_r": {
                        "type": "number",
                        "description": "移动止损挂在峰值回撤该 R 倍数处",
                    },
                    "tp_trigger_r": {
                        "type": "number",
                        "description": "浮盈达到该 R 倍数时落袋一部分（本仓已落袋则无效）",
                    },
                    "tp_fraction": {
                        "type": "number",
                        "description": "落袋的仓位比例 0~1；填 0 表示本仓不落袋、全靠移动止损",
                    },
                },
                "required": [
                    "breakeven_trigger_r", "trailing_trigger_r",
                    "trailing_distance_r", "tp_trigger_r", "tp_fraction",
                ],
            },
        },
    }]


class LadderTool:
    """持仓期间的阶梯试算器

    与 RiskLevelTool 的区别：开仓那个要定 R（止损宽度、仓位、杠杆都还没定），
    这个的 R 已经冻结，只能移动各级触发线，所以入参和返回值完全不同。
    """

    def __init__(
        self,
        direction: str,
        entry_price: float,
        size_btc: float,
        current_stop: float,
        liquidation_price: float,
        r_unit: float,
        mfe_price: float,
        btc_price: float,
        tp_taken: bool,
        defaults: Dict[str, float],
    ):
        self.is_long = str(direction).upper() == "LONG"
        self.entry_price = float(entry_price)
        self.size_btc = float(size_btc)
        self.current_stop = float(current_stop)
        self.liquidation_price = float(liquidation_price)
        self.r_unit = float(r_unit)
        self.mfe_price = float(mfe_price) or self.entry_price
        self.btc_price = float(btc_price)
        self.tp_taken = bool(tp_taken)
        self.defaults = dict(defaults)
        self.call_log: List[Dict[str, Any]] = []

    @property
    def schema(self) -> List[Dict[str, Any]]:
        return build_ladder_tool_schema()

    def dispatch(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name != LADDER_TOOL_NAME:
            return {"error": f"未知工具 {name}，可用工具: {LADDER_TOOL_NAME}"}
        if not isinstance(args, dict):
            return {"error": "参数格式错误"}
        repeated = _repeat_guard(self.call_log, args)
        if repeated is not None:
            return repeated
        result = self.compute(**args)
        self.call_log.append({"args": args, "result": result, "_key": _args_key(args)})
        return result

    def _num(self, name: str, value, warnings: List[str], allow_zero=False):
        """只做类型与结构校验，不做区间钳制 —— 区间由 AI 全权决定。"""
        default = self.defaults.get(name, 0.0)
        try:
            v = float(value)
        except (TypeError, ValueError):
            warnings.append(f"{name} 不是数值（收到 {value!r}），已回退当前生效值 {default}")
            return default
        if v != v or v in (float("inf"), float("-inf")):
            warnings.append(f"{name} 不是有限数值，已回退当前生效值 {default}")
            return default
        if v < 0 or (v == 0 and not allow_zero):
            warnings.append(f"{name} 必须为正数（收到 {v}），已回退当前生效值 {default}")
            return default
        return v

    def compute(
        self,
        breakeven_trigger_r=None,
        trailing_trigger_r=None,
        trailing_distance_r=None,
        tp_trigger_r=None,
        tp_fraction=None,
        **_ignored,
    ) -> Dict[str, Any]:
        if self.r_unit <= 0:
            return {"error": "本仓 R 未知（缺少初始止损），无法试算阶梯"}

        warnings: List[str] = []
        be_r = self._num("breakeven_trigger_r", breakeven_trigger_r, warnings)
        tr_r = self._num("trailing_trigger_r", trailing_trigger_r, warnings)
        td_r = self._num("trailing_distance_r", trailing_distance_r, warnings)
        tp_r = self._num("tp_trigger_r", tp_trigger_r, warnings)
        tp_f = self._num("tp_fraction", tp_fraction, warnings, allow_zero=True)
        if tp_f > 1:
            warnings.append(f"tp_fraction {tp_f} 超过 1（整仓），已按 1 处理")
            tp_f = 1.0

        sign = 1 if self.is_long else -1
        R = self.r_unit
        profit = sign * (self.btc_price - self.entry_price)

        ladder = resolve_ladder_stop(
            self.is_long, self.entry_price, self.mfe_price, self.current_stop,
            R, be_r, tr_r, td_r,
        )
        new_stop = ladder["resulting_stop"]

        # 立即离场判断：止损落到现价的不利侧，下一 tick 就会平仓
        exits_now = (new_stop >= self.btc_price) if self.is_long \
            else (new_stop <= self.btc_price)
        if exits_now:
            warnings.append(
                f"这组参数会把止损推到 ${new_stop:,.0f}，已越过现价 "
                f"${self.btc_price:,.0f}，下一次检查就会平仓离场"
            )

        tp_price = self.entry_price + sign * R * tp_r
        tp_fires_now = (not self.tp_taken) and tp_f > 0 and profit >= tp_r * R
        remaining = self.size_btc * (1 - tp_f) if tp_fires_now else self.size_btc

        if td_r >= tr_r and tr_r > 0:
            warnings.append(
                f"trailing_distance_r ({td_r}) >= trailing_trigger_r ({tr_r})："
                "移动止损启动时就会落在成本价下方，等于放弃保本"
            )
        if not self.tp_taken and tp_f > 0 and tp_r >= tr_r > 0:
            warnings.append(
                f"tp_trigger_r ({tp_r}) >= trailing_trigger_r ({tr_r})："
                "移动止损会先于止盈生效，这笔部分止盈可能永远不触发"
            )

        return {
            "direction": "LONG" if self.is_long else "SHORT",
            "entry_price": round(self.entry_price, 2),
            "current_price": round(self.btc_price, 2),
            "r_unit_usd": round(R, 2),
            "r_unit_pct": round(R / self.entry_price * 100, 3) if self.entry_price else 0,
            "profit_now_r": round(profit / R, 3),
            "peak_r": round(ladder["peak_r"], 3),
            "current_stop_price": round(self.current_stop, 2),
            "breakeven_trigger_r": be_r,
            "trailing_trigger_r": tr_r,
            "trailing_distance_r": td_r,
            "resulting_stop_price": round(new_stop, 2),
            "resulting_stop_r": round(sign * (new_stop - self.entry_price) / R, 3),
            "resulting_stop_stage": ladder["stage"] or "INIT",
            "stop_moved": ladder["improved"],
            "exits_immediately": exits_now,
            "tp_trigger_r": tp_r,
            "tp_fraction": tp_f,
            "tp_price": round(tp_price, 2),
            "tp_taken_already": self.tp_taken,
            "tp_fires_immediately": tp_fires_now,
            "profit_at_tp_usd": round(self.size_btc * R * tp_r * tp_f, 2),
            "size_btc_after_tp": round(remaining, 6),
            "liquidation_price": round(self.liquidation_price, 2),
            "warnings": warnings,
        }

    def summary(self) -> str:
        if not self.call_log:
            return "无工具调用"
        parts = []
        for c in self.call_log:
            a, res = c["args"], c["result"]
            if "error" in res:
                parts.append(f"{json.dumps(a, ensure_ascii=False)}→{res['error']}")
                continue
            parts.append(
                f"be={res['breakeven_trigger_r']}R tr={res['trailing_trigger_r']}R"
                f"/{res['trailing_distance_r']}R tp={res['tp_trigger_r']}R"
                f"×{res['tp_fraction']:.0%} → 止损=${res['resulting_stop_price']:,.0f}"
                f"({res['resulting_stop_r']:+.2f}R)"
                + ("  ⚠️立即离场" if res["exits_immediately"] else "")
            )
        return " | ".join(parts)


class RiskLevelTool:
    """有状态的工具执行器：捕获本轮的现价 / ATR / 权益，供模型反复试算"""

    def __init__(
        self,
        entry_price: float,
        klines: List[List],
        equity: float,
        long_level: PositionLevel,
        short_level: PositionLevel,
        risk_cfg,
        min_notional: float,
        default_size_hint: str = "50%",
        default_leverage: int = 5,
    ):
        self.entry_price = float(entry_price)
        self.equity = max(float(equity or 0), 0.0)
        self.levels = {"LONG": long_level, "SHORT": short_level}
        self.risk_cfg = risk_cfg
        self.min_notional = float(min_notional)
        self.default_size_hint = default_size_hint
        self.default_leverage = default_leverage
        self.call_log: List[Dict[str, Any]] = []

        # ATR 只算一次：同一轮决策里模型可能调用多次，重算既浪费也可能不一致
        try:
            self.atr = float(long_level.atr_calc.calculate(klines).value)
        except Exception as e:
            logger.warning(f"⚠️ 风控工具 ATR 计算失败，回退到入场价 2%: {e}")
            self.atr = self.entry_price * 0.02

    @property
    def schema(self) -> List[Dict[str, Any]]:
        r = self.risk_cfg
        return build_tool_schema(
            r.ai_stop_atr_mult_min, r.ai_stop_atr_mult_max,
            r.ai_tp_trigger_r_min, r.ai_tp_trigger_r_max,
        )

    def dispatch(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """LLMClient.chat_with_tools 的 dispatch 回调"""
        if name != TOOL_NAME:
            return {"error": f"未知工具 {name}，可用工具: {TOOL_NAME}"}
        if not isinstance(args, dict):
            return {"error": "参数格式错误"}
        repeated = _repeat_guard(self.call_log, args)
        if repeated is not None:
            return repeated
        result = self.compute(**args)
        self.call_log.append({"args": args, "result": result, "_key": _args_key(args)})
        return result

    @staticmethod
    def _clamp(name: str, value, lo: float, hi: float, default: float) -> tuple:
        """返回 (生效值, 告警文案或 None)

        缺失/非数值和越界是两回事，文案要分开：告警说错了原因，模型就会去
        改本来没问题的那个参数。
        """
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default, f"{name} 不是数值（收到 {value!r}），已回退默认值 {default}"
        if v != v or v in (float("inf"), float("-inf")):
            return default, f"{name} 不是有限数值，已回退默认值 {default}"
        clamped = min(max(v, lo), hi)
        if clamped != v:
            return clamped, f"{name} 超出允许区间 [{lo}, {hi}]，已钳制为 {clamped}"
        return clamped, None

    def compute(
        self,
        direction: str = "LONG",
        stop_atr_mult: Optional[float] = None,
        tp_trigger_r: Optional[float] = None,
        position_size_hint: Optional[str] = None,
        leverage: Optional[int] = None,
        **_ignored,
    ) -> Dict[str, Any]:
        direction = str(direction or "").upper()
        if direction not in self.levels:
            return {"error": f"direction 必须是 LONG 或 SHORT，收到 {direction!r}"}

        r = self.risk_cfg
        stop_mult, stop_warn = self._clamp(
            "stop_atr_mult", stop_atr_mult,
            r.ai_stop_atr_mult_min, r.ai_stop_atr_mult_max, r.ai_stop_atr_mult_min,
        )
        tp_r, tp_warn = self._clamp(
            "tp_trigger_r", tp_trigger_r,
            r.ai_tp_trigger_r_min, r.ai_tp_trigger_r_max, r.tp_trigger_r,
        )

        size_pct = SIZE_PCT_MAP.get(position_size_hint or self.default_size_hint, 0.50)
        try:
            lev = max(1, min(20, int(leverage if leverage is not None else self.default_leverage)))
        except (TypeError, ValueError):
            lev = self.default_leverage

        margin = self.equity * size_pct
        notional = margin * lev
        if size_pct > 0:
            notional = max(self.min_notional, notional)

        is_long = direction == "LONG"
        level = self.levels[direction]
        built = level.preview(
            self.entry_price, self.atr, stop_mult, lev, notional,
        )
        stop_price = built["stop_loss"]
        liquidation = built["liquidation_price"]

        # R 取实际止损距离：止损可能因穿透强平价被拉回，此时 R 小于 ATR × 倍数
        r_unit = abs(self.entry_price - stop_price)
        qty = notional / self.entry_price if self.entry_price else 0.0
        loss_at_stop = qty * r_unit
        loss_pct_equity = (loss_at_stop / self.equity * 100) if self.equity else 0.0

        tp_price = self.entry_price + (1 if is_long else -1) * r_unit * tp_r
        tp_fraction = r.tp_fraction
        profit_at_tp = qty * r_unit * tp_r * tp_fraction

        warnings = [w for w in (stop_warn, tp_warn) if w]
        if abs(r_unit - self.atr * stop_mult) > self.entry_price * 1e-6:
            warnings.append(
                "止损距离被强平价约束收窄，实际 R 小于 ATR × 倍数；"
                "考虑降低杠杆或减小仓位"
            )

        return {
            "direction": direction,
            "entry_price": round(self.entry_price, 2),
            "atr": round(self.atr, 2),
            "stop_atr_mult": stop_mult,
            "tp_trigger_r": tp_r,
            "stop_price": round(stop_price, 2),
            "stop_distance_usd": round(r_unit, 2),
            "stop_distance_pct": round(r_unit / self.entry_price * 100, 3) if self.entry_price else 0,
            "liquidation_price": round(liquidation, 2),
            "position_size_hint": position_size_hint or self.default_size_hint,
            "leverage": lev,
            "margin_usd": round(margin, 2),
            "notional_usd": round(notional, 2),
            "r_unit_usd": round(r_unit, 2),
            "loss_at_stop_usd": round(loss_at_stop, 2),
            "loss_pct_of_equity": round(loss_pct_equity, 3),
            "tp_price": round(tp_price, 2),
            "tp_fraction": tp_fraction,
            "profit_at_tp_usd": round(profit_at_tp, 2),
            "warnings": warnings,
        }

    def summary(self) -> str:
        """本轮工具调用记录，写日志用"""
        if not self.call_log:
            return "无工具调用"
        parts = []
        for c in self.call_log:
            a, res = c["args"], c["result"]
            if "error" in res:
                parts.append(f"{json.dumps(a, ensure_ascii=False)}→{res['error']}")
            else:
                parts.append(
                    f"{res['direction']} stop={res['stop_atr_mult']}ATR"
                    f"(${res['stop_price']:,.0f}) tp={res['tp_trigger_r']}R"
                    f"(${res['tp_price']:,.0f}) 亏损=${res['loss_at_stop_usd']:,.0f}"
                    f"({res['loss_pct_of_equity']:.2f}%)"
                )
        return " | ".join(parts)
