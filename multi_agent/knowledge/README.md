# 知识库入口 · 市场状态总览与三步分析流程

市场状态分为两条正交的维度，AI 研判必须**先识别两个维度，再综合给出方向**。

| 维度 | 含义 | 枚举值 | 详细文件 |
|---|---|---|---|
| 趋势状态 | 价格的中期方向 | UP_TREND / DOWN_TREND / RANGE / UNCLEAR | `regimes/trend_regime.md` |
| 波动状态 | 价格波动幅度和能量 | LOW_VOL_COMPRESSION / NORMAL_VOL / BREAKOUT_EXPANSION / HIGH_VOL_EXTREME | `regimes/volatility_regime.md` |

两个维度的正交组合矩阵和每格规则见 `regimes/regime_matrix.md`。

单指标深度解读见 `indicators/indicator_guide.md`。
指标组合冲突处理见 `indicators/combination_rules.md`。

---

## 标准分析流程（三步法）

1. **第一步：判趋势状态**
   - 看 `regimes/trend_regime.md` 的判定条件
   - 输出 `trend_regime` 枚举值

2. **第二步：判波动状态**
   - 看 `regimes/volatility_regime.md` 的判定条件
   - 输出 `volatility_regime` 枚举值

3. **第三步：综合方向研判**
   - 根据 `regimes/regime_matrix.md` 找到所在格子，决定默认 bias 和 confidence 上限
   - 用 `indicators/indicator_guide.md` 和 `indicators/combination_rules.md` 解读具体指标
   - 输出 `bias` / `confidence` / `key_drivers` / `risks` / `action`

---

## 输出一致性自检清单

在给出最终结果前，AI 必须按顺序回答自己以下问题：

1. 我输出的 `trend_regime` 是否符合 `regimes/trend_regime.md` 的判定条件？
2. 我输出的 `volatility_regime` 是否符合 `regimes/volatility_regime.md` 的判定条件？
3. 我的 `bias` 是否落在 `regimes/regime_matrix.md` 当前格子的允许范围内？
4. 如果 `bias` 与 `trend_regime` 方向相反，我是否在 `key_drivers` 中列出了 ≥2 条反转确认证据？
5. 我的 `confidence` 是否同时满足趋势/波动两个维度的上限约束？
6. 如果 `volatility_regime = LOW_VOL_COMPRESSION` 或 `HIGH_VOL_EXTREME`，我是否避免了"主动加仓"类的 action？
7. 我的 `action` 是否与 `confidence` 匹配？（confidence < 60 → 持仓观望 / 等待入场）

任何一条不满足，必须回到上一步重新调整。

---

## 与策略备忘录的关系

复盘备忘录（来自 `strategy_summarizer.md`）会注入到 prompt 里，
它的作用是对**特定 regime 下的常见错误**做补丁，而不是覆盖以上长期规则。
当备忘录与本文件矛盾时，以本文件为准；备忘录中的建议应理解为"该 regime 的当前实测偏差提醒"。
