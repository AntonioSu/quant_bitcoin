"""信号聚合器

整合所有数据源，判断交易模式触发条件:
- 做空模式 (做空): 三灯全绿才触发
  - 费率 >= 0.03%
  - 聪明钱多空比 < 0.75 (大户看空，跟随做空)
  - CVD 顶背离信号
- 做多模式 (做多): 三灯全绿才触发
  - 聪明钱多空比 > 1.5 (大户看多，跟随做多)
  - CVD 底背离信号
- 做多模式优先级 > 做空模式 > 空闲

数据来源: 使用全局 market 实例，避免重复请求
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from .config import TradingConfig, ParameterSet
from .market_data import market
from ..indicators.cvd_divergence import DivergenceType
from ..utils import logger


def _build_result(
    target_mode: "TradingMode",
    conditions: Dict[str, bool],
    values: Dict[str, float],
    ok_reason: str,
    fail_prefix: str,
) -> "SignalResult":
    all_green = all(conditions.values())
    confidence = sum(conditions.values()) / len(conditions)
    if all_green:
        logger.info(ok_reason)
        reason = ok_reason
    else:
        failed = [k for k, v in conditions.items() if not v]
        reason = f"{fail_prefix}: {', '.join(failed)} 条件不满足"
        logger.debug(reason)
    return SignalResult(
        mode=target_mode if all_green else TradingMode.IDLE,
        conditions=conditions,
        values=values,
        confidence=confidence,
        reason=reason,
    )


class TradingMode(Enum):
    """交易模式"""
    IDLE = "idle"          # 空闲
    SHORT = "short"        # 开仓模式 (做空收租)
    LONG = "long"        # 平仓模式 (抄底做多)


@dataclass
class SignalResult:
    """信号判断结果"""
    mode: TradingMode
    conditions: Dict[str, bool]   # 各条件是否满足
    values: Dict[str, float]      # 各指标实际值
    confidence: float             # 置信度 (0-1)
    reason: str                   # 触发/未触发原因


class SignalAggregator:
    """信号聚合器
    
    使用全局 market 实例获取数据，避免重复请求。
    """
    
    def __init__(
        self, 
        config: Optional[TradingConfig] = None
    ):
        """
        Args:
            config: 交易配置 (默认使用 Standard)
        """
        self.config = config or TradingConfig.get_preset(ParameterSet.STANDARD)
    
    def check_short_conditions(self) -> SignalResult:
        """检查神盾模式触发条件（做空）- 三灯全绿"""
        cfg = self.config.short
        
        fg_value = market.fear_greed.value if market.fear_greed else 50
        fr_value = market.funding_rate.value if market.funding_rate else 0
        tt_value = market.top_trader.value if market.top_trader else 1.0
        
        # CVD 顶背离检测 (使用全局 market 中的结果)
        if market.cvd:
            cvd_bearish = market.cvd.is_valid_signal and market.cvd.divergence == DivergenceType.BEARISH
            cvd_value = market.cvd.cvd_change_pct
            price_change = market.cvd.price_change_pct
            divergence_strength = market.cvd.strength
            divergence_type = "顶背离" if cvd_bearish else "无"
        else:
            cvd_bearish = False
            cvd_value = 0.0
            price_change = 0.0
            divergence_strength = 0.0
            divergence_type = "无"

        return _build_result(
            TradingMode.SHORT,
            conditions={
                # "fear_greed": fg_value >= cfg.fear_greed_threshold,
                # "funding_rate": fr_value >= cfg.funding_rate_threshold,
                "top_trader_ratio": tt_value <= cfg.top_trader_ratio_threshold,
                "cvd_divergence": cvd_bearish,
            },
            values={
                "fear_greed": fg_value,
                "funding_rate": fr_value,
                "top_trader_ratio": tt_value,
                "cvd_change_pct": cvd_value,
                "price_change_pct": price_change,
                "divergence_strength": divergence_strength,
                "divergence_type": divergence_type,
            },
            ok_reason="🛡️ 做空模式触发: 聪明钱看空 + CVD 顶背离",
            fail_prefix="做空模式未触发",
        )
    
    def check_long_conditions(self) -> SignalResult:
        """
        检查长矛模式触发条件（做多）- 三灯全绿
        
        1. 绝望冰点: F&G <= threshold   (暂时不使用)
        2. 聪明钱看多: 多空比 > threshold
        3. 微观底背离: CVD 底背离信号
        
        使用全局 market 中的 CVD 结果
        """
        cfg = self.config.long
        
        fg_value = market.fear_greed.value if market.fear_greed else 50
        tt_value = market.top_trader.value if market.top_trader else 1.0
        
        # CVD 底背离检测 (使用全局 market 中的结果)
        if market.cvd:
            cvd_bullish = market.cvd.is_valid_signal and market.cvd.divergence == DivergenceType.BULLISH
            cvd_value = market.cvd.cvd_change_pct
            price_change = market.cvd.price_change_pct
            divergence_strength = market.cvd.strength
            divergence_type = "底背离" if cvd_bullish else "无"
        else:
            cvd_bullish = False
            cvd_value = 0.0
            price_change = 0.0
            divergence_strength = 0.0
            divergence_type = "无"
        
        # 条件判断
        cond_fg = fg_value <= cfg.fear_greed_threshold
        cond_tt = tt_value > cfg.top_trader_ratio_threshold
        cond_cvd = cvd_bullish
        
        return _build_result(
            TradingMode.LONG,
            conditions={
                # "fear_greed": cond_fg,
                "top_trader_ratio": cond_tt,
                "cvd_divergence": cond_cvd,
            },
            values={
                "fear_greed": fg_value,
                "top_trader_ratio": tt_value,
                "cvd_change_pct": cvd_value,
                "price_change_pct": price_change,
                "divergence_strength": divergence_strength,
                "divergence_type": divergence_type,
            },
            ok_reason="⚔️ 做多模式触发: 聪明钱看多 + CVD 底背离",
            fail_prefix="做多模式未触发",
        )
    
    def evaluate(self, current_mode: TradingMode = TradingMode.IDLE) -> SignalResult:
        """
        综合评估当前应处于什么模式
        
        优先级: 开仓模式 > 平仓模式 > 空闲
        
        使用全局 market 数据，无需传入 klines
        """
        # 检查做多触发条件
        long_result = self.check_long_conditions()
        if long_result.mode == TradingMode.LONG:
            return long_result

        # 检查做空触发条件
        short_result = self.check_short_conditions()
        if short_result.mode == TradingMode.SHORT:
            return short_result
        
        # 无信号
        fg_value = market.fear_greed.value if market.fear_greed else 50
        fr_value = market.funding_rate.value if market.funding_rate else 0
        tt_value = market.top_trader.value if market.top_trader else 1.0
        
        return SignalResult(
            mode=TradingMode.IDLE,
            conditions={},
            values={
                "fear_greed": fg_value,
                "funding_rate": fr_value,
                "top_trader_ratio": tt_value,
            },
            confidence=0.0,
            reason="无交易信号",
        )

