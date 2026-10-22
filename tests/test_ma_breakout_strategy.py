"""MA 突破机械策略测试

覆盖指标的收盘过滤和策略状态机，重点是止损出场后的重进场封锁——
没有它，价格还在均线上方时被硬止损扫掉会立刻原价重开，和回测行为不符。
"""

import time

from core.ma_breakout_strategy import (
    MABreakoutStrategy, DEFAULT_SIZE_HINT, DEFAULT_LEVERAGE,
)
from indicators.ma_breakout import MABreakoutCalculator, MABreakoutResult

HOUR_MS = 3600 * 1000
DAY_MS = 24 * HOUR_MS


def _kline(close: float, volume: float, close_time: int) -> list:
    """构造一根 Binance 格式的K线（只有收盘价/成交量/收盘时间参与计算）"""
    return [close_time - DAY_MS, close, close, close, close, volume,
            close_time, 0.0, 0, 0.0, 0.0, "0"]


def _series(closes: list, volumes: list = None, include_open_bar: bool = False) -> list:
    """按天生成K线；include_open_bar=True 时追加一根尚未收盘的当前K线"""
    now = int(time.time() * 1000)
    volumes = volumes or [100.0] * len(closes)
    n = len(closes)
    bars = [
        _kline(c, v, now - (n - i) * DAY_MS)
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]
    if include_open_bar:
        bars.append(_kline(closes[-1], volumes[-1], now + DAY_MS))
    return bars


def _result(above_ma: bool, vol_ratio: float = 2.0) -> MABreakoutResult:
    ma = 100.0
    return MABreakoutResult(
        close=ma + 10 if above_ma else ma - 10,
        ma=ma,
        vol_ratio=vol_ratio,
        above_ma=above_ma,
        volume_confirmed=vol_ratio >= 1.2,
        bar_time="2026-09-27 00:00",
        ma_period=40,
        timeframe="1d",
    )


# ── 指标 ────────────────────────────────────────────────────

def test_calculator_ignores_unclosed_bar():
    """未收盘的当前K线不能参与计算，否则信号一天内会反复翻转"""
    calc = MABreakoutCalculator(ma_period=5, vol_mult=1.2, vol_ma_period=5)

    closes = [100.0] * 5 + [110.0]
    # 追加一根未收盘的巨量暴跌K线，结果不应受它影响
    klines = _series(closes, volumes=[100.0] * 6)
    klines.append(_kline(10.0, 99999.0, int(time.time() * 1000) + DAY_MS))

    res = calc.calculate(klines)
    assert res is not None
    assert res.close == 110.0, f"用到了未收盘K线: close={res.close}"


def test_calculator_entry_requires_volume():
    calc = MABreakoutCalculator(ma_period=5, vol_mult=1.5, vol_ma_period=5)

    # 站上均线但缩量
    quiet = calc.calculate(_series([100.0] * 5 + [110.0], [100.0] * 5 + [100.0]))
    assert quiet.above_ma is True
    assert quiet.volume_confirmed is False
    assert quiet.entry_ok is False

    # 站上均线且放量
    loud = calc.calculate(_series([100.0] * 5 + [110.0], [100.0] * 5 + [300.0]))
    assert loud.entry_ok is True


def test_calculator_returns_none_when_insufficient():
    calc = MABreakoutCalculator(ma_period=40, vol_ma_period=20)
    assert calc.calculate(_series([100.0] * 10)) is None


# ── 策略状态机 ──────────────────────────────────────────────

def test_open_long_on_confirmed_breakout():
    s = MABreakoutStrategy()
    d = s.decide(_result(above_ma=True, vol_ratio=2.0), "NONE")
    assert d.action == "开多"
    assert d.position_size_hint == DEFAULT_SIZE_HINT
    assert d.leverage_hint == DEFAULT_LEVERAGE


def test_no_entry_on_quiet_breakout():
    s = MABreakoutStrategy()
    d = s.decide(_result(above_ma=True, vol_ratio=0.5), "NONE")
    assert d.action == "等待入场"


def test_close_on_break_below_ma():
    s = MABreakoutStrategy()
    s.decide(_result(above_ma=True), "NONE")
    d = s.decide(_result(above_ma=False), "LONG")
    assert d.action == "平仓"
    assert d.close_ratio == 1.0


def test_hold_while_above_ma():
    s = MABreakoutStrategy()
    d = s.decide(_result(above_ma=True), "LONG")
    assert d.action == "持仓观望"


def test_stopped_out_blocks_reentry_until_price_crosses_back():
    """硬止损扫出后，价格仍在均线上方时不得重开"""
    s = MABreakoutStrategy()

    s.decide(_result(above_ma=True), "NONE")   # 开多
    s.decide(_result(above_ma=True), "LONG")   # 持仓

    # 安全网平掉仓位，但价格还在均线上方且放量
    blocked = s.decide(_result(above_ma=True, vol_ratio=3.0), "NONE")
    assert blocked.action == "等待入场", f"止损后立刻重开: {blocked.reason}"

    still_blocked = s.decide(_result(above_ma=True, vol_ratio=3.0), "NONE")
    assert still_blocked.action == "等待入场"

    # 价格跌回均线下方 → 解除封锁
    s.decide(_result(above_ma=False), "NONE")
    reopened = s.decide(_result(above_ma=True, vol_ratio=3.0), "NONE")
    assert reopened.action == "开多"


def test_normal_exit_does_not_block_next_signal():
    """正常跌破离场后，下一次有效突破应当能照常进场"""
    s = MABreakoutStrategy()

    s.decide(_result(above_ma=True), "NONE")
    s.decide(_result(above_ma=False), "LONG")   # 跌破平仓
    s.decide(_result(above_ma=False), "NONE")   # 空仓等待

    d = s.decide(_result(above_ma=True, vol_ratio=2.0), "NONE")
    assert d.action == "开多"


def test_closes_unexpected_short():
    s = MABreakoutStrategy()
    d = s.decide(_result(above_ma=True), "SHORT")
    assert d.action == "平仓"


def test_no_data_is_inert():
    s = MABreakoutStrategy()
    assert s.decide(None, "NONE").action == "等待入场"
    assert s.decide(None, "LONG").action == "持仓观望"
