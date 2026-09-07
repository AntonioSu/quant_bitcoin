# BTC 神盾-长矛双模交易系统
export HTTPS_PROXY=http://gfw.in.zhihu.com:18080
50%现货压舱 + 50%合约打猎，神盾模式（做空收租）与长矛模式（抄底做多）

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 启动监控服务
```bash
# 前台运行
python run_server.py

# 24小时后台运行
./run_daemon.sh start
./run_daemon.sh status
./run_daemon.sh stop
```

### 3. 访问监控面板
浏览器打开: http://localhost:8088

## 系统架构

### 数据流总览

```
┌─────────────────────────────────────────────────────────┐
│                    数据采集层 (data_sources/)             │
│  F&G · 资金费率 · 大户比 · CVD · RSI · 宏观 · 链上 · 期权  │
└────────────────────────┬────────────────────────────────┘
                         │ 6 维度市场快照
                         ▼
┌─────────────────────────────────────────────────────────┐
│               AI 研判层 (multi_agent/)                    │
│                                                         │
│  MarketAnalyzer.fetch()                                 │
│  ├── [委员会模式] DecisionCommittee                       │
│  │   ├── Bull Researcher 🐂  构造多头论证                 │
│  │   ├── Bear Researcher 🐻  构造空头论证                 │
│  │   ├── Risk Reviewer ⚠️   风险审查 + 仓位约束            │
│  │   └── Decision Manager 👔 综合裁定最终信号              │
│  │                                                      │
│  └── [单体模式] 单次 LLM 直接研判 (回退)                   │
│                                                         │
│  输出: bias · confidence_level · action · entry_ok       │
│        position_size_hint · leverage_hint · key_drivers  │
└────────────────────────┬────────────────────────────────┘
                         │ 存入 market.ai_analysis.raw
                         ▼
┌─────────────────────────────────────────────────────────┐
│             信号聚合层 (core/signal_aggregator.py)        │
│                                                         │
│  读取 market.ai_analysis.raw，执行 5 道入场检查:           │
│  ┌─────────────────────────────────────────────┐        │
│  │ 1. ai_direction    AI bias 匹配目标方向       │        │
│  │ 2. ai_confidence   置信度 ≥ 阈值             │        │
│  │ 3. ai_action       action = 加多/加空         │        │
│  │ 4. committee_entry  委员会 entry_ok=true      │        │
│  │ 5. entry_guard     无禁止关键词+无反转风险     │        │
│  └─────────────────────────────────────────────┘        │
│  全部通过 → SignalResult(LONG/SHORT)                     │
│  任一失败 → SignalResult(IDLE)                           │
│                                                         │
│  持仓时: evaluate_exit() → ExitSignal(平仓/减仓/持有)     │
└────────────────────────┬────────────────────────────────┘
                         │ SignalResult / ExitSignal
                         ▼
┌─────────────────────────────────────────────────────────┐
│              交易执行层 (server/trading_scheduler/)       │
│                                                         │
│  Scheduler 调度循环:                                     │
│  ├── 无仓位 → evaluate() → LONG/SHORT → 开仓             │
│  └── 有仓位 → evaluate_exit() → 平仓/减仓/持有            │
│                                                         │
│  执行器: BinanceAdapter → Binance API                    │
└─────────────────────────────────────────────────────────┘
```

### 置信度等级系统 (5 级)

LLM 根据 6 个维度 (情绪/资金面、技术指标、资金流/主力行为、宏观、链上、衍生品) 的同向共振数量输出置信度等级，每个等级 1:1 映射仓位和杠杆:

| confidence_level | 维度共振 | position_size_hint | leverage_hint 上限 |
|---|---|---|---|
| VERY_STRONG | ≥5 维度同向 | 100% | ≤ 10x |
| STRONG | 4 维度同向 | 75% | ≤ 5x |
| MODERATE | 3 维度同向 | 50% | ≤ 5x |
| CAUTIOUS | 2 维度同向 | 25% | ≤ 3x |
| WEAK | ≤1 维度同向 | 0% (不入场) | ≤ 2x |

