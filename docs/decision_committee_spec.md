# Decision Committee Spec

目标: 在不改变 `MarketAnalyzer` 对外接口的前提下，引入“多空辩论 + 风险审查 + 结构化输出”，学习 TradingAgents 的角色分工优势，同时保持本项目 BTC 专用数据源、交易状态和 Web 面板不被大幅重构。

## 1. 背景

当前系统由 `MarketAnalyzer` 单次 LLM 调用完成趋势识别、指标解释、方向判断、动作建议和风险提示。优点是简单、成本低；缺点是在多空信号混杂时，单体 prompt 容易把“分析”和“拍板”混在一起，输出看似完整但缺少反方质询。

TradingAgents 的核心价值不是数据源，而是决策组织方式:

- Analyst 先生成事实报告。
- Bull / Bear 分别构造多空论据。
- Manager 判断哪一边证据更强。
- Risk / Portfolio Manager 再把方向转换成能否交易、如何控制仓位。

本项目第一阶段只借鉴这个决策形态，不引入 LangGraph/LangChain。

## 2. 设计目标

- 保留 `MarketAnalyzer.fetch(market) -> DataPoint` 不变。
- 保留 `market.ai_analysis.raw` 当前主字段不变: `bias`, `confidence`, `summary`, `action`, `key_drivers`, `risks`, `horizon`, `trend_regime`, `volatility_regime`。
- 新增 `entry_ok`, `position_size_hint`, `invalidations`, `committee` 等扩展字段。
- Committee 内部顺序运行 `bull -> bear -> risk -> manager`。
- Manager 最终输出仍兼容当前 `SignalAggregator` 与前端。
- 使用 Pydantic 做结构校验、归一化和 fallback，不依赖模型原生 structured output。
- 首版通过 feature flag 控制启用，便于灰度和回滚。

## 3. 非目标

- 不搬 TradingAgents 整包。
- 不引入 LangGraph、LangChain、ToolNode。
- 不替换现有 `LLMClient`。
- 不重做 `data_sources/` 和 `indicators/`。
- 不改变执行器下单逻辑。
- 不让 LLM 直接决定杠杆或下单数量，只输出仓位倾向和风控理由。

## 4. 总体架构

```text
MarketData
  -> MarketAnalyzer._build_snapshot()
  -> MarketAnalyzer._build_dynamic_context()
  -> DecisionCommittee.run(snapshot, context)
       -> Bull Researcher
       -> Bear Researcher
       -> Risk Reviewer
       -> Decision Manager
  -> MarketAnalyzer._normalize()
  -> DataPoint(raw=analysis)
  -> SignalAggregator
       -> bias/confidence/action
       -> entry_ok guard
       -> existing keyword/risk guards
```

`DecisionCommittee` 是 `MarketAnalyzer` 的内部实现细节。外部仍只看到一次 AI 综合研判。

## 5. 文件规划

新增:

- `multi_agent/schemas.py`
- `multi_agent/decision_committee.py`
- `multi_agent/prompts/bull_researcher.md`
- `multi_agent/prompts/bear_researcher.md`
- `multi_agent/prompts/risk_reviewer.md`
- `multi_agent/prompts/decision_manager.md`
- `tests/test_decision_committee.py`

修改:

- `multi_agent/market_analyzer.py`
- `core/signal_aggregator.py`
- `server/api.py`
- `web/js/ai_analysis.js`
- 可选: `web/index.html`, `web/css/ai_analysis.css`

## 6. 数据结构

### 6.1 DebateCase

Bull 和 Bear 都输出同一种结构，区别在 `side`。

```json
{
  "side": "bull | bear",
  "thesis": "核心论点，中文，1-2 句",
  "confidence": 0,
  "evidence": [
    {
      "factor": "必须包含具体数值",
      "weight": "high | medium | low",
      "source": "technical | flow | sentiment | derivatives | macro | onchain | news"
    }
  ],
  "invalidations": [
    "什么条件出现则本方观点失效，必须可观察"
  ],
  "best_action": "加多 | 加空 | 持仓观望 | 等待入场 | 减仓 | 离场"
}
```

约束:

- `evidence` 2-5 条。
- `confidence` 0-100，永远不得为 100。
- Bull 不得输出 `side=bear`，Bear 不得输出 `side=bull`。
- 证据必须来自 snapshot 或动态上下文，不允许编造外部行情。

### 6.2 RiskReview

```json
{
  "entry_ok": false,
  "risk_level": "low | medium | high | extreme",
  "allowed_actions": ["加多", "加空", "持仓观望", "等待入场", "减仓", "离场"],
  "position_size_hint": "0% | 25% | 50% | 75% | 100%",
  "blockers": [
    "阻止开仓的风险，如高波动、清算风险、距离止损过近"
  ],
  "risk_controls": [
    "若交易需要满足的控制条件"
  ]
}
```

约束:

- `risk_level=extreme` 时 `entry_ok=false`。
- `position_size_hint=0%` 时 `entry_ok=false`。
- 如果 `volatility_regime=HIGH_VOL_EXTREME`，必须至少一个 blocker 提到流动性、爆仓或滑点。
- 如果 `confidence < 60` 的方向性观点进入 risk review，默认不得允许开仓。

