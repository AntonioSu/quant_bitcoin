#!/usr/bin/env python3
"""硬止损安全网 + AI 推进止损 + 追高护栏测试

设计前提：持仓期间的保本 / 移动止损 / 部分止盈全部由 Trading AI 决定，
代码只保留强平 + 硬止损两道兜底，并保证止损永不回退。

验证场景:
1. 安全网不再自动推进止损（浮盈 2R 后止损仍在开仓位置）
2. 安全网不再自动部分止盈
3. 峰值价（MFE）仍每 tick 记录，供 AI 读 peak_r
4. 硬止损触发 → 平仓，原因为「止损触发」
5. AI stop_r 推进止损（保本 / 锁利）并触发 Live 重挂钩子
6. AI stop_r 棘轮：不接受回退
7. AI stop_r 越过现价 → 拒绝（应改用平仓）
8. AI 推进过的止损被打到 → 原因为「AI移动止损触发」
9. SHORT 方向镜像
10. 缓存命中的决策不重复推进止损
11. 强平优先于止损
12. 状态落盘 / 恢复（含旧文件里废弃字段）
13. 追高护栏拦截区间顺方向 60~100% 的开仓
14. 突破区间 (>100%) 放行
15. K 线不足时不拦截
16. AI 止损 ATR 倍数钳制
17. 全平后通知 Trading AI 层（重开冷却）
"""

import asyncio

from core import ParameterSet, TradingConfig
from multi_agent.trading_advisor import TradingDecision
from server.trading_scheduler.sim_scheduler import SimTradingScheduler

# R = |entry - initial_stop| = 1000
ENTRY = 80000.0
LONG_STOP = 79000.0
SHORT_STOP = 81000.0
R = 1000.0