Python 后端 (`schemas.py`) 对 LLM 输出执行硬约束:
- WEAK → 强制 position=0%, 阻断入场
- HIGH_VOL_EXTREME 波动环境 → 强制 leverage ≤ 3x

### 模块结构

```
quant_bitcoin/
├── multi_agent/            # AI 多智能体研判
│   ├── market_analyzer.py  # 入口: 市场综合分析
│   ├── decision_committee.py # 4角色辩论委员会
│   ├── trading_advisor.py  # 交易执行建议
│   ├── schemas.py          # 数据结构 + 置信度常量 + 硬约束
│   ├── prompts/            # LLM 提示词
│   │   ├── bull_researcher.md
│   │   ├── bear_researcher.md
│   │   ├── risk_reviewer.md
│   │   ├── decision_manager.md
│   │   └── trading_advisor.md
│   └── knowledge/          # 知识库 (规则表)
│       ├── README.md       # 自检清单
│       ├── regimes/        # 趋势×波动矩阵
│       └── indicators/     # 指标解读+组合规则
├── core/                   # 核心交易逻辑
│   ├── config.py           # 参数配置 (保守/标准/激进)
│   ├── market_data.py      # 全局市场数据 (market 单例)
│   ├── signal_aggregator.py # 信号聚合 + 入场/离场判定
│   ├── trading_engine.py   # 主引擎
│   ├── aegis_executor.py   # 做空执行器
│   └── spear_executor.py   # 做多执行器
├── data_sources/           # 数据采集
│   ├── fear_greed.py       # F&G 指数
│   ├── funding_rate.py     # 资金费率
│   ├── top_trader.py       # 大户多空比
│   ├── exchange_netflow.py # 交易所净流入
│   └── ...                 # 宏观/链上/期权等
├── indicators/             # 技术指标
│   ├── atr.py              # ATR 止损计算
│   ├── rsi.py              # RSI
│   └── cvd_divergence.py   # CVD 背离
├── server/                 # Web 服务
│   ├── api.py              # FastAPI 后端
│   ├── scheduler.py        # 24h 调度器
│   ├── trading_scheduler/  # 交易调度 (模拟/实盘)
│   ├── state_store.py      # 状态持久化
│   └── history_store.py    # 历史数据存储
├── notifications/          # 通知
│   └── feishu_trade.py     # 飞书交易通知
├── web/                    # 前端监控面板
│   ├── index.html
│   ├── js/                 # JavaScript
│   └── css/                # 样式
├── binance_utils/          # 交易所适配
│   └── binance_adapter.py  # Binance API
└── data/                   # 运行时数据
    ├── trading_state.json
    └── history/
```

## 状态持久化

交易状态自动保存到 `data/trading_state.json`，服务重启后自动恢复：
- 账户余额 (spot_usdt, futures_usdt)
- 持仓信息 (BTC数量、方向、入场价)
- 交易记录 (trades)
- 模式状态 (aegis/spear)

## 历史数据

指标数据由 `server/history_store.py` 自动落盘到 `data/history/`，服务重启后不丢失。

### 落盘的数据类型

| 类型 | 说明 | 存储文件 |
|------|------|---------|
| `fear_greed` | F&G 指数 | `data/history/fear_greed.json` |
| `funding_rate` | 资金费率 | `data/history/funding_rate.json` |
| `top_trader_ratio` | 大户多空比 | `data/history/top_trader_ratio.json` |
| `btc_price` | BTC 价格 | `data/history/btc_price.json` |
| `etf_flow` | ETF 资金流 | `data/history/etf_flow.json` |
| `exchange_netflow` | 交易所净流入 | `data/history/exchange_netflow.json` |

### 对外暴露的历史接口

按日聚合的两类历史通过独立接口提供（本地历史 + 数据源增量合并）：

```bash
# ETF 每日资金流 (limit=0 返回全部，按日期倒序)
curl "http://localhost:8088/api/etf-flow?limit=30"

# 交易所每日净流入
curl "http://localhost:8088/api/exchange-netflow?limit=30"
```

