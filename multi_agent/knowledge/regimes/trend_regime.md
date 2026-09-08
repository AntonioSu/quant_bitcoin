# 【市场状态 · 趋势维度】Trend Regime 长期定义

趋势状态是市场状态的第一维度，描述价格的中期方向。
它**只描述方向**，不包含波动幅度（那是波动维度的事，见 `volatility_regime.md`）。

枚举值固定为 4 个，AI 研判时必须输出其中之一：

- `UP_TREND`
- `DOWN_TREND`
- `RANGE`
- `UNCLEAR`

## UP_TREND（上涨趋势）

### 判定条件（至少满足 3 条）
- MA 多头排列：EMA7 > EMA25
- MACD 在零轴上方
- RSI > 50 且 trend_strength 不弱
- 价格在 EMA7 之上
- OBV 趋势为 up 或 4H 价格高低点均在抬升

### 指标解读偏向
- **流动性（M2 / 稳定币）+ 趋势结构（MA）权重最高**；MACD/RSI 仅为辅助（默认 low）
- 反转信号（RSI 顶背离、CVD 顶背离）需要**多重确认**才能采纳，且不得单靠 RSI/MACD
- RSI > 70 不立即等于做空，强势中 RSI 可持续在 70~85
- 回调到 EMA7 附近 + 稳定币仍在流入 → 顺势加仓点（RSI 回到 50 仅作可选旁证）

### 默认 bias 倾向
- LONG 是默认方向；若 M2 扩张或稳定币流入，偏多叙事加强
- SHORT 只在出现 ≥3 个反转信号且至少含放量、稳定币转流出/CVD 顶背离、结构破坏中的两项时考虑
- 仅靠"RSI 超买"或"MACD 死叉"**不得**给 SHORT

## DOWN_TREND（下跌趋势）

### 判定条件（至少满足 3 条）
- MA 空头排列：EMA7 < EMA25
- MACD 在零轴下方
- RSI < 50
- 价格在 EMA7 之下
- OBV 趋势为 down 或 4H 价格高低点均在下移

### 指标解读偏向
- **流动性 + MA 结构权重最高**；RSI/MACD 权重降低
- 反弹信号（RSI 超卖、MACD 金叉、CVD 底背离）单独出现时**不构成做多依据**
- RSI < 30 是"空单短线反弹风险提示"，不是做多入场信号
- 反弹到 EMA7 附近被压制 → 顺势加空点；若稳定币仍在流出，空头叙事更强

### 默认 bias 倾向
- SHORT 或 NEUTRAL 是默认方向；稳定币流出 / M2 收缩时优先 SHORT
- LONG 必须同时满足以下至少两项确认，且**其中至少一项来自流动性或真实买盘**（不能两项都是 RSI/MACD）：
  1. 稳定币 moderate/strong_inflow 或 M2 expanding
  2. 放量买盘（taker_buy_ratio > 0.55 或 vol_ratio > 1.5 且 OBV 拐头）
  3. CVD 底背离（需 is_valid_signal）
  4. 价格放量收回布林下轨或关键均线
- 单凭 RSI 超卖、MACD 金叉、恐惧情绪、布林下轨破位**禁止**给 LONG

## RANGE（震荡 / 盘整）

### 判定条件
- MA 走平或频繁交叉（EMA7 与 EMA25 距离 < 1%）
- 价格反复在最近高点/低点之间往返
- RSI 在 40~60 之间徘徊
- MACD 在零轴附近反复穿越

### 指标解读偏向
- 趋势类振荡指标（MACD/RSI）权重进一步降低
- 优先看稳定币供应变化与 ETF/净流判断区间资金偏置
- 支撑/压力位、布林带更有用；RSI 区间仅作辅助
- 趋势型反转信号（CVD 背离、量价背离）可信度提升

### 默认 bias 倾向
- 没有突破前优先 NEUTRAL
- 在区间下沿 + 放量 + 稳定币流入 → 可考虑 LONG（RSI 拐头不够）
- 在区间上沿 + 缩量 + 稳定币流出 → 可考虑 SHORT
- 任何情况下 confidence_level 上限 MODERATE

## UNCLEAR（趋势不明）

### 判定条件
- 4H 与 1D 趋势方向矛盾
- 重大宏观事件导致价格异常跳动
- 指标之间互相打架（如 MA 多头 + 稳定币流出 + RSI 超买）

### 处理方式
- 默认 NEUTRAL
- confidence_level WEAK
- action 优先"等待入场"或"持仓观望"
- 不应主动给出新的方向性入场建议

---

## 输出约束

AI 研判输出的 JSON 必须包含字段：

```json
{
  "trend_regime": "UP_TREND | DOWN_TREND | RANGE | UNCLEAR"
}
```

如果 trend_regime 与 bias 方向矛盾（例如 trend_regime=DOWN_TREND 但 bias=LONG），
key_drivers 必须包含至少两条反转确认证据，否则应改为 NEUTRAL。
