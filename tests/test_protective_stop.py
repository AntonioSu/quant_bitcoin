#!/usr/bin/env python3
"""保本 / 移动止损 + 追高护栏测试

验证场景:
1. 浮盈未达 0.5R → 止损不动
2. 浮盈达 0.5R → 止损移到成本价
3. 峰值浮盈达 1.5R → 止损跟到峰值回撤 1.25R
4. 价格回落 → 止损棘轮不回退
5. SHORT 方向镜像成立
6. 平仓原因反映止损档位（保本 / 移动止盈）
7. 追高护栏拦截区间顺方向 60~100% 的开仓
8. 突破区间 (>100%) 放行
9. K 线不足时不拦截
"""

import asyncio


from core import ParameterSet, TradingConfig
from server.trading_scheduler.sim_scheduler import SimTradingScheduler

# R = |entry - initial_stop| = 1000
ENTRY = 80000.0
LONG_STOP = 79000.0
SHORT_STOP = 81000.0
R = 1000.0


def make_scheduler():
    return SimTradingScheduler(
        config=TradingConfig.get_preset(ParameterSet.STANDARD),
        check_interval=60,
    )


def arm(sched, direction="LONG"):
    pos = sched.position
    pos.direction = direction
    pos.entry_price = ENTRY
    pos.size_btc = 0.05
    pos.leverage = 5
    pos.stop_loss = LONG_STOP if direction == "LONG" else SHORT_STOP
    pos.liquidation_price = 0.0
    sched._arm_protective_stop()
    return pos


def make_klines(high=80000.0, low=78000.0, count=20, last_close=79000.0):
    """构造 [ts, open, high, low, close, volume] K 线，最后一根为进行中的当前根"""
    bars = [[i, low, high, low, high, 100.0] for i in range(count - 1)]
    bars.append([count - 1, last_close, last_close, last_close, last_close, 100.0])
    return bars


def test_1_below_breakeven_trigger_keeps_stop():
    print("\n[Test 1] 浮盈 0.3R → 止损不动")
    sched = make_scheduler()
    pos = arm(sched)

    asyncio.run(sched._update_protective_stop(ENTRY + 0.3 * R))

    assert pos.stop_loss == LONG_STOP, pos.stop_loss
    assert pos.stop_stage == "INIT", pos.stop_stage
    print(f"  ✅ 止损保持 ${pos.stop_loss:,.0f}, stage={pos.stop_stage}")


def test_2_breakeven_moves_stop_to_entry():
    print("\n[Test 2] 浮盈 0.5R → 止损移到成本价")
    sched = make_scheduler()
    pos = arm(sched)

    asyncio.run(sched._update_protective_stop(ENTRY + 0.5 * R))

    assert pos.stop_loss == ENTRY, pos.stop_loss
    assert pos.stop_stage == "BREAKEVEN", pos.stop_stage
    print(f"  ✅ 止损 ${LONG_STOP:,.0f} → ${pos.stop_loss:,.0f} (保本)")


def test_3_trailing_follows_peak():
    print("\n[Test 3] 峰值浮盈 2R → 止损跟到峰值回撤 1.25R")
    sched = make_scheduler()
    pos = arm(sched)

    peak = ENTRY + 2 * R
    asyncio.run(sched._update_protective_stop(peak))

    expected = peak - 1.25 * R
    assert pos.stop_loss == expected, f"{pos.stop_loss} != {expected}"
    assert pos.stop_stage == "TRAILING", pos.stop_stage
    assert pos.stop_loss > ENTRY, "移动止损应已锁定利润"
    print(f"  ✅ 峰值 ${peak:,.0f} → 止损 ${pos.stop_loss:,.0f} (锁定 +{pos.stop_loss - ENTRY:,.0f})")


def test_4_ratchet_never_retreats():
    print("\n[Test 4] 价格回落 → 止损棘轮不回退")
    sched = make_scheduler()
    pos = arm(sched)

    asyncio.run(sched._update_protective_stop(ENTRY + 2 * R))
    locked = pos.stop_loss

    for price in (ENTRY + 1.6 * R, ENTRY + 0.8 * R, ENTRY + 0.1 * R):
        asyncio.run(sched._update_protective_stop(price))
        assert pos.stop_loss == locked, f"止损回退了: {pos.stop_loss} != {locked}"

    assert pos.mfe_price == ENTRY + 2 * R, pos.mfe_price
    print(f"  ✅ 三次回落止损稳定在 ${locked:,.0f}")