> 其余落盘数据目前只供前端图表内部使用，未开放独立的读取/删除接口。

## 三档参数

### Conservative (保守)

高门槛，低风险，信号少但准确度高

**做空条件 (三灯全绿):**

| 指标 | 阈值 |
|------|------|
| F&G 指数 | ≥ 85 (极度贪婪) |
| 资金费率 | ≥ 0.05% |
| 巨鲸净流入 | > 5000 BTC |

**做多条件 (三灯全绿):**

| 指标 | 阈值 |
|------|------|
| F&G 指数 | ≤ 15 (极度恐惧) |
| 巨鲸净流出 | < -5000 BTC |
| CVD 底背离 | 价格跌幅 < 3%, CVD 跌幅 > 20% |

**仓位管理:**

| 参数 | 值 |
|------|------|
| 单次最大亏损 | 1.0% 权益 |
| ATR止损倍数 | 2.0 (止损宽，不易被震出) |
| 做空杠杆 | 5x |
| 做多杠杆 | 10x |

---

### Standard (标准)

默认参数，平衡收益与风险

**做空条件 (三灯全绿):**

| 指标 | 阈值 |
|------|------|
| F&G 指数 | ≥ 75 (极度贪婪) |
| 资金费率 | ≥ 0.03% |
| 巨鲸净流入 | > 2000 BTC |

**做多条件 (三灯全绿):**

| 指标 | 阈值 |
|------|------|
| F&G 指数 | ≤ 25 (极度恐惧) |
| 巨鲸净流出 | < -2000 BTC |
| CVD 底背离 | 价格跌幅 < 3%, CVD 跌幅 > 20% |

**仓位管理:**

| 参数 | 值 |
|------|------|
| 单次最大亏损 | 1.5% 权益 |
| ATR止损倍数 | 1.5 |
| 做空杠杆 | 5x |
| 做多杠杆 | 10x |

---

### Aggressive (激进)

低门槛，高风险，信号多但误触率高

**做空条件 (三灯全绿):**

| 指标 | 阈值 |
|------|------|
| F&G 指数 | ≥ 70 (贪婪) |
| 资金费率 | ≥ 0.01% |
| 巨鲸净流入 | > 1000 BTC |

**做多条件 (三灯全绿):**

| 指标 | 阈值 |
|------|------|
| F&G 指数 | ≤ 32 (恐惧) |
| 巨鲸净流出 | < -1000 BTC |
| CVD 底背离 | 价格跌幅 < 3%, CVD 跌幅 > 20% |

**仓位管理:**

| 参数 | 值 |
|------|------|
| 单次最大亏损 | 5.0% 权益 |
| ATR止损倍数 | 1.2 (止损紧，容易被震出) |
| 做空杠杆 | 5x |
| 做多杠杆 | 10x |

### 开仓与平仓分离

- **信号决定开仓**: 三灯全绿时开仓，信号变化不会平掉现有仓位
- **止损决定平仓**: 仓位由价格驱动的止损管理，AI 只能在信号反转时减仓/平仓

### 风险管理参数 (`core/config.py` 的 `RiskConfig`)

阶梯五项只是**兜底默认值** —— 实际取值由 Trading AI 按仓、按小时决定，见下节。

| 参数 | 默认值 | 含义 | 谁定 |
|------|--------|------|------|
| `breakeven_trigger_r` | 0.5 | 峰值浮盈达 0.5R → 止损移到成本价 | AI 每小时可改 |
| `trailing_trigger_r` | 1.5 | 峰值浮盈达 1.5R → 启动移动止损 | AI 每小时可改 |
| `trailing_distance_r` | 1.25 | 移动止损挂在峰值回撤 1.25R 处 | AI 每小时可改 |
| `tp_trigger_r` | 1.0 | 浮盈达 1.0R → 部分止盈落袋 | AI 每小时可改 |
| `tp_fraction` | 0.5 | 止盈平掉的仓位比例（0 = 本仓不落袋） | AI 每小时可改 |
| `ai_stop_atr_mult_min/max` | 1.0 / 3.0 | AI 自定止损 ATR 倍数的允许区间 | 固定 |
| `range_lookback_hours` | 48 | 追高护栏的区间回看窗口 | 固定 |
| `max_entry_range_pct` | 60 | 顺方向位置 ≥ 60% 视为追高，拒绝开仓 | 固定 |
| `breakout_range_pct` | 100 | 突破区间（创新高/新低）放行 | 固定 |

