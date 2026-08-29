#!/usr/bin/env python3
"""仓位换算：position_size_hint 按保证金占权益，再乘杠杆得到名义本金。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import TradingConfig, ParameterSet
from multi_agent.trading_advisor import TradingDecision
from server.trading_scheduler.sim_scheduler import SimTradingScheduler


def _scheduler(equity: float = 500.0) -> SimTradingScheduler:
    sched = SimTradingScheduler(
        config=TradingConfig.get_preset(ParameterSet.AGGRESSIVE),
        check_interval=60,
    )
    sched.equity = equity
    return sched


def _decision(size_hint: str, leverage: int) -> TradingDecision:
    return TradingDecision(
        action="开多",
        position_size_hint=size_hint,
        leverage_hint=leverage,
        reason="test",
    )


def test_half_margin_times_leverage():
    notional, leverage = _scheduler(500)._resolve_ai_sizing(_decision("50%", 5))
    assert leverage == 5
    assert notional == 1250.0  # 500 * 50% * 5


def test_full_margin_times_leverage():
    notional, leverage = _scheduler(500)._resolve_ai_sizing(_decision("100%", 5))
    assert leverage == 5
    assert notional == 2500.0  # 500 * 100% * 5


def test_quarter_margin_custom_leverage():
    notional, leverage = _scheduler(1000)._resolve_ai_sizing(_decision("25%", 3))
    assert leverage == 3
    assert notional == 750.0  # 1000 * 25% * 3


def test_default_decision_uses_half_margin():
    notional, leverage = _scheduler(500)._resolve_ai_sizing(None)
    assert leverage == 5
    assert notional == 1250.0


def test_zero_hint_does_not_force_min_notional():
    notional, leverage = _scheduler(500)._resolve_ai_sizing(_decision("0%", 5))
    assert leverage == 5
    assert notional == 0.0


def test_tiny_equity_still_meets_min_notional():
    notional, leverage = _scheduler(5)._resolve_ai_sizing(_decision("50%", 5))
    assert leverage == 5
    assert notional == 50.0


if __name__ == "__main__":
    tests = [
        test_half_margin_times_leverage,
        test_full_margin_times_leverage,
        test_quarter_margin_custom_leverage,
        test_default_decision_uses_half_margin,
        test_zero_hint_does_not_force_min_notional,
        test_tiny_equity_still_meets_min_notional,
    ]
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"  ✅ {test_fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {test_fn.__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
