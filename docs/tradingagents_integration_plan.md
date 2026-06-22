# TradingAgents 融合调研

调研对象: [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)

结论先行: 不建议把 TradingAgents 整包搬进来。它主要面向股票/多资产研究，依赖 LangGraph、LangChain tool calling、yfinance/Alpha Vantage 等数据流；本项目已经有 BTC 专用数据源、指标、交易状态、风控和 Web 面板。更合适的融合方式是学习它的角色编排、结构化输出、辩论式决策和记忆闭环，把这些能力内化到现有 `multi_agent/` 和 `core/`。

## 本项目现状

本项目已经具备以下基础:

- `multi_agent/market_analyzer.py`: 把 BTC 多维数据快照交给 LLM，输出 `bias/confidence_level/action/key_drivers/risks`。
- `multi_agent/news_analyzer.py`: 对加密新闻做多空情绪评分，并保留来源、权重、可信度等 provenance。
- `multi_agent/reflector.py`: 平仓后对“开仓研判 vs 实际 PnL”做复盘。
- `multi_agent/strategy_summarizer.py`: 聚合复盘，生成可注入下一轮研判的策略备忘录。
- `core/signal_aggregator.py`: 目前由 AI 方向、动作和置信度门槛驱动开仓，并叠加一些硬风控守门。

这说明本项目不是缺少 LLM，而是缺少 TradingAgents 那种“多角色互相制衡”的组织层。

## TradingAgents 值得学的特长

### 1. 图式工作流

TradingAgents 用 LangGraph 把流程拆成:

`analysts -> bull/bear debate -> research manager -> trader -> risk debate -> portfolio manager`

它的强项是每个角色只处理一类问题，并通过状态对象传递中间报告。这个模式能减少单个大 prompt 同时做“看数据、辩论、下结论、控风险”的混乱。

对本项目的启发: 可以在 `multi_agent/` 下新增轻量 `decision_committee.py`，不一定立刻引入 LangGraph，先用顺序函数把角色跑通。

### 2. 牛熊双边辩论

TradingAgents 不是让一个 MarketAnalyzer 直接拍板，而是让 Bull Researcher 和 Bear Researcher 分别论证，再由 Research Manager 判断哪边更强。

对 BTC 交易特别有用: 当前系统若遇到“指标多空混杂”，单体 LLM 容易写出看似均衡但实际不可执行的结论。引入多空辩论后，可以显式产出:

- 多头论据及失效条件
- 空头论据及失效条件
- 哪一边证据更强
- 为什么另一边暂时不成立

### 3. 风险三方审查

TradingAgents 的风险层有 aggressive / neutral / conservative 三类观点，最后由 Portfolio Manager 合成。这比单纯置信度门槛更细，因为它能区分:

- 方向对，但入场价格不好
- 方向对，但波动/杠杆/清算风险过高
- 方向对，但当前已有仓位不适合加仓
- 信号强，但关键风险事件临近

对本项目的启发: 在 `SignalAggregator` 开仓前增加 `RiskCommittee` 输出，作为 `entry_guard_ok` 的上游依据。

### 4. 结构化输出

TradingAgents 对 Research Manager、Trader、Portfolio Manager 使用 Pydantic schema，先得到可靠字段，再渲染成 Markdown。这比“让模型返回 JSON 然后手动截取代码块”更稳。

本项目当前多个 agent 都手动解析 JSON。可以逐步引入 Pydantic schema，但保留现有 `LLMClient`，不要一次性换成 LangChain。

### 5. 记忆日志与结果回填

TradingAgents 有 append-only decision log，会把 pending decision 后续补上收益和 reflection，再把历史经验注入下一轮。这个思想与你现有 `AnalysisMemory + Reflector + StrategySummarizer` 很接近。

差异在于 TradingAgents 的日志更偏“可读审计记录”，本项目更偏“结构化交易记忆”。可以补一个 Markdown 决策日志，用于人工复盘和 Web 展示，不替代现有 JSON 记忆。

## 推荐融合路线

### Phase 1: 不引入重依赖，先学决策形态

新增:

- `multi_agent/schemas.py`
- `multi_agent/decision_committee.py`
- `multi_agent/prompts/bull_researcher.md`
- `multi_agent/prompts/bear_researcher.md`
- `multi_agent/prompts/research_manager.md`
- `multi_agent/prompts/risk_reviewer.md`

建议输出结构:

```json
{
  "bias": "LONG|SHORT|NEUTRAL",
  "confidence_level": "VERY_STRONG|STRONG|MODERATE|CAUTIOUS|WEAK",
  "action": "加多|加空|持仓观望|等待入场|减仓|离场",
  "entry_ok": false,
  "position_size_hint": "0%-100%",
  "invalidations": [],
  "key_drivers": [],
  "risks": [],
  "committee": {
    "bull_case": "...",
    "bear_case": "...",
    "risk_review": "..."
  }
}
```

接入点:

- `MarketAnalyzer._analyze()` 内部先生成 snapshot，再交给 committee。
- `SignalAggregator` 不直接信任 `bias/action/confidence_level`，还要检查 `entry_ok == true`。

### Phase 2: 把风险审查接到交易状态

TradingAgents 的 Portfolio Manager 很适合迁移到本项目，但要改成 BTC 语境:

- 当前现货/合约仓位
- 杠杆倍数
- ATR 止损距离
- 近 24h 爆仓/资金费率
- 支撑阻力距离
- 最大单笔亏损

输出不要只是 Buy/Sell，而要贴合本项目:

- `ALLOW_LONG`
- `ALLOW_SHORT`
- `WAIT`
- `REDUCE`
- `EXIT`

这样能自然接入 `core/signal_aggregator.py` 和后续执行器。

### Phase 3: 可选引入 LangGraph

只有在 Phase 1/2 的轻量 committee 证明效果更好后，再考虑 LangGraph。原因:

- 当前项目依赖很轻，服务长期跑在监控进程里。
- LangGraph/LangChain 会显著增加依赖面和调试复杂度。
- 本项目的数据源已经是本地 Python 对象，不需要 TradingAgents 那套 ToolNode 数据流。

如果引入，建议只包住 `multi_agent/` 决策流，不要碰 `data_sources/`、`indicators/`、`server/`。

## 不建议直接搬的部分

- 股票 fundamentals analyst: 对 BTC 不适用。
- yfinance/Alpha Vantage 数据流: 本项目已有更贴近 BTC 的资金费率、合约、链上、ETF、稳定币、MVRV 等数据。
- TradingAgents 整套 CLI: 本项目已有 Web 面板和调度器。
- PortfolioRating 的 Buy/Overweight/Hold 等股票仓位语义: 可以借鉴五档制，但应改成 BTC 交易动作语义。

## 最小可行改造建议

第一步最值得做的是“多空辩论 + 风险审查 + 结构化输出”，范围小、收益大:

1. 保留 `MarketAnalyzer` 对外接口不变。
2. 在 `_analyze()` 里调用 `DecisionCommittee.run(snapshot, memory_context)`。
3. Committee 内部顺序跑三个 LLM 角色: bull, bear, risk.
4. 最终由 manager 汇总为当前 `MarketAnalyzer` 兼容的 JSON。
5. `SignalAggregator` 增加 `entry_ok` 守门。
6. Web 面板展示 `committee` 的多空/风险摘要。

这个方案能学到 TradingAgents 的核心优点，同时不会把项目改成另一个框架。
