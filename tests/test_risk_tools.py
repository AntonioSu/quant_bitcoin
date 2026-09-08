#!/usr/bin/env python3
"""Trading AI 风控工具 (function calling) 测试

验证场景:
1. 工具算出的止损价与真实开仓路径一致
2. 多空方向镜像；非法方向报错
3. 倍数越界被钳制并给出 warnings
4. 亏损金额随仓位/杠杆/止损宽度线性放大
5. 止损被强平价约束时 R 收窄并告警（+1R 参照价随之收窄）
6. 工具 schema 结构符合 OpenAI function calling 格式
7. LLMClient.chat_with_tools 完整跑通一轮工具调用
8. 工具抛异常时错误回灌给模型，不中断决策
9. 工具调用轮数上限保护
10. 空仓才构造工具，持仓时不构造
"""

import json

from core import ParameterSet, TradingConfig
from multi_agent.risk_tools import TOOL_NAME, RiskLevelTool
from server.trading_scheduler.sim_scheduler import SimTradingScheduler
from utils.llm_client import LLMClient

ENTRY = 80000.0
EQUITY = 1000.0


def make_klines(count=60, high=81000.0, low=79000.0):
    """构造有稳定真实波幅的 K 线，ATR 可算"""
    return [[i, low, high, low, high, 100.0] for i in range(count)]


def make_scheduler(equity=EQUITY):
    sched = SimTradingScheduler(
        config=TradingConfig.get_preset(ParameterSet.STANDARD),
        check_interval=60,
    )
    sched.equity = equity
    return sched


def make_tool(equity=EQUITY, klines=None):
    sched = make_scheduler(equity)
    return sched._build_risk_tool(ENTRY, klines or make_klines())


def test_1_tool_matches_real_open_path():
    print("\n[Test 1] 工具算的止损价 == 真实开仓算的止损价")
    sched = make_scheduler()
    klines = make_klines()
    tool = sched._build_risk_tool(ENTRY, klines)

    res = tool.compute(direction="LONG", stop_atr_mult=1.5,
                       position_size_hint="50%", leverage=5)

    # 真实开仓路径
    real = sched.long_level.calculate(
        entry_price=ENTRY, klines=klines, atr_multiplier=1.5,
        leverage=5, notional_value=EQUITY * 0.5 * 5,
    )
    assert abs(res["stop_price"] - real["stop_loss"]) < 0.01, (res, real)
    assert abs(res["atr"] - real["atr"]) < 0.01, (res, real)
    print(f"  ✅ 止损 ${res['stop_price']:,.0f} 一致, ATR=${res['atr']:,.0f}")


def test_2_direction_mirror_and_validation():
    print("\n[Test 2] 多空镜像 + 非法方向")
    tool = make_tool()

    lng = tool.compute(direction="LONG", stop_atr_mult=1.5)
    sht = tool.compute(direction="SHORT", stop_atr_mult=1.5)

    assert lng["stop_price"] < ENTRY < sht["stop_price"], (lng, sht)
    assert lng["price_at_plus_1r"] > ENTRY > sht["price_at_plus_1r"], (lng, sht)
    assert abs(lng["r_unit_usd"] - sht["r_unit_usd"]) < 0.01

    bad = tool.compute(direction="SIDEWAYS", stop_atr_mult=1.5)
    assert "error" in bad, bad
    print(f"  ✅ LONG 止损 ${lng['stop_price']:,.0f} / SHORT ${sht['stop_price']:,.0f}，非法方向报错")


def test_3_out_of_range_multipliers_clamped():
    print("\n[Test 3] 倍数越界 → 钳制 + warnings")
    tool = make_tool()
    risk = TradingConfig.get_preset(ParameterSet.STANDARD).risk

    res = tool.compute(direction="LONG", stop_atr_mult=9.0)
    assert res["stop_atr_mult"] == risk.ai_stop_atr_mult_max, res
    assert len(res["warnings"]) >= 1, res

    low = tool.compute(direction="LONG", stop_atr_mult=0.1)
    assert low["stop_atr_mult"] == risk.ai_stop_atr_mult_min, low

    junk = tool.compute(direction="LONG", stop_atr_mult="很宽")
    assert junk["stop_atr_mult"] == risk.ai_stop_atr_mult_min, junk

    # 缺失/非数值不能报成「超出区间」：告警说错原因，模型会去改没问题的参数
    assert any("不是数值" in w for w in junk["warnings"]), junk
    assert not any("超出允许区间" in w for w in junk["warnings"]), junk
    assert all("超出允许区间" in w for w in res["warnings"]), res
    print(f"  ✅ 钳制生效且原因分开，warnings={res['warnings'][0][:26]}...")