### 6.3 CommitteeDecision

Manager 最终输出，必须兼容当前 `MarketAnalyzer`。

```json
{
  "trend_regime": "UP_TREND | DOWN_TREND | RANGE | UNCLEAR",
  "volatility_regime": "LOW_VOL_COMPRESSION | NORMAL_VOL | BREAKOUT_EXPANSION | HIGH_VOL_EXTREME",
  "bias": "LONG | SHORT | NEUTRAL",
  "confidence": 0,
  "summary": "≤40 字，中文",
  "action": "加多 | 加空 | 持仓观望 | 减仓 | 离场 | 等待入场",
  "entry_ok": false,
  "position_size_hint": "0% | 25% | 50% | 75% | 100%",
  "key_drivers": [
    {"factor": "含具体数值", "side": "bull | bear", "weight": "high | medium | low"}
  ],
  "risks": ["1-3 条反向风险"],
  "invalidations": ["观点失效条件"],
  "horizon": "4H~24H",
  "committee": {
    "bull_case": "多头摘要",
    "bear_case": "空头摘要",
    "risk_review": "风险摘要",
    "manager_rationale": "为什么最终如此决策"
  }
}
```

硬约束:

- `entry_ok=false` 时，`action` 不得为 `加多` 或 `加空`。
- `bias=LONG` 时，`action=加多` 必须同时满足 `entry_ok=true`。
- `bias=SHORT` 时，`action=加空` 必须同时满足 `entry_ok=true`。
- `confidence < 60` 时，`action` 必须是 `持仓观望` 或 `等待入场`。
- `key_drivers` 3-5 条，仍按当前前端习惯区分 `side=bull/bear`。
- 保持 `trend_regime` 和 `volatility_regime` 的现有枚举不变。

## 7. Pydantic 校验策略

`multi_agent/schemas.py` 定义:

- `Driver`
- `DebateCase`
- `RiskReview`
- `CommitteeSnapshotContext`
- `CommitteeDecision`

解析流程:

1. 从 LLM 文本中提取 JSON。
2. 使用 Pydantic `model_validate`。
3. 对枚举、置信度、列表长度做规范化。
4. 失败时记录日志，并进入 fallback。

Fallback 策略:

- Bull/Bear 单边失败: 用空 case 代替，confidence=0，thesis 标记失败原因。
- Risk 失败: 默认 `entry_ok=false`, `risk_level=high`, `position_size_hint=0%`。
- Manager 失败: 回退到当前旧版 `MarketAnalyzer._analyze()` 单体 prompt 逻辑，避免 AI 分析完全不可用。

## 8. Prompt 设计

### 8.1 Bull Researcher

职责:

- 只负责寻找多头证据。
- 必须承认多头观点的失效条件。
- 不直接输出最终交易决策。

输入:

- snapshot JSON
- 知识库规则摘要
- 上次研判和策略备忘录

输出: `DebateCase(side="bull")`

### 8.2 Bear Researcher

职责:

- 只负责寻找空头证据。
- 必须承认空头观点的失效条件。
- 不直接输出最终交易决策。

输出: `DebateCase(side="bear")`

### 8.3 Risk Reviewer

职责:

- 不判断多空哪边更正确。
- 只判断当前是否适合开仓、加仓、减仓或等待。
- 明确风险阻断项和仓位倾向。

输入:

- snapshot JSON
- bull case
- bear case
- 已有风险知识库

输出: `RiskReview`

### 8.4 Decision Manager

职责:

- 综合 bull / bear / risk。
- 遵守 `market_analyzer.md` 当前所有硬约束。
- 输出 `CommitteeDecision`。

关键规则:

- Manager 可以选择 LONG、SHORT 或 NEUTRAL。
- 方向与 action 必须一致。
- 如果 Risk Reviewer 不允许入场，则只能输出观望、等待、减仓或离场。
- 如果要相对上次研判翻转，必须给出高权重变化原因。

## 9. MarketAnalyzer 集成

建议改造:

```python
def fetch(self, market) -> DataPoint:
    snapshot = self._build_snapshot(market)
    prompt = self._build_prompt(snapshot)
    analysis = self._analyze(prompt, snapshot=snapshot)
    ...
```

`_analyze()` 内部:

```python
if self._committee_enabled:
    context = self._build_dynamic_context()
    result = self.committee.run(snapshot=snapshot, context=context)
    return self._normalize(result)

return self._analyze_legacy(prompt)
```

需要从现有 `_analyze()` 中拆出:

- `_load_static_system_prompt()`
- `_build_dynamic_context()`
- `_append_dynamic_context(prompt, context)`
- `_analyze_legacy(prompt)`

这样旧逻辑保留，committee 失败时能回退。

Feature flag:

- 环境变量: `ENABLE_DECISION_COMMITTEE=true|false`
- 初始默认: `false`
- 手动验证后再改为默认开启

## 10. SignalAggregator 集成

