#!/usr/bin/env python3
"""持仓期间 AI 每小时重调阶梯参数

核心不变量：
  - 执行仍在 60 秒的纯计算里，AI 只改参数
  - 工具算出的价位必须与调度器真正执行的一致（共用 resolve_ladder_stop）
  - 棘轮是结构性的：放宽参数不能让已推进的止损退回去

验证场景:
1. 工具与调度器对同一组参数得出同一个止损价
2. AI 收紧 trailing_distance_r → 止损跟得更近
3. AI 放宽 trailing_distance_r → 已推进的止损不回退（棘轮）
4. AI 用 tp_fraction=0 关闭本仓落袋
5. AI 改保本线 → 保本触发点随之移动
6. 阶梯覆盖值落盘 / 恢复
7. 平仓后阶梯覆盖值清空，不污染下一仓
8. exits_immediately 标志
9. trailing_distance >= trailing_trigger 告警
10. tp_trigger >= trailing_trigger 告警
11. 非法值回落当前生效值，而不是崩掉或用 0
12. 阶梯参数穿过护栏不丢失
13. 缓存命中时不重复写阶梯
14. R 坐标区分「冲高回落」与「缓慢推进」
15. 持仓才构造 LadderTool，空仓不构造
"""

import asyncio
import json

from core import ParameterSet, TradingConfig
from multi_agent.risk_tools import LADDER_TOOL_NAME, resolve_ladder_stop
from multi_agent.trading_advisor import TradingDecision
from server.trading_scheduler.sim_scheduler import SimTradingScheduler
from utils.llm_client import LLMClient

ENTRY = 80000.0
R = 1000.0


def make_scheduler():
    return SimTradingScheduler(
        config=TradingConfig.get_preset(ParameterSet.STANDARD),
        check_interval=60,
    )


def arm(sched, direction="LONG", size=0.05):
    pos = sched.position
    pos.direction = direction
    pos.entry_price = ENTRY
    pos.size_btc = size
    pos.leverage = 5
    pos.stop_loss = ENTRY - R if direction == "LONG" else ENTRY + R
    pos.liquidation_price = ENTRY - 15000.0 if direction == "LONG" else ENTRY + 15000.0
    sched._arm_protective_stop()
    return pos


def test_1_tool_matches_scheduler_stop():
    """工具和安全网必须走同一份阶梯数学，否则模型看到的是假数字。"""
    print("\n[Test 1] 工具算的止损 == 调度器执行的止损")
    sched = make_scheduler()
    pos = arm(sched)

    peak = ENTRY + 2.0 * R
    asyncio.run(sched._update_protective_stop(peak))
    real_stop = pos.stop_loss

    # 用同样的峰值、同样的参数问工具（把止损退回初始值以复现推进过程）
    fresh = make_scheduler()
    p2 = arm(fresh)
    p2.mfe_price = peak
    tool = fresh._build_ladder_tool(peak)
    res = tool.compute(
        breakeven_trigger_r=0.5, trailing_trigger_r=1.5,
        trailing_distance_r=1.25, tp_trigger_r=1.0, tp_fraction=0.5,
    )

    assert abs(res["resulting_stop_price"] - real_stop) < 0.01, \
        f"工具 {res['resulting_stop_price']} vs 实际 {real_stop}"
    assert res["resulting_stop_stage"] == "TRAILING", res["resulting_stop_stage"]
    print(f"  ✅ 两边一致: ${real_stop:,.0f} ({res['resulting_stop_r']:+.2f}R)")


def test_2_ai_tightens_trailing_distance():
    print("\n[Test 2] AI 收紧移动止损距离 → 止损跟得更近")
    sched = make_scheduler()
    pos = arm(sched)
    pos.trailing_distance_r = 0.5      # 默认 1.25 → 收紧到 0.5

    peak = ENTRY + 2.0 * R
    asyncio.run(sched._update_protective_stop(peak))

    assert abs(pos.stop_loss - (peak - 0.5 * R)) < 1e-6, pos.stop_loss
    print(f"  ✅ 峰值 ${peak:,.0f} → 止损 ${pos.stop_loss:,.0f}（回撤 0.5R）")


def test_3_widening_cannot_retreat_advanced_stop():
    """棘轮是结构性的：这是全权授权下最重要的一条安全性质。"""
    print("\n[Test 3] AI 放宽距离 → 已推进的止损不回退")
    sched = make_scheduler()
    pos = arm(sched)

    peak = ENTRY + 2.0 * R
    asyncio.run(sched._update_protective_stop(peak))
    tight_stop = pos.stop_loss

    # AI 反悔，把距离放宽到 3R（候选止损会远低于现有止损）
    pos.trailing_distance_r = 3.0
    asyncio.run(sched._update_protective_stop(peak))

    assert pos.stop_loss == tight_stop, \
        f"止损被放宽退回了: {tight_stop} → {pos.stop_loss}"
    print(f"  ✅ 止损仍锁在 ${tight_stop:,.0f}，放宽无效")