def test_5_short_direction_mirrors():
    print("\n[Test 5] SHORT 方向镜像")
    sched = make_scheduler()
    pos = arm(sched, "SHORT")

    asyncio.run(sched._update_protective_stop(ENTRY - 0.5 * R))
    assert pos.stop_loss == ENTRY, pos.stop_loss
    assert pos.stop_stage == "BREAKEVEN", pos.stop_stage

    trough = ENTRY - 2 * R
    asyncio.run(sched._update_protective_stop(trough))
    expected = trough + 1.25 * R
    assert pos.stop_loss == expected, f"{pos.stop_loss} != {expected}"
    assert pos.stop_loss < ENTRY, "空单移动止损应低于成本价"

    asyncio.run(sched._update_protective_stop(ENTRY - 1.0 * R))
    assert pos.stop_loss == expected, "空单止损不应回退"
    print(f"  ✅ 保本 ${ENTRY:,.0f} → 移动止损 ${pos.stop_loss:,.0f}")


def test_6_exit_reason_reflects_stage():
    print("\n[Test 6] 平仓原因区分保本 / 移动止盈")
    sched = make_scheduler()
    arm(sched)

    # 冲到 2R 后回落击穿移动止损
    asyncio.run(sched._update_protective_stop(ENTRY + 2 * R))
    trades = asyncio.run(sched._check_safety_exits(ENTRY + 0.5 * R))

    assert len(trades) == 1, trades
    assert trades[0]["trigger_reason"] == "移动止盈触发", trades[0]["trigger_reason"]
    assert trades[0]["pnl"] > 0, "移动止盈应为盈利出场"
    print(f"  ✅ {trades[0]['trigger_reason']}, PnL=${trades[0]['pnl']:+,.2f}")


def test_7_range_guard_blocks_chasing():
    print("\n[Test 7] 追高护栏拦截区间顺方向 60~100%")
    sched = make_scheduler()
    klines = make_klines(high=80000.0, low=78000.0)

    # LONG @79600 → 区间 80% → 追高
    reject = sched._check_range_guard("LONG", 79600.0, klines)
    assert reject is not None and "追高护栏" in reject, reject
    print(f"  ✅ 拒绝追多: {reject}")

    # SHORT @78400 → 顺方向 80% → 杀跌
    reject = sched._check_range_guard("SHORT", 78400.0, klines)
    assert reject is not None and "追高护栏" in reject, reject
    print(f"  ✅ 拒绝杀跌: {reject}")

    # LONG @78600 → 区间 30% → 放行
    assert sched._check_range_guard("LONG", 78600.0, klines) is None
    # SHORT @79600 → 顺方向 20% → 放行
    assert sched._check_range_guard("SHORT", 79600.0, klines) is None
    print("  ✅ 区间下沿做多 / 上沿做空均放行")


def test_8_breakout_passes():
    print("\n[Test 8] 突破区间放行")
    sched = make_scheduler()
    klines = make_klines(high=80000.0, low=78000.0)

    assert sched._check_range_guard("LONG", 80500.0, klines) is None
    assert sched._check_range_guard("SHORT", 77500.0, klines) is None

    pos_pct = sched._entry_range_position("LONG", 80500.0, klines)
    assert pos_pct > 100.0, pos_pct
    print(f"  ✅ 突破位置 {pos_pct:.0f}% 放行（多空双向）")


def test_10_partial_tp_banks_half_at_trigger():
    print("\n[Test 10] 浮盈 1.0R → 平掉一半仓位落袋")
    sched = make_scheduler()
    pos = arm(sched)
    size_before = pos.size_btc

    trade = asyncio.run(sched._check_partial_take_profit(ENTRY + 1.0 * R))

    assert trade is not None, "应触发部分止盈"
    assert trade["action"] == "REDUCE", trade["action"]
    assert trade["pnl"] > 0, trade["pnl"]
    assert abs(pos.size_btc - size_before * 0.5) < 1e-9, pos.size_btc
    assert pos.tp_taken is True
    assert pos.is_active, "剩余半仓应继续持有"
    print(f"  ✅ 落袋 PnL=${trade['pnl']:+,.2f}, 剩余 {pos.size_btc:.4f} BTC")


def test_11_partial_tp_below_trigger_does_nothing():
    print("\n[Test 11] 浮盈 0.9R → 不触发止盈")
    sched = make_scheduler()
    pos = arm(sched)

    assert asyncio.run(sched._check_partial_take_profit(ENTRY + 0.9 * R)) is None
    assert pos.tp_taken is False
    assert pos.size_btc == 0.05
    print("  ✅ 未达阈值不落袋")


def test_12_partial_tp_only_once_per_position():
    print("\n[Test 12] 部分止盈每仓只执行一次")
    sched = make_scheduler()
    pos = arm(sched)

    first = asyncio.run(sched._check_partial_take_profit(ENTRY + 1.0 * R))
    second = asyncio.run(sched._check_partial_take_profit(ENTRY + 2.0 * R))

    assert first is not None
    assert second is None, "第二次不应再落袋"
    assert abs(pos.size_btc - 0.025) < 1e-9, pos.size_btc
    print("  ✅ 第二次调用被 tp_taken 拦下")