def test_4_loss_scales_with_size_and_leverage():
    print("\n[Test 4] 亏损金额随仓位/杠杆/止损宽度放大")
    tool = make_tool()

    small = tool.compute(direction="LONG", stop_atr_mult=1.0,
                         position_size_hint="25%", leverage=2)
    big = tool.compute(direction="LONG", stop_atr_mult=1.0,
                       position_size_hint="100%", leverage=10)

    # 名义本金放大 20 倍 → 亏损也放大 20 倍
    assert abs(big["notional_usd"] / small["notional_usd"] - 20) < 0.01, (small, big)
    assert abs(big["loss_at_stop_usd"] / small["loss_at_stop_usd"] - 20) < 0.01, (small, big)

    # 止损放宽一倍 → R 与亏损同步翻倍
    wide = tool.compute(direction="LONG", stop_atr_mult=2.0,
                        position_size_hint="25%", leverage=2)
    assert abs(wide["r_unit_usd"] / small["r_unit_usd"] - 2) < 0.01, (small, wide)
    assert abs(wide["loss_at_stop_usd"] / small["loss_at_stop_usd"] - 2) < 0.01

    # 权益变化要反映到亏损占比
    assert "max_loss_pct_limit" not in small, "不应引入未被系统执行的上限"
    print(f"  ✅ 25%/2x 亏 ${small['loss_at_stop_usd']:,.0f}"
          f"({small['loss_pct_of_equity']:.2f}%) → 100%/10x 亏 "
          f"${big['loss_at_stop_usd']:,.0f}({big['loss_pct_of_equity']:.2f}%)")


def test_5_liquidation_constrains_r():
    print("\n[Test 5] 止损穿透强平价 → R 收窄并告警")
    tool = make_tool()

    # 20x 杠杆强平价约在 -4.5%，3ATR 止损远于此
    res = tool.compute(direction="LONG", stop_atr_mult=3.0,
                       position_size_hint="100%", leverage=20)
    naive = res["atr"] * 3.0
    assert res["r_unit_usd"] < naive, res
    assert res["stop_price"] > res["liquidation_price"], res
    assert any("强平价" in w for w in res["warnings"]), res
    # +1R 参照价必须基于收窄后的真实 R
    assert abs(res["price_at_plus_1r"] - (ENTRY + res["r_unit_usd"])) < 0.02, res
    print(f"  ✅ 名义 R ${naive:,.0f} → 实际 R ${res['r_unit_usd']:,.0f}"
          f"（强平 ${res['liquidation_price']:,.0f}）")


def test_6_schema_shape():
    print("\n[Test 6] 工具 schema 符合 function calling 格式")
    tool = make_tool()
    schema = tool.schema

    assert isinstance(schema, list) and len(schema) == 1
    fn = schema[0]
    assert fn["type"] == "function"
    assert fn["function"]["name"] == TOOL_NAME
    params = fn["function"]["parameters"]
    assert params["type"] == "object"
    for key in ("direction", "stop_atr_mult", "position_size_hint", "leverage"):
        assert key in params["properties"], key
    assert "tp_trigger_r" not in params["properties"], "止盈线已交给 AI 自己决定，不再是工具入参"
    assert set(params["required"]) == {"direction", "stop_atr_mult"}
    # 允许区间要写进 description，否则模型不知道边界
    assert "3.0" in params["properties"]["stop_atr_mult"]["description"]
    json.dumps(schema)  # 必须可序列化
    print("  ✅ schema 合法且可序列化")


class FakeLLM(LLMClient):
    """替换 _make_request，按脚本返回响应并记录收到的 payload"""

    def __init__(self, scripted):
        super().__init__(model_name="fake", key="k", api_url="http://x")
        self.scripted = list(scripted)
        self.seen_messages = []
        self.seen_tools = []

    def _make_request(self, messages, temperature=0.6, extra_body=None, tools=None):
        self.seen_messages.append(list(messages))
        self.seen_tools.append(tools)
        msg = self.scripted.pop(0)
        return {"choices": [{"message": msg}], "usage": {}}