def test_4_ai_disables_partial_tp():
    print("\n[Test 4] tp_fraction=0 → 本仓不落袋")
    sched = make_scheduler()
    pos = arm(sched)
    pos.tp_fraction = 0.0

    trade = asyncio.run(sched._check_partial_take_profit(ENTRY + 1.5 * R))
    assert trade is None, "AI 已关闭落袋，不应触发"
    assert abs(pos.size_btc - 0.05) < 1e-9, pos.size_btc
    assert pos.ladder(sched.config.risk)["tp_fraction"] == 0.0
    print("  ✅ 浮盈 1.5R 也不落袋，全交给移动止损")


def test_5_ai_moves_breakeven_line():
    print("\n[Test 5] AI 改保本线 → 触发点随之移动")
    sched = make_scheduler()
    pos = arm(sched)
    pos.breakeven_trigger_r = 1.2      # 默认 0.5 → 推迟到 1.2R

    asyncio.run(sched._update_protective_stop(ENTRY + 0.8 * R))
    assert pos.stop_stage == "INIT", "0.8R 已不足新的 1.2R 保本线"

    asyncio.run(sched._update_protective_stop(ENTRY + 1.2 * R))
    assert pos.stop_stage == "BREAKEVEN", pos.stop_stage
    assert abs(pos.stop_loss - ENTRY) < 1e-6
    print("  ✅ 0.8R 不动，1.2R 才保本")


def test_6_ladder_overrides_survive_state_roundtrip():
    print("\n[Test 6] 阶梯覆盖值落盘与恢复")
    sched = make_scheduler()
    pos = arm(sched)
    pos.breakeven_trigger_r = 0.8
    pos.trailing_trigger_r = 2.0
    pos.trailing_distance_r = 0.9
    pos.tp_trigger_r = 1.8
    pos.tp_fraction = 0.0
    pos.atr_at_open = 1500.0
    pos.stop_atr_mult = 1.4

    state = sched._get_position_state()
    fresh = make_scheduler()
    fresh._apply_position_state(state)

    lad = fresh.position.ladder(fresh.config.risk)
    assert lad["breakeven_trigger_r"] == 0.8, lad
    assert lad["trailing_trigger_r"] == 2.0, lad
    assert lad["trailing_distance_r"] == 0.9, lad
    assert lad["tp_trigger_r"] == 1.8, lad
    assert lad["tp_fraction"] == 0.0, "关闭落袋必须能被持久化"
    assert fresh.position.atr_at_open == 1500.0
    assert fresh.position.stop_atr_mult == 1.4
    print("  ✅ 五个阶梯字段 + ATR 留档全部往返正确")


def test_7_reset_clears_overrides():
    """上一仓的临时判断绝不能延续到下一仓。"""
    print("\n[Test 7] 平仓后阶梯覆盖值清空")
    sched = make_scheduler()
    pos = arm(sched)
    pos.trailing_distance_r = 0.3
    pos.tp_fraction = 0.0

    pos.reset()

    lad = pos.ladder(sched.config.risk)
    risk = sched.config.risk
    assert lad["trailing_distance_r"] == risk.trailing_distance_r, lad
    assert lad["tp_fraction"] == risk.tp_fraction, lad
    print("  ✅ 回到配置默认，无跨仓污染")


def test_8_exits_immediately_flag():
    print("\n[Test 8] 会立刻平仓的参数组被标记")
    sched = make_scheduler()
    pos = arm(sched)
    # 峰值到过 2R，价格已回落到 0.5R
    pos.mfe_price = ENTRY + 2.0 * R
    price = ENTRY + 0.5 * R
    tool = sched._build_ladder_tool(price)

    # 距离 0.5R → 止损 = 峰值 - 0.5R = 1.5R，已在现价（0.5R）之上
    res = tool.compute(
        breakeven_trigger_r=0.5, trailing_trigger_r=1.5,
        trailing_distance_r=0.5, tp_trigger_r=1.0, tp_fraction=0.5,
    )

    assert res["exits_immediately"] is True, res
    assert any("平仓离场" in w for w in res["warnings"]), res["warnings"]
    print(f"  ✅ 止损 ${res['resulting_stop_price']:,.0f} 已越过现价，已告警")


def test_9_warns_distance_exceeds_trigger():
    print("\n[Test 9] 距离 >= 启动线 → 告警放弃保本")
    sched = make_scheduler()
    arm(sched)
    tool = sched._build_ladder_tool(ENTRY + 0.2 * R)

    res = tool.compute(
        breakeven_trigger_r=0.5, trailing_trigger_r=1.5,
        trailing_distance_r=1.5, tp_trigger_r=1.0, tp_fraction=0.5,
    )
    assert any("放弃保本" in w for w in res["warnings"]), res["warnings"]
    print("  ✅ 已告警")