def test_13_partial_tp_replaces_stop_order_even_when_unchanged():
    """回归测试：平仓会撤掉交易所止损单，剩余仓位必须重挂，
    否则常规路径（保本已在 0.5R 触发 → 止损价不变）下实盘会裸奔。"""
    print("\n[Test 13] 止盈后无条件重挂止损单")
    sched = make_scheduler()
    pos = arm(sched)

    calls = []

    async def spy():
        calls.append(pos.stop_loss)

    sched._on_stop_loss_moved = spy

    # 先让保本在 0.5R 触发，使 TP 时止损价已等于成本价（improved=False）
    asyncio.run(sched._update_protective_stop(ENTRY + 0.5 * R))
    assert pos.stop_loss == ENTRY
    calls.clear()

    asyncio.run(sched._check_partial_take_profit(ENTRY + 1.0 * R))

    assert calls, "止损价未变化时也必须重挂交易所止损单"
    assert pos.stop_loss == ENTRY
    print(f"  ✅ 重挂被调用 {len(calls)} 次, 止损=${pos.stop_loss:,.0f}")


def test_14_short_partial_tp_mirrors():
    print("\n[Test 14] SHORT 部分止盈镜像")
    sched = make_scheduler()
    pos = arm(sched, "SHORT")

    assert asyncio.run(sched._check_partial_take_profit(ENTRY - 0.9 * R)) is None
    trade = asyncio.run(sched._check_partial_take_profit(ENTRY - 1.0 * R))

    assert trade is not None and trade["pnl"] > 0, trade
    assert abs(pos.size_btc - 0.025) < 1e-9, pos.size_btc
    assert pos.stop_loss == ENTRY, pos.stop_loss
    print(f"  ✅ 空单落袋 PnL=${trade['pnl']:+,.2f}, 止损=${pos.stop_loss:,.0f}")


def test_15_tp_taken_survives_state_roundtrip():
    print("\n[Test 15] tp_taken 落盘与恢复")
    sched = make_scheduler()
    pos = arm(sched)
    asyncio.run(sched._check_partial_take_profit(ENTRY + 1.0 * R))
    assert pos.tp_taken is True

    state = sched._get_position_state()
    assert state["tp_taken"] is True, state

    fresh = make_scheduler()
    fresh._apply_position_state(state)
    assert fresh.position.tp_taken is True, "恢复后应保持已止盈，避免重复落袋"

    # 旧状态文件没有该字段时应回退为未止盈
    legacy = dict(state)
    legacy.pop("tp_taken")
    other = make_scheduler()
    other._apply_position_state(legacy)
    assert other.position.tp_taken is False
    print("  ✅ 落盘/恢复正确，旧文件向后兼容")


def test_16_safety_exits_emits_tp_then_keeps_position():
    print("\n[Test 16] 安全网内触发止盈后仓位仍在")
    sched = make_scheduler()
    pos = arm(sched)

    trades = asyncio.run(sched._check_safety_exits(ENTRY + 1.0 * R))

    assert len(trades) == 1, trades
    assert trades[0]["action"] == "REDUCE", trades[0]
    assert pos.is_active and abs(pos.size_btc - 0.025) < 1e-9
    assert pos.stop_loss == ENTRY, pos.stop_loss
    print(f"  ✅ 落袋后剩余 {pos.size_btc:.4f} BTC, 止损=${pos.stop_loss:,.0f}")


def test_17_ai_risk_params_clamped_to_range():
    print("\n[Test 17] AI 风控参数超界钳制 / 缺省回落")
    sched = make_scheduler()
    risk = sched.config.risk
    default_mult = sched.config.long.atr_multiplier

    # 未指定 → 用档位默认
    assert sched._clamp_ai_risk(
        None, risk.ai_stop_atr_mult_min, risk.ai_stop_atr_mult_max,
        "止损", default_mult) == default_mult

    # 区间内 → 原样采纳
    assert sched._clamp_ai_risk(
        2.0, risk.ai_stop_atr_mult_min, risk.ai_stop_atr_mult_max,
        "止损", default_mult) == 2.0

    # 离谱大 / 离谱小 → 钳到边界
    assert sched._clamp_ai_risk(
        1e6, risk.ai_stop_atr_mult_min, risk.ai_stop_atr_mult_max,
        "止损", default_mult) == risk.ai_stop_atr_mult_max
    assert sched._clamp_ai_risk(
        0.01, risk.ai_stop_atr_mult_min, risk.ai_stop_atr_mult_max,
        "止损", default_mult) == risk.ai_stop_atr_mult_min
    print(f"  ✅ 缺省={default_mult}, 采纳=2.0, "
          f"钳制区间=[{risk.ai_stop_atr_mult_min}, {risk.ai_stop_atr_mult_max}]")


