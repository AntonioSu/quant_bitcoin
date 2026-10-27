#!/usr/bin/env python3
"""
BTC 均线突破策略回测

策略规则:
1. 收盘价上穿 N 日均线 → 做多（多头模式下为买入，空头模式下为平空）
2. 收盘价下破 N 日均线 → 做空（多头模式下为卖出离场，空头模式下为开空）
3. 信号在收盘确认，下一根 K 线开盘成交（避免未来函数）

用法:
    python -m backtest.ma_breakout_backtest                    # 默认 BTCUSDT 日线 MA25 多空双向
    python -m backtest.ma_breakout_backtest --mode long        # 只做多
    python -m backtest.ma_breakout_backtest --interval 4h      # 4小时线
    python -m backtest.ma_breakout_backtest --sweep            # 扫描不同均线周期的胜率
    python -m backtest.ma_breakout_backtest --plot             # 输出图表
"""

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import requests

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "klines"
OUTPUT_DIR = PROJECT_ROOT / "data" / "backtest"

INTERVAL_MS = {
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


# ══════════════════════════════════════════════════════════════
# 数据获取
# ══════════════════════════════════════════════════════════════

def fetch_klines(symbol: str, interval: str, start_ms: int = 0) -> pd.DataFrame:
    """分页拉取 Binance 全量历史 K 线"""
    rows: List[list] = []
    cursor = start_ms
    now_ms = int(time.time() * 1000)

    while cursor < now_ms:
        resp = requests.get(
            BINANCE_KLINES_URL,
            params={"symbol": symbol, "interval": interval, "startTime": cursor, "limit": 1000},
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
        time.sleep(0.15)  # 避免触发限频

    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df = df[["open_time", "open", "high", "low", "close", "volume", "close_time"]]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def load_klines(symbol: str, interval: str, offline: bool = False) -> pd.DataFrame:
    """读取本地缓存，并增量补齐最新 K 线"""
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
        except Exception as exc:  # noqa: BLE001 - 网络失败时退回缓存
            if cached.empty:
                raise RuntimeError(f"无缓存且无法访问 Binance: {exc}") from exc
            print(f"⚠️  拉取失败，使用本地缓存: {exc}")

    if cached.empty:
        raise RuntimeError(f"没有 {symbol} {interval} 的 K 线数据")

    df = cached.copy()
    # 丢弃尚未收盘的当前 K 线
    df = df[df["close_time"] < int(time.time() * 1000)]
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# 回测核心
# ══════════════════════════════════════════════════════════════

@dataclass
class Trade:
    direction: int          # 1=多 -1=空
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: Optional[pd.Timestamp] = None
    exit_price: float = 0.0
    bars: int = 0
    ret: float = 0.0        # 扣费后收益率
    entry_idx: int = 0
    bull: bool = False      # 进场时是否处于牛市状态

    @property
    def side(self) -> str:
        return "多" if self.direction == 1 else "空"

    @property
    def is_win(self) -> bool:
        return self.ret > 0


@dataclass
class Result:
    ma_period: int
    mode: str
    trades: List[Trade] = field(default_factory=list)
    equity: pd.Series = field(default_factory=pd.Series)
    open_trade: Optional[Trade] = None
    bars: int = 0
    buy_hold: float = 0.0
    vol_mult: float = 0.0
    exit_mult: float = 0.0
    exit_wait: int = 0
    climax_mult: float = 0.0
    exit_ma: int = 0
    reentry: str = "cross"
    regime_ma: int = 0
    regime_rule: str = "price"
    exit_ma_bull: int = 0
    exit_ma_bear: int = 0
    bull_share: float = 0.0     # 牛市状态占比
    signals_skipped: int = 0    # 被成交量过滤掉的突破次数
    exits_delayed: int = 0      # 因缩量而推迟的离场次数
    exits_climax: int = 0       # 天量派发触发的离场次数

    @property
    def closed(self) -> List[Trade]:
        return self.trades

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


def build_positions(
    desired: np.ndarray,
    exit_sign: np.ndarray,
    vol_ratio: np.ndarray,
    bar_dir: np.ndarray,
    entry_mult: float,
    exit_mult: float,
    exit_wait: int,
    climax_mult: float,
    gate: str,
    reentry: str = "cross",
) -> tuple:
    """
    把均线信号转成实际仓位

    进场: desired（进场均线）给出方向，且价格站在离场均线正确一侧，
          突破当根必须放量 ≥ entry_mult 倍均量；缩量突破按 gate 处理
          （flat = 空仓观望，hold = 维持原仓位）
    离场: 价格跌破 exit_sign 代表的离场均线即走；若 exit_mult > 0 则要求放量，
          缩量时最多再观察 exit_wait 根
    派发: 持仓中出现 ≥ climax_mult 倍均量的逆势K线，直接离场（天量见顶）
    重进: cross = 必须等进场信号先失效再重新出现（默认）
          fast  = 价格重回离场均线之上即可再进
    """
    n = len(desired)
    out = np.zeros(n)
    cur = 0.0
    waited = 0
    blocked = 0.0          # 刚离场的方向，等进场信号重置后解禁
    skipped_entry = 0
    delayed_exit = 0
    climax_exit = 0

    for i in range(n):
        d = desired[i]
        x = exit_sign[i]
        vr = vol_ratio[i]

        # 逆势天量 → 派发离场
        if cur != 0 and climax_mult > 0 and vr >= climax_mult and bar_dir[i] == -cur:
            if reentry == "cross":
                blocked = cur
            cur = 0.0
            waited = 0
            climax_exit += 1
            out[i] = cur
            continue

        # 离场判断：价格跌破离场均线
        prev = 0.0
        if cur != 0 and x != cur:
            if exit_mult > 0 and vr < exit_mult and waited < exit_wait:
                waited += 1
                delayed_exit += 1
                out[i] = cur
                continue
            prev = cur
            cur = 0.0
            waited = 0
            if reentry == "cross":
                blocked = prev

        if cur != 0:
            waited = 0
            out[i] = cur
            continue

        # 进场信号已经失效过一次，解除上一次离场的封锁
        if blocked != 0 and d != blocked:
            blocked = 0.0

        # 进场判断：进场均线给方向，且已站上离场均线
        if d != 0 and d != blocked and x == d:
            if entry_mult <= 0 or vr >= entry_mult:
                cur = d
                blocked = 0.0
            else:
                skipped_entry += 1
                if gate == "hold":
                    cur = prev
        out[i] = cur

    return out, skipped_entry, delayed_exit, climax_exit


def run_backtest(
    df: pd.DataFrame,
    ma_period: int,
    mode: str,
    fee: float,
    vol_mult: float = 0.0,
    vol_ma: int = 20,
    vol_gate: str = "flat",
    exit_mult: float = 0.0,
    exit_wait: int = 3,
    climax_mult: float = 0.0,
    exit_ma: int = 0,
    reentry: str = "cross",
    regime_ma: int = 0,
    regime_rule: str = "price",
    exit_ma_bull: int = 0,
    exit_ma_bear: int = 0,
) -> Result:
    """
    执行回测

    Args:
        df: 含 open/close/volume/date 的 K 线
        ma_period: 均线周期
        mode: long=只做多 / short=只做空 / both=多空反手
        fee: 单边手续费率（0.0005 = 0.05%）
        vol_mult: 进场要求的放量倍数（相对成交量均线），0 表示不过滤
        vol_ma: 成交量均线周期
        vol_gate: flat=缩量突破空仓观望 / hold=缩量突破维持原仓
        exit_mult: 离场要求的放量倍数，缩量跌破则延后，0 表示跌破立即走
        exit_wait: 缩量跌破最多观察几根K线
        climax_mult: 逆势天量派发离场的倍数阈值，0 表示不启用
        exit_ma: 离场均线周期，0 表示与进场均线相同
        reentry: cross=需重新突破才可再进 / fast=重回离场均线之上即可再进
        regime_ma: 牛熊判断均线周期，0 表示不启用状态切换
        regime_rule: price=价格在均线上方 / slope=均线向上 / both=两者同时满足
        exit_ma_bull: 牛市用的离场均线（慢线，让利润奔跑）
        exit_ma_bear: 非牛市用的离场均线（快线，快速止损）
    """
    data = df.copy()
    data["ma"] = data["close"].rolling(ma_period).mean()

    # 收盘价相对均线的位置 → 目标仓位
    raw = np.sign(data["close"] - data["ma"])
    raw = raw.where(data["ma"].notna())
    if mode == "long":
        target = raw.clip(lower=0)
    elif mode == "short":
        target = raw.clip(upper=0)
    else:
        target = raw
    target = target.fillna(0)

    # 离场参考线：牛市用慢线让利润奔跑，其余用快线快速止损
    regime_active = bool(regime_ma and exit_ma_bull and exit_ma_bear)
    if regime_active:
        trend = data["close"].rolling(regime_ma).mean()
        above = data["close"] > trend
        rising = trend > trend.shift(1)
        bull = {"price": above, "slope": rising, "both": above & rising}[regime_rule]
        bull = bull.where(trend.notna(), False)      # 预热期按非牛市处理
        data["bull"] = bull
        fast = data["close"].rolling(exit_ma_bear).mean()
        slow = data["close"].rolling(exit_ma_bull).mean()
        exit_line = slow.where(bull, fast)
    elif exit_ma and exit_ma != ma_period:
        exit_line = data["close"].rolling(exit_ma).mean()
    else:
        exit_line = None

    if exit_line is None:
        exit_sign = raw.fillna(0)
    else:
        exit_sign = np.sign(data["close"] - exit_line).where(exit_line.notna()).fillna(0)

    skipped = delayed = climaxed = 0
    if (vol_mult > 0 or exit_mult > 0 or climax_mult > 0 or regime_active
            or (exit_ma and exit_ma != ma_period)):
        vol_avg = data["volume"].rolling(vol_ma).mean()
        vol_ratio = (data["volume"] / vol_avg).fillna(0).to_numpy()
        bar_dir = np.sign(data["close"] - data["open"]).to_numpy()
        gated, skipped, delayed, climaxed = build_positions(
            target.to_numpy(), exit_sign.to_numpy(), vol_ratio, bar_dir,
            vol_mult, exit_mult, exit_wait, climax_mult, vol_gate, reentry,
        )
        target = pd.Series(gated, index=data.index)

    # 第 i 根收盘出信号 → 第 i+1 根开盘建仓，即持仓在 i+1 开盘到 i+2 开盘之间生效
    position = target.shift(1).fillna(0).to_numpy()
    opens = data["open"].to_numpy()
    dates = data["date"].to_numpy()
    bulls = data["bull"].to_numpy() if regime_active else np.zeros(len(data), dtype=bool)
    n = len(data)

    equity_vals = [1.0]
    equity_idx = [dates[0]]
    trades: List[Trade] = []
    current: Optional[Trade] = None
    nav = 1.0

    for i in range(1, n):
        pos = position[i]            # open[i] → open[i+1] 区间的持仓
        prev_pos = position[i - 1]

        # 换仓：先结算旧仓，再开新仓，成交价都是 open[i]
        if pos != prev_pos:
            turnover = abs(pos - prev_pos)
            nav *= 1 - fee * turnover

            if current is not None:
                current.exit_time = dates[i]
                current.exit_price = opens[i]
                current.bars = i - current.entry_idx
                gross = current.direction * (current.exit_price / current.entry_price - 1)
                current.ret = gross - 2 * fee
                trades.append(current)
                current = None

            if pos != 0:
                current = Trade(direction=int(pos), entry_time=dates[i],
                                entry_price=opens[i], entry_idx=i,
                                bull=bool(bulls[i - 1]))

        # 区间盈亏（下一根开盘价才能结算，最后一根跳过）
        if i + 1 < n and pos != 0:
            nav *= 1 + pos * (opens[i + 1] / opens[i] - 1)

        equity_vals.append(nav)
        equity_idx.append(dates[i])

    result = Result(
        ma_period=ma_period,
        mode=mode,
        trades=trades,
        equity=pd.Series(equity_vals, index=pd.DatetimeIndex(equity_idx)),
        open_trade=current,
        bars=n,
        buy_hold=float(data["close"].iloc[-1] / data["close"].iloc[0] - 1),
        vol_mult=vol_mult,
        exit_mult=exit_mult,
        exit_wait=exit_wait,
        climax_mult=climax_mult,
        exit_ma=exit_ma,
        reentry=reentry,
        regime_ma=regime_ma if regime_active else 0,
        regime_rule=regime_rule,
        exit_ma_bull=exit_ma_bull if regime_active else 0,
        exit_ma_bear=exit_ma_bear if regime_active else 0,
        bull_share=float(data["bull"].mean()) if regime_active else 0.0,
        signals_skipped=skipped,
        exits_delayed=delayed,
        exits_climax=climaxed,
    )
    return result


# ══════════════════════════════════════════════════════════════
# 报告输出
# ══════════════════════════════════════════════════════════════

def print_report(res: Result, df: pd.DataFrame, symbol: str, interval: str, fee: float) -> None:
    trades = res.closed
    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win]
    years = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
    cagr = (1 + res.total_return) ** (1 / years) - 1 if years > 0 and res.total_return > -1 else float("nan")

    mode_label = {"long": "仅做多", "short": "仅做空", "both": "多空反手"}[res.mode]
    line = "─" * 52

    print(f"\n{line}")
    if res.regime_ma:
        exit_desc = f" → 牛市跌破 MA{res.exit_ma_bull} / 其余跌破 MA{res.exit_ma_bear} 平仓"
    elif res.exit_ma and res.exit_ma != res.ma_period:
        exit_desc = f" → 跌破 MA{res.exit_ma} 平仓"
    else:
        exit_desc = ""
    print(f"  {symbol} {interval}  MA{res.ma_period} 突破策略{exit_desc} · {mode_label}")
    print(line)
    print(f"  区间      {df['date'].iloc[0]:%Y-%m-%d} ~ {df['date'].iloc[-1]:%Y-%m-%d}  ({len(df)} 根 / {years:.1f} 年)")
    print(f"  手续费    单边 {fee * 100:.3f}%")
    if res.vol_mult > 0:
        print(f"  进场量能  需放量 ≥ {res.vol_mult:.1f}× 均量   (过滤掉 {res.signals_skipped} 次缩量突破)")
    if res.exit_mult > 0:
        print(f"  离场量能  需放量 ≥ {res.exit_mult:.1f}× 均量，缩量最多等 {res.exit_wait} 根"
              f"   (推迟 {res.exits_delayed} 次)")
    if res.climax_mult > 0:
        print(f"  天量派发  逆势 ≥ {res.climax_mult:.1f}× 均量即离场   (触发 {res.exits_climax} 次)")
    if res.regime_ma:
        rule = {"price": f"收盘价在 MA{res.regime_ma} 上方",
                "slope": f"MA{res.regime_ma} 向上",
                "both": f"收盘价在 MA{res.regime_ma} 上方且均线向上"}[res.regime_rule]
        print(f"  牛市定义  {rule}   (占全部时间的 {res.bull_share:.1%})")
    print(line)
    print(f"  交易次数  {len(trades)}  (盈利 {len(wins)} / 亏损 {len(losses)})")
    print(f"  ★ 胜率    {res.win_rate:.2%}")
    if trades:
        print("  平均盈利  " + (f"{np.mean([t.ret for t in wins]):+.2%}" if wins else "—"))
        print("  平均亏损  " + (f"{np.mean([t.ret for t in losses]):+.2%}" if losses else "—"))
        print(f"  盈亏比    {res.profit_factor:.2f}")
        print(f"  期望收益  {np.mean([t.ret for t in trades]):+.2%} / 笔")
        print(f"  最大单笔  {max(t.ret for t in trades):+.2%} / {min(t.ret for t in trades):+.2%}")
        print(f"  平均持仓  {np.mean([t.bars for t in trades]):.1f} 根")
    print(line)
    print(f"  策略总收益 {res.total_return:+.2%}   年化 {cagr:+.2%}")
    print(f"  最大回撤   {res.max_drawdown:.2%}")
    print(f"  买入持有   {res.buy_hold:+.2%}")
    if res.open_trade:
        t = res.open_trade
        print(f"  当前持仓   {t.side} @ {t.entry_price:,.0f} (自 {pd.Timestamp(t.entry_time):%Y-%m-%d})")
    print(line)

    # 牛熊状态拆解
    if res.regime_ma and trades:
        print("  分状态:")
        for flag, name in ((True, f"牛市(MA{res.exit_ma_bull}离场)"),
                           (False, f"其余(MA{res.exit_ma_bear}离场)")):
            sub = [t for t in trades if t.bull == flag]
            if sub:
                wr = sum(t.is_win for t in sub) / len(sub)
                print(f"    {name:<18} {len(sub):>3} 笔   胜率 {wr:.2%}   "
                      f"均盈亏 {np.mean([t.ret for t in sub]):+.2%}   "
                      f"累计 {sum(t.ret for t in sub):+.2%}")
        print(line)

    # 分方向拆解
    if res.mode == "both" and trades:
        print("  分方向:")
        for d, name in ((1, "做多"), (-1, "做空")):
            sub = [t for t in trades if t.direction == d]
            if sub:
                wr = sum(t.is_win for t in sub) / len(sub)
                print(f"    {name}  {len(sub):>3} 笔   胜率 {wr:.2%}   累计 {sum(t.ret for t in sub):+.2%}")
        print(line)


MODE_LABEL = {"long": "仅做多", "short": "仅做空", "both": "多空反手"}


def print_sweep(df: pd.DataFrame, mode: str, fee: float, periods: List[int], **kw) -> None:
    print(f"\n  均线周期敏感性扫描 ({MODE_LABEL[mode]})")
    print("  " + "─" * 62)
    print(f"  {'MA':>4} {'交易数':>7} {'胜率':>8} {'盈亏比':>8} {'总收益':>11} {'最大回撤':>10}")
    print("  " + "─" * 62)
    for p in periods:
        r = run_backtest(df, p, mode, fee, **kw)
        mark = " ←" if p == 25 else ""
        print(f"  {p:>4} {len(r.closed):>7} {r.win_rate:>8.2%} {r.profit_factor:>8.2f} "
              f"{r.total_return:>10.1%} {r.max_drawdown:>10.1%}{mark}")
    print("  " + "─" * 62)


def print_yearly(res: Result, df: pd.DataFrame) -> None:
    """按自然年拆解收益（权益曲线连续，跨年持仓不重置）"""
    eq = res.equity
    price = df.set_index("date")["close"]

    def year_return(series: pd.Series, year: int) -> float:
        sub = series[series.index.year == year]
        before = series[series.index < sub.index[0]]
        base = before.iloc[-1] if len(before) else sub.iloc[0]
        return float(sub.iloc[-1] / base - 1)

    print(f"\n  分年度收益 (MA{res.ma_period} · {MODE_LABEL[res.mode]}"
          f"{f' · 放量 {res.vol_mult:g}x' if res.vol_mult > 0 else ''})")
    print("  " + "─" * 62)
    print(f"  {'年份':>6} {'交易数':>7} {'胜率':>8} {'策略收益':>11} {'买入持有':>11} {'超额':>10}")
    print("  " + "─" * 62)

    positive = 0
    years = sorted({int(y) for y in eq.index.year})
    for y in years:
        strat = year_return(eq, y)
        hold = year_return(price, y)
        done = [t for t in res.closed if pd.Timestamp(t.exit_time).year == y]
        wr = sum(t.is_win for t in done) / len(done) if done else float("nan")
        positive += strat > 0
        flag = "✓" if strat > 0 else "✗"
        wr_txt = f"{wr:>8.1%}" if done else f"{'—':>8}"
        print(f"  {y:>6} {len(done):>7} {wr_txt} {strat:>10.1%} {hold:>10.1%} "
              f"{strat - hold:>+9.1%}  {flag}")
    print("  " + "─" * 62)
    print(f"  {positive}/{len(years)} 年为正收益")


def _metrics(r: Result, df: pd.DataFrame) -> tuple:
    """年化、Calmar、Sharpe"""
    eq = r.equity
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (1 + r.total_return) ** (1 / years) - 1 if years > 0 and r.total_return > -1 else float("nan")
    daily = np.log(eq.replace(0, np.nan)).diff().dropna()
    bars_per_year = len(eq) / years if years > 0 else 0
    sharpe = daily.mean() / daily.std() * np.sqrt(bars_per_year) if daily.std() else float("nan")
    calmar = cagr / abs(r.max_drawdown) if r.max_drawdown else float("nan")
    return cagr, calmar, sharpe


def print_regime_sweep(df: pd.DataFrame, ma_period: int, mode: str, fee: float, base: dict) -> None:
    """扫描牛熊切换的参数组合，并与固定离场均线对照"""
    base = {k: v for k, v in base.items()
            if k not in ("regime_ma", "regime_rule", "exit_ma_bull", "exit_ma_bear", "exit_ma")}
    print(f"\n  牛熊切换离场扫描 (进场 MA{ma_period} · {MODE_LABEL[mode]} · "
          f"进场放量 {base.get('vol_mult', 0):g}x)")
    print("  " + "─" * 82)
    print(f"  {'配置':>30} {'交易数':>7} {'胜率':>8} {'年化':>8} {'最大回撤':>9} "
          f"{'Calmar':>7} {'Sharpe':>7}")
    print("  " + "─" * 82)

    variants = [(f"固定 MA{ma_period} 离场", {}), ("固定 MA10 离场", {"exit_ma": 10})]
    for rma in (100, 150, 200, 250):
        for rule in ("price", "slope", "both"):
            variants.append((
                f"MA{rma} {rule} → 牛25/其余10",
                {"regime_ma": rma, "regime_rule": rule, "exit_ma_bull": 25, "exit_ma_bear": 10},
            ))

    for label, extra in variants:
        r = run_backtest(df, ma_period, mode, fee, **{**base, **extra})
        cagr, calmar, sharpe = _metrics(r, df)
        print(f"  {label:>30} {len(r.closed):>7} {r.win_rate:>8.2%} {cagr:>8.1%} "
              f"{r.max_drawdown:>9.1%} {calmar:>7.2f} {sharpe:>7.2f}")
    print("  " + "─" * 82)


def print_exit_ma_sweep(df: pd.DataFrame, ma_period: int, mode: str, fee: float,
                        base: dict, exit_mas: List[int]) -> None:
    """扫描离场均线周期：用更快的均线做跟踪止损"""
    print(f"\n  离场均线扫描 (进场 MA{ma_period} · {MODE_LABEL[mode]} · "
          f"进场放量 {base.get('vol_mult', 0):g}x · 重进场 {base.get('reentry', 'cross')})")
    print("  " + "─" * 74)
    print(f"  {'离场均线':>8} {'交易数':>7} {'胜率':>8} {'盈亏比':>8} {'持仓':>7} "
          f"{'总收益':>11} {'最大回撤':>10} {'收益/回撤':>10}")
    print("  " + "─" * 74)
    for x in exit_mas:
        r = run_backtest(df, ma_period, mode, fee, **{**base, "exit_ma": x})
        hold = np.mean([t.bars for t in r.closed]) if r.closed else 0
        ratio = r.total_return / abs(r.max_drawdown) if r.max_drawdown else float("nan")
        label = f"MA{x}" + ("(同)" if x == ma_period else "")
        print(f"  {label:>8} {len(r.closed):>7} {r.win_rate:>8.2%} {r.profit_factor:>8.2f} "
              f"{hold:>6.1f}根 {r.total_return:>10.1%} {r.max_drawdown:>10.1%} {ratio:>10.1f}")
    print("  " + "─" * 74)


def print_exit_sweep(df: pd.DataFrame, ma_period: int, mode: str, fee: float, base: dict) -> None:
    """扫描离场端的量能规则：放量确认 × 观察窗口，以及天量派发"""
    print(f"\n  离场规则扫描 (MA{ma_period} · {MODE_LABEL[mode]} · "
          f"进场放量 {base.get('vol_mult', 0):g}x)")
    print("  " + "─" * 72)
    print(f"  {'离场规则':>22} {'交易数':>7} {'胜率':>8} {'盈亏比':>8} {'总收益':>11} {'最大回撤':>10}")
    print("  " + "─" * 72)

    variants = [("跌破立即走（基准）", {})]
    for m in (1.0, 1.2, 1.5):
        for w in (1, 2, 3, 5):
            variants.append((f"缩量等{w}根 / 放量{m:g}x走", {"exit_mult": m, "exit_wait": w}))
    for c in (2.5, 3.0, 4.0):
        variants.append((f"逆势天量{c:g}x 提前离场", {"climax_mult": c}))

    for label, extra in variants:
        r = run_backtest(df, ma_period, mode, fee, **{**base, **extra})
        print(f"  {label:>22} {len(r.closed):>7} {r.win_rate:>8.2%} {r.profit_factor:>8.2f} "
              f"{r.total_return:>10.1%} {r.max_drawdown:>10.1%}")
    print("  " + "─" * 72)


def print_vol_sweep(df: pd.DataFrame, ma_period: int, mode: str, fee: float,
                    vol_ma: int, vol_gate: str, mults: List[float]) -> None:
    print(f"\n  成交量过滤敏感性扫描 (MA{ma_period} · {MODE_LABEL[mode]} · "
          f"量均线 {vol_ma} · 缩量时{'空仓' if vol_gate == 'flat' else '持原仓'})")
    print("  " + "─" * 66)
    print(f"  {'放量倍数':>8} {'交易数':>7} {'胜率':>8} {'盈亏比':>8} {'总收益':>11} {'最大回撤':>10}")
    print("  " + "─" * 66)
    for m in mults:
        r = run_backtest(df, ma_period, mode, fee, vol_mult=m, vol_ma=vol_ma, vol_gate=vol_gate)
        label = "无过滤" if m == 0 else f"{m:.1f}x"
        print(f"  {label:>8} {len(r.closed):>7} {r.win_rate:>8.2%} {r.profit_factor:>8.2f} "
              f"{r.total_return:>10.1%} {r.max_drawdown:>10.1%}")
    print("  " + "─" * 66)


def _tag(res: Result, symbol: str, interval: str) -> str:
    vol = f"_vol{res.vol_mult:g}" if res.vol_mult > 0 else ""
    ex = f"_exit{res.exit_mult:g}w{res.exit_wait}" if res.exit_mult > 0 else ""
    cx = f"_climax{res.climax_mult:g}" if res.climax_mult > 0 else ""
    if res.regime_ma:
        xm = f"_rg{res.regime_ma}bull{res.exit_ma_bull}bear{res.exit_ma_bear}"
    elif res.exit_ma and res.exit_ma != res.ma_period:
        xm = f"_xma{res.exit_ma}"
    else:
        xm = ""
    return f"{symbol}_{interval}_ma{res.ma_period}_{res.mode}{vol}{ex}{cx}{xm}"


def save_trades(res: Result, symbol: str, interval: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"trades_{_tag(res, symbol, interval)}.csv"
    pd.DataFrame([{
        "方向": t.side,
        "开仓时间": pd.Timestamp(t.entry_time).strftime("%Y-%m-%d %H:%M"),
        "开仓价": round(t.entry_price, 2),
        "平仓时间": pd.Timestamp(t.exit_time).strftime("%Y-%m-%d %H:%M"),
        "平仓价": round(t.exit_price, 2),
        "持仓K线": t.bars,
        "收益率": round(t.ret, 4),
        "结果": "盈" if t.is_win else "亏",
    } for t in res.closed]).to_csv(path, index=False)
    return path


def plot_result(res: Result, df: pd.DataFrame, symbol: str, interval: str) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ma = df["close"].rolling(res.ma_period).mean()

    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True,
                             gridspec_kw={"height_ratios": [2, 1]})

    ax = axes[0]
    ax.plot(df["date"], df["close"], lw=0.9, color="#333", label=f"{symbol} close")
    ax.plot(df["date"], ma, lw=1.2, color="#d9822b", label=f"MA{res.ma_period}")
    for t in res.closed:
        color = "#2e9e5b" if t.direction == 1 else "#c0392b"
        ax.axvspan(pd.Timestamp(t.entry_time), pd.Timestamp(t.exit_time), color=color, alpha=0.10)
    ax.set_yscale("log")
    ax.set_title(f"{symbol} {interval} | MA{res.ma_period} breakout ({res.mode}) | "
                 f"win rate {res.win_rate:.1%} of {len(res.closed)} trades")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)

    ax = axes[1]
    bh = df.set_index("date")["close"] / df["close"].iloc[0]
    ax.plot(res.equity.index, res.equity.values, lw=1.3, color="#2c6fbb", label="Strategy equity")
    ax.plot(bh.index, bh.values, lw=1.0, color="#999", ls="--", label="Buy & hold")
    ax.set_yscale("log")
    ax.set_ylabel("Equity (x)")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)

    fig.tight_layout()
    path = OUTPUT_DIR / f"backtest_{_tag(res, symbol, interval)}.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="BTC 均线突破策略回测")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1d", choices=list(INTERVAL_MS))
    parser.add_argument("--ma", type=int, default=25, help="均线周期，默认 25")
    parser.add_argument("--mode", default="both", choices=["long", "short", "both"],
                        help="long=只做多 short=只做空 both=多空反手（默认）")
    parser.add_argument("--fee", type=float, default=0.0005, help="单边手续费率，默认 0.05%%")
    parser.add_argument("--vol-mult", type=float, default=0.0,
                        help="进场要求的放量倍数（相对成交量均线），0=不过滤")
    parser.add_argument("--vol-ma", type=int, default=20, help="成交量均线周期，默认 20")
    parser.add_argument("--vol-gate", default="flat", choices=["flat", "hold"],
                        help="缩量突破时 flat=空仓观望（默认） hold=维持原仓位")
    parser.add_argument("--exit-mult", type=float, default=0.0,
                        help="离场要求的放量倍数，缩量跌破则延后，0=跌破立即走")
    parser.add_argument("--exit-wait", type=int, default=3, help="缩量跌破最多观察几根K线")
    parser.add_argument("--climax-mult", type=float, default=0.0,
                        help="逆势天量派发离场的倍数阈值，0=不启用")
    parser.add_argument("--exit-ma", type=int, default=0,
                        help="离场均线周期（如 10 表示跌破 MA10 平仓），0=与进场均线相同")
    parser.add_argument("--reentry", default="cross", choices=["cross", "fast"],
                        help="cross=需重新突破进场均线才可再进（默认） fast=重回离场均线之上即可再进")
    parser.add_argument("--regime-ma", type=int, default=0,
                        help="牛熊判断均线周期（如 200），0=不启用状态切换")
    parser.add_argument("--regime-rule", default="price", choices=["price", "slope", "both"],
                        help="price=价格在均线上方（默认） slope=均线向上 both=两者同时满足")
    parser.add_argument("--exit-ma-bull", type=int, default=25, help="牛市离场均线，默认 25")
    parser.add_argument("--exit-ma-bear", type=int, default=10, help="非牛市离场均线，默认 10")
    parser.add_argument("--regime-sweep", action="store_true", help="扫描牛熊判断参数")
    parser.add_argument("--vol-sweep", action="store_true", help="扫描不同放量倍数")
    parser.add_argument("--exit-sweep", action="store_true", help="扫描离场端的量能规则")
    parser.add_argument("--exit-ma-sweep", action="store_true", help="扫描不同离场均线周期")
    parser.add_argument("--yearly", action="store_true", help="按自然年拆解收益")
    parser.add_argument("--start", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--offline", action="store_true", help="只用本地缓存，不联网")
    parser.add_argument("--sweep", action="store_true", help="扫描不同均线周期")
    parser.add_argument("--plot", action="store_true", help="输出图表 PNG")
    parser.add_argument("--csv", action="store_true", help="导出逐笔交易 CSV")
    args = parser.parse_args(argv)

    df = load_klines(args.symbol, args.interval, offline=args.offline)
    if args.start:
        df = df[df["date"] >= pd.Timestamp(args.start, tz="UTC")]
    if args.end:
        df = df[df["date"] <= pd.Timestamp(args.end, tz="UTC")]
    df = df.reset_index(drop=True)

    if len(df) < args.ma + 10:
        print(f"❌ K 线不足（{len(df)} 根），无法计算 MA{args.ma}")
        return 1

    vol_kw = dict(vol_mult=args.vol_mult, vol_ma=args.vol_ma, vol_gate=args.vol_gate,
                  exit_mult=args.exit_mult, exit_wait=args.exit_wait,
                  climax_mult=args.climax_mult, exit_ma=args.exit_ma, reentry=args.reentry,
                  regime_ma=args.regime_ma, regime_rule=args.regime_rule,
                  exit_ma_bull=args.exit_ma_bull, exit_ma_bear=args.exit_ma_bear)
    res = run_backtest(df, args.ma, args.mode, args.fee, **vol_kw)
    print_report(res, df, args.symbol, args.interval, args.fee)

    if args.yearly:
        print_yearly(res, df)

    if args.sweep:
        print_sweep(df, args.mode, args.fee, [5, 10, 15, 20, 25, 30, 50, 60, 100, 150, 200], **vol_kw)

    if args.vol_sweep:
        print_vol_sweep(df, args.ma, args.mode, args.fee, args.vol_ma, args.vol_gate,
                        [0, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0])

    if args.exit_sweep:
        print_exit_sweep(df, args.ma, args.mode, args.fee,
                         dict(vol_mult=args.vol_mult, vol_ma=args.vol_ma, vol_gate=args.vol_gate))

    if args.regime_sweep:
        print_regime_sweep(df, args.ma, args.mode, args.fee, vol_kw)

    if args.exit_ma_sweep:
        print_exit_ma_sweep(df, args.ma, args.mode, args.fee, vol_kw,
                            [3, 5, 7, 10, 13, 15, 20, args.ma])

    if args.csv:
        print(f"\n  逐笔交易 → {save_trades(res, args.symbol, args.interval)}")
    if args.plot:
        print(f"  图表 → {plot_result(res, df, args.symbol, args.interval)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
