"""交易系统配置

三档参数矩阵:
- Conservative (保守): 高门槛，低风险
- Standard (标准): 默认参数
- Aggressive (激进): 低门槛，高风险
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict


class ParameterSet(Enum):
    """参数组"""
    CONSERVATIVE = "conservative"
    STANDARD = "standard"
    AGGRESSIVE = "aggressive"


@dataclass
class ShortConfig:
    """神盾模式配置（做空）"""
    fear_greed_threshold: int          # F&G 指数阈值 (>=)
    funding_rate_threshold: float      # 资金费率阈值 (%, >=)
    top_trader_ratio_threshold: float  # 聪明钱多空比阈值 (<, 大户看空时跟随做空)
    
    # 平仓条件
    exit_funding_rate: float = 0.01   # 费率回落阈值 (<)
    exit_fear_greed: int = 50          # F&G 回落阈值 (<)
    
    # 风控参数
    max_loss_pct: float = 1.5          # 单次最大亏损 (% of 全局权益)
    atr_multiplier: float = 2.0        # ATR 止损倍数

    # 执行参数
    leverage: int = 5                 # 杠杆 (固定5倍)


@dataclass
class LongConfig:
    """长矛模式配置（做多）"""
    fear_greed_threshold: int          # F&G 指数阈值 (<=)
    top_trader_ratio_threshold: float  # 聪明钱多空比阈值 (>, 大户看多时跟随做多)
    max_loss_pct: float                # 单次最大亏损 (% of 全局权益)
    atr_multiplier: float              # ATR 止损倍数
    cvd_lookback_periods: int = 6      # CVD 回看周期
    leverage: int = 10                 # 杠杆


@dataclass
class RiskConfig:
    """持仓风险管理配置（三档共用）

    R = 开仓时的初始止损距离 |入场价 - 初始止损|，是单笔风险单位。
    止损只朝有利方向移动，永不回退。
    """
    # 保本 / 移动止损
    breakeven_trigger_r: float = 0.5    # 浮盈达到 0.5R → 止损移到成本价
    trailing_trigger_r: float = 1.5     # 峰值浮盈达到 1.5R → 启动移动止损
    trailing_distance_r: float = 1.25   # 移动止损挂在峰值回撤 1.25R 处

    # 部分止盈：填补 0.5R~1.5R 的空档（浮盈到过这一段但两端机制都不落袋）
    # 回测（148 个独立事件，15m 逐根模拟）：无止盈总收益 6.36R，去掉最赚 5 笔即
    # 转负 -1.16R；本方案 8.15R，去掉最赚 5 笔仍为 +1.89R。优势更分散、更稳健。
    tp_trigger_r: float = 1.0           # 浮盈达到 1.0R → 落袋一部分
    tp_fraction: float = 0.5            # 止盈平掉的仓位比例（0 = 关闭部分止盈）

    # AI 自定风控的允许区间。LLM 只输出「相对量」（ATR 倍数 / R 倍数），不输出
    # 绝对价格：模型看不到带标签的现价，一旦幻觉出一个绝对价位就可能贴近强平价。
    # 超出区间即钳制，并记日志。
    ai_stop_atr_mult_min: float = 1.0   # AI 止损倍数下限（太紧会被噪声扫掉）
    ai_stop_atr_mult_max: float = 3.0   # AI 止损倍数上限（太宽单笔风险过大）
    ai_tp_trigger_r_min: float = 0.5    # AI 止盈线下限（低于保本线没意义）
    ai_tp_trigger_r_max: float = 3.0    # AI 止盈线上限

    # 追高护栏：开仓价在回看区间中的顺方向位置
    range_lookback_hours: int = 48      # 区间回看窗口
    max_entry_range_pct: float = 60.0   # 顺方向位置 >= 此值视为追高，拒绝开仓
    breakout_range_pct: float = 100.0   # 突破区间（创新高/新低）放行


@dataclass
class TradingConfig:
    """交易系统完整配置"""
    short: ShortConfig
    long: LongConfig
    preset: ParameterSet = ParameterSet.STANDARD
    risk: RiskConfig = field(default_factory=RiskConfig)
    
    @classmethod
    def get_preset(cls, preset: ParameterSet) -> "TradingConfig":
        """获取预设参数组"""
        presets = {
            # 保守模式: 高门槛，低风险
            ParameterSet.CONSERVATIVE: cls(
                # 做空模式，跟随大户看空时做空
                short=ShortConfig(
                    fear_greed_threshold=85,
                    funding_rate_threshold=0.05, # 资金费率阈值大于0.05%时，跟随做空
                    top_trader_ratio_threshold=0.5,  # 聪明钱多空比 < 0.5 (极度看空，跟随做空)
                    max_loss_pct=1.0,
                    atr_multiplier=2.0,
                ),
                # 做多模式，跟随大户看多时做多
                long=LongConfig(
                    fear_greed_threshold=15,
                    top_trader_ratio_threshold=2.0,  # 聪明钱多空比 > 2.0 (极度看多，跟随做多)
                    max_loss_pct=1.0,
                    atr_multiplier=2.0,
                ),
                preset=ParameterSet.CONSERVATIVE,
            ),
            # 标准模式: 默认参数
            ParameterSet.STANDARD: cls(
                # 做空模式，跟随大户看空时做空
                short=ShortConfig(
                    fear_greed_threshold=75,
                    funding_rate_threshold=0.003, # 资金费率阈值大于0.003%时，跟随做空
                    top_trader_ratio_threshold=0.6,  # 聪明钱多空比 < 0.6 (过度看空，跟随做空)
                    max_loss_pct=1.5,
                    atr_multiplier=1.5,
                ),
                # 做多模式，跟随大户看多时做多
                long=LongConfig(
                    fear_greed_threshold=25,
                    top_trader_ratio_threshold=1.8,  # 聪明钱多空比 > 1.8 (过度看多，跟随做多)
                    max_loss_pct=1.5,
                    atr_multiplier=1.5,
                ),
                preset=ParameterSet.STANDARD,
            ),
            # 激进模式: 低门槛，高风险
            ParameterSet.AGGRESSIVE: cls(
                # 做空模式，跟随大户看空时做空
                short=ShortConfig(
                    fear_greed_threshold=70,
                    funding_rate_threshold=0.001, # 资金费率阈值大于0.001%时，跟随做空
                    top_trader_ratio_threshold=0.8,  # 聪明钱多空比 < 0.8 (偏空，跟随做空)
                    max_loss_pct=5.0,
                    atr_multiplier=1.2,
                ),
                # 做多模式，跟随大户看多时做多
                long=LongConfig(
                    fear_greed_threshold=32,
                    top_trader_ratio_threshold=1.2,  # 聪明钱多空比 > 1.2 (偏多，跟随做多)
                    max_loss_pct=5.0,
                    atr_multiplier=1.2,
                ),
                preset=ParameterSet.AGGRESSIVE,
            ),
        }
        return presets[preset]
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "short": {
                "fear_greed_threshold": self.short.fear_greed_threshold,
                "funding_rate_threshold": self.short.funding_rate_threshold,
                "top_trader_ratio_threshold": self.short.top_trader_ratio_threshold,
                "max_loss_pct": self.short.max_loss_pct,
                "atr_multiplier": self.short.atr_multiplier,
                "leverage": self.short.leverage,
            },
            "long": {
                "fear_greed_threshold": self.long.fear_greed_threshold,
                "top_trader_ratio_threshold": self.long.top_trader_ratio_threshold,
                "max_loss_pct": self.long.max_loss_pct,
                "atr_multiplier": self.long.atr_multiplier,
                "leverage": self.long.leverage,
            },
            "risk": {
                "breakeven_trigger_r": self.risk.breakeven_trigger_r,
                "trailing_trigger_r": self.risk.trailing_trigger_r,
                "trailing_distance_r": self.risk.trailing_distance_r,
                "tp_trigger_r": self.risk.tp_trigger_r,
                "tp_fraction": self.risk.tp_fraction,
                "range_lookback_hours": self.risk.range_lookback_hours,
                "max_entry_range_pct": self.risk.max_entry_range_pct,
                "breakout_range_pct": self.risk.breakout_range_pct,
            },
        }