def test_18_ai_tp_trigger_drives_partial_tp():
    print("\n[Test 18] AI 自定止盈线生效（0.6R 而非默认 1.0R）")
    sched = make_scheduler()
    pos = arm(sched)
    pos.tp_trigger_r = 0.6

    # 0.5R 未达 0.6R → 不触发
    assert asyncio.run(sched._check_partial_take_profit(ENTRY + 0.5 * R)) is None
    trade = asyncio.run(sched._check_partial_take_profit(ENTRY + 0.6 * R))

    assert trade is not None, "应按 AI 的 0.6R 触发"
    assert "0.60R" in trade["trigger_reason"], trade["trigger_reason"]
    assert abs(pos.size_btc - 0.025) < 1e-9
    print(f"  ✅ {trade['trigger_reason']}")


def test_19_position_tp_trigger_falls_back_to_config():
    print("\n[Test 19] 未指定时回落配置默认止盈线")
    sched = make_scheduler()
    pos = arm(sched)
    pos.tp_trigger_r = None  # AI 未给

    assert asyncio.run(sched._check_partial_take_profit(ENTRY + 0.9 * R)) is None
    trade = asyncio.run(sched._check_partial_take_profit(ENTRY + 1.0 * R))
    assert trade is not None
    assert "1.00R" in trade["trigger_reason"], trade["trigger_reason"]
    print(f"  ✅ {trade['trigger_reason']}")


def test_20_ai_stop_mult_changes_r_and_stop():
    """止损倍数变了，R 也跟着变，保本/止盈/移动止损全部按新 R 缩放。"""
    print("\n[Test 20] AI 放宽止损 → R 放大，各档位同步缩放")
    sched = make_scheduler()
    pos = sched.position
    pos.direction = "LONG"
    pos.entry_price = ENTRY
    pos.size_btc = 0.05
    pos.leverage = 5
    pos.stop_loss = ENTRY - 2000.0   # AI 给了更宽的止损 → R = 2000
    pos.liquidation_price = 0.0
    sched._arm_protective_stop()

    assert pos.risk_unit == 2000.0, pos.risk_unit
    # 原来 0.5R=500 点就保本，现在需要 1000 点
    asyncio.run(sched._update_protective_stop(ENTRY + 500.0))
    assert pos.stop_stage == "INIT", "500 点已不足 0.5R"
    asyncio.run(sched._update_protective_stop(ENTRY + 1000.0))
    assert pos.stop_stage == "BREAKEVEN", pos.stop_stage
    print(f"  ✅ R={pos.risk_unit:,.0f}, 保本线随之抬到 +1,000 点")


def test_21_ai_risk_survives_state_roundtrip():
    print("\n[Test 21] tp_trigger_r 落盘与恢复")
    sched = make_scheduler()
    pos = arm(sched)
    pos.tp_trigger_r = 2.5

    state = sched._get_position_state()
    assert state["tp_trigger_r"] == 2.5, state

    fresh = make_scheduler()
    fresh._apply_position_state(state)
    assert fresh.position.tp_trigger_r == 2.5

    legacy = dict(state)
    legacy.pop("tp_trigger_r")
    other = make_scheduler()
    other._apply_position_state(legacy)
    assert other.position.tp_trigger_r is None, "缺字段的旧文件应还原为未设置"
    assert other.position.ladder(other.config.risk)["tp_trigger_r"] == \
        other.config.risk.tp_trigger_r, "未设置应回落配置默认"

    # 旧文件里 0 是当时的「未设置」哨兵，不能被当成「AI 要求 0R 止盈」
    zero_legacy = dict(state)
    zero_legacy["tp_trigger_r"] = 0.0
    third = make_scheduler()
    third._apply_position_state(zero_legacy)
    assert third.position.ladder(third.config.risk)["tp_trigger_r"] == \
        third.config.risk.tp_trigger_r, "旧哨兵 0 应回落配置默认"
    print("  ✅ 落盘/恢复正确，旧文件（缺字段 / 0 哨兵）都向后兼容")


def test_9_insufficient_klines_does_not_block():
    print("\n[Test 9] K 线不足 → 不拦截")
    sched = make_scheduler()

    assert sched._entry_range_position("LONG", 80000.0, []) is None
    assert sched._check_range_guard("LONG", 80000.0, [[0, 1, 2, 3, 4, 5]]) is None
    # 区间退化（高=低）也不应拦截
    flat = make_klines(high=79000.0, low=79000.0)
    assert sched._check_range_guard("LONG", 79000.0, flat) is None
    print("  ✅ 数据不足时放行，不误伤")