### 谁决定止损止盈

**AI 决定参数，代码负责执行。** 这两件事被拆成两个时钟：

| | 频率 | 由谁 |
|---|---|---|
| 定/改阶梯参数 | 每小时（随新研判） | Trading AI，走 tool call |
| 触发判断与离场 | 每 60 秒 | 纯代码，不含网络请求 |

开仓时 AI 定 `stop_atr_mult`（止损距离 = ATR × 该倍数），这一步定下 **1R 并冻结**，
之后谁都改不了。持仓期间每小时可以整组重调五个阶梯参数。
**AI 只输出相对倍数，不输出绝对价格**——它拿到的现价可能有延迟，
凭感觉写出的绝对价位可能贴近强平价。

阶梯五项**不设区间钳制**，只做类型校验：非数值、负数、无穷一律回落当前生效值并把
原因告知模型。覆盖值存在 `Position` 上而非全局配置，平仓即清空，不污染下一仓。

#### 全权授权下仍然成立的三条

1. **棘轮是结构性的，不是参数。** `resolve_ladder_stop` 只返回比现有止损更靠有利
   方向的候选值，所以调宽 `trailing_distance_r` 不会让已推进的止损退回去 ——
   「放宽以求解套」在这套代码里无效。
2. **1R 开仓即冻结。** AI 能移动各级触发线，改不了那把尺子本身。
3. **强平价钳制**是最后一道底线：止损若穿透强平价会被拉回强平价内侧。

#### 为什么触发判断没有一起挪到每小时

用真实 1m K 线跑完整阶梯做过对照（两个 45 天窗口，各 2004 笔，止损距离 2% / 3.75%）：

| | 每 60 秒 | 每小时 |
|---|---|---|
| 每笔收益 | 基准 | −0.008R ~ +0.062R（方向不一致，等于噪声） |
| 胜率 | 基准 | 高 1~7 个百分点（躲掉假止损） |
| **最差单笔** | −1.06R ~ −1.36R | **−1.18R ~ −1.92R（四个单元格全部恶化）** |

均值上没有代价，但左尾一致更差：−1.92R 意味着止损被击穿近两倍距离，而
「单笔最大亏损 ≈ 1R」正是整套 R 制度和所有回测数字赖以成立的前提。
典型的卖波动率曲线 —— 多赚小钱，偶尔亏一次大的。

（实盘止损真挂在交易所侧 `place_stop_loss`，由币安逐笔执行，不受轮询频率影响；
这个尾部风险只落在模拟盘上。）

#### AI 用 tool call 试算风险（`multi_agent/risk_tools.py`）

光让 AI 输出倍数是「盲填」：它看不到 ATR，不知道 1.5 倍到底是多少美元、会亏多少钱。
因此空仓决策时会给它一个 function calling 工具 `compute_risk_levels`，把相对倍数换算成
真实数字再反馈：

| 入参 | 出参（节选） |
|------|------|
| `direction`、`stop_atr_mult`、`tp_trigger_r`、`position_size_hint`、`leverage` | `stop_price`、`tp_price`、`liquidation_price`、`r_unit_usd`、`loss_at_stop_usd`、`loss_pct_of_equity`、`warnings` |

要点：

- 工具复用 `PositionLevel.preview` 和 `SIZE_PCT_MAP` 的同一套公式，保证 AI 看到的
  数字和真正下单时用的数字一致；两边算法一旦漂移，AI 的判断就失去意义
  （`tests/test_risk_tools.py` 的 Test 1 就是钉这个一致性的）
