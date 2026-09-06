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


def test_9_insufficient_klines_does_not_block():
    print("\n[Test 9] K 线不足 → 不拦截")
    sched = make_scheduler()

    assert sched._entry_range_position("LONG", 80000.0, []) is None
    assert sched._check_range_guard("LONG", 80000.0, [[0, 1, 2, 3, 4, 5]]) is None
    # 区间退化（高=低）也不应拦截
    flat = make_klines(high=79000.0, low=79000.0)
    assert sched._check_range_guard("LONG", 79000.0, flat) is None
    print("  ✅ 数据不足时放行，不误伤")
