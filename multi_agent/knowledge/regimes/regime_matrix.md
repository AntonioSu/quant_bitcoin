# 【市场状态 · 综合矩阵】趋势 × 波动 16 格规则表

把 `trend_regime.md` 的 4 种趋势状态和 `volatility_regime.md` 的 4 种波动状态正交组合（共 16 格），
针对每一格说明：偏好方向、confidence_level 上限、特别规则。
AI 研判必须先识别落在哪一格，再决定 bias 与 confidence_level。

> 简写：UP=UP_TREND，DOWN=DOWN_TREND，RANGE=RANGE，UNC=UNCLEAR。
> LOW=LOW_VOL_COMPRESSION，NORM=NORMAL_VOL，BRK=BREAKOUT_EXPANSION，EXT=HIGH_VOL_EXTREME。

## 矩阵速查表

| 趋势 \\ 波动 | LOW | NORM | BRK | EXT |
|---|---|---|---|---|
| UP   | 顺势偏多 / CAUTIOUS~MODERATE | 顺势 LONG / MODERATE~VERY_STRONG | 顺突破 LONG / STRONG~VERY_STRONG | 减仓观望 / CAUTIOUS~MODERATE |
| DOWN | 顺势偏空 / CAUTIOUS~MODERATE | 顺势 SHORT / MODERATE~VERY_STRONG | 顺突破 SHORT / STRONG~VERY_STRONG | 减仓观望 / CAUTIOUS~MODERATE |
| RANGE | 区间内反转 / CAUTIOUS~MODERATE | 区间内反转 / MODERATE~STRONG | 突破出现 → 切换为顺突破 | 不可靠 / WEAK |
| UNC | NEUTRAL / CAUTIOUS | NEUTRAL / CAUTIOUS~MODERATE | 不入场，等方向确认 | NEUTRAL / WEAK |

## 关键组合详解

### UP × LOW（趋势明确但波动收敛）
- 趋势已确立（均线多头排列），但成交量萎缩等待方向确认
- 若 ≥2 个维度顺势偏多（如 MA 多头 + 情绪极端/链上净流出/MVRV 低估），可给出 LONG MODERATE
- 不要因为缩量就完全否定已确立的趋势
- 禁止逆势做空，除非趋势结构明确破坏（如跌破 EMA25 + 放量）
- confidence_level 上限 MODERATE

### DOWN × LOW（下跌趋势收敛）
- 趋势已确立（均线空头排列），但成交量萎缩
- 若 ≥2 个维度顺势偏空（如 MA 空头 + 资金流出/大户比偏空），可给出 SHORT MODERATE
- 禁止逆势抄底，除非有放量 + 多重反转确认
- confidence_level 上限 MODERATE

### UP × NORM（最舒服的顺势做多场景）
- 默认 LONG MODERATE ~ VERY_STRONG（取决于维度共振数量）
- 反弹回 EMA7 + RSI 回到 50 上方 → 加仓点
- 唯一允许给 SHORT 的情况：≥3 个反转维度同时出现（含 MACD 死叉 + CVD 顶背离）
- confidence_level 上限 VERY_STRONG（需 ≥5 维度同向）

### UP × BRK（顺势加速）
- 顺突破 LONG，confidence_level STRONG~VERY_STRONG（多维确认）
- RSI 80+ 不立即看空；动能为王
- 此时反向背离信号**忽略**

### UP × EXT（顶部高波动）
- 警惕"恐慌做多顶部"
- LONG 现仓优先减仓
- 不主动增加新仓
- confidence_level 上限 MODERATE

### DOWN × NORM（顺势做空主战场）
- 默认 SHORT MODERATE ~ VERY_STRONG（取决于维度共振数量）
- 反弹到 EMA7 被压制 + RSI 反弹 60 失败 → 加仓点
- LONG 必须同时满足 ≥2 项反转确认（放量、MACD 翻转、CVD/RSI 底背离、收回下轨/均线）
- **来自模拟盘复盘**：本格中"靠 RSI 超卖抢反弹"样本胜率为 0，禁止仅凭超卖做多

### DOWN × BRK（破位加速下跌）
- 顺突破 SHORT，confidence_level STRONG~VERY_STRONG
- RSI 30 以下不立即抄底
- 反向信号不可信，等待右侧确认

### DOWN × EXT（恐慌底部）
- 警惕"恐慌做空底部"
- SHORT 现仓优先收紧止损或减仓
- LONG 仍需多维确认才考虑，不要单凭恐惧入场
- confidence_level 上限 MODERATE

### RANGE × LOW（典型震荡 + 收敛）
- **不预测方向**
- 默认 NEUTRAL，confidence_level MODERATE
- 主要 action："等待入场"，但若有 ≥3 个维度同向共振可考虑轻仓入场
- 任何方向性建议都要在 risks 中说明可能是噪音

### RANGE × BRK（突破出现）
- 立刻把趋势状态切换为 UP 或 DOWN
- 此格本质上是过渡状态，不长期维持
- 出现时 confidence_level 可上调，但必须等量价确认 1~2 根 K 线

### UNC × 任意
- 默认 NEUTRAL
- confidence_level WEAK
- 不建议主动加仓或新开仓
- 优先 action："持仓观望"

## 通用 confidence_level 约束

- 任何格子的 confidence_level 上限不超过对应趋势上限与波动上限的较低者
- confidence_level 为 STRONG 时必须列出至少 4 条 key_drivers，VERY_STRONG 至少 5 条
- 任何格子在 bias 与趋势方向相反时，confidence_level 自动下调一档