def test_10_warns_tp_after_trailing():
    print("\n[Test 10] 止盈线 >= 移动止损启动线 → 告警可能永不触发")
    sched = make_scheduler()
    arm(sched)
    tool = sched._build_ladder_tool(ENTRY + 0.2 * R)

    res = tool.compute(
        breakeven_trigger_r=0.5, trailing_trigger_r=1.5,
        trailing_distance_r=1.25, tp_trigger_r=2.5, tp_fraction=0.5,
    )
    assert any("永远不触发" in w for w in res["warnings"]), res["warnings"]
    print("  ✅ 已告警")


def test_11_invalid_values_fall_back_to_effective():
    """全权授权不等于接受垃圾：非数值/负数回落当前生效值并告知模型。"""
    print("\n[Test 11] 非法值回落当前生效值")
    sched = make_scheduler()
    arm(sched)
    tool = sched._build_ladder_tool(ENTRY + 0.2 * R)

    res = tool.compute(
        breakeven_trigger_r="abc", trailing_trigger_r=-1.0,
        trailing_distance_r=float("inf"), tp_trigger_r=0, tp_fraction=1.5,
    )
    risk = sched.config.risk
    assert res["breakeven_trigger_r"] == risk.breakeven_trigger_r, res
    assert res["trailing_trigger_r"] == risk.trailing_trigger_r, res
    assert res["trailing_distance_r"] == risk.trailing_distance_r, res
    assert res["tp_trigger_r"] == risk.tp_trigger_r, "0 触发线无意义，应回落"
    assert res["tp_fraction"] == 1.0, "超过整仓应按 1 处理"
    assert len(res["warnings"]) >= 5, res["warnings"]
    print(f"  ✅ 5 项非法值全部回落并告警 ({len(res['warnings'])} 条)")


class FakeLLM(LLMClient):
    def __init__(self, scripted):
        super().__init__(model_name="fake", key="k", api_url="http://x")
        self.scripted = list(scripted)
        self.seen_tools = []

    def _make_request(self, messages, temperature=0.6, extra_body=None, tools=None):
        self.seen_tools.append(tools)
        return {"choices": [{"message": self.scripted.pop(0)}], "usage": {}}


def _holding_signal(bias="LONG", confidence=70):
    return {
        "_memory_id": "sig-1",
        "bias": bias,
        "confidence": confidence,
        "trend_regime": "UP_TREND",
        "volatility_regime": "NORMAL_VOL",
        "entry_ok": True,
        "summary": "s",
    }


def _decide(sched, advisor_payload, signal, btc_price):
    pos = sched.position
    return sched.trading_advisor.decide(
        signal=signal,
        position_direction=pos.direction,
        position_entry=pos.entry_price,
        position_size_btc=pos.size_btc,
        position_leverage=pos.leverage,
        position_stop_loss=pos.stop_loss,
        position_liquidation=pos.liquidation_price,
        btc_price=btc_price,
        equity=1000.0,
        ladder_tool=sched._build_ladder_tool(btc_price),
        position_risk=sched._build_position_risk(btc_price, []),
    )


def test_12_ladder_survives_policy_guard():
    """护栏管动作，不该顺手吞掉 AI 的阶梯调整。"""
    print("\n[Test 12] 阶梯参数穿过护栏不丢失")
    sched = make_scheduler()
    arm(sched)
    price = ENTRY + 0.2 * R

    # 护栏会把「平仓」降级/拦截为持仓观望，途中重建 TradingDecision
    payload = json.dumps({
        "action": "平仓",
        "trailing_distance_r": 0.7,
        "tp_fraction": 0.0,
        "reason": "MODERATE 看多，收紧保护",
    })
    sched.trading_advisor.llm = FakeLLM([{"role": "assistant", "content": payload}])

    decision = _decide(sched, payload, _holding_signal(), price)

    assert decision.action == "持仓观望", decision.action
    assert decision.trailing_distance_r == 0.7, "阶梯参数被护栏吞掉了"
    assert decision.tp_fraction == 0.0, "关闭落袋的意图被吞掉了"

    assert sched._apply_ai_ladder(decision) is True
    lad = sched.position.ladder(sched.config.risk)
    assert lad["trailing_distance_r"] == 0.7, lad
    assert lad["tp_fraction"] == 0.0, lad
    print("  ✅ 动作被拦截，阶梯调整仍然生效")


