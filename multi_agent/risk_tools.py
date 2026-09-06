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

# 名义本金 = 权益 × 该比例 × 杠杆
SIZE_PCT_MAP = {"0%": 0.0, "25%": 0.25, "50%": 0.50, "75%": 0.75, "100%": 1.0}


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
        result = self.compute(**args) if isinstance(args, dict) else {"error": "参数格式错误"}
        self.call_log.append({"args": args, "result": result})
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