- `r_unit_usd` 取**实际**止损距离：止损穿透强平价被拉回时 R 会收窄，此时 `warnings`
  会提示降杠杆，止盈价也按收窄后的 R 计算
- 只在**空仓**时挂这个工具。持仓中的 R 已冻结，入参完全不同，改阶梯走下面那个工具
- 提示词要求正常只调 1 次、最多 2 次（`warnings` 非空或亏损过大才重试）。实测
  不给收敛条件时模型会在仓位/杠杆之间反复试算，把轮数耗尽
- 达到轮数上限时会追加一条显式指令再问一次。**只去掉 tools 是不够的**：DeepSeek 系
  会把工具调用语法当普通文本吐出来（`<｜｜DSML｜｜tool_calls>`），下游 JSON 解析直接失败
- 工具执行异常、参数非 JSON、未知工具名都以 `{"error": ...}` 回灌给模型自行纠正，
  不会中断决策

通用的 tool call 循环在 `LLMClient.chat_with_tools()`，与业务无关，可复用于其他 agent。

#### 持仓中重调阶梯（`compute_ladder_levels`）

每小时新研判到达时缓存失效，AI 被调用一次，此时挂的是另一个工具。R 已冻结，
所以入参是五条触发线，出参是这一仓的真实后果：

| 入参 | 出参（节选） |
|------|------|
| `breakeven_trigger_r`、`trailing_trigger_r`、`trailing_distance_r`、`tp_trigger_r`、`tp_fraction` | `resulting_stop_price`、`resulting_stop_r`、**`exits_immediately`**、`tp_price`、**`tp_fires_immediately`**、`profit_at_tp_usd`、`warnings` |

- `exits_immediately=true` 表示这组参数会把止损推到现价的不利侧，**下一次检查就平仓**。
  提示词禁止在该标志为真时沿用参数（除非确实想立刻离场）
- 三类矛盾组合会主动告警：距离 ≥ 启动线（等于放弃保本）、
  止盈线 ≥ 移动止损启动线（止盈可能永不触发）、非法值回落
- 阶梯数学只有一份实现 `resolve_ladder_stop`，`_update_protective_stop` 和
  `LadderTool` 都调它。两边一旦漂移，模型看到的就是假数字
  （`tests/test_hourly_ladder.py` 的 Test 1 钉这个一致性）
- 护栏（`_apply_policy`）会重建 `TradingDecision`，途中会丢掉阶梯字段。护栏管的是
  动作（开/平/减），不该连带否掉阶梯调整，所以 `decide()` 在护栏之后把它们补回来

#### AI 每小时看到的输入

阶梯每一级都用 R 定义，而此前传给这一层的只有美元和绝对价格 —— 模型连 1R 是多少
都算不出来。现在 `_build_position_risk()` 补齐了一整套 R 坐标：

| 分组 | 字段 |
|------|------|
| R 坐标系 | `r_unit_usd` / `r_unit_pct`、`initial_stop`、`profit_r`、**`peak_r`**、`drawdown_from_peak_r`、`stop_r`、`stop_stage`、`dist_to_liq_r`、`tp_taken` |
| 当前生效阶梯 | 五项实际取值（覆盖值优先，否则配置默认） |
| 波动重标定 | `atr_at_open` → `atr_now` 的比值 |
| 路径 | 开仓以来的 4H 收盘价，换算成 R |
| 论点变化 | `trend_regime` / `volatility_regime`，以及开仓时的 bias / confidence 基准 |

其中 **`peak_r` 是必须给的**：整个棘轮是峰值驱动的，「冲到 1.4R 又跌回 0.3R」和
「一路磨到 0.3R」当前浮盈完全相同，但前者是突破失败该收紧、后者是缓慢推进该给空间。
只看当前浮盈无法区分这两种相反的局面。

`trend_regime` / `volatility_regime` 信号里一直有，但此前没传到这一层 ——
而它们恰好最该驱动阶梯松紧。`atr_at_open` 是新增的持久化字段：R 是用开仓那一刻的
ATR 定的，波动翻倍后同样的 R 已不是同样的风险，这是放宽移动止损距离最正当的理由。