def _tool_call_msg(call_id="c1", args=None):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "arguments": json.dumps(args or {
                    "direction": "LONG", "stop_atr_mult": 1.5,
                }),
            },
        }],
    }


def test_7_chat_with_tools_round_trip():
    print("\n[Test 7] chat_with_tools 跑通一轮工具调用")
    tool = make_tool()
    llm = FakeLLM([
        _tool_call_msg(),
        {"role": "assistant", "content": '{"action": "开多"}'},
    ])

    out = llm.chat_with_tools(
        system_prompt="sys", prompt="user",
        tools=tool.schema, dispatch=tool.dispatch, usage_tag="[test]",
    )

    assert out == '{"action": "开多"}', out
    # 第二轮消息里必须有 assistant(tool_calls) + tool 结果，且 id 对得上
    second = llm.seen_messages[1]
    assert second[2].get("tool_calls"), second[2]
    assert second[3]["role"] == "tool" and second[3]["tool_call_id"] == "c1", second[3]
    payload = json.loads(second[3]["content"])
    assert payload["direction"] == "LONG" and payload["stop_price"] < ENTRY, payload
    assert llm.seen_tools[0] is not None, "第一轮必须带 tools"
    assert len(tool.call_log) == 1 and "无工具调用" not in tool.summary()
    print(f"  ✅ 回灌正确: {tool.summary()}")


def test_8_tool_error_is_fed_back():
    print("\n[Test 8] 工具异常/坏参数 → 错误回灌，不中断")
    tool = make_tool()

    def boom(name, args):
        raise ValueError("炸了")

    llm = FakeLLM([_tool_call_msg(), {"role": "assistant", "content": "ok"}])
    assert llm.chat_with_tools("s", "u", tool.schema, boom) == "ok"
    fed = json.loads(llm.seen_messages[1][3]["content"])
    assert "ValueError" in fed["error"], fed

    # arguments 不是合法 JSON
    broken = _tool_call_msg()
    broken["tool_calls"][0]["function"]["arguments"] = "{不是json"
    llm2 = FakeLLM([broken, {"role": "assistant", "content": "ok"}])
    assert llm2.chat_with_tools("s", "u", tool.schema, tool.dispatch) == "ok"
    fed2 = json.loads(llm2.seen_messages[1][3]["content"])
    assert "error" in fed2, fed2

    # 未知工具名
    assert "error" in tool.dispatch("unknown_tool", {})
    print("  ✅ 三类错误都以 error 回灌，模型可自行纠正")


def test_9_max_rounds_guard():
    print("\n[Test 9] 工具调用轮数上限 → 强制收敛")
    tool = make_tool()
    # 一直要求调用工具，最后一次收敛请求返回文本
    llm = FakeLLM([
        _tool_call_msg("c1"), _tool_call_msg("c2"),
        {"role": "assistant", "content": "final"},
    ])

    out = llm.chat_with_tools("s", "u", tool.schema, tool.dispatch, max_rounds=2)

    assert out == "final", out
    assert len(llm.seen_messages) == 3, len(llm.seen_messages)
    assert llm.seen_tools[-1] is None, "收敛请求不应再带 tools"

    # 收敛请求必须显式禁止工具调用：只是去掉 tools 的话，DeepSeek 系会把
    # 工具调用语法当文本吐出来，下游 JSON 解析必然失败
    final_msg = llm.seen_messages[-1][-1]
    assert final_msg["role"] == "user", final_msg
    assert "不要再调用任何工具" in final_msg["content"], final_msg
    assert "JSON" in final_msg["content"], final_msg
    print("  ✅ 达上限后显式下收敛指令并去掉 tools，拿到最终答案")


def test_10_tool_only_built_when_flat():
    print("\n[Test 10] 只有空仓才构造工具")
    sched = make_scheduler()
    assert sched._build_risk_tool(ENTRY, make_klines()) is not None
    assert sched._build_risk_tool(ENTRY, []) is None, "无 K 线不构造"

    sched.position.direction = "LONG"
    sched.position.size_btc = 0.05
    sched.position.entry_price = ENTRY
    assert sched.position.is_active
    assert sched._build_risk_tool(ENTRY, make_klines()) is None, "持仓中不构造"
    print("  ✅ 空仓构造 / 持仓与无数据均跳过")
