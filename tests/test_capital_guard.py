#!/usr/bin/env python3
"""测试 LiveTradingScheduler 四层防护

验证场景:
1. API 报错 → 保留本地仓位，不重置
2. 已有仓位 → 拒绝再开仓
3. 余额不足 / 资金上限 → 拒绝开仓
4. 平仓后同周期 → 不立刻重开
5. 同步异常 + 冷却期 → 拒绝开仓
"""

import sys
import os
import asyncio
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from stock_btc.core import TradingConfig, ParameterSet, TradingMode
from stock_btc.server.trading_scheduler.live_scheduler import LiveTradingScheduler, OPEN_COOLDOWN_SEC


class FakeExecutor:
    """模拟合约执行器，不调用真实 API"""

    def __init__(self, portfolio=None, order_success=True):
        self._portfolio = portfolio or {
            "position": 0.0, "direction": "NONE", "entry_price": 0.0,
            "balance": 500.0, "total_balance": 500.0, "unrealized_pnl": 0.0,
            "leverage": 2, "margin": 0.0, "liquidation_price": 0.0, "mark_price": 0.0,
        }
        self._order_success = order_success
        self.call_log = []

    def get_portfolio(self, asset_id):
        self.call_log.append(("get_portfolio", asset_id))
        return dict(self._portfolio)

    def execute_buy(self, symbol, amount_usdt, price):
        self.call_log.append(("execute_buy", symbol, amount_usdt, price))
        if not self._order_success:
            return {"success": False, "message": "mock fail"}
        return {
            "success": True,
            "order": {"average": price, "filled": amount_usdt / price},
        }

    def execute_short(self, symbol, amount_usdt, price):
        self.call_log.append(("execute_short", symbol, amount_usdt, price))
        if not self._order_success:
            return {"success": False, "message": "mock fail"}
        return {
            "success": True,
            "order": {"average": price, "filled": amount_usdt / price},
        }

    def execute_sell(self, symbol, ratio, price):
        self.call_log.append(("execute_sell", symbol, ratio, price))
        return {"success": True, "order": {"average": price, "filled": 0.01}}

    def execute_cover(self, symbol, ratio, price):
        self.call_log.append(("execute_cover", symbol, ratio, price))
        return {"success": True, "order": {"average": price, "filled": 0.01}}


def make_scheduler(portfolio=None, max_capital=500.0):
    executor = FakeExecutor(portfolio=portfolio)
    config = TradingConfig.get_preset(ParameterSet.AGGRESSIVE)
    sched = LiveTradingScheduler(
        config=config,
        futures_executor=executor,
        check_interval=60,
        max_capital=max_capital,
    )
    return sched, executor


def test_1_api_error_preserves_local_state():
    """API 报错时不重置本地仓位"""
    print("\n[Test 1] API 报错 → 保留本地状态")

    error_portfolio = {
        "position": 0.0, "direction": "NONE", "entry_price": 0.0,
        "balance": 0.0, "total_balance": 0.0, "unrealized_pnl": 0.0,
        "leverage": 2, "margin": 0.0, "liquidation_price": 0.0,
        "mark_price": 0.0, "_error": True,
    }
    sched, executor = make_scheduler(portfolio=error_portfolio)

    sched.position.direction = "SHORT"
    sched.position.size_btc = 0.02
    sched.position.entry_price = 74000.0
    sched.position.stop_loss = 75000.0
    sched.current_mode = TradingMode.SHORT

    asyncio.run(sched._sync_position())

    assert sched.position.direction == "SHORT", f"Expected SHORT, got {sched.position.direction}"
    assert sched.position.size_btc == 0.02, f"Expected 0.02, got {sched.position.size_btc}"
    assert sched.position.stop_loss == 75000.0
    assert sched.current_mode == TradingMode.SHORT
    assert sched._consecutive_sync_errors == 1
    print("  ✅ 仓位保留，sync_errors=1")


def test_2_active_position_blocks_open():
    """已有仓位 → 拒绝再开仓"""
    print("\n[Test 2] 已有仓位 → 拒绝开仓")

    sched, executor = make_scheduler()

    sched.position.direction = "SHORT"
    sched.position.size_btc = 0.02
    sched.position.entry_price = 74000.0
    sched.current_mode = TradingMode.SHORT

    result = asyncio.run(sched._open_short(74000.0, []))
    assert result is None, "Should refuse to open when position active"

    short_calls = [c for c in executor.call_log if c[0] == "execute_short"]
    assert len(short_calls) == 0, "Should NOT call exchange"
    print("  ✅ 有仓位时拒绝，未调用交易所")