在 `_check_ai_direction()` 读取:

```python
entry_ok = bool(ai_raw.get("entry_ok", False))
position_size_hint = ai_raw.get("position_size_hint")
invalidations = ai_raw.get("invalidations", [])
```

开仓守门新增:

```python
committee_entry_ok = entry_ok is True
entry_guard_ok = (
    action_allows_entry
    and committee_entry_ok
    and not no_entry_action
    and not no_entry_keyword
    and not low_confidence_entry
    and not reversal_risk_short
)
```

兼容策略:

- 如果 `ENABLE_DECISION_COMMITTEE=false` 或旧分析没有 `entry_ok` 字段，则维持当前行为。
- 如果 `entry_ok=false`，即使 `bias/confidence/action` 看起来满足，也不得开仓。

`conditions` 建议新增:

- `committee_entry_ok`

`values` 建议新增:

- `entry_ok`
- `position_size_hint`

## 11. API 与前端

`server/api.py::_ai_analysis_payload()` 新增:

- `ai_entry_ok`
- `ai_position_size_hint`
- `ai_invalidations`
- `ai_committee`

Web 面板第一版只展示:

- 多头摘要
- 空头摘要
- 风险审查摘要
- 是否允许入场
- 仓位倾向

前端展示不是首要阻塞项，但 API 字段应先预留。

## 12. 日志与可观测性

每次 committee 调用记录:

- bull token usage: `usage_tag="[bull]"`
- bear token usage: `usage_tag="[bear]"`
- risk token usage: `usage_tag="[risk]"`
- manager token usage: `usage_tag="[manager]"`
- 最终结论: `bias/confidence/action/entry_ok`
- fallback 原因

建议 `committee` 字段保留简短摘要，完整原始输出可选写入 `data/last_committee_analysis.json`，避免 WebSocket payload 过大。

## 13. 成本与延迟控制

首版 committee 会从 1 次 LLM 调用变成最多 4 次。

控制策略:

- Bull/Bear/Risk 使用同一个 quick model。
- Manager 可沿用当前模型。
- 每个角色输出短 JSON，限制冗长推理。
- static knowledge 继续放 system prompt，动态上下文放 user prompt，延续当前 prompt caching 思路。
- 新闻分析仍独立，不重复拉取。

可选优化:

- Bull 和 Bear 未来可以并发跑，但首版顺序跑，便于日志和故障定位。
- 若 snapshot 明显未就绪或旧逻辑判断为低质量数据，直接 NEUTRAL，不进入 committee。

## 14. 测试计划

单元测试:

- `CommitteeDecision` 校验 `entry_ok=false` 时不得 `action=加多/加空`。
- `confidence < 60` 时 action 自动归一到观望/等待。
- Risk 解析失败时默认阻断开仓。
- Manager 解析失败时回退 legacy。
- `SignalAggregator` 在 `entry_ok=false` 时不触发 LONG/SHORT。
- 无 `entry_ok` 字段时旧行为不变。

集成测试:

- 构造强多 snapshot，确认输出 LONG 且 `entry_ok=true` 才允许加多。
- 构造强空 snapshot，确认输出 SHORT 且 `entry_ok=true` 才允许加空。
- 构造高波动 snapshot，确认即使方向明确也 `entry_ok=false` 或降仓位。
- 构造上次研判 LONG、当前指标反转 snapshot，确认 manager 给出翻转理由。

人工验收:

- 手动刷新 AI 分析，日志能看到 4 个角色。
- Web 面板仍能正常展示原有 AI 字段。
- `SignalAggregator.reason` 能解释被 committee 阻断的原因。

## 15. 实施步骤

1. 新增 `multi_agent/schemas.py`，先完成 schema、枚举、校验和 normalize。
2. 新增四个 prompt 文件，严格要求 JSON 输出。
3. 新增 `DecisionCommittee`，实现 `run()`、解析、fallback 和日志。
4. 拆分 `MarketAnalyzer._analyze()`，接入 feature flag。
5. 修改 `SignalAggregator`，增加 `entry_ok` 守门和兼容逻辑。
6. 扩展 API payload。
7. 最小扩展前端展示 committee 摘要。
8. 添加测试并跑现有测试。
9. 本地用 `ENABLE_DECISION_COMMITTEE=true` 手动跑一次 AI 刷新，观察日志和输出。

## 16. 验收标准

- 未开启 feature flag 时，系统行为与当前版本一致。
- 开启 feature flag 后，`market.ai_analysis.raw` 仍包含现有字段。
- `entry_ok=false` 能阻断开仓。
- 任一角色 LLM 输出异常不会导致整个 AI 分析崩溃。
- 测试覆盖新增 schema、committee fallback、SignalAggregator 守门。
- Web/API 不因新增字段破坏旧展示。

## 17. 后续扩展

- Bull/Bear 并发执行。
- Risk Reviewer 读取实时持仓、ATR 止损、支撑阻力距离。
- 写入 Markdown decision log，方便人工复盘。
- 若顺序编排变复杂，再评估 LangGraph。
