"""策略绩效计算

从交易记录计算夏普率、最大回撤、胜率等指标。
"""

import math
from datetime import datetime
from typing import List, Dict, Any, Optional


def _parse_time(t: str) -> datetime:
    return datetime.strptime(t, "%Y-%m-%d %H:%M:%S")


def _pair_trades(trades: List[dict]) -> List[dict]:
    """将开平仓记录配对，返回完整的 round-trip 列表。"""
    pairs = []
    pending_open: Optional[dict] = None

    for t in trades:
        action = t.get("action", "")
        if action in ("LONG", "SHORT"):
            pending_open = t
        elif action in ("CLOSE", "REDUCE", "TP1_HALF") and pending_open:
            open_time = _parse_time(pending_open["time"])
            close_time = _parse_time(t["time"])
            pairs.append({
                "open_time": open_time,
                "close_time": close_time,
                "hold_seconds": (close_time - open_time).total_seconds(),
                "direction": pending_open["action"],
                "entry_price": pending_open["price"],
                "exit_price": t["price"],
                "amount": t.get("amount", 0),
                "pnl": t.get("pnl", 0),
            })
            if action == "CLOSE":
                pending_open = None
    return pairs


class PerformanceTracker:

    def calculate(self, trades: List[dict], initial_equity: float) -> Dict[str, Any]:
        pairs = _pair_trades(trades)
        total_trades = len(pairs)

        if total_trades == 0:
            return self._empty_result(initial_equity)

        pnls = [p["pnl"] for p in pairs]
        total_pnl = sum(pnls)

        # --- 权益曲线（按平仓时间严格递增） ---
        equity_curve = [{"equity": round(initial_equity, 2),
                         "time": pairs[0]["open_time"].strftime("%Y-%m-%d %H:%M:%S")}]
        eq = initial_equity
        for p in pairs:
            eq += p["pnl"]
            close_ts = p["close_time"].strftime("%Y-%m-%d %H:%M:%S")
            equity_curve.append({"equity": round(eq, 2), "time": close_ts})

        # --- 最大回撤 ---
        peak = initial_equity
        max_dd = 0.0
        eq = initial_equity
        for p in pairs:
            eq += p["pnl"]
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # --- 胜率 / 盈亏比 ---
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / total_trades

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0
        profit_factor = (sum(wins) / abs(sum(losses))) if losses else float("inf")

        # --- 平均持仓时间 ---
        hold_secs = [p["hold_seconds"] for p in pairs]
        avg_hold_sec = sum(hold_secs) / len(hold_secs)

        # --- 夏普率 ---
        returns = [p["pnl"] / initial_equity for p in pairs]
        sharpe = self._calc_sharpe(returns, avg_hold_sec)

        return {
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": round(total_pnl / initial_equity * 100, 2),
            "sharpe_ratio": round(sharpe, 2) if math.isfinite(sharpe) else None,
            "max_drawdown_pct": round(max_dd * 100, 2),
            "win_rate": round(win_rate * 100, 1),
            "win_count": len(wins),
            "loss_count": len(losses),
            "profit_factor": round(profit_factor, 2) if math.isfinite(profit_factor) else None,
            "avg_pnl": round(total_pnl / total_trades, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "avg_hold_minutes": round(avg_hold_sec / 60, 1),
            "equity_curve": equity_curve,
            "initial_equity": initial_equity,
            "current_equity": round(initial_equity + total_pnl, 2),
        }

    @staticmethod
    def _calc_sharpe(returns: List[float], avg_hold_sec: float) -> float:
        """年化夏普率。用平均持仓时间推算年交易次数做年化。"""
        n = len(returns)
        if n < 2 or avg_hold_sec <= 0:
            return 0.0

        mean_r = sum(returns) / n
        var_r = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
        std_r = math.sqrt(var_r)

        if std_r == 0:
            return float("inf") if mean_r > 0 else 0.0

        trades_per_year = (365.25 * 24 * 3600) / avg_hold_sec
        return (mean_r / std_r) * math.sqrt(trades_per_year)

    @staticmethod
    def _empty_result(initial_equity: float) -> Dict[str, Any]:
        return {
            "total_trades": 0,
            "total_pnl": 0,
            "total_return_pct": 0,
            "sharpe_ratio": None,
            "max_drawdown_pct": 0,
            "win_rate": 0,
            "win_count": 0,
            "loss_count": 0,
            "profit_factor": None,
            "avg_pnl": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "avg_hold_minutes": 0,
            "equity_curve": [{"equity": initial_equity, "time": None}],
            "initial_equity": initial_equity,
            "current_equity": initial_equity,
        }
