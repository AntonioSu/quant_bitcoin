"""
BIST Divergence Scanner — Python port of the TradingView Pine Script
"UYUMSUZLUK TARAMA (2026)"

Scans a basket of BIST (Istanbul Stock Exchange) stocks for multi-indicator
divergences (Regular & Hidden, Positive & Negative) across 11 technical
indicators: MACD, MACD-Histogram, RSI, Stochastic, CCI, Momentum, OBV,
Volume-Weighted MACD, Chaikin Money Flow, Money Flow Index, and an optional
external indicator.

Usage:
    scanner = BISTDivergenceScanner()
    results = scanner.scan(symbols=["AKBNK", "GARAN", ...], timeframe="1d")
    for r in results:
        print(r)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Flag, auto
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Divergence type flags (combinable)
# ---------------------------------------------------------------------------
class DivType(Flag):
    NONE = 0
    POS_REGULAR = auto()  # Positive regular  (bullish) — price ↓, indicator ↑
    NEG_REGULAR = auto()  # Negative regular  (bearish) — price ↑, indicator ↓
    POS_HIDDEN  = auto()  # Positive hidden   (bullish) — price ↑, indicator ↓ (trend continuation)
    NEG_HIDDEN  = auto()  # Negative hidden   (bearish) — price ↓, indicator ↑ (trend continuation)


INDICATOR_NAMES = [
    "MACD", "Hist", "RSI", "Stoch", "CCI",
    "MOM", "OBV", "VWMACD", "CMF", "MFI", "Extrn",
]


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------
@dataclass
class IndicatorDivergence:
    name: str
    div_type: DivType = DivType.NONE
    bar_distance: int = 0  # how many bars back the divergence spans


@dataclass
class SymbolScanResult:
    symbol: str
    positive_divergences: List[IndicatorDivergence] = field(default_factory=list)
    negative_divergences: List[IndicatorDivergence] = field(default_factory=list)

    @property
    def has_positive(self) -> bool:
        return len(self.positive_divergences) > 0

    @property
    def has_negative(self) -> bool:
        return len(self.negative_divergences) > 0

    def summary(self) -> str:
        parts = []
        if self.positive_divergences:
            names = [d.name for d in self.positive_divergences]
            parts.append(f"POZ({len(names)}): {','.join(names)}")
        if self.negative_divergences:
            names = [d.name for d in self.negative_divergences]
            parts.append(f"NEG({len(names)}): {','.join(names)}")
        return f"{self.symbol:8s} | " + " | ".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Technical-indicator helpers (pure numpy / pandas, no external TA lib needed)
# ---------------------------------------------------------------------------
def _sma(series: np.ndarray, period: int) -> np.ndarray:
    s = pd.Series(series)
    return s.rolling(period, min_periods=period).mean().values


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    s = pd.Series(series)
    return s.ewm(span=period, adjust=False).mean().values


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain).ewm(alpha=1 / period, min_periods=period).mean().values
    avg_loss = pd.Series(loss).ewm(alpha=1 / period, min_periods=period).mean().values
    rs = np.where(avg_loss != 0, avg_gain / avg_loss, 100.0)
    return 100.0 - 100.0 / (1.0 + rs)


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _momentum(close: np.ndarray, period: int = 10) -> np.ndarray:
    mom = np.full_like(close, np.nan)
    mom[period:] = close[period:] - close[:-period]
    return mom


def _cci(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 10) -> np.ndarray:
    tp = (high + low + close) / 3.0
    tp_sma = _sma(tp, period)
    tp_series = pd.Series(tp)
    mad = tp_series.rolling(period, min_periods=period).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
    ).values
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.where(mad != 0, (tp - tp_sma) / (0.015 * mad), 0.0)
    return result


def _obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    direction = np.sign(np.diff(close, prepend=close[0]))
    return np.cumsum(direction * volume)


def _stochastic(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                k_period: int = 14, smooth: int = 3) -> np.ndarray:
    h_high = pd.Series(high).rolling(k_period, min_periods=k_period).max().values
    l_low = pd.Series(low).rolling(k_period, min_periods=k_period).min().values
    denom = h_high - l_low
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_k = np.where(denom != 0, 100.0 * (close - l_low) / denom, 50.0)
    return _sma(raw_k, smooth)


def _vwma(close: np.ndarray, volume: np.ndarray, period: int) -> np.ndarray:
    cv = pd.Series(close * volume).rolling(period, min_periods=period).sum().values
    v = pd.Series(volume).rolling(period, min_periods=period).sum().values
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(v != 0, cv / v, close)


def _vwmacd(close: np.ndarray, volume: np.ndarray,
            fast: int = 12, slow: int = 26) -> np.ndarray:
    return _vwma(close, volume, fast) - _vwma(close, volume, slow)


def _cmf(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         volume: np.ndarray, period: int = 21) -> np.ndarray:
    denom = high - low
    with np.errstate(divide="ignore", invalid="ignore"):
        mfm = np.where(denom != 0, ((close - low) - (high - close)) / denom, 0.0)
    mfv = mfm * volume
    return np.where(
        _sma(volume, period) != 0,
        _sma(mfv, period) / _sma(volume, period),
        0.0,
    )


def _mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         volume: np.ndarray, period: int = 14) -> np.ndarray:
    tp = (high + low + close) / 3.0
    rmf = tp * volume
    delta = np.diff(tp, prepend=tp[0])
    pos_flow = np.where(delta > 0, rmf, 0.0)
    neg_flow = np.where(delta < 0, rmf, 0.0)
    pos_sum = pd.Series(pos_flow).rolling(period, min_periods=period).sum().values
    neg_sum = pd.Series(neg_flow).rolling(period, min_periods=period).sum().values
    with np.errstate(divide="ignore", invalid="ignore"):
        mfi = np.where(neg_sum != 0, 100.0 - 100.0 / (1.0 + pos_sum / neg_sum), 100.0)
    return mfi


# ---------------------------------------------------------------------------
# Pivot-point detection
# ---------------------------------------------------------------------------
def _pivot_high(source: np.ndarray, left: int, right: int) -> List[Tuple[int, float]]:
    """Return list of (bar_index, value) for pivot highs."""
    pivots = []
    for i in range(left, len(source) - right):
        val = source[i]
        if np.isnan(val):
            continue
        is_pivot = True
        for j in range(1, left + 1):
            if source[i - j] >= val:
                is_pivot = False
                break
        if is_pivot:
            for j in range(1, right + 1):
                if source[i + j] > val:
                    is_pivot = False
                    break
        if is_pivot:
            pivots.append((i, val))
    return pivots


def _pivot_low(source: np.ndarray, left: int, right: int) -> List[Tuple[int, float]]:
    """Return list of (bar_index, value) for pivot lows."""
    pivots = []
    for i in range(left, len(source) - right):
        val = source[i]
        if np.isnan(val):
            continue
        is_pivot = True
        for j in range(1, left + 1):
            if source[i - j] <= val:
                is_pivot = False
                break
        if is_pivot:
            for j in range(1, right + 1):
                if source[i + j] < val:
                    is_pivot = False
                    break
        if is_pivot:
            pivots.append((i, val))
    return pivots


# ---------------------------------------------------------------------------
# Core divergence detection (mirrors Pine Script logic exactly)
# ---------------------------------------------------------------------------
def _check_positive_divergence(
    indicator: np.ndarray,
    price_src: np.ndarray,
    pivot_lows: List[Tuple[int, float]],
    bar_idx: int,
    prd: int,
    maxpp: int,
    maxbars: int,
    dont_confirm: bool,
    cond_type: int,  # 1 = positive regular, 2 = positive hidden
) -> int:
    """
    cond_type 1: Positive Regular — indicator makes higher low while price makes lower low
    cond_type 2: Positive Hidden  — indicator makes lower low while price makes higher low
    Returns: bar distance of divergence (0 = none found).
    """
    startpoint = 0 if dont_confirm else 1
    if not dont_confirm:
        if not (indicator[bar_idx] > indicator[bar_idx - 1]
                or price_src[bar_idx] > price_src[bar_idx - 1]):
            return 0

    recent_pivots = [
        (pos, val) for pos, val in pivot_lows
        if pos < bar_idx - startpoint
    ]
    recent_pivots.sort(key=lambda x: x[0], reverse=True)
    recent_pivots = recent_pivots[:maxpp]

    for pp_pos, pp_val in recent_pivots:
        length = bar_idx - pp_pos + prd
        if length > maxbars:
            break
        if length <= 5:
            continue

        ind_now = indicator[bar_idx - startpoint]
        ind_then = indicator[bar_idx - length] if (bar_idx - length) >= 0 else np.nan
        price_now = price_src[bar_idx - startpoint]

        if np.isnan(ind_then) or np.isnan(ind_now):
            continue

        match = False
        if cond_type == 1:
            match = ind_now > ind_then and price_now < pp_val
        elif cond_type == 2:
            match = ind_now < ind_then and price_now > pp_val

        if not match:
            continue

        # Check the "virtual line" constraint — no violations between the two points
        slope_ind = (ind_now - ind_then) / (length - startpoint)
        vline_ind = ind_now - slope_ind

        close_now = price_src[bar_idx - startpoint]
        close_then = price_src[bar_idx - length] if (bar_idx - length) >= 0 else close_now
        slope_price = (close_now - close_then) / (length - startpoint)
        vline_price = close_now - slope_price

        arrived = True
        for y in range(1 + startpoint, length):
            idx = bar_idx - y
            if idx < 0:
                arrived = False
                break
            if indicator[idx] < vline_ind or price_src[idx] < vline_price:
                arrived = False
                break
            vline_ind -= slope_ind
            vline_price -= slope_price

        if arrived:
            return length

    return 0


def _check_negative_divergence(
    indicator: np.ndarray,
    price_src: np.ndarray,
    pivot_highs: List[Tuple[int, float]],
    bar_idx: int,
    prd: int,
    maxpp: int,
    maxbars: int,
    dont_confirm: bool,
    cond_type: int,  # 1 = negative regular, 2 = negative hidden
) -> int:
    """
    cond_type 1: Negative Regular — indicator makes lower high while price makes higher high
    cond_type 2: Negative Hidden  — indicator makes higher high while price makes lower high
    Returns: bar distance of divergence (0 = none found).
    """
    startpoint = 0 if dont_confirm else 1
    if not dont_confirm:
        if not (indicator[bar_idx] < indicator[bar_idx - 1]
                or price_src[bar_idx] < price_src[bar_idx - 1]):
            return 0

    recent_pivots = [
        (pos, val) for pos, val in pivot_highs
        if pos < bar_idx - startpoint
    ]
    recent_pivots.sort(key=lambda x: x[0], reverse=True)
    recent_pivots = recent_pivots[:maxpp]

    for pp_pos, pp_val in recent_pivots:
        length = bar_idx - pp_pos + prd
        if length > maxbars:
            break
        if length <= 5:
            continue

        ind_now = indicator[bar_idx - startpoint]
        ind_then = indicator[bar_idx - length] if (bar_idx - length) >= 0 else np.nan
        price_now = price_src[bar_idx - startpoint]

        if np.isnan(ind_then) or np.isnan(ind_now):
            continue

        match = False
        if cond_type == 1:
            match = ind_now < ind_then and price_now > pp_val
        elif cond_type == 2:
            match = ind_now > ind_then and price_now < pp_val

        if not match:
            continue

        slope_ind = (ind_now - ind_then) / (length - startpoint)
        vline_ind = ind_now - slope_ind
        close_now = price_src[bar_idx - startpoint]
        close_then = price_src[bar_idx - length] if (bar_idx - length) >= 0 else close_now
        slope_price = (close_now - close_then) / (length - startpoint)
        vline_price = close_now - slope_price

        arrived = True
        for y in range(1 + startpoint, length):
            idx = bar_idx - y
            if idx < 0:
                arrived = False
                break
            if indicator[idx] > vline_ind or price_src[idx] > vline_price:
                arrived = False
                break
            vline_ind -= slope_ind
            vline_price -= slope_price

        if arrived:
            return length

    return 0


# ---------------------------------------------------------------------------
# Main Scanner Class
# ---------------------------------------------------------------------------
class BISTDivergenceScanner:
    """
    Multi-indicator divergence scanner.

    Parameters mirror the Pine Script inputs:
        prd        – pivot detection lookback/lookforward period (default 5)
        source     – 'Close' or 'High/Low' for pivot detection
        search_div – 'Regular', 'Hidden', or 'Regular/Hidden'
        show_limit – minimum number of divergences to report (default 1)
        maxpp      – max pivot points to check (default 10)
        maxbars    – max bars to look back (default 100)
        dont_confirm – if True, don't wait for bar confirmation
        enabled_indicators – dict of indicator_name -> bool
    """

    def __init__(
        self,
        prd: int = 5,
        source: str = "Close",
        search_div: str = "Regular/Hidden",
        show_limit: int = 1,
        maxpp: int = 10,
        maxbars: int = 100,
        dont_confirm: bool = False,
        enabled_indicators: Optional[dict] = None,
    ):
        self.prd = prd
        self.source = source
        self.search_div = search_div
        self.show_limit = show_limit
        self.maxpp = maxpp
        self.maxbars = maxbars
        self.dont_confirm = dont_confirm

        defaults = {
            "MACD": True, "Hist": True, "RSI": True, "Stoch": True,
            "CCI": True, "MOM": True, "OBV": True, "VWMACD": True,
            "CMF": True, "MFI": True, "Extrn": False,
        }
        self.enabled = {**defaults, **(enabled_indicators or {})}

    def _compute_indicators(self, df: pd.DataFrame) -> dict[str, np.ndarray]:
        """Compute all 11 indicators from OHLCV DataFrame."""
        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        v = df["volume"].values.astype(float)

        macd_line, signal_line, histogram = _macd(c)

        return {
            "MACD":   macd_line,
            "Hist":   histogram,
            "RSI":    _rsi(c, 14),
            "Stoch":  _stochastic(c, h, l, 14, 3),
            "CCI":    _cci(h, l, c, 10),
            "MOM":    _momentum(c, 10),
            "OBV":    _obv(c, v),
            "VWMACD": _vwmacd(c, v, 12, 26),
            "CMF":    _cmf(h, l, c, v, 21),
            "MFI":    _mfi(h, l, c, v, 14),
            "Extrn":  c,  # placeholder — user can inject custom series
        }

    def scan_symbol(
        self,
        df: pd.DataFrame,
        symbol: str = "",
        external_indicator: Optional[np.ndarray] = None,
    ) -> SymbolScanResult:
        """
        Scan one symbol's OHLCV data for divergences on the latest bar.

        Parameters:
            df – DataFrame with columns: open, high, low, close, volume
                 (index should be datetime, sorted ascending)
            symbol – ticker label
            external_indicator – optional custom indicator array

        Returns:
            SymbolScanResult with all detected divergences.
        """
        if len(df) < self.maxbars + self.prd + 10:
            return SymbolScanResult(symbol=symbol)

        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)

        # Pivot source
        ph_src = c if self.source == "Close" else h
        pl_src = c if self.source == "Close" else l

        pivot_highs = _pivot_high(ph_src, self.prd, self.prd)
        pivot_lows = _pivot_low(pl_src, self.prd, self.prd)

        indicators = self._compute_indicators(df)
        if external_indicator is not None:
            indicators["Extrn"] = external_indicator

        bar_idx = len(c) - 1
        price_low_src = c if self.source == "Close" else l
        price_high_src = c if self.source == "Close" else h

        check_regular = self.search_div in ("Regular", "Regular/Hidden")
        check_hidden = self.search_div in ("Hidden", "Regular/Hidden")

        all_divs: list[Tuple[str, DivType, int]] = []

        for name in INDICATOR_NAMES:
            if not self.enabled.get(name, False):
                continue
            ind = indicators[name]
            if ind is None:
                continue

            # Positive regular (bullish) — compare against pivot lows
            if check_regular:
                d = _check_positive_divergence(
                    ind, price_low_src, pivot_lows, bar_idx,
                    self.prd, self.maxpp, self.maxbars,
                    self.dont_confirm, cond_type=1,
                )
                if d > 0:
                    all_divs.append((name, DivType.POS_REGULAR, d))

            # Negative regular (bearish) — compare against pivot highs
            if check_regular:
                d = _check_negative_divergence(
                    ind, price_high_src, pivot_highs, bar_idx,
                    self.prd, self.maxpp, self.maxbars,
                    self.dont_confirm, cond_type=1,
                )
                if d > 0:
                    all_divs.append((name, DivType.NEG_REGULAR, d))

            # Positive hidden (bullish trend continuation)
            if check_hidden:
                d = _check_positive_divergence(
                    ind, price_low_src, pivot_lows, bar_idx,
                    self.prd, self.maxpp, self.maxbars,
                    self.dont_confirm, cond_type=2,
                )
                if d > 0:
                    all_divs.append((name, DivType.POS_HIDDEN, d))

            # Negative hidden (bearish trend continuation)
            if check_hidden:
                d = _check_negative_divergence(
                    ind, price_high_src, pivot_highs, bar_idx,
                    self.prd, self.maxpp, self.maxbars,
                    self.dont_confirm, cond_type=2,
                )
                if d > 0:
                    all_divs.append((name, DivType.NEG_HIDDEN, d))

        # Apply minimum divergence count filter
        if len(all_divs) < self.show_limit:
            return SymbolScanResult(symbol=symbol)

        result = SymbolScanResult(symbol=symbol)
        for name, dt, dist in all_divs:
            div_info = IndicatorDivergence(name=name, div_type=dt, bar_distance=dist)
            if dt in (DivType.POS_REGULAR, DivType.POS_HIDDEN):
                result.positive_divergences.append(div_info)
            else:
                result.negative_divergences.append(div_info)

        return result

    def scan_batch(
        self,
        ohlcv_dict: dict[str, pd.DataFrame],
    ) -> List[SymbolScanResult]:
        """
        Scan multiple symbols. ohlcv_dict maps symbol -> OHLCV DataFrame.
        Returns list of SymbolScanResult (only those with at least one divergence).
        """
        results = []
        for symbol, df in ohlcv_dict.items():
            r = self.scan_symbol(df, symbol=symbol)
            if r.has_positive or r.has_negative:
                results.append(r)
        return results

    def print_report(self, results: List[SymbolScanResult]) -> None:
        """Print a human-readable scan report (mirrors the Pine Script labels)."""
        pos_list = [r for r in results if r.has_positive]
        neg_list = [r for r in results if r.has_negative]

        print("=" * 60)
        print("  BIST DIVERGENCE SCAN RESULTS")
        print("=" * 60)

        print("\n--- POZ UYUMSUZLUK (Positive / Bullish Divergences) ---")
        if pos_list:
            for r in pos_list:
                divs = r.positive_divergences
                types = []
                for d in divs:
                    label = "R" if d.div_type == DivType.POS_REGULAR else "H"
                    types.append(f"{d.name}({label})")
                print(f"  {r.symbol:10s} {', '.join(types)}")
        else:
            print("  (none)")

        print("\n--- NEG UYUMSUZLUK (Negative / Bearish Divergences) ---")
        if neg_list:
            for r in neg_list:
                divs = r.negative_divergences
                types = []
                for d in divs:
                    label = "R" if d.div_type == DivType.NEG_REGULAR else "H"
                    types.append(f"{d.name}({label})")
                print(f"  {r.symbol:10s} {', '.join(types)}")
        else:
            print("  (none)")

        print("=" * 60)


# ---------------------------------------------------------------------------
# Predefined BIST stock groups (mirrors the Pine Script a01..a40 mappings)
# ---------------------------------------------------------------------------
BIST_GROUPS = {
    "1": [
        "A1CAP", "ACSEL", "ADEL", "ADESE", "ADGYO", "AEFES", "AFYON", "AGESA",
        "AGHOL", "AGROT", "AGYO", "AHGAZ", "AKBNK", "AKCNS", "AKENR", "AKFGY",
        "AKFYE", "AKGRT", "AKMGY", "AKSA", "AKSEN", "AKSGY", "AKSUE", "AKYHO",
        "ALARK", "ALBRK", "ALCAR", "ALCTL", "ALFAS", "ALGYO", "ALKA", "ALKIM",
        "RUZYE", "ALTIN", "ALVES", "ANELE", "ANGEN", "ANHYT", "ANSGR", "ARASE",
    ],
    "2": [
        "ARCLK", "ARDYZ", "ARENA", "ARSAN", "ARTMS", "ARZUM", "ASELS", "ASGYO",
        "ASTOR", "ASUZU", "ATAGY", "ATAKP", "ATATP", "ATEKS", "ATLAS", "ATSYH",
        "AVGYO", "AVHOL", "AVOD", "AVPGY", "AVTUR", "AYCES", "AYDEM", "AYEN",
        "AYES", "AYGAZ", "AZTEK", "BAGFS", "BAKAB", "BALAT", "BANVT", "BARMA",
        "BASCM", "BASGZ", "BAYRK", "BEGYO", "BERA", "BEYAZ", "BFREN", "BIENY",
    ],
    "3": [
        "DOFRB", "GENKM", "LXGYO", "SVGYO", "BIZIM", "ATATR", "NETCD", "AKHAN",
        "UCAYM", "TEHOL", "BJKAS", "BLCYT", "BMSCH", "BMSTL", "BNTAS", "BOBET",
        "BORLS", "BORSK", "BOSSA", "BRISA", "BRKO", "BRKSN", "BRKVY", "BRLSM",
        "BRMEN", "BRSAN", "BRYAT", "BSOKE", "BTCIM", "BUCIM", "BURCE", "BURVA",
        "BVSAN", "BYDNR", "CANTE", "CASA", "CATES", "CCOLA", "CELHA", "CEMAS",
    ],
    "4": [
        "CEMTS", "CEOEM", "CIMSA", "CLEBI", "CMBTN", "CMENT", "CONSE", "COSMO",
        "CRDFA", "CRFSA", "CUSAN", "CVKMD", "CWENE", "TRHOL", "DAGI", "DAPGM",
        "DARDL", "DENGE", "DERHL", "DERIM", "DESA", "DESPC", "DEVA", "DGATE",
        "DGGYO", "DGNMO", "DIRIT", "DITAS", "DMRGD", "DMSAS", "DNISI", "DOAS",
        "BIGTK", "DOCO", "DOFER", "DOGUB", "DOHOL", "DOKTA", "DURDO", "DYOBY",
    ],
    "5": [
        "DZGYO", "EBEBK", "ECILC", "ECZYT", "EDATA", "EDIP", "EGEEN", "EGEPO",
        "EGGUB", "EGPRO", "EGSER", "EKGYO", "EKIZ", "EKOS", "EKSUN", "ELITE",
        "EMKEL", "EMNIS", "ENERY", "ENJSA", "ENKAI", "ENSRI", "EPLAS", "ERBOS",
        "ERCB", "EREGL", "ERSU", "ESCAR", "ESCOM", "ESEN", "ETILR", "ETYAT",
        "EUHOL", "EUKYO", "EUPWR", "EUREN", "EUYO", "EYGYO", "FADE", "FENER",
    ],
    "6": [
        "FLAP", "FMIZP", "FONET", "FORMT", "FORTE", "FRIGO", "FROTO", "FZLGY",
        "GARAN", "GARFA", "GEDIK", "GEDZA", "GENIL", "GENTS", "GEREL", "GESAN",
        "GIPTA", "GLBMD", "GLCVY", "GLRYH", "GLYHO", "GMTAS", "GOKNR", "GOLTS",
        "GOODY", "GOZDE", "GRNYO", "GRSEL", "GRTHO", "GSDDE", "GSDHO", "GSRAY",
        "GUBRF", "GWIND", "GZNMI", "HALKB", "HATEK", "HATSN", "HDFGS", "HEDEF",
    ],
    "7": [
        "HEKTS", "HKTM", "HLGYO", "HTTBT", "HUBVC", "HUNER", "HURGZ", "ICBCT",
        "ICUGS", "IDGYO", "IEYHO", "IHAAS", "IHEVA", "IHGZT", "IHLAS", "IHLGM",
        "IHYAY", "IMASM", "INDES", "INFO", "INGRM", "INTEM", "INVEO", "INVES",
        "TRENJ", "ISATR", "ISBIR", "ISBTR", "ISCTR", "ISDMR", "ISFIN", "ISGSY",
        "ISGYO", "ISKPL", "ISKUR", "ISMEN", "ISSEN", "IZENR", "IZFAS", "IZINV",
    ],
    "8": [
        "IZMDC", "JANTS", "KAPLM", "KAREL", "KARSN", "KARTN", "KARYE", "KATMR",
        "KAYSE", "KBORU", "KCAER", "KCHOL", "KENT", "KERVN", "KERVT", "KFEIN",
        "KGYO", "KIMMR", "KLGYO", "KLKIM", "KLMSN", "KLNMA", "KLRHO", "KLSER",
        "KLSYN", "KMPUR", "KNFRT", "KONKA", "KONTR", "KONYA", "KOPOL", "KORDS",
        "TRMET", "TRALT", "KRDMA", "KRDMB", "KRDMD", "KRGYO", "KRONT", "KRPLS",
    ],
    "9": [
        "KRSTL", "KRTEK", "KRVGD", "KSTUR", "KTLEV", "KTSKR", "KUTPO", "KUVVA",
        "KUYAS", "KZBGY", "KZGYO", "LIDER", "LIDFA", "LINK", "LKMNH", "LMKDC",
        "LOGO", "LRSHO", "LUKSK", "MAALT", "MACKO", "MAGEN", "MAKIM", "MAKTK",
        "MANAS", "MARBL", "MARKA", "MARTI", "MAVI", "MEDTR", "MEGAP", "MEGMT",
        "MEKAG", "MEPET", "MERCN", "MERIT", "MERKO", "METRO", "METUR", "MGROS",
    ],
    "10": [
        "MHRGY", "MIATK", "LYDHO", "MMCAS", "MNDRS", "MNDTR", "MOBTL", "MOGAN",
        "MPARK", "MRGYO", "MRSHL", "MSGYO", "MTRKS", "MTRYO", "MZHLD", "NATEN",
        "NETAS", "NIBAS", "NTGAZ", "NTHOL", "NUGYO", "NUHCM", "OBAMS", "OBASE",
        "ODAS", "OFSYM", "ONCSM", "ORCAY", "ORGE", "ORMA", "OSMEN", "OSTIM",
        "OTKAR", "OTTO", "OYAKC", "OYAYO", "OYLUM", "OYYAT", "OZGYO", "OZKGY",
    ],
    "11": [
        "OZRDN", "OZSUB", "PAGYO", "PAMEL", "PAPIL", "PARSN", "PASEU", "PATEK",
        "PCILT", "FRMPL", "PEKGY", "PENGD", "PENTA", "PETKM", "PETUN", "PGSUS",
        "PINSU", "PKART", "PKENT", "PLTUR", "PNLSN", "PNSUT", "POLHO", "POLTK",
        "PRDGS", "PRKAB", "PRKAB", "PRZMA", "PSDTC", "PSGYO", "QNBTR", "PAHOL",
        "ECOGR", "RALYH", "RAYSG", "REEDR", "RNPOL", "RODRG", "RTALB", "RUBNS",
    ],
    "12": [
        "RYGYO", "RYSAS", "SAFKR", "SAHOL", "SAMAT", "SANEL", "SANFM", "SANKO",
        "SARKY", "SASA", "SAYAS", "SDTTR", "SEGYO", "SEKFK", "SEKUR", "SELEC",
        "SELGD", "SELVA", "SEYKM", "SILVR", "SISE", "SKBNK", "SKTAS", "SKYLP",
        "SKYMD", "SMART", "SMRTG", "SNGYO", "SNICA", "GATEG", "SNPAM", "SODSN",
        "SOKE", "SOKM", "SONME", "SRVGY", "SUMAS", "SUNTK", "SURGY", "SUWEN",
    ],
    "13": [
        "TABGD", "TARKM", "MCARD", "EMPAE", "TAVHL", "TBORG", "TCELL", "TDGYO",
        "TEKTU", "TERA", "LYDYE", "TEZOL", "TGSAS", "THYAO", "TKFEN", "TKNSA",
        "TLMAN", "TMPOL", "TMSN", "TNZTP", "TOASO", "TRCAS", "TRGYO", "TRILC",
        "TSGYO", "TSKB", "ZGYO", "ZERGY", "VAKFA", "TUCLK", "TUKAS", "TUPRS",
        "TUREX", "TURGG", "TURSG", "UFUK", "ULAS", "ULKER", "ULUFA", "ULUSE",
    ],
    "14": [
        "ULUUN", "UMPAS", "UNLU", "USAK", "INTEK", "VAKBN", "VAKFN", "VAKKO",
        "VANGD", "VBTYZ", "VERTU", "VERUS", "VESBE", "VESTL", "VKFYO", "VKGYO",
        "VKING", "VRGYO", "YAPRK", "YATAS", "YAYLA", "YBTAS", "YEOTK", "YESIL",
        "YGGYO", "DMLKT", "YKBNK", "YKSLN", "YONGA", "YUNSA", "YYAPI", "YYLGD",
        "ZEDUR", "ZOREN", "ZRGYO", "DOFRB", "", "", "", "",
    ],
}


# ---------------------------------------------------------------------------
# Example / demo usage
# ---------------------------------------------------------------------------
def demo_with_random_data():
    """Quick demonstration with synthetic data."""
    np.random.seed(42)
    n = 300
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    volume = np.random.randint(100_000, 1_000_000, size=n).astype(float)

    df = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=pd.date_range("2025-01-01", periods=n, freq="D"))

    scanner = BISTDivergenceScanner(
        prd=5,
        source="Close",
        search_div="Regular/Hidden",
        show_limit=1,
        maxpp=10,
        maxbars=100,
    )

    result = scanner.scan_symbol(df, symbol="DEMO")
    print(result.summary() or "No divergences found for DEMO")

    fake_basket = {f"SYM{i:02d}": df.copy() for i in range(5)}
    results = scanner.scan_batch(fake_basket)
    scanner.print_report(results)


if __name__ == "__main__":
    demo_with_random_data()