def make_scheduler():
    return SimTradingScheduler(
        config=TradingConfig.get_preset(ParameterSet.STANDARD),
        check_interval=300,
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


def ai_stop(stop_r, reason="test", from_cache=False):
    d = TradingDecision(action="持仓观望", stop_r=stop_r, reason=reason)
    d._from_cache = from_cache
    return d


def test_1_safety_net_never_moves_stop_by_itself():
    print("\n[Test 1] 浮盈 2R → 安全网不自动推进止损")
    sched = make_scheduler()
    pos = arm(sched)

    for price in (ENTRY + 0.5 * R, ENTRY + 1.0 * R, ENTRY + 2.0 * R):
        sched._track_mfe(price)
        trades = asyncio.run(sched._check_safety_exits(price))
        assert trades == [], trades

    assert pos.stop_loss == LONG_STOP, pos.stop_loss
    assert not pos.stop_moved_by_ai
    print(f"  ✅ 止损保持在开仓位置 ${pos.stop_loss:,.0f}")


def test_2_safety_net_never_takes_partial_profit():
    print("\n[Test 2] 浮盈 1R / 3R → 安全网不自动落袋")
    sched = make_scheduler()
    pos = arm(sched)

    for price in (ENTRY + 1.0 * R, ENTRY + 3.0 * R):
        trades = asyncio.run(sched._check_safety_exits(price))
        assert trades == [], trades
    assert pos.size_btc == 0.05, pos.size_btc
    print("  ✅ 仓位完整，没有机械止盈")


def test_3_mfe_is_tracked_every_tick():
    print("\n[Test 3] 峰值价每 tick 记录")
    sched = make_scheduler()
    pos = arm(sched)

    for price in (ENTRY + 0.3 * R, ENTRY + 1.7 * R, ENTRY + 0.9 * R):
        sched._track_mfe(price)
    assert pos.mfe_price == ENTRY + 1.7 * R, pos.mfe_price

    risk = sched._build_position_risk(ENTRY + 0.9 * R, [])
    assert abs(risk["peak_r"] - 1.7) < 1e-9, risk
    assert abs(risk["profit_r"] - 0.9) < 1e-9, risk
    assert abs(risk["drawdown_from_peak_r"] - 0.8) < 1e-9, risk
    assert abs(risk["stop_r"] - (-1.0)) < 1e-9, risk
    print(f"  ✅ peak_r={risk['peak_r']:.2f}, profit_r={risk['profit_r']:.2f}, "
          f"drawdown={risk['drawdown_from_peak_r']:.2f}")


def test_4_hard_stop_closes_with_plain_reason():
    print("\n[Test 4] 价格触及初始止损 → 平仓，原因「止损触发」")
    sched = make_scheduler()
    arm(sched)

    trades = asyncio.run(sched._check_safety_exits(LONG_STOP - 1))
    assert len(trades) == 1, trades
    assert trades[0]["trigger_reason"] == "止损触发", trades[0]["trigger_reason"]
    assert trades[0]["pnl"] < 0
    assert not sched.position.is_active
    print(f"  ✅ {trades[0]['trigger_reason']}, PnL=${trades[0]['pnl']:+,.2f}")


def test_5_ai_stop_r_moves_stop_and_fires_hook():
    print("\n[Test 5] AI stop_r=0 → 保本；stop_r=0.8 → 锁利；每次触发重挂钩子")
    sched = make_scheduler()
    pos = arm(sched)
    calls = []

    async def spy():
        calls.append(pos.stop_loss)

    sched._on_stop_loss_moved = spy

    moved = asyncio.run(sched._apply_ai_stop(ai_stop(0.0), ENTRY + 0.6 * R))
    assert moved and pos.stop_loss == ENTRY, pos.stop_loss
    assert pos.stop_moved_by_ai

    moved = asyncio.run(sched._apply_ai_stop(ai_stop(0.8), ENTRY + 1.5 * R))
    assert moved and pos.stop_loss == ENTRY + 0.8 * R, pos.stop_loss

    assert calls == [ENTRY, ENTRY + 0.8 * R], calls
    print(f"  ✅ 止损 ${LONG_STOP:,.0f} → ${ENTRY:,.0f} → ${pos.stop_loss:,.0f}，钩子 {len(calls)} 次")


def test_6_ai_stop_ratchet_never_retreats():
    print("\n[Test 6] AI 要求放宽止损 → 棘轮拒绝")
    sched = make_scheduler()
    pos = arm(sched)

    assert asyncio.run(sched._apply_ai_stop(ai_stop(0.5), ENTRY + 1.2 * R))
    locked = pos.stop_loss

    for retreat in (0.0, -0.5, -1.0, -3.0):
        moved = asyncio.run(sched._apply_ai_stop(ai_stop(retreat), ENTRY + 1.2 * R))
        assert not moved and pos.stop_loss == locked, (retreat, pos.stop_loss)
    print(f"  ✅ 四次回退请求均被拒，止损稳定在 ${locked:,.0f}")


def test_7_ai_stop_crossing_price_is_rejected():
    print("\n[Test 7] AI stop_r 越过现价 → 拒绝")
    sched = make_scheduler()
    pos = arm(sched)

    # 现价 +0.5R，要求止损到 +0.7R → 等于立刻平仓
    moved = asyncio.run(sched._apply_ai_stop(ai_stop(0.7), ENTRY + 0.5 * R))
    assert not moved and pos.stop_loss == LONG_STOP, pos.stop_loss
    # 恰好等于现价也拒绝
    moved = asyncio.run(sched._apply_ai_stop(ai_stop(0.5), ENTRY + 0.5 * R))
    assert not moved and pos.stop_loss == LONG_STOP
    print("  ✅ 越过 / 等于现价均被拒，止损未动")


def test_8_ai_moved_stop_hit_reports_ai_reason():
    print("\n[Test 8] AI 推进后的止损被打到 → 原因「AI移动止损触发」")
    sched = make_scheduler()
    arm(sched)

    assert asyncio.run(sched._apply_ai_stop(ai_stop(0.6), ENTRY + 2.0 * R))
    trades = asyncio.run(sched._check_safety_exits(ENTRY + 0.55 * R))

    assert len(trades) == 1, trades
    assert trades[0]["trigger_reason"] == "AI移动止损触发", trades[0]["trigger_reason"]
    assert trades[0]["pnl"] > 0, "锁利后离场应为盈利"
    print(f"  ✅ {trades[0]['trigger_reason']}, PnL=${trades[0]['pnl']:+,.2f}")


def test_9_short_direction_mirrors():
    print("\n[Test 9] SHORT 方向镜像")
    sched = make_scheduler()
    pos = arm(sched, "SHORT")

    sched._track_mfe(ENTRY - 1.8 * R)
    assert pos.mfe_price == ENTRY - 1.8 * R

    assert asyncio.run(sched._apply_ai_stop(ai_stop(0.0), ENTRY - 1.0 * R))
    assert pos.stop_loss == ENTRY, pos.stop_loss

    assert asyncio.run(sched._apply_ai_stop(ai_stop(0.7), ENTRY - 1.5 * R))
    assert pos.stop_loss == ENTRY - 0.7 * R, pos.stop_loss

    # 回退拒绝
    assert not asyncio.run(sched._apply_ai_stop(ai_stop(0.2), ENTRY - 1.5 * R))
    # 越过现价拒绝
    assert not asyncio.run(sched._apply_ai_stop(ai_stop(1.6), ENTRY - 1.5 * R))
    assert pos.stop_loss == ENTRY - 0.7 * R

    locked = pos.stop_loss
    trades = asyncio.run(sched._check_safety_exits(ENTRY - 0.6 * R))
    assert len(trades) == 1 and trades[0]["trigger_reason"] == "AI移动止损触发"
    assert trades[0]["pnl"] > 0
    print(f"  ✅ 空单保本 → 锁利 ${locked:,.0f} → 触发离场 PnL=${trades[0]['pnl']:+,.2f}")


def test_10_cached_decision_does_not_move_stop():
    print("\n[Test 10] 缓存命中的决策不重复推进止损")
    sched = make_scheduler()
    pos = arm(sched)

    moved = asyncio.run(sched._apply_ai_stop(ai_stop(0.0, from_cache=True), ENTRY + 1.0 * R))
    assert not moved and pos.stop_loss == LONG_STOP
    # 没给 stop_r 也不动
    moved = asyncio.run(sched._apply_ai_stop(ai_stop(None), ENTRY + 1.0 * R))
    assert not moved and pos.stop_loss == LONG_STOP
    print("  ✅ 缓存 / 未给 stop_r 均不动止损")


def test_11_liquidation_takes_priority():
    print("\n[Test 11] 强平优先于止损")
    sched = make_scheduler()
    pos = arm(sched)
    pos.liquidation_price = ENTRY - 0.5 * R  # 人为设一个比止损更近的强平价

    trades = asyncio.run(sched._check_safety_exits(ENTRY - 0.6 * R))
    assert len(trades) == 1 and trades[0]["trigger_reason"] == "强平触发", trades
    print("  ✅ 强平触发")


def test_12_state_roundtrip_ignores_legacy_fields():
    print("\n[Test 12] 状态落盘 / 恢复，旧文件废弃字段被忽略")
    sched = make_scheduler()
    pos = arm(sched)
    sched._track_mfe(ENTRY + 1.3 * R)
    assert asyncio.run(sched._apply_ai_stop(ai_stop(0.4), ENTRY + 1.3 * R))

    state = sched._get_position_state()
    assert state["initial_stop"] == LONG_STOP
    assert state["mfe_price"] == ENTRY + 1.3 * R
    assert state["stop_loss"] == ENTRY + 0.4 * R
    for legacy in ("stop_stage", "tp_taken", "tp_trigger_r", "trailing_distance_r"):
        assert legacy not in state, legacy

    fresh = make_scheduler()
    fresh._apply_position_state(state)
    assert fresh.position.risk_unit == R
    assert fresh.position.stop_moved_by_ai
    assert fresh.position.mfe_price == ENTRY + 1.3 * R

    # 旧状态文件带着阶梯字段也能正常恢复
    legacy_state = dict(state)
    legacy_state.update({"stop_stage": "TRAILING", "tp_taken": True,
                         "tp_trigger_r": 1.0, "trailing_distance_r": 1.25})
    other = make_scheduler()
    other._apply_position_state(legacy_state)
    assert other.position.stop_loss == ENTRY + 0.4 * R
    assert not hasattr(other.position, "tp_taken")
    print("  ✅ 落盘/恢复正确，旧字段无副作用")


def test_13_range_guard_blocks_chasing():
    print("\n[Test 13] 追高护栏拦截区间顺方向 60~100%")
    sched = make_scheduler()
    klines = make_klines(high=80000.0, low=78000.0)

    reject = sched._check_range_guard("LONG", 79600.0, klines)
    assert reject is not None and "追高护栏" in reject, reject
    reject = sched._check_range_guard("SHORT", 78400.0, klines)
    assert reject is not None and "追高护栏" in reject, reject

    assert sched._check_range_guard("LONG", 78600.0, klines) is None
    assert sched._check_range_guard("SHORT", 79600.0, klines) is None
    print("  ✅ 追多 / 杀跌拒绝，区间下沿做多 / 上沿做空放行")


def test_14_breakout_passes():
    print("\n[Test 14] 突破区间放行")
    sched = make_scheduler()
    klines = make_klines(high=80000.0, low=78000.0)

    assert sched._check_range_guard("LONG", 80500.0, klines) is None
    assert sched._check_range_guard("SHORT", 77500.0, klines) is None
    pos_pct = sched._entry_range_position("LONG", 80500.0, klines)
    assert pos_pct > 100.0, pos_pct
    print(f"  ✅ 突破位置 {pos_pct:.0f}% 放行")


def test_15_insufficient_klines_does_not_block():
    print("\n[Test 15] K 线不足 → 不拦截")
    sched = make_scheduler()

    assert sched._entry_range_position("LONG", 80000.0, []) is None
    assert sched._check_range_guard("LONG", 80000.0, [[0, 1, 2, 3, 4, 5]]) is None
    flat = make_klines(high=79000.0, low=79000.0)
    assert sched._check_range_guard("LONG", 79000.0, flat) is None
    print("  ✅ 数据不足时放行")


def test_16_ai_stop_mult_clamped_to_range():
    print("\n[Test 16] AI 止损 ATR 倍数超界钳制 / 缺省回落")
    sched = make_scheduler()
    risk = sched.config.risk
    default_mult = sched.config.long.atr_multiplier
    lo, hi = risk.ai_stop_atr_mult_min, risk.ai_stop_atr_mult_max

    assert sched._clamp_ai_risk(None, lo, hi, "止损", default_mult) == default_mult
    assert sched._clamp_ai_risk(2.0, lo, hi, "止损", default_mult) == 2.0
    assert sched._clamp_ai_risk(1e6, lo, hi, "止损", default_mult) == hi
    assert sched._clamp_ai_risk(0.01, lo, hi, "止损", default_mult) == lo
    print(f"  ✅ 缺省={default_mult}, 钳制区间=[{lo}, {hi}]")


def test_17_close_notifies_advisor_for_reentry_cooldown():
    print("\n[Test 17] 全平后通知 Trading AI 层，拦截同方向立刻重开")
    sched = make_scheduler()
    arm(sched)

    trades = asyncio.run(sched._check_safety_exits(LONG_STOP - 1))
    asyncio.run(sched._record_trades(trades))

    adv = sched.trading_advisor
    assert adv._last_close and adv._last_close["direction"] == "LONG", adv._last_close
    block = adv._reentry_block_reason("LONG", "any-signal")
    assert block and "冷却" in block, block
    assert adv._reentry_block_reason("SHORT", "any-signal") is None, "反向不受影响"
    print(f"  ✅ {block}")
