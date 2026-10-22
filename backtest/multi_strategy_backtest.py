#!/usr/bin/env python3
"""
BTC 多信号族策略回测

对比三类信号族，并检验它们的组合是否优于单独使用:

  trend     MA + MACD + CVD        顺势持有，吃大波段
  reversal  RSI + 成交量 + 布林带   抄底反弹，吃均值回归
  breakout  唐奇安通道 + 成交量      突破新高追入

数据说明:
  CVD 由 K 线的主动买入量重建 (2 × taker_buy - volume 的累计)，可回溯到 2017 年。
  OI (未平仓量) 无法回测: Binance openInterestHist 接口只给最近 30 天，
  指定更早的 startTime 直接返回 -1130 错误。OI 只能作为实盘过滤器。

统一执行口径 (与 ma_breakout_backtest.py 一致):
  第 i 根收盘出信号 → 第 i+1 根开盘成交，无未来函数；仅做多；双边扣费。

用法:
    python -m backtest.multi_strategy_backtest --family trend
    python -m backtest.multi_strategy_backtest --family all
    python -m backtest.multi_strategy_backtest --combo
    python -m backtest.multi_strategy_backtest --robust
"""

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "klines_ext"

FEE = 0.0005


# ══════════════════════════════════════════════════════════════
# 数据: 带主动买入量的 K 线
# ══════════════════════════════════════════════════════════════

COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]
KEEP = ["open_time", "open", "high", "low", "close", "volume",
        "close_time", "trades", "taker_buy_base"]


def fetch_klines(symbol: str, interval: str, start_ms: int = 0) -> pd.DataFrame:
    """分页拉取全量历史 K 线，保留主动买入量用于重建 CVD"""
    rows: List[list] = []
    cursor = start_ms
    now_ms = int(time.time() * 1000)

    while cursor < now_ms:
        resp = requests.get(
            BINANCE_KLINES_URL,
            params={"symbol": symbol, "interval": interval,
                    "startTime": cursor, "limit": 1000},
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][6] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.15)

    df = pd.DataFrame(rows, columns=COLUMNS)[KEEP]
    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = df[col].astype(float)
    return df


def load_klines(symbol: str, interval: str, offline: bool = False) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{symbol}_{interval}.csv"
    cached = pd.read_csv(cache_file) if cache_file.exists() else pd.DataFrame()

    if not offline:
        start_ms = int(cached["close_time"].iloc[-1]) + 1 if len(cached) else 0
        try:
            fresh = fetch_klines(symbol, interval, start_ms)
            if len(fresh):
                cached = pd.concat([cached, fresh], ignore_index=True)
                cached = cached.drop_duplicates("open_time").sort_values("open_time")
                cached.to_csv(cache_file, index=False)
        except Exception as exc:  # noqa: BLE001
            if cached.empty:
                raise RuntimeError(f"无缓存且无法访问 Binance: {exc}") from exc
            print(f"⚠️  拉取失败，使用本地缓存: {exc}")

    if cached.empty:
        raise RuntimeError(f"没有 {symbol} {interval} 的 K 线数据")

    df = cached[cached["close_time"] < int(time.time() * 1000)].copy()
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# 指标
# ══════════════════════════════════════════════════════════════

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    close, vol = d["close"], d["volume"]

    # CVD: 主动买 - 主动卖 的累计。taker_buy 是主动买，其余是主动卖
    d["delta"] = 2 * d["taker_buy_base"] - vol
    d["cvd"] = d["delta"].cumsum()

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    d["macd"] = ema12 - ema26
    d["macd_signal"] = d["macd"].ewm(span=9, adjust=False).mean()
    d["macd_hist"] = d["macd"] - d["macd_signal"]

    # RSI (Wilder)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    # 布林带
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    d["bb_mid"], d["bb_up"], d["bb_low"] = mid, mid + 2 * std, mid - 2 * std
    d["bb_pctb"] = (close - d["bb_low"]) / (d["bb_up"] - d["bb_low"])

    # 量比
    d["vol_ratio"] = (vol / vol.rolling(20).mean()).fillna(0)

    return d