### 保本 / 部分止盈 / 移动止损机制

**R = 开仓时的止损距离 = ATR × ATR止损倍数**，是单笔风险单位。
棘轮式止损保护浮盈，并在 `tp_trigger_r` 处落袋一部分、剩余仓位继续参与上涨。
下面用默认阶梯举例，实际每一级由 AI 按仓、按小时决定：

```
阶段1 (开仓):
  止损价 = 入场价 - ATR × ATR止损倍数        (LONG，SHORT 反向)
  这一步定下 1R 并冻结

阶段2 (峰值浮盈 ≥ breakeven_trigger_r，默认 0.5R → 保本):
  止损价 = 入场价                            风险归零，不卖任何仓位

阶段3 (浮盈 ≥ tp_trigger_r，默认 1.0R → 部分止盈):
  平掉 tp_fraction（默认 50%）落袋，剩余止损压到成本价   每仓只执行一次

阶段4 (峰值浮盈 ≥ trailing_trigger_r，默认 1.5R → 移动止损):
  止损价 = 峰值价 - trailing_distance_r × R  (默认 1.25R)
  止损只朝有利方向移动，永远不回退
```

阶段3 的依据：0.5R 保本与 1.5R 移动止损之间存在空档，历史上约三成交易的峰值
浮盈正落在这一段，两端机制都不落袋，最终只能拿到 0。148 个独立事件的逐根回测
显示，无止盈方案总收益 6.36R 但去掉最赚的 5 笔即转为 −1.16R（全部盈利押在极少
数交易上）；加入 1.0R 半仓止盈后为 8.15R，去掉最赚 5 笔仍有 +1.89R。

注意这份回测是针对**默认阶梯**做的。AI 把 `tp_trigger_r` 调到 `trailing_trigger_r`
之上时，部分止盈已经离开了它被设计填补的空档，这份依据不再适用 —— 工具会就此告警。

### 示例 (Standard, ATR ≈ $980, ATR止损倍数 1.5 → R = $1,470)

```
开多 @ $69,000
  初始止损 = $69,000 - $1,470 = $67,530

涨到 $69,735 (+0.5R) → 保本: 止损上移到 $69,000

涨到 $70,470 (+1.0R) → 部分止盈: 平掉一半仓位落袋，剩余半仓止损仍在 $69,000

涨到 $71,205 (+1.5R) → 剩余半仓启动移动止损:
  止损 = $71,205 - $1,838 = $69,367   (已锁定 +$367)

继续涨到 $73,000 (+2.7R):
  止损上移 = $73,000 - $1,838 = $71,162

回落到 $71,162 → 移动止盈触发，全平
```

### 追高护栏

历史数据显示，开仓价落在近 48h 区间「顺方向 60~100%」区段的交易胜率仅 19%，
是震荡行情下的主要亏损来源（追高做多 / 杀跌做空）。开仓前检查：

```
顺方向位置 = LONG:  (开仓价 - 区间低) / (区间高 - 区间低)
             SHORT: 1 - 上式

< 60%   → 放行（区间下沿做多 / 上沿做空）
60~100% → 拒绝开仓（追高）
> 100%  → 放行（已突破区间，属于趋势跟随而非追高）
```

区间由已收盘的 4H K 线构造（排除进行中的当前根），因此突破时位置可以 > 100%。
K 线不足或区间退化时不拦截。
## 指标解释

