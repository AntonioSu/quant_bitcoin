# 趋势 × 波动 组合矩阵

把 `trend_regimes.md` 的 4 种趋势状态和 `volatility_regimes.md` 的 4 种波动状态正交组合（共 16 格），
针对每一格说明：偏好方向、confidence 上限、特别规则。
AI 研判必须先识别落在哪一格，再决定 bias 与 confidence。

> 简写：UP=UP_TREND，DOWN=DOWN_TREND，RANGE=RANGE，UNC=UNCLEAR。
> LOW=LOW_VOL_COMPRESSION，NORM=NORMAL_VOL，BRK=BREAKOUT_EXPANSION，EXT=HIGH_VOL_EXTREME。

## 矩阵速查表

| 趋势 \\ 波动 | LOW | NORM | BRK | EXT |
|---|---|---|---|---|
| UP   | 等待回踩 / NEUTRAL 上限 55 | 顺势 LONG 标准 65 | 顺突破 LONG 上限 75 | 减仓观望 上限 60 |
| DOWN | 等待破位 / NEUTRAL 上限 55 | 顺势 SHORT 标准 65 | 顺突破 SHORT 上限 75 | 减仓观望 上限 60 |
| RANGE | 区间内反转 上限 55 | 区间内反转 上限 60 | 突破出现 → 切换为顺突破 | 不可靠 上限 50 |
| UNC | NEUTRAL 上限 50 | NEUTRAL 上限 55 | 不入场，等方向确认 | NEUTRAL 上限 45 |

## 关键组合详解

### UP × NORM（最舒服的顺势做多场景）
- 默认 LONG 55~70
- 反弹回 EMA7 + RSI 回到 50 上方 → 加仓点
- 唯一允许给 SHORT 的情况：≥3 个反转维度同时出现（含 MACD 死叉 + CVD 顶背离）
- confidence 上限 75

### UP × BRK（顺势加速）
- 顺突破 LONG，confidence 可到 70~80（多维确认）
- RSI 80+ 不立即看空；动能为王
- 此时反向背离信号**忽略**

### UP × EXT（顶部高波动）
- 警惕"恐慌做多顶部"
- LONG 现仓优先减仓
- 不主动增加新仓
- confidence 上限 60

### DOWN × NORM（顺势做空主战场）
- 默认 SHORT 55~70
- 反弹到 EMA7 被压制 + RSI 反弹 60 失败 → 加仓点
- LONG 必须同时满足 ≥2 项反转确认（放量、MACD 翻转、CVD/RSI 底背离、收回下轨/均线）
- **来自模拟盘复盘**：本格中"靠 RSI 超卖抢反弹"样本胜率为 0，禁止仅凭超卖做多

### DOWN × BRK（破位加速下跌）
- 顺突破 SHORT，confidence 可到 70~80
- RSI 30 以下不立即抄底
- 反向信号不可信，等待右侧确认

### DOWN × EXT（恐慌底部）
- 警惕"恐慌做空底部"
- SHORT 现仓优先收紧止损或减仓
- LONG 仍需多维确认才考虑，不要单凭恐惧入场
- confidence 上限 60

### RANGE × LOW（典型震荡 + 收敛）
- **不预测方向**
- 默认 NEUTRAL，confidence ≤ 55
- 主要 action："等待入场"
- 任何方向性建议都要在 risks 中说明可能是噪音

### RANGE × BRK（突破出现）
- 立刻把趋势状态切换为 UP 或 DOWN
- 此格本质上是过渡状态，不长期维持
- 出现时 confidence 可显著上升，但必须等量价确认 1~2 根 K 线

### UNC × 任意
- 默认 NEUTRAL
- confidence 上限 50
- 不建议主动加仓或新开仓
- 优先 action："持仓观望"

## 通用 confidence 约束

- 任何格子的 confidence 上限不超过对应趋势上限与波动上限的较小值
- 任何格子 confidence ≥ 70 都必须列出至少 4 条 key_drivers
- 任何格子在 bias 与趋势方向相反时，confidence 上限自动 ×0.7（向下取整）
