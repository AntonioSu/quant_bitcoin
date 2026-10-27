"""MA 突破机械策略

把 MABreakoutResult 直接翻译成开平仓指令，绕过两层 AI 决策。
「标准」模拟盘用它，目的是让实盘行为可以和 backtest/ma_breakout_backtest.py
的回测结果逐笔对照——只要中间夹了 LLM，回测数字就失去参照意义。

规则（仅做多）:
  空仓 + 收盘站上 MA40 + 放量 ≥ 1.2× 均量  → 开多
  持多 + 收盘跌破 MA40                      → 平仓
  其余                                      → 不动

与回测的已知差异，看实盘结果时要记住:
  1. 回测没有止损，实盘保留了 ATR 硬止损和强平保护。被止损扫出后不会立刻
     重新进场，要等价格先跌回均线下方（_armed），否则会在同一个信号上反复开仓。
  2. 回测在日线收盘后的下一根开盘成交，实盘是收盘后第一个 5 分钟节拍成交，
     两者差几分钟；但若服务在信号出现后才启动，会以当时的价格补进场。
  3. _armed 只存在内存里，重启后默认放行。重启恰好发生在止损出场后会多开一笔。
"""

from typing import Optional

from indicators.ma_breakout import MABreakoutCalculator, MABreakoutResult
from multi_agent.trading_advisor import TradingDecision
from utils import logger

# 回测是满仓不加杠杆的现货口径，实盘沿用以便对照
DEFAULT_SIZE_HINT = "100%"
DEFAULT_LEVERAGE = 1
# 回测无止损；这里取允许范围的上限，让硬止损尽量少干预均线规则
DEFAULT_STOP_ATR_MULT = 3.0


class MABreakoutStrategy:
    """均线突破的机械执行器（无 LLM）"""

    name = "MA突破"

    def __init__(
        self,
        calculator: Optional[MABreakoutCalculator] = None,
        size_hint: str = DEFAULT_SIZE_HINT,
        leverage: int = DEFAULT_LEVERAGE,
        stop_atr_mult: float = DEFAULT_STOP_ATR_MULT,
    ):
        self.calculator = calculator or MABreakoutCalculator()
        self.size_hint = size_hint
        self.leverage = leverage
        self.stop_atr_mult = stop_atr_mult

        self._armed = True          # 是否允许开新仓
        self._had_position = False  # 上一拍是否持仓，用于识别非本策略触发的离场

    @property
    def label(self) -> str:
        c = self.calculator
        return f"{c.timeframe} MA{c.ma_period} 突破 + {c.vol_mult:g}× 放量确认"

    def decide(self, result: Optional[MABreakoutResult], position_direction: str) -> TradingDecision:
        """
        Args:
            result: 最新的 MA 突破状态；None 表示数据不足
            position_direction: 当前仓位方向 LONG / SHORT / NONE

        Returns:
            TradingDecision，交由调度器的既有开平仓流程执行
        """
        has_long = position_direction == "LONG"

        if result is None:
            self._had_position = has_long
            return TradingDecision(
                action="持仓观望" if has_long else "等待入场",
                position_size_hint="0%",
                reason=f"{self.calculator.timeframe} K线不足，{self.name}暂不决策",
            )

        # 仓位凭空消失（硬止损 / 强平）→ 封锁重进，等价格跌回均线下方再说
        if self._had_position and position_direction == "NONE":
            self._armed = False
            logger.info(f"🔒 {self.name}: 非策略离场，封锁重进直到收盘价跌破 MA{result.ma_period}")

        # 价格回到均线下方，一次完整的突破周期结束，解除封锁
        if not result.above_ma:
            self._armed = True

        self._had_position = has_long
        state = (
            f"收盘{result.close:,.0f} vs MA{result.ma_period} {result.ma:,.0f} "
            f"({result.deviation_pct:+.1f}%), 量比{result.vol_ratio:.2f}"
        )

        # 持有多单：唯一的离场理由是跌破均线
        if has_long:
            if not result.above_ma:
                return TradingDecision(
                    action="平仓",
                    close_ratio=1.0,
                    reason=f"跌破 MA{result.ma_period}: {state}"[:80],
                )
            return TradingDecision(action="持仓观望", reason=f"仍在均线上方: {state}"[:80])

        # 方向不符的残留仓位（本策略只做多）：直接平掉
        if position_direction == "SHORT":
            return TradingDecision(
                action="平仓",
                close_ratio=1.0,
                reason=f"{self.name}仅做多，平掉空单"[:80],
            )

        # 空仓：站上均线且放量才进
        if not self._armed:
            return TradingDecision(
                action="等待入场",
                position_size_hint="0%",
                reason=f"止损后封锁中，待跌破 MA{result.ma_period}: {state}"[:80],
            )

        if result.entry_ok:
            return TradingDecision(
                action="开多",
                position_size_hint=self.size_hint,
                leverage_hint=self.leverage,
                stop_atr_mult=self.stop_atr_mult,
                reason=f"站上 MA{result.ma_period} 且放量: {state}"[:80],
            )

        missing = "未站上均线" if not result.above_ma else "缩量突破"
        return TradingDecision(
            action="等待入场",
            position_size_hint="0%",
            reason=f"{missing}: {state}"[:80],
        )


def build_default_strategy() -> MABreakoutStrategy:
    """按回测最优参数构造策略实例"""
    return MABreakoutStrategy()
