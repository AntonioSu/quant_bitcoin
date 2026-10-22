"""MA 突破信号（单线突破 + 成交量确认）

与 indicators/ma_signal.py 的双均线金叉/死叉不同，这里是一条线的突破规则:
- 收盘价站上 MA(N)，且当根成交量 ≥ vol_mult × 均量 → 允许开多
- 收盘价跌破 MA(N) → 离场（不要求放量配合，跌破就走）

进场要放量、离场不要放量是刻意的不对称: 进场可以挑，错过只是机会成本;
离场必须干脆，回测里给离场加量能条件会把小亏拖成大亏。

参数取自 backtest/ma_breakout_backtest.py 的全样本回测
（BTCUSDT 日线 MA40 + 1.2 倍放量，2017-08 ~ 2026-09，仅做多）:
胜率 40.4%，盈亏比 5.91，年化 +77%，最大回撤 -54.6%。
"""

import time
from dataclasses import dataclass
from typing import List, Optional

from utils import logger

# 回测最优参数（日线全样本 Calmar 1.41，MA35~MA45 是整片稳定区而非单点尖峰）
DEFAULT_MA_PERIOD = 40
DEFAULT_VOL_MULT = 1.2
DEFAULT_VOL_MA_PERIOD = 20
DEFAULT_TIMEFRAME = "1d"
# 拉取根数：MA40 + 未收盘当前根，多留一倍余量应对交易所缺K
DEFAULT_KLINE_LIMIT = 120


@dataclass
class MABreakoutResult:
    """MA 突破状态（基于最后一根已收盘K线）"""
    close: float
    ma: float
    vol_ratio: float             # 当根成交量 / 均量
    above_ma: bool
    volume_confirmed: bool
    bar_time: str                # 该K线的收盘时间，用于确认没用到未收盘数据
    ma_period: int
    timeframe: str

    @property
    def entry_ok(self) -> bool:
        """是否满足开多条件"""
        return self.above_ma and self.volume_confirmed

    @property
    def deviation_pct(self) -> float:
        """收盘价偏离均线的百分比"""
        return (self.close - self.ma) / self.ma * 100 if self.ma else 0.0

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "ma_period": self.ma_period,
            "close": round(self.close, 2),
            "ma": round(self.ma, 2),
            "deviation_pct": round(self.deviation_pct, 2),
            "vol_ratio": round(self.vol_ratio, 2),
            "above_ma": self.above_ma,
            "volume_confirmed": self.volume_confirmed,
            "entry_ok": self.entry_ok,
            "bar_time": self.bar_time,
        }


class MABreakoutCalculator:
    """单线均线突破 + 成交量确认"""

    def __init__(
        self,
        ma_period: int = DEFAULT_MA_PERIOD,
        vol_mult: float = DEFAULT_VOL_MULT,
        vol_ma_period: int = DEFAULT_VOL_MA_PERIOD,
        timeframe: str = DEFAULT_TIMEFRAME,
    ):
        self.ma_period = ma_period
        self.vol_mult = vol_mult
        self.vol_ma_period = vol_ma_period
        self.timeframe = timeframe

    @property
    def min_klines(self) -> int:
        """至少需要多少根已收盘K线（多留一根给未收盘的当前根）"""
        return max(self.ma_period, self.vol_ma_period) + 1

    @staticmethod
    def _closed_bars(klines: List[List]) -> List[List]:
        """只保留已收盘的K线

        回测用的是收盘价，实盘必须一致。Binance 返回的最后一根通常是
        进行中的当前K线，直接拿它算会让信号在一天之内反复翻转。
        """
        now_ms = int(time.time() * 1000)
        closed = []
        for k in klines:
            try:
                if int(k[6]) < now_ms:
                    closed.append(k)
            except (IndexError, TypeError, ValueError):
                closed.append(k)
        return closed

    def calculate(self, klines: List[List]) -> Optional[MABreakoutResult]:
        """
        Args:
            klines: K线数据 [[open_time, open, high, low, close, volume, close_time, ...], ...]
                    需要至少 min_klines 根

        Returns:
            MABreakoutResult；K线不足时返回 None
        """
        bars = self._closed_bars(klines)
        if len(bars) < self.min_klines:
            logger.warning(
                f"MA突破: 已收盘K线不足 ({len(bars)}/{self.min_klines})，跳过计算"
            )
            return None

        closes = [float(k[4]) for k in bars]
        volumes = [float(k[5]) for k in bars]

        ma = sum(closes[-self.ma_period:]) / self.ma_period
        vol_avg = sum(volumes[-self.vol_ma_period:]) / self.vol_ma_period
        vol_ratio = volumes[-1] / vol_avg if vol_avg > 0 else 0.0

        last = bars[-1]
        try:
            bar_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(last[6]) / 1000))
        except (IndexError, TypeError, ValueError):
            bar_time = "unknown"

        return MABreakoutResult(
            close=closes[-1],
            ma=ma,
            vol_ratio=vol_ratio,
            above_ma=closes[-1] > ma,
            volume_confirmed=vol_ratio >= self.vol_mult,
            bar_time=bar_time,
            ma_period=self.ma_period,
            timeframe=self.timeframe,
        )
