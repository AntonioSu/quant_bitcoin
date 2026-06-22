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
│   │   ├── market_analyzer.md
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

### API 接口

```bash
# 重置状态为初始值
curl -X POST http://localhost:8088/api/reset
```

## 历史数据

所有指标数据自动保存到 `data/history/` 目录，服务重启后可查看历史记录。

### 历史数据 API

```bash
# 获取 F&G 指数历史 (最近100条)
curl http://localhost:8088/api/history/fear_greed

# 获取最近7天的资金费率
curl "http://localhost:8088/api/history/funding_rate?days=7"

# 获取巨鲸流向统计
curl http://localhost:8088/api/history/whale_netflow/stats

# 获取 BTC 价格历史
curl "http://localhost:8088/api/history/btc_price?limit=500"

# 清除某类历史数据
curl -X DELETE http://localhost:8088/api/history/fear_greed

# 清除所有历史数据
curl -X DELETE http://localhost:8088/api/history/all
```

### 支持的数据类型

| 类型 | 说明 | 存储文件 |
|------|------|---------|
| `fear_greed` | F&G 指数 | `data/history/fear_greed.json` |
| `funding_rate` | 资金费率 | `data/history/funding_rate.json` |
| `whale_netflow` | 巨鲸净流向 | `data/history/whale_netflow.json` |
| `btc_price` | BTC 价格 | `data/history/btc_price.json` |

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
| 移动止盈倍数 | 0.5 |
| 做空杠杆 | 2x |
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
| 移动止盈倍数 | 0.5 |
| 做空杠杆 | 2x |
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
| 单次最大亏损 | 2.5% 权益 |
| ATR止损倍数 | 1.2 (止损紧，容易被震出) |
| 移动止盈倍数 | 0.5 |
| 做空杠杆 | 2x |
| 做多杠杆 | 10x |

### 开仓与平仓分离

- **信号决定开仓**: 三灯全绿时开仓，信号变化不会平掉现有仓位
- **止损/止盈决定平仓**: 仓位由价格驱动的止损和止盈管理

### 止盈止损机制

```
阶段1 (TP1前):
  止损价 = 入场价 - ATR × ATR止损倍数
  TP1价  = 入场价 + ATR × ATR止损倍数 (1:1 盈亏比)

阶段2 (TP1后 → 移动止盈):
  TP1 触发 → 平掉 50% 仓位，启动移动止盈
  trailing_stop = 最高价 - ATR × 移动止盈倍数
  止盈线只会往有利方向移动，永远不回退
  价格跌破止盈线 → 平掉剩余 50%
```

### 示例 (Standard, ATR ≈ $980)

```
开多 @ $69,000
  止损 = $69,000 - $1,470 = $67,530
  TP1  = $69,000 + $1,470 = $70,470

涨到 $70,470 → TP1: 平 50%, trailing_dist = $490
  止盈线 = $70,470 - $490 = $69,980

继续涨到 $72,000:
  止盈线上移 = $72,000 - $490 = $71,510

回落到 $71,510 → 移动止盈触发: 平剩余 50%
```
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