### F&G 指数计算逻辑
- 天级更新
- 使用 `FearGreedIndex` 计算器
- 使用api(https://api.alternative.me/fng/)获取
- 数据来源: https://alternative.me/crypto/fear-and-greed-index/
- API: https://api.alternative.me/fng/
- 指数范围:
  - 0-24: 极度恐惧 (Extreme Fear)
  - 25-49: 恐惧 (Fear)
  - 50-74: 贪婪 (Greed)
  - 75-100: 极度贪婪 (Extreme Greed)

### 资金费率
- **数据源**：Binance 合约资金费率（支持币本位和U本位）
- **更新频率**：每8小时结算一次（00:00, 08:00, 16:00 UTC）
- **费率含义**：
  - 正费率：多头支付空头（市场看多情绪强，适合做空收租）
  - 负费率：空头支付多头（市场看空情绪强）
- **做空策略**：当费率 ≥ 0.02% 时，年化收益约 21.9%（0.02% × 3 × 365）
- **合约类型**：
  - 币本位：`BTCUSD_PERP`（做空模式推荐）
  - U本位：`BTCUSDT`（常规交易）

### 聪明钱（Top Trader Long/Short Ratio）
- **更新频率**：币安免费API，大概一个小时更新一次，本系统是每5分钟更新一次
- **数据源**：币安官方API - Top 20% 大户的多空持仓比例
- **更新频率**：实时更新，缓存5分钟
- **核心指标**：聪明钱多空比（Long/Short Ratio）
  - **longAccount**：做多账户占比（如 62.91%）
  - **shortAccount**：做空账户占比（如 37.09%）
  - **longShortRatio**：多空比率 = longAccount / shortAccount（如 1.70）
- **市场含义**：
  - **高多空比（> 2.0）**：大户过度看多 → 市场过热，适合做多（长矛模式）
  - **低多空比（< 0.5）**：大户过度看空 → 市场超跌，适合做空（神盾模式）
  - **中性（0.8-1.2）**：多空平衡，观望


### CVD 计算逻辑
- 使用 `CVDDivergenceDetector` 检测器
- 回看周期：6 根 4H K线

#### 1. CVD 序列计算（cvd_values 的生成过程）

**输入数据**：K 线数组
```python
# klines 格式：[[timestamp, open, high, low, close, volume], ...]
klines = [
    [1700000000, 65000, 65500, 64800, 64900, 1500],  # K1: 阴线
    [1700014400, 64900, 65200, 64700, 65100, 1200],  # K2: 阳线
    [1700028800, 65100, 65300, 64900, 65000, 1800],  # K3: 阴线
    [1700043200, 65000, 65400, 64800, 65200, 1000],  # K4: 阳线
    [1700057600, 65200, 65500, 64600, 64800, 2000],  # K5: 阴线
    [1700072000, 64800, 65000, 64500, 64600, 2500],  # K6: 阴线
]
```

**计算过程**：逐根 K 线计算 Volume Delta 并累积
```python
cvd_values = []
cumulative = 0.0

for kline in klines:
    open_price = kline[1]
    close_price = kline[4]
    volume = kline[5]
    
    # 判断 K 线类型，计算 Volume Delta
    if close_price > open_price:  # 阳线，买方主导
        delta = +volume
    elif close_price < open_price:  # 阴线，卖方主导
        delta = -volume
    else:  # 十字星（收盘价 = 开盘价），中性
        delta = 0
    
    # 累积求和
    cumulative += delta
    cvd_values.append(cumulative)
```

#### 2. 价格变化百分比（price_change_pct）
```python
start_price = klines[0][4]   # 第一根 K 线收盘价
end_price = klines[-1][4]    # 最后一根 K 线收盘价
price_change_pct = (end_price - start_price) / start_price * 100
```

#### 3. CVD 变化百分比（cvd_change_pct）
```python
# cvd_values 是步骤1计算出的 CVD 序列
start_cvd = cvd_values[0]   # 第一根 K 线的 CVD = -1500
end_cvd = cvd_values[-1]    # 最后一根 K 线的 CVD = -5600
cvd_change_pct = (end_cvd - start_cvd) / abs(start_cvd) * 100
```

#### 4. 底背离判断条件
```python
if price_change_pct > -3% and cvd_change_pct < -20%:
    signal = "底背离"  # 价格横盘/微跌，但卖方力量耗尽
```

**判断逻辑**：
- **条件1**：`price_change_pct > -3%` → 价格下跌幅度 < 3%（价格横盘或微跌）
- **条件2**：`cvd_change_pct < -20%` → CVD 下跌幅度 > 20%（卖方力量断崖式衰竭）

