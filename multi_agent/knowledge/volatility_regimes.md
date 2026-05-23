# 波动状态（Volatility Regime）长期定义

波动状态是市场状态的第二维度，描述价格波动幅度和能量。
它**只描述波动**，与趋势方向无关（方向见 `trend_regimes.md`）。

枚举值固定为 4 个，AI 研判时必须输出其中之一：

- `LOW_VOL_COMPRESSION`
- `NORMAL_VOL`
- `BREAKOUT_EXPANSION`
- `HIGH_VOL_EXTREME`

## LOW_VOL_COMPRESSION（低波动收敛）

### 判定条件（至少满足 2 条）
- 布林带宽 bandwidth < 0.03 或 is_squeeze = true
- 4H ATR 低于最近 30 根 K 线均值 70%
- 成交量持续缩量：vol_ratio < 0.7 且持续 ≥ 4 根 4H
- 4H 价格区间 < 1.5%

### 指标解读偏向
- 趋势类信号失效率高，权重降低
- 任何看似的"金叉/死叉"都需要等待量能配合
- RSI 中位区间反复，背离信号容易误报
- 布林收窄是**变盘前兆**，但不指示方向

### 默认 bias 倾向
- 默认 NEUTRAL
- confidence 上限 **55**
- 优先 action："等待入场" / "持仓观望"
- **禁止**在收敛期建议加仓

## NORMAL_VOL（正常波动）

### 判定条件
- 布林带宽 0.03 ~ 0.06
- ATR 接近最近 30 根均值 ±30% 区间
- 成交量稳定（vol_ratio 0.7 ~ 1.5）

### 指标解读偏向
- 所有指标按 `indicator_guide.md` 和 `combination_rules.md` 默认规则解读
- 各类信号都可作为主因素，不需要额外打折或加权
- 这是规则的"基准状态"

### 默认 bias 倾向
- 顺势交易标准门槛：≥3 个维度同向 → confidence 55~70
- 逆势交易仍需多重确认

## BREAKOUT_EXPANSION（突破扩张）

### 判定条件（至少满足 2 条）
- 布林带宽 0.06 ~ 0.10 且从收窄区间扩张而来
- 单根 4H 实体 > 最近 14 根 ATR
- vol_ratio > 1.5 且方向明确
- 价格刚跌破/突破关键支撑或压力

### 指标解读偏向
- 突破方向 + 放量 = 主要信号
- 顺突破方向的信号权重提升
- 反向背离信号此时**不可信**（尚未到衰竭阶段）
- 不要试图"预测"突破方向，等价格用动作给出答案

### 默认 bias 倾向
- 顺突破方向 confidence 可到 65~75
- 逆突破方向 confidence 上限 50
- action 偏"加仓 / 顺势入场"，但要标注追高/追空风险

## HIGH_VOL_EXTREME（高波动极端）

### 判定条件（任一满足）
- 4H 价格变化 > ±5%
- 单小时爆仓 > $500M
- 资金费率年化 > 50% 或 < -50%
- 恐惧贪婪指数 < 15 或 > 85

### 指标解读偏向
- 所有常规信号可靠性下降
- 极端值具有"反向指标"属性
- 流动性事件可能在分钟级翻转方向
- 风控优先级 > 入场信号

### 默认 bias 倾向
- confidence 上限 **60**
- action 偏"减仓 / 观望"，**不建议加仓**
- 若已有仓位，应主动评估止损是否需要收紧

---

## 输出约束

AI 研判输出的 JSON 必须包含字段：

```json
{
  "volatility_regime": "LOW_VOL_COMPRESSION | NORMAL_VOL | BREAKOUT_EXPANSION | HIGH_VOL_EXTREME"
}
```

特别规则：

- 如果 volatility_regime = LOW_VOL_COMPRESSION，confidence 不得超过 55
- 如果 volatility_regime = HIGH_VOL_EXTREME，confidence 不得超过 60 且 risks 必须列明流动性风险
- 如果 volatility_regime = BREAKOUT_EXPANSION 且 bias 与突破方向相反，confidence 不得超过 50