def test_13_cached_decision_does_not_rewrite_ladder():
    print("\n[Test 13] 缓存命中时不重复写阶梯")
    sched = make_scheduler()
    arm(sched)

    decision = TradingDecision(action="持仓观望", trailing_distance_r=0.6)
    assert sched._apply_ai_ladder(decision) is True

    decision._from_cache = True
    decision.trailing_distance_r = 0.2
    assert sched._apply_ai_ladder(decision) is False, "缓存决策不该改阶梯"
    assert sched.position.ladder(sched.config.risk)["trailing_distance_r"] == 0.6
    print("  ✅ 只有真正调过 LLM 的那次才写入")


def test_14_position_risk_separates_peak_from_current():
    """冲高回落和缓慢推进在当前浮盈上一样，必须靠峰值区分。"""
    print("\n[Test 14] R 坐标区分冲高回落 / 缓慢推进")
    faded = make_scheduler()
    p1 = arm(faded)
    p1.mfe_price = ENTRY + 1.4 * R          # 冲到 1.4R
    risk_faded = faded._build_position_risk(ENTRY + 0.3 * R, [])

    ground = make_scheduler()
    p2 = arm(ground)
    p2.mfe_price = ENTRY + 0.3 * R          # 一路磨上来
    risk_ground = ground._build_position_risk(ENTRY + 0.3 * R, [])

    assert abs(risk_faded["profit_r"] - risk_ground["profit_r"]) < 1e-9, \
        "两者当前浮盈本应相同"
    assert abs(risk_faded["peak_r"] - 1.4) < 1e-9, risk_faded["peak_r"]
    assert abs(risk_ground["peak_r"] - 0.3) < 1e-9, risk_ground["peak_r"]
    assert abs(risk_faded["drawdown_from_peak_r"] - 1.1) < 1e-9, risk_faded
    assert risk_ground["drawdown_from_peak_r"] == 0.0, risk_ground
    print("  ✅ 当前浮盈同为 +0.30R，峰值 1.40R vs 0.30R 可区分")


def test_15_ladder_tool_only_when_holding():
    print("\n[Test 15] 持仓才构造 LadderTool")
    sched = make_scheduler()
    assert sched._build_ladder_tool(ENTRY) is None, "空仓不该构造"

    arm(sched)
    tool = sched._build_ladder_tool(ENTRY)
    assert tool is not None
    assert tool.schema[0]["function"]["name"] == LADDER_TOOL_NAME

    # R 未知（缺初始止损）时明确报错，而不是拿 0 去算
    sched.position.initial_stop = 0.0
    assert sched._build_ladder_tool(ENTRY) is None
    print("  ✅ 空仓 / R 未知都不构造")


def test_17_repeat_call_is_short_circuited():
    """模型会用一模一样的参数反复调用直到耗尽轮数，必须硬兜底。"""
    print("\n[Test 17] 同参数重复试算 → 直接要求收敛")
    sched = make_scheduler()
    arm(sched)
    tool = sched._build_ladder_tool(ENTRY + 0.2 * R)

    args = {
        "breakeven_trigger_r": 0.5, "trailing_trigger_r": 1.5,
        "trailing_distance_r": 1.25, "tp_trigger_r": 1.0, "tp_fraction": 0.5,
    }
    first = tool.dispatch(LADDER_TOOL_NAME, dict(args))
    assert "note" not in first, "首次调用不该有收敛提示"

    second = tool.dispatch(LADDER_TOOL_NAME, dict(args))
    assert "note" in second, second
    assert "不要再重复调用" in second["note"], second["note"]
    # 结果数值仍与首次一致，且不重复计入调用日志
    assert second["resulting_stop_price"] == first["resulting_stop_price"]
    assert len(tool.call_log) == 1, tool.call_log

    # 换一组不同的参数应正常计算
    third = tool.dispatch(LADDER_TOOL_NAME, {**args, "trailing_distance_r": 0.8})
    assert "note" not in third, third
    assert len(tool.call_log) == 2
    print("  ✅ 重复调用被短路，换参数正常放行")


def test_16_shared_ladder_math_mirrors_short():
    print("\n[Test 16] resolve_ladder_stop 空头镜像")
    res = resolve_ladder_stop(
        is_long=False, entry_price=ENTRY, mfe_price=ENTRY - 2.0 * R,
        current_stop=ENTRY + R, r_unit=R,
        breakeven_trigger_r=0.5, trailing_trigger_r=1.5, trailing_distance_r=1.25,
    )
    assert res["stage"] == "TRAILING", res
    assert abs(res["peak_r"] - 2.0) < 1e-9, res
    # 空头止损在上方：峰值 + 1.25R
    assert abs(res["resulting_stop"] - (ENTRY - 2.0 * R + 1.25 * R)) < 1e-6, res
    assert res["improved"] is True
    print(f"  ✅ 空头止损落在 ${res['resulting_stop']:,.0f}")