def test_3_capital_guard_blocks_when_balance_low():
    """余额不足 → 拒绝开仓"""
    print("\n[Test 3] 余额不足 → 拒绝开仓")

    low_balance = {
        "position": 0.0, "direction": "NONE", "entry_price": 0.0,
        "balance": 10.0, "total_balance": 10.0, "unrealized_pnl": 0.0,
        "leverage": 2, "margin": 0.0, "liquidation_price": 0.0, "mark_price": 0.0,
    }
    sched, executor = make_scheduler(portfolio=low_balance)

    asyncio.run(sched._sync_position())

    result = asyncio.run(sched._open_short(74000.0, []))
    assert result is None, "Should refuse when balance too low"

    short_calls = [c for c in executor.call_log if c[0] == "execute_short"]
    assert len(short_calls) == 0, "Should NOT call exchange"
    print(f"  ✅ 余额=$10, 保证金需=${sched.OPEN_NOTIONAL / sched.config.long.leverage:.0f}, 拒绝开仓")


def test_4_capital_guard_blocks_with_max_capital():
    """max_capital 限制 → 余额够但上限不够 → 拒绝"""
    print("\n[Test 4] max_capital 限制生效")

    rich_balance = {
        "position": 0.0, "direction": "NONE", "entry_price": 0.0,
        "balance": 10000.0, "total_balance": 10000.0, "unrealized_pnl": 0.0,
        "leverage": 2, "margin": 0.0, "liquidation_price": 0.0, "mark_price": 0.0,
    }
    sched, executor = make_scheduler(portfolio=rich_balance, max_capital=30.0)

    asyncio.run(sched._sync_position())

    result = asyncio.run(sched._open_long(74000.0, []))
    assert result is None, "Should refuse when max_capital too low"
    print("  ✅ 交易所有 $10000，但 max_capital=$30 < 保证金，拒绝开仓")


def test_5_sync_error_blocks_open():
    """同步异常 → 拒绝开仓"""
    print("\n[Test 5] 同步异常 → 拒绝开仓")

    sched, executor = make_scheduler()
    sched._consecutive_sync_errors = 2

    result = asyncio.run(sched._open_short(74000.0, []))
    assert result is None, "Should refuse when sync errors > 0"
    print("  ✅ 同步连续失败 2 次，拒绝开仓")


def test_6_cooldown_blocks_rapid_reopen():
    """冷却期 → 拒绝快速重开"""
    print("\n[Test 6] 冷却期 → 拒绝快速重开")

    sched, executor = make_scheduler()
    asyncio.run(sched._sync_position())

    sched._last_open_ts = time.time()

    result = asyncio.run(sched._open_short(74000.0, []))
    assert result is None, "Should refuse during cooldown"
    print(f"  ✅ 距上次开仓 < {OPEN_COOLDOWN_SEC}s，拒绝开仓")


def test_7_normal_open_succeeds():
    """正常情况 → 开仓成功"""
    print("\n[Test 7] 正常情况 → 开仓成功")

    sched, executor = make_scheduler()
    asyncio.run(sched._sync_position())

    sched._last_open_ts = 0

    result = asyncio.run(sched._open_short(74000.0, []))
    assert result is not None, "Should succeed when all guards pass"
    assert sched.position.direction == "SHORT"
    assert sched._last_open_ts > 0
    print(f"  ✅ 开空成功: {result['amount']:.6f} BTC @ ${result['price']:,.0f}")


if __name__ == "__main__":
    print("=" * 60)
    print("LiveTradingScheduler 四层防护测试")
    print("=" * 60)

    tests = [
        test_1_api_error_preserves_local_state,
        test_2_active_position_blocks_open,
        test_3_capital_guard_blocks_when_balance_low,
        test_4_capital_guard_blocks_with_max_capital,
        test_5_sync_error_blocks_open,
        test_6_cooldown_blocks_rapid_reopen,
        test_7_normal_open_succeeds,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"结果: {passed} passed, {failed} failed, {len(tests)} total")
    print(f"{'=' * 60}")

    sys.exit(1 if failed else 0)