def ma(d: pd.DataFrame, n: int) -> pd.Series:
    return d["close"].rolling(n).mean()


def cvd_rising(d: pd.DataFrame, n: int) -> pd.Series:
    """CVD 站上自身均线 = 买盘力量在累积"""
    return d["cvd"] > d["cvd"].rolling(n).mean()


# ══════════════════════════════════════════════════════════════
# 回测核心
# ══════════════════════════════════════════════════════════════

@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: Optional[pd.Timestamp] = None
    exit_price: float = 0.0
    bars: int = 0
    ret: float = 0.0
    entry_idx: int = 0

    @property
    def is_win(self) -> bool:
        return self.ret > 0


@dataclass
class Result:
    label: str
    trades: List[Trade] = field(default_factory=list)
    equity: pd.Series = field(default_factory=pd.Series)
    open_trade: Optional[Trade] = None
    buy_hold: float = 0.0
    exposure: float = 0.0     # 持仓时间占比

    @property
    def win_rate(self) -> float:
        return sum(t.is_win for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def total_return(self) -> float:
        return float(self.equity.iloc[-1]) - 1 if len(self.equity) else 0.0

    @property
    def max_drawdown(self) -> float:
        if not len(self.equity):
            return 0.0
        return float((self.equity / self.equity.cummax() - 1).min())

    @property
    def profit_factor(self) -> float:
        gains = sum(t.ret for t in self.trades if t.ret > 0)
        losses = -sum(t.ret for t in self.trades if t.ret <= 0)
        return gains / losses if losses > 0 else float("inf")


def simulate(d: pd.DataFrame, entry: np.ndarray, exit_: np.ndarray,
             label: str, fee: float = FEE, max_bars: int = 0) -> Result:
    """
    entry/exit 都是「第 i 根收盘时的判断」，第 i+1 根开盘成交。

    Args:
        max_bars: 时间止损，持仓超过 N 根强制离场；0 表示不启用
    """
    n = len(d)
    target = np.zeros(n)
    cur = 0.0
    held = 0

    for i in range(n):
        if cur != 0:
            held += 1
            if exit_[i] or (max_bars and held >= max_bars):
                cur = 0.0
                held = 0
        elif entry[i]:
            cur = 1.0
            held = 0
        target[i] = cur

    position = np.concatenate([[0.0], target[:-1]])   # 信号滞后一根成交
    opens = d["open"].to_numpy()
    dates = d["date"].to_numpy()

    equity_vals, equity_idx = [1.0], [dates[0]]
    trades: List[Trade] = []
    current: Optional[Trade] = None
    nav = 1.0

    for i in range(1, n):
        prev_pos, pos = position[i - 1], position[i]
        px, prev_px = opens[i], opens[i - 1]

        if prev_pos != 0:
            nav *= 1 + prev_pos * (px / prev_px - 1)

        if pos != prev_pos:
            nav *= 1 - fee          # 换仓一次扣一次费，开平各一次
            if prev_pos != 0 and current is not None:
                current.exit_time = dates[i]
                current.exit_price = px
                current.bars = i - current.entry_idx
                current.ret = (px / current.entry_price - 1) - 2 * fee
                trades.append(current)
                current = None
            if pos != 0:
                current = Trade(entry_time=dates[i], entry_price=px, entry_idx=i)

        equity_vals.append(nav)
        equity_idx.append(dates[i])

    res = Result(label=label)
    res.trades = trades
    res.equity = pd.Series(equity_vals, index=pd.DatetimeIndex(equity_idx))
    res.buy_hold = float(d["close"].iloc[-1] / d["open"].iloc[1] - 1) if n > 1 else 0.0
    res.exposure = float((position != 0).mean())
    if current is not None:
        current.exit_price = float(d["close"].iloc[-1])
        current.ret = current.exit_price / current.entry_price - 1 - 2 * fee
        res.open_trade = current
    return res


# ══════════════════════════════════════════════════════════════
# 三大信号族
# ══════════════════════════════════════════════════════════════

def sig_trend(d: pd.DataFrame, ma_n: int = 40, cvd_n: int = 20,
              vol_mult: float = 1.2, use_macd: bool = True, use_cvd: bool = True,
              exit_macd: bool = False, exit_cvd: bool = False,
              ) -> Tuple[np.ndarray, np.ndarray]:
    """
    趋势族: MA + MACD + CVD

    进场: 价格站上 MA (主信号)，再叠加可选的 MACD 柱翻正 / CVD 站上自身均线，
          且当根放量。use_* 开关用于拆解各指标的边际贡献。
    离场: 跌破 MA；exit_* 打开后任一指标转负也离场
    """
    above = d["close"] > ma(d, ma_n)
    macd_ok = d["macd_hist"] > 0
    cvd_ok = cvd_rising(d, cvd_n)

    entry = above & (d["vol_ratio"] >= vol_mult)
    if use_macd:
        entry &= macd_ok
    if use_cvd:
        entry &= cvd_ok

    exit_ = ~above
    if exit_macd:
        exit_ |= ~macd_ok
    if exit_cvd:
        exit_ |= ~cvd_ok
    return entry.fillna(False).to_numpy(), exit_.fillna(True).to_numpy()


def sig_reversal(d: pd.DataFrame, rsi_low: float = 30, rsi_exit: float = 65,
                 vol_mult: float = 1.5, max_bars: int = 20,
                 ) -> Tuple[np.ndarray, np.ndarray]:
    """
    反转族: RSI + 成交量 + 布林带

    进场: 前一根 RSI 超卖且触及布林下轨 (恐慌)，当根 RSI 回升上穿阈值
          且放量 (承接盘出现) → 抄底
    离场: RSI 进入强势区 或 价格摸到布林上轨 (均值回归完成)
    """
    rsi = d["rsi"]
    oversold = (rsi.shift(1) < rsi_low) & (d["close"].shift(1) <= d["bb_low"].shift(1))
    recover = rsi >= rsi_low
    entry = oversold & recover & (d["vol_ratio"] >= vol_mult)

    exit_ = (rsi >= rsi_exit) | (d["close"] >= d["bb_up"])
    return entry.fillna(False).to_numpy(), exit_.fillna(True).to_numpy()


def sig_breakout(d: pd.DataFrame, entry_n: int = 20, exit_n: int = 10,
                 vol_mult: float = 1.5) -> Tuple[np.ndarray, np.ndarray]:
    """
    突破族: 唐奇安通道 + 成交量 (OI 无历史数据，无法纳入回测)

    进场: 收盘创 entry_n 根新高，且当根放量
    离场: 收盘创 exit_n 根新低 (海龟式)
    """
    hh = d["high"].rolling(entry_n).max().shift(1)
    ll = d["low"].rolling(exit_n).min().shift(1)
    entry = (d["close"] > hh) & (d["vol_ratio"] >= vol_mult)
    exit_ = d["close"] < ll
    return entry.fillna(False).to_numpy(), exit_.fillna(True).to_numpy()


def sig_baseline(d: pd.DataFrame, ma_n: int = 40,
                 vol_mult: float = 1.2) -> Tuple[np.ndarray, np.ndarray]:
    """已上线的基准: MA40 突破 + 1.2x 放量。用于校验引擎与横向对比"""
    above = d["close"] > ma(d, ma_n)
    entry = above & (d["vol_ratio"] >= vol_mult)
    return entry.fillna(False).to_numpy(), (~above).fillna(True).to_numpy()


FAMILIES: Dict[str, Callable] = {
    "baseline": sig_baseline,
    "trend": sig_trend,
    "reversal": sig_reversal,
    "breakout": sig_breakout,
}


# ══════════════════════════════════════════════════════════════
# 报告
# ══════════════════════════════════════════════════════════════

def metrics(r: Result) -> Tuple[float, float, float, float]:
    eq = r.equity
    if len(eq) < 2:
        return float("nan"), float("nan"), float("nan"), float("nan")
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (1 + r.total_return) ** (1 / years) - 1 if years > 0 and r.total_return > -1 else float("nan")
    daily = np.log(eq.replace(0, np.nan)).diff().dropna()
    bars_per_year = len(eq) / years if years > 0 else 0
    sharpe = daily.mean() / daily.std() * np.sqrt(bars_per_year) if daily.std() else float("nan")
    calmar = cagr / abs(r.max_drawdown) if r.max_drawdown else float("nan")
    return cagr, calmar, sharpe, years


ROW = "  {:<22} {:>6} {:>8} {:>7} {:>10} {:>9} {:>8} {:>7} {:>7}"
HEAD = ROW.format("策略", "交易", "胜率", "盈亏比", "总收益", "年化", "回撤", "Calmar", "仓位")


def print_table(results: List[Result], title: str) -> None:
    print(f"\n  {title}")
    print("  " + "─" * 94)
    print(HEAD)
    print("  " + "─" * 94)
    for r in results:
        cagr, calmar, _, _ = metrics(r)
        print(ROW.format(
            r.label, len(r.trades), f"{r.win_rate:.2%}", f"{r.profit_factor:.2f}",
            f"{r.total_return:+.1%}", f"{cagr:+.1%}", f"{r.max_drawdown:.1%}",
            f"{calmar:.2f}", f"{r.exposure:.0%}",
        ))
    print("  " + "─" * 94)


def print_detail(r: Result, d: pd.DataFrame) -> None:
    cagr, calmar, sharpe, years = metrics(r)
    wins = [t for t in r.trades if t.is_win]
    losses = [t for t in r.trades if not t.is_win]
    line = "─" * 52
    print(f"\n{line}\n  {r.label}\n{line}")
    print(f"  区间      {d['date'].iloc[0]:%Y-%m-%d} ~ {d['date'].iloc[-1]:%Y-%m-%d}  ({years:.1f} 年)")
    print(f"  交易次数  {len(r.trades)}  (盈利 {len(wins)} / 亏损 {len(losses)})")
    print(f"  ★ 胜率    {r.win_rate:.2%}")
    if r.trades:
        print("  平均盈利  " + (f"{np.mean([t.ret for t in wins]):+.2%}" if wins else "—"))
        print("  平均亏损  " + (f"{np.mean([t.ret for t in losses]):+.2%}" if losses else "—"))
        print(f"  盈亏比    {r.profit_factor:.2f}")
        print(f"  期望收益  {np.mean([t.ret for t in r.trades]):+.2%} / 笔")
        print(f"  平均持仓  {np.mean([t.bars for t in r.trades]):.1f} 根")
    print(f"  持仓占比  {r.exposure:.1%}")
    print(line)
    print(f"  策略总收益 {r.total_return:+.2%}   年化 {cagr:+.2%}")
    print(f"  最大回撤   {r.max_drawdown:.2%}   Calmar {calmar:.2f}   Sharpe {sharpe:.2f}")
    print(f"  买入持有   {r.buy_hold:+.2%}")
    print(line)


def print_yearly(results: List[Result]) -> None:
    print("\n  分年度收益")
    print("  " + "─" * 94)
    years = sorted({int(y) for r in results for y in r.equity.index.year})
    print("  {:<22}".format("策略") + "".join(f"{y:>8}" for y in years) + f"{'正年数':>9}")
    print("  " + "─" * 94)
    for r in results:
        eq = r.equity
        cells, pos = [], 0
        for y in years:
            sub = eq[eq.index.year == y]
            if not len(sub):
                cells.append(f"{'—':>8}")
                continue
            before = eq[eq.index < sub.index[0]]
            base = before.iloc[-1] if len(before) else sub.iloc[0]
            ret = float(sub.iloc[-1] / base - 1)
            pos += ret > 0
            cells.append(f"{ret:>+7.0%} ")
        print(f"  {r.label:<22}" + "".join(cells) + f"{pos:>5}/{len(years)}")
    print("  " + "─" * 94)


# ══════════════════════════════════════════════════════════════
# 分析模块
# ══════════════════════════════════════════════════════════════

def run_ablate(d: pd.DataFrame) -> None:
    """拆解趋势族: MACD 和 CVD 各自的边际贡献"""
    combos = [
        ("MA40 裸突破",          dict(vol_mult=0, use_macd=False, use_cvd=False)),
        ("MA40 +量",             dict(vol_mult=1.2, use_macd=False, use_cvd=False)),
        ("MA40 +量 +MACD",       dict(vol_mult=1.2, use_macd=True, use_cvd=False)),
        ("MA40 +量 +CVD",        dict(vol_mult=1.2, use_macd=False, use_cvd=True)),
        ("MA40 +量 +MACD +CVD",  dict(vol_mult=1.2, use_macd=True, use_cvd=True)),
        ("MA40 +MACD 无量",      dict(vol_mult=0, use_macd=True, use_cvd=False)),
        ("MA40 +CVD 无量",       dict(vol_mult=0, use_macd=False, use_cvd=True)),
        ("MA40 +量, MACD离场",   dict(vol_mult=1.2, use_macd=False, use_cvd=False, exit_macd=True)),
        ("MA40 +量, CVD离场",    dict(vol_mult=1.2, use_macd=False, use_cvd=False, exit_cvd=True)),
    ]
    results = [build(d, "trend", label=name, **kw) for name, kw in combos]
    print_table(results, "趋势族拆解: 每个指标的边际贡献")


def run_sweep(d: pd.DataFrame, family: str) -> None:
    """各族的参数敏感性扫描，看最优点是孤峰还是平台"""
    if family == "trend":
        grid = [(f"MA{n} 量{v:g}", dict(ma_n=n, vol_mult=v, use_macd=False, use_cvd=False))
                for n in (25, 30, 35, 40, 45, 50, 60) for v in (1.0, 1.2, 1.5)]
    elif family == "breakout":
        grid = [(f"唐奇安{e}/{x} 量{v:g}", dict(entry_n=e, exit_n=x, vol_mult=v))
                for e in (10, 20, 30, 40, 55) for x in (10, 20) for v in (1.0, 1.5)]
    elif family == "reversal":
        grid = [(f"RSI{lo:g}→{hi:g} 量{v:g}", dict(rsi_low=lo, rsi_exit=hi, vol_mult=v))
                for lo in (25, 30, 35, 40) for hi in (55, 65, 75) for v in (1.0, 1.5)]
    else:
        return
    results = [build(d, family, label=name, **kw) for name, kw in grid]
    results.sort(key=lambda r: -r.total_return)
    print_table(results, f"{family} 参数扫描 (按总收益排序)")


def equal_weight(results: List[Result], d: pd.DataFrame, label: str) -> Result:
    """等权组合: 每个子策略分一份资金，各自独立进出，权益曲线相加"""
    n = len(results)
    idx = results[0].equity.index
    blended = sum(r.equity.reindex(idx).ffill().fillna(1.0) for r in results) / n

    out = Result(label=label)
    out.equity = blended
    out.trades = [t for r in results for t in r.trades]
    out.trades.sort(key=lambda t: t.entry_time)
    out.buy_hold = results[0].buy_hold
    out.exposure = float(np.mean([r.exposure for r in results]))
    return out


# 各族扫描出来的最优参数
BEST = {
    "trend": dict(ma_n=40, vol_mult=1.2, use_macd=False, use_cvd=False),
    "reversal": dict(rsi_low=35, rsi_exit=75, vol_mult=1.0),
    "breakout": dict(entry_n=10, exit_n=10, vol_mult=1.5),
}


def _held(d: pd.DataFrame, entry: np.ndarray, exit_: np.ndarray) -> np.ndarray:
    """还原持仓布尔序列，用于算重叠度"""
    n = len(entry)
    out = np.zeros(n, dtype=bool)
    cur = False
    for i in range(n):
        if cur and exit_[i]:
            cur = False
        elif not cur and entry[i]:
            cur = True
        out[i] = cur
    return out


def run_combo(d: pd.DataFrame) -> None:
    """三族的各种拼法，均使用各自扫描出的最优参数"""
    base = build(d, "baseline", label="基准 MA40+量")
    trend = build(d, "trend", label="趋势 MA40+量", **BEST["trend"])
    rev = build(d, "reversal", label="反转 RSI35→75", **BEST["reversal"])
    brk = build(d, "breakout", label="突破 唐奇安10/10", **BEST["breakout"])

    e_t, x_t = FAMILIES["trend"](d, **BEST["trend"])
    e_r, x_r = FAMILIES["reversal"](d, **BEST["reversal"])
    e_b, x_b = FAMILIES["breakout"](d, **BEST["breakout"])
    above = (d["close"] > ma(d, 40)).fillna(False).to_numpy()

    variants = [
        ("并联 任一进/全部出", e_t | e_r | e_b, x_t & x_r & x_b),
        ("并联 任一进/任一出", e_t | e_r | e_b, x_t | x_r | x_b),
        ("确认制 趋势+突破同意", e_t & e_b, x_t | x_b),
        ("确认制 突破需趋势在位", e_b & above, x_b),
        ("确认制 趋势需突破点火", e_t & e_b, x_t),
        ("市况切换 顺势突破/逆势抄底", np.where(above, e_b, e_r), np.where(above, x_b, x_r)),
    ]
    combos = [simulate(d, e, x, name) for name, e, x in variants]
    port = equal_weight([trend, rev, brk], d, "等权三族组合")
    port2 = equal_weight([trend, brk], d, "等权 趋势+突破")

    print_table([base, trend, rev, brk] + combos + [port, port2], "组合方式对比")

    # 重叠度: 三族是不是在同一时间下同一个注
    h = {"趋势": _held(d, e_t, x_t), "反转": _held(d, e_r, x_r), "突破": _held(d, e_b, x_b)}
    print("\n  持仓重叠度 (两两同时在场的时间 / 任一在场的时间)")
    print("  " + "─" * 50)
    names = list(h)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = (h[a] & h[b]).sum()
            either = (h[a] | h[b]).sum()
            print(f"  {a} ∩ {b}: {both / either:.1%}   "
                  f"(各自在场 {h[a].mean():.0%} / {h[b].mean():.0%})")
    allthree = (h["趋势"] | h["反转"] | h["突破"]).mean()
    print(f"  至少一族在场的时间: {allthree:.0%}")
    print("  " + "─" * 50)


def candidates(d: pd.DataFrame) -> List[Tuple[str, Result]]:
    """待检验的几个头部方案，统一构造，便于在任意子样本上重跑"""
    trend = build(d, "trend", label="趋势 MA40+量", **BEST["trend"])
    rev = build(d, "reversal", label="反转 RSI35→75", **BEST["reversal"])
    brk = build(d, "breakout", label="突破 唐奇安10/10", **BEST["breakout"])
    e_b, x_b = FAMILIES["breakout"](d, **BEST["breakout"])
    above = (d["close"] > ma(d, 40)).fillna(False).to_numpy()

    out = [
        ("基准 MA40+量", build(d, "baseline", label="基准 MA40+量")),
        ("突破 唐奇安10/10", brk),
        ("确认制 突破+趋势在位", simulate(d, e_b & above, x_b, "确认制 突破+趋势在位")),
        ("等权三族组合", equal_weight([trend, rev, brk], d, "等权三族组合")),
        ("基准+MACD离场", build(d, "trend", label="基准+MACD离场", ma_n=40, vol_mult=1.2,
                             use_macd=False, use_cvd=False, exit_macd=True)),
    ]
    return out


def run_validate(d: pd.DataFrame) -> None:
    """头部方案的分样本与分年度检验: 全样本排名能不能在子样本里复现"""
    mid = len(d) // 2
    cut = d["date"].iloc[mid]
    recent = d[d["date"] >= d["date"].iloc[-1] - pd.Timedelta(days=3 * 365)]

    samples = [
        ("全样本", d),
        (f"前半段 (~{cut:%Y-%m})", d.iloc[:mid].reset_index(drop=True)),
        (f"后半段 ({cut:%Y-%m}~)", d.iloc[mid:].reset_index(drop=True)),
        ("最近3年", recent.reset_index(drop=True)),
    ]

    print("\n  分样本 Calmar 对照 (全样本最优的方案，在子样本里还是最优吗)")
    print("  " + "─" * 82)
    names = [n for n, _ in candidates(d)]
    print("  {:<24}".format("样本") + "".join(f"{n[:14]:>15}" for n in names))
    print("  " + "─" * 82)
    for sname, sub in samples:
        cells = []
        for _, r in candidates(sub):
            cagr, calmar, _, _ = metrics(r)
            cells.append(f"{calmar:>15.2f}")
        print(f"  {sname:<24}" + "".join(cells))
    print("  " + "─" * 82)

    print("\n  同上，看总收益")
    print("  " + "─" * 82)
    print("  {:<24}".format("样本") + "".join(f"{n[:14]:>15}" for n in names))
    print("  " + "─" * 82)
    for sname, sub in samples:
        cells = [f"{r.total_return:>+14.0%} " for _, r in candidates(sub)]
        print(f"  {sname:<24}" + "".join(cells))
    print("  " + "─" * 82)

    print("\n  同上，看最大回撤 (越小越好)")
    print("  " + "─" * 82)
    print("  {:<24}".format("样本") + "".join(f"{n[:14]:>15}" for n in names))
    print("  " + "─" * 82)
    for sname, sub in samples:
        cells = [f"{r.max_drawdown:>14.1%} " for _, r in candidates(sub)]
        print(f"  {sname:<24}" + "".join(cells))
    print("  " + "─" * 82)

    print("\n  持仓时间占比 (回撤低是不是只因为在场时间短)")
    print("  " + "─" * 82)
    print("  {:<24}".format("样本") + "".join(f"{n[:14]:>15}" for n in names))
    print("  " + "─" * 82)
    for sname, sub in samples:
        cells = [f"{r.exposure:>14.0%} " for _, r in candidates(sub)]
        print(f"  {sname:<24}" + "".join(cells))
    print("  " + "─" * 82)

    print_yearly([r for _, r in candidates(d)])


FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
FUNDING_CACHE = CACHE_DIR / "BTCUSDT_funding.csv"


def load_funding(symbol: str = "BTCUSDT", offline: bool = False) -> pd.Series:
    """
    资金费率历史 (每 8 小时一条，2019-09 起)。

    用作 OI 的长历史代理: 两者都反映杠杆多头的堆积程度。
    OI 本身的 openInterestHist 接口只给最近 30 天，无法回测。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = pd.read_csv(FUNDING_CACHE) if FUNDING_CACHE.exists() else pd.DataFrame()

    if not offline:
        # startTime=0 会被当成「不指定」，只返回最新 500 条，必须从 1 开始
        cursor = int(cached["fundingTime"].iloc[-1]) + 1 if len(cached) else 1
        rows = []
        now_ms = int(time.time() * 1000)
        while cursor < now_ms:
            batch = requests.get(FUNDING_URL, params={
                "symbol": symbol, "limit": 1000, "startTime": cursor}, timeout=15).json()
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1]["fundingTime"]) + 1
            if len(batch) < 1000:
                break
            time.sleep(0.15)
        if rows:
            fresh = pd.DataFrame(rows)[["fundingTime", "fundingRate"]]
            cached = pd.concat([cached, fresh], ignore_index=True)
            cached = cached.drop_duplicates("fundingTime").sort_values("fundingTime")
            cached.to_csv(FUNDING_CACHE, index=False)

    if cached.empty:
        raise RuntimeError("没有资金费率数据")

    s = pd.Series(
        cached["fundingRate"].astype(float).to_numpy(),
        index=pd.to_datetime(cached["fundingTime"].astype("int64"), unit="ms", utc=True),
    )
    return s.resample("1D").mean()      # 一天三次 → 日均


def run_funding(d: pd.DataFrame, offline: bool = False) -> None:
    """用资金费率代理 OI，检验「杠杆持仓在堆积」能否改进突破进场"""
    fr = load_funding(offline=offline)
    d = d.copy()
    d["funding"] = d["date"].dt.floor("D").map(fr).astype(float)
    d = d[d["funding"].notna()].reset_index(drop=True)

    print(f"\n  资金费率样本: {d['date'].iloc[0]:%Y-%m-%d} ~ {d['date'].iloc[-1]:%Y-%m-%d} "
          f"({len(d)} 天)  均值 {d['funding'].mean() * 100:.4f}%")

    f = d["funding"]
    base_e, base_x = FAMILIES["baseline"](d, ma_n=40, vol_mult=1.2)
    filters = [
        ("无过滤 (基准)", np.ones(len(d), dtype=bool)),
        ("费率为正 (多头付费)", (f > 0).to_numpy()),
        ("费率上升 (5日)", (f > f.rolling(5).mean()).fillna(False).to_numpy()),
        ("费率上升 (20日)", (f > f.rolling(20).mean()).fillna(False).to_numpy()),
        ("费率不过热 (<0.05%)", (f < 0.0005).to_numpy()),
        ("费率不过热 (<0.03%)", (f < 0.0003).to_numpy()),
        ("正且不过热", ((f > 0) & (f < 0.0005)).to_numpy()),
    ]
    results = [simulate(d, base_e & mask, base_x, name) for name, mask in filters]
    print_table(results, "资金费率作为进场过滤 (OI 的长历史代理)")


def run_robust(d: pd.DataFrame, family: str, **kw) -> None:
    """分样本检验: 前后半段各跑一次，看最优参数是否稳定"""
    mid = len(d) // 2
    halves = [("前半段", d.iloc[:mid].reset_index(drop=True)),
              ("后半段", d.iloc[mid:].reset_index(drop=True)),
              ("全样本", d)]
    results = []
    for name, sub in halves:
        r = build(sub, family, label=f"{family} {name}", **kw)
        results.append(r)
    print_table(results, f"{family} 分样本检验")


# ══════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════

def build(d: pd.DataFrame, name: str, label: str = "",
          max_bars: int = 0, **sig_kw) -> Result:
    entry, exit_ = FAMILIES[name](d, **sig_kw)
    return simulate(d, entry, exit_, label or name, max_bars=max_bars)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="BTC 多信号族策略回测")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1d")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--family", default="all",
                   choices=["all", "baseline", "trend", "reversal", "breakout"])
    p.add_argument("--start", default="", help="起始日期 YYYY-MM-DD")
    p.add_argument("--yearly", action="store_true")
    p.add_argument("--ablate", action="store_true", help="拆解趋势族各指标贡献")
    p.add_argument("--sweep", default="", choices=["", "trend", "reversal", "breakout"])
    p.add_argument("--combo", action="store_true", help="组合方式对比")
    p.add_argument("--robust", action="store_true", help="分样本检验")
    p.add_argument("--validate", action="store_true", help="头部方案分样本+逐年检验")
    p.add_argument("--funding", action="store_true", help="资金费率代理 OI 的过滤检验")
    args = p.parse_args(argv)

    d = load_klines(args.symbol, args.interval, args.offline)
    if args.start:
        d = d[d["date"] >= pd.Timestamp(args.start, tz="UTC")].reset_index(drop=True)
    d = add_indicators(d)

    print(f"\n  数据: {args.symbol} {args.interval}  "
          f"{d['date'].iloc[0]:%Y-%m-%d} ~ {d['date'].iloc[-1]:%Y-%m-%d}  ({len(d)} 根)")

    if args.ablate:
        run_ablate(d)
        return 0
    if args.sweep:
        run_sweep(d, args.sweep)
        return 0
    if args.combo:
        run_combo(d)
        return 0
    if args.validate:
        run_validate(d)
        return 0
    if args.funding:
        run_funding(d, args.offline)
        return 0
    if args.robust:
        for fam in (["trend", "reversal", "breakout"] if args.family == "all" else [args.family]):
            run_robust(d, fam)
        return 0

    names = ["baseline", "trend", "reversal", "breakout"] if args.family == "all" else [args.family]
    results = []
    for name in names:
        r = build(d, name)
        r.label = {"baseline": "基准 MA40+量1.2",
                   "trend": "趋势 MA+MACD+CVD",
                   "reversal": "反转 RSI+量+布林",
                   "breakout": "突破 唐奇安+量"}.get(name, name)
        results.append(r)

    print_table(results, "三大信号族对比 (仅做多，双边扣费 0.05%)")
    if len(results) == 1:
        print_detail(results[0], d)
    if args.yearly:
        print_yearly(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
