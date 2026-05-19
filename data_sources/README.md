# data_sources 数据源文档

系统所有外部数据源的 API 说明、请求格式、输出结构和环境变量配置。

---

## 目录

| 模块 | 数据源 | API 提供方 | 需要 API Key |
|------|--------|-----------|:------------:|
| [fear_greed.py](#fear_greedpy) | 恐惧贪婪指数 | Alternative.me | 否 |
| [funding_rate.py](#funding_ratepy) | 资金费率 | Binance | 否 |
| [top_trader.py](#top_traderpy) | 聪明钱多空比 | Binance | 否 |
| [etf_flow.py](#etf_flowpy) | ETF 资金流 | SoSoValue | 可选 |
| [open_interest.py](#open_interestpy) | 合约未平仓量 | Binance | 否 |
| [liquidation.py](#liquidationpy) | 爆仓数据 | Binance WebSocket | 否 |
| [crypto_news.py](#crypto_newspy) | 加密新闻 | CryptoPanic / RSS | 可选 |
| [whale_alert.py](#whale_alertpy) | 巨鲸链上净流 | CryptoQuant / Bitquery | 是 |
| [btc_taker_kline.py](#btc_taker_klinepy) | Taker 买卖量 | Binance K线 | 否 |
| [btc_whale_realtime.py](#btc_whale_realtimepy) | 实时大额交易 | Binance WebSocket | 否 |

---

## fear_greed.py

**类名:** `FearGreedIndex` / **缓存:** 3600s

### API

```
GET https://api.alternative.me/fng/?limit=1
```

### 请求参数

| 参数 | 值 | 说明 |
|------|-----|------|
| limit | 1 | 返回最新一条 |

### 响应示例

```json
{
  "data": [{
    "value": "25",
    "value_classification": "Extreme Fear",
    "timestamp": "1716076800",
    "time_until_update": "43200"
  }]
}
```

### 输出 (DataPoint)

| 字段 | 说明 |
|------|------|
| `value` | 指数值 0-100 (0=极度恐惧, 100=极度贪婪) |
| `raw.classification` | 分类: Extreme Fear / Fear / Neutral / Greed / Extreme Greed |
| `raw.time_until_update` | 距下次更新秒数 |

### 环境变量

无 (公开 API)

---

## funding_rate.py

**类名:** `FundingRate` / **缓存:** 60s

### API

| 类型 | U本位 | 币本位 |
|------|-------|--------|
| 实时 | `GET https://fapi.binance.com/fapi/v1/premiumIndex` | `GET https://dapi.binance.com/dapi/v1/premiumIndex` |
| 历史 | `GET https://fapi.binance.com/fapi/v1/fundingRate` | `GET https://dapi.binance.com/dapi/v1/fundingRate` |

根据 symbol 后缀自动选择: `BTCUSDT` → U本位, `BTCUSD_PERP` → 币本位

### 请求参数

| 参数 | 值 | 说明 |
|------|-----|------|
| symbol | BTCUSDT | 交易对 |
| limit | 100 | 历史条数 (仅历史接口) |

### 响应示例 (premiumIndex)

```json
{
  "symbol": "BTCUSDT",
  "lastFundingRate": "0.00020000",
  "nextFundingTime": 1716091200000,
  "markPrice": "67500.00",
  "indexPrice": "67450.00"
}
```

### 输出 (DataPoint)

| 字段 | 说明 |
|------|------|
| `value` | 费率百分比 (如 0.02 表示 0.02%) |
| `raw.last_rate` | 原始费率 (0.0002) |
| `raw.annual_yield` | 年化收益率 = rate × 3 × 365 |
| `raw.next_funding_time` | 下次结算时间 |
| `raw.mark_price` | 标记价格 |

### 环境变量

无 (公开 API)

---

## top_trader.py

**类名:** `TopTraderRatio` / **缓存:** 300s

### API

```
GET https://fapi.binance.com/futures/data/topLongShortAccountRatio
```

### 请求参数

| 参数 | 值 | 说明 |
|------|-----|------|
| symbol | BTCUSDT | 交易对 |
| period | 1h | 周期: 5m/15m/30m/1h/2h/4h/6h/12h/1d |
| limit | 1 | 返回条数 |

### 响应示例

```json
[{
  "symbol": "BTCUSDT",
  "longAccount": "0.6200",
  "shortAccount": "0.3800",
  "longShortRatio": "1.6316",
  "timestamp": 1716076800000
}]
```

### 输出 (DataPoint)

| 字段 | 说明 |
|------|------|
| `value` | 多空比 (>1 多头主导, <1 空头主导) |
| `raw.long_account` | 多头账户占比 |
| `raw.short_account` | 空头账户占比 |

### 环境变量

无 (公开 API)

---

## etf_flow.py

**类名:** `ETFFlow` / **缓存:** 1800s

### API

```
GET https://openapi.sosovalue.com/openapi/v1/etfs/summary-history
```

文档: https://sosovalue.gitbook.io/soso-value-api-doc/2.-etf/summary-history

### 请求参数

| 参数 | 值 | 说明 |
|------|-----|------|
| symbol | BTC | 币种 |
| country_code | US | 国家 |
| limit | 30 | 返回天数 (免费版最多约21个交易日) |

### 请求头

```
Accept: application/json
x-soso-api-key: <SOSOVALUE_API_KEY>  (可选)
```

### 响应示例

```json
{
  "code": 0,
  "data": [{
    "date": "2026-05-18",
    "total_net_inflow": -648640566.63,
    "total_value_traded": 3140216542.49,
    "total_net_assets": 100485432785.17,
    "cum_net_inflow": 57691368283.94
  }]
}
```

### 输出 (DataPoint)

| 字段 | 说明 |
|------|------|
| `value` | 当日净流入 (USD) |
| `raw.daily_flow_usd` | 当日净流入 |
| `raw.flow_3d_usd` | 近3日累计净流入 |
| `raw.flow_7d_usd` | 近7日累计净流入 |
| `raw.cum_flow_usd` | 历史累计净流入 |
| `raw.total_net_assets_usd` | ETF 总资产规模 |
| `raw.streak_days` | 连续流入/流出天数 (正=流入, 负=流出) |

### 本地历史数据

**文件:** `data/etf_flow_history.json`

SoSoValue 免费 API 仅返回最近约 21 个交易日，无法获取完整历史。因此采用**本地存储 + API 增量更新**的混合方案：

#### 历史数据来源: farside.co.uk

```
GET https://farside.co.uk/bitcoin-etf-flow-all-data/
```

这是业界最常引用的免费 BTC ETF 资金流公开数据源，提供从 ETF 上市 (2024-01-11) 至今的每日净流入流出数据。

**页面结构:** HTML `<table class="etf">`, 每行一个交易日, 列依次为:

| 列 | 内容 | 说明 |
|----|------|------|
| 0 | Date | 日期 (格式: `11 Jan 2024`) |
| 1-12 | IBIT, FBTC, BITB, ARKB, BTCO, EZBC, BRRR, HODL, BTCW, MSBT, GBTC, BTC | 各 ETF 基金当日净流入 (百万美元) |
| 13 | Total | 全部 ETF 合计 |

- 正数 `655.3` 表示净流入 +$655.3M
- 括号 `(95.1)` 表示净流出 -$95.1M (英美财务惯例)
- `-` 表示当日无数据或基金未上市

**爬取解析方式:** 通过 `requests` + 正则解析 HTML 表格, 转换为标准 JSON 格式。

#### 本地 JSON 格式

```json
[
  {
    "date": "2024-01-11",
    "daily_flow": 655300000.0,
    "daily_flow_m": 655.3,
    "cum_flow": 655300000.0,
    "etf_flows": {
      "IBIT": 111700000.0,
      "FBTC": 227000000.0,
      "GBTC": -95100000.0,
      ...
    }
  }
]
```

| 字段 | 说明 |
|------|------|
| `daily_flow` | 当日全部 ETF 合计净流入 (USD) |
| `daily_flow_m` | 同上, 百万美元单位 |
| `cum_flow` | 从首日起的累计净流入 (USD) |
| `etf_flows` | 各 ETF 基金的分项净流入 (USD) |

#### 运行时增量更新

每次访问 `GET /api/etf-flow` 时自动执行:

```
① 读取本地 data/etf_flow_history.json
      ↓
② 调 SoSoValue API 拉最近 60 天数据
      ↓
③ 按日期对比, 将本地没有的新交易日追加写入 JSON
      ↓
④ 合并去重后返回前端 (newest first)
```

#### 数据规模

- 初始爬取: **605 个交易日** (2024-01-11 ~ 2026-05-18)
- 文件大小: ~120KB
- 每天自动增长 1 条 (~200 字节)

### 环境变量

| 变量 | 必需 | 说明 |
|------|:----:|------|
| `SOSOVALUE_API_KEY` | 可选 | 无 key 也能用，有 key 限额更高 |
| `HTTPS_PROXY` | 可选 | 代理地址 |

---

## open_interest.py

**类名:** `OpenInterest` / **缓存:** 60s

### API

| 用途 | URL |
|------|-----|
| 实时 OI | `GET https://fapi.binance.com/fapi/v1/openInterest` |
| 历史 OI | `GET https://fapi.binance.com/futures/data/openInterestHist` |

### 请求参数

| 参数 | 值 | 说明 |
|------|-----|------|
| symbol | BTCUSDT | 交易对 |
| period | 4h | 历史周期: 5m/15m/30m/1h/2h/4h/6h/12h/1d |
| limit | 30 | 历史条数 |

### 响应示例 (历史)

```json
[{
  "symbol": "BTCUSDT",
  "sumOpenInterest": "75000.000",
  "sumOpenInterestValue": "5062500000.00",
  "timestamp": 1716076800000
}]
```

### 输出 (DataPoint)

| 字段 | 说明 |
|------|------|
| `value` | OI 价值 (USD) |
| `raw.oi_contracts` | OI 合约数量 (BTC) |
| `raw.oi_value_usd` | OI 价值 (USD) |
| `raw.change_pct_1h` | 1小时变化 % |
| `raw.change_pct_4h` | 4小时变化 % |
| `raw.change_pct_24h` | 24小时变化 % |

### 环境变量

无 (公开 API)

---

## liquidation.py

**类名:** `Liquidation` + `_LiquidationCollector` (WebSocket) / **缓存:** 60s

### WebSocket

```
wss://fstream.binance.com:9443/ws/!forceOrder@arr   (直连)
wss://fstream.binance.com/ws/!forceOrder@arr         (有代理时)
```

### 消息格式

```json
{
  "e": "forceOrder",
  "o": {
    "s": "BTCUSDT",
    "S": "SELL",
    "p": "67500.00",
    "q": "0.500",
    "T": 1716076800000
  }
}
```

- `S=SELL` → 多头被爆仓 (long liquidation)
- `S=BUY` → 空头被爆仓 (short liquidation)

### 输出 (DataPoint)

| 字段 | 说明 |
|------|------|
| `value` | 窗口内爆仓总额 (USD) |
| `raw.long_liquidation_usd` | 多头爆仓金额 |
| `raw.short_liquidation_usd` | 空头爆仓金额 |
| `raw.long_short_ratio` | 多空爆仓比 |
| `raw.total_count` | 爆仓笔数 |
| `raw.max_single_usd` | 最大单笔爆仓 |
| `raw.ws_connected` | WebSocket 连接状态 |

### 环境变量

| 变量 | 必需 | 说明 |
|------|:----:|------|
| `HTTPS_PROXY` | 可选 | 有代理时切换到 443 端口 |

---

## crypto_news.py

**类名:** `CryptoNewsSentiment` / **缓存:** 900s

### API (优先 CryptoPanic, 无 key 则用 RSS)

| 来源 | URL |
|------|-----|
| CryptoPanic | `GET https://cryptopanic.com/api/v1/posts/?auth_token=<key>&currencies=BTC&kind=news&limit=20` |
| CoinTelegraph RSS | `GET https://cointelegraph.com/rss` |
| Decrypt RSS | `GET https://decrypt.co/feed` |

### 输出 (DataPoint)

| 字段 | 说明 |
|------|------|
| `value` | 新闻条数 |
| `raw.news_count` | 新闻总数 |
| `raw.headlines` | 前 5 条标题 |
| `raw.articles` | `[{title, url, summary}, ...]` 最多 20-30 条 |

> 注: 此模块只获取新闻原文, 情感分析由 `indicators/news_analyzer.py` 通过 LLM 完成。

### 环境变量

| 变量 | 必需 | 说明 |
|------|:----:|------|
| `CRYPTOPANIC_API_KEY` | 可选 | 有则用 CryptoPanic, 无则降级 RSS |

---

## whale_alert.py

**类名:** `WhaleAlert` / **缓存:** 1800s

> ⚠️ 当前仅在 `tests/` 中使用，未接入主数据刷新流程。

### API

| Provider | URL | 方法 |
|----------|-----|------|
| CryptoQuant | `https://api.cryptoquant.com/v1/btc/exchange-flows/netflow` | GET |
| Bitquery | `https://streaming.bitquery.io/graphql` | POST (GraphQL) |

### CryptoQuant 请求

```
GET https://api.cryptoquant.com/v1/btc/exchange-flows/netflow
  ?exchange=all_exchange&window=day&limit=1

Headers:
  Authorization: Bearer <CRYPTOQUANT_API_KEY>
```

### Bitquery 请求

```
POST https://streaming.bitquery.io/graphql

Headers:
  Authorization: Bearer <BITQUERY_API_KEY>
  Content-Type: application/json

Body: GraphQL 查询 (EVM.Transfers, 监控 Binance 钱包地址,
      BTCB 合约 0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c)
```

### 输出 (DataPoint)

| 字段 | 说明 |
|------|------|
| `value` | 交易所净流入 (BTC) |
| `raw.inflow` | 流入量 |
| `raw.outflow` | 流出量 |
| `raw.netflow` | 净流入 (正=卖压, 负=囤币) |

### 环境变量

| 变量 | 必需 | 说明 |
|------|:----:|------|
| `CRYPTOQUANT_API_KEY` | CryptoQuant 必需 | |
| `BITQUERY_API_KEY` | Bitquery 必需 | |

---

## btc_taker_kline.py

**类名:** `TakerAnalyzer` / **无缓存** (从 K线数据计算)

### 数据来源

不直接调 API。由 `core/market_data.py` 传入 Binance K线数据，使用 K线字段:
- `kline[5]` — 总成交量 (BTC)
- `kline[9]` — 主动买入量 (BTC)

```
原始数据源: GET https://api.binance.com/api/v3/klines
            ?symbol=BTCUSDT&interval=4h&limit=50
```

### 输出 (TakerData)

| 字段 | 说明 |
|------|------|
| `taker_buy_btc` | 主动买入量 (BTC) |
| `taker_sell_btc` | 主动卖出量 (BTC) |
| `total_volume_btc` | 总成交量 |
| `buy_sell_ratio` | 买卖比 (>1 买方主导) |
| `buy_ratio_pct` | 买入占比 % |

### 环境变量

无

---

## btc_whale_realtime.py

**独立监控脚本** (不继承 DataSourceBase, 未接入主流程)

### WebSocket

```
wss://stream.binance.com:9443/ws/btcusdt@aggTrade   (直连)
wss://stream.binance.com/ws/btcusdt@aggTrade         (有代理时)
```

### 配置常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `WHALE_THRESHOLD` | 10.0 BTC | 大于此值视为大单 |
| `MAX_RECORDS` | 100 | 最近大单记录数 |

### 消息字段

| 字段 | 说明 |
|------|------|
| `q` | 成交量 (BTC) |
| `p` | 成交价 |
| `T` | 时间戳 (ms) |
| `m` | `true`=主动卖出, `false`=主动买入 |

### 环境变量

| 变量 | 必需 | 说明 |
|------|:----:|------|
| `HTTPS_PROXY` | 可选 | 代理地址 |

---

## 环境变量汇总

```bash
# ETF 资金流 (SoSoValue)
SOSOVALUE_API_KEY=SOSO-xxx          # 可选

# 新闻 (CryptoPanic)
CRYPTOPANIC_API_KEY=xxx             # 可选, 无则用 RSS

# 巨鲸链上 (未接入主流程)
CRYPTOQUANT_API_KEY=xxx             # whale_alert CryptoQuant
BITQUERY_API_KEY=xxx                # whale_alert Bitquery

# 代理
HTTPS_PROXY=http://proxy:port       # 多个模块使用
```

> Binance 相关接口 (funding_rate, top_trader, open_interest, liquidation, klines) 均为公开 API, 无需 key。
