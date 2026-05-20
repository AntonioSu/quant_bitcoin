---
name: crypto-trading-system
description: 构建生产级加密货币交易系统，支持双策略执行（对冲+方向性）、多数据源集成和健壮的错误处理。适用于开发加密货币交易机器人、集成 Binance API、构建实时监控仪表板，或处理恐惧与贪婪指数、资金费率和巨鲸追踪等场景。
---

# 加密货币交易系统开发

基于 stock_btc 项目的经验构建可靠的加密货币交易系统：一个具有实时监控和状态持久化的 BTC 双策略交易系统。

## 架构概览

### 核心组件

```
交易系统
├── 核心策略层
│   ├── 信号聚合器（多指标融合）
│   ├── Aegis 执行器（对冲模式 - 做空 + 资金费率）
│   └── Spear 执行器（方向性 - 做多 + ATR 止损）
├── 数据源层
│   ├── 恐惧与贪婪指数
│   ├── 资金费率（Binance）
│   └── 巨鲸预警 / 链上数据
├── 交易所集成
│   ├── 现货交易
│   └── 合约交易（杠杆）
├── 状态管理
│   ├── 基于 JSON 的持久化
│   └── 线程安全操作
└── 监控与控制
    ├── FastAPI 后端
    ├── WebSocket 实时推送
    └── Web 仪表板
```

### 设计原则

1. **关注点分离**：数据源、指标、执行器和 API 服务器彼此隔离
2. **状态持久化**：所有关键状态保存到 JSON，可在重启后恢复
3. **防御性编程**：重试逻辑、超时处理、用于测试的模拟模式
4. **实时监控**：WebSocket 推送实现即时反馈

## 常见问题与解决方案

### 1. API 请求失败

**问题**：外部 API（恐惧与贪婪指数、Binance）超时或被限流

**解决方案**：实现带指数退避的重试装饰器

```python
def retry_request(max_retries: int = 3, delay: float = 1.0):
    """请求重试装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        logger.warning(f"{func.__name__} failed (attempt {attempt + 1}/{max_retries}): {e}")
                        time.sleep(delay * (attempt + 1))  # 指数退避
            logger.error(f"{func.__name__} final failure: {last_error}")
            raise last_error
        return wrapper
    return decorator
```

**要点**：
- 使用指数退避：`delay * (attempt + 1)`
- 记录每次重试尝试以便调试
- 在抛出异常前始终记录最终错误

### 2. 状态持久化与恢复

**问题**：服务重启导致交易状态丢失（仓位、余额、历史记录）

**解决方案**：线程安全的 JSON 状态存储

```python
class StateStore:
    """线程安全的 JSON 状态存储"""
    
    def __init__(self, filepath: str):
        self.filepath = filepath
        self._lock = Lock()
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    def save(self, state: Dict[str, Any]) -> bool:
        with self._lock:
            try:
                state["_saved_at"] = datetime.now().isoformat()
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2, ensure_ascii=False)
                return True
            except Exception as e:
                logger.error(f"Save state failed: {e}")
                return False
    
    def load(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not os.path.exists(self.filepath):
                return None
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
```

**关键特性**：
- 使用 `Lock()` 保证线程安全（对 FastAPI + 后台调度器至关重要）
- 自动创建目录结构
- 添加时间戳便于调试
- 优雅降级（文件不存在时返回 None）

**需要持久化的内容**：
```json
{
  "spot_usdt": 50000,
  "futures_usdt": 50000,
  "btc_spot": 0.5,
  "futures_position": {
    "symbol": "BTCUSD_PERP",
    "side": "short",
    "size": 1.0,
    "entry_price": 65000
  },
  "trades": [],
  "aegis_state": "active",
  "_saved_at": "2026-03-15T23:04:32"
}
```

### 3. Binance API 集成

**问题**：多种 API（现货、合约、USDⓈ-M vs Coin-M）、认证、错误码

**解决方案**：使用 CCXT 库结合基类模式

```python
class BinanceBase:
    """Binance 集成基类"""
    
    def __init__(self):
        config = self._load_config()
        self.exchange = ccxt.binance({
            'apiKey': config['binance']['api_key'],
            'secret': config['binance']['secret_key'],
            'enableRateLimit': True,  # 关键：自动限流
            'options': {
                'defaultType': 'spot',  # 或 'future'
                'adjustForTimeDifference': True  # 处理时钟偏差
            }
        })
        
        if config['binance'].get('testnet'):
            self.exchange.set_sandbox_mode(True)
```

**关键配置**：
- `enableRateLimit: True` - 自动节流请求
- `adjustForTimeDifference: True` - 修复时间戳错误
- 开发环境使用测试网

**常见错误处理**：
```python
try:
    result = exchange.create_order(...)
except ccxt.InsufficientFunds:
    logger.error("余额不足")
except ccxt.InvalidOrder as e:
    logger.error(f"无效订单: {e}")
except ccxt.NetworkError:
    logger.error("网络错误，重试中...")
except ccxt.ExchangeError as e:
    logger.error(f"交易所错误: {e}")
```

### 4. 多数据源同步

**问题**：恐惧与贪婪指数每日更新、资金费率每 8 小时更新、巨鲸数据实时更新 - 如何协调？

**解决方案**：带缓存的异步数据获取

```python
class DataSource:
    """外部数据源基类"""
    
    def __init__(self):
        self._cache = None
        self._cache_time = None
        self._cache_ttl = 300  # 5 分钟
    
    @retry_request(max_retries=3)
    def fetch(self) -> Any:
        """带缓存的数据获取"""
        now = time.time()
        if self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache
        
        data = self._fetch_impl()  # 在子类中实现
        self._cache = data
        self._cache_time = now
        return data
    
    def _fetch_impl(self) -> Any:
        raise NotImplementedError
```

**调度器模式**（FastAPI 后台任务）：
```python
@app.on_event("startup")
async def start_scheduler():
    scheduler = BackgroundScheduler()
    
    # 每 1 小时更新恐惧与贪婪指数
    scheduler.add_job(update_fear_greed, 'interval', hours=1)
    
    # 每 5 分钟更新资金费率
    scheduler.add_job(update_funding_rate, 'interval', minutes=5)
    
    # 每 10 分钟检查巨鲸净流入
    scheduler.add_job(update_whale_data, 'interval', minutes=10)
    
    scheduler.start()
```

### 5. 日志最佳实践

**问题**：没有良好的日志，生产环境调试几乎不可能

**解决方案**：带 Emoji 标识的结构化日志

```python
import logging
from datetime import datetime

def setup_logger(name: str, log_file: str = None):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 控制台处理器
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    
    # 按天轮转的文件处理器
    if log_file:
        from logging.handlers import TimedRotatingFileHandler
        file_handler = TimedRotatingFileHandler(
            log_file, when='midnight', backupCount=7
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger
```

**Emoji 约定**（方便快速扫描日志）：
```python
logger.info(f"🛡️ Aegis 模式激活 @ ${price:,.0f}")
logger.info(f"🗡️ Spear 模式：买入 {amount:.4f} BTC")
logger.info(f"📊 恐惧与贪婪指数: {value} ({classification})")
logger.info(f"💰 资金费率: {rate:.4f}%")
logger.info(f"🐋 巨鲸净流入: {flow:+,.0f} BTC")
logger.warning(f"⚠️ 检测到高波动性")
logger.error(f"❌ 订单失败: {error}")
```

### 6. 无需真金白银的测试

**问题**：无法用真钱测试策略

**解决方案**：模拟模式 + 测试网

```python
class WhaleAlert:
    def __init__(self, mock_mode: bool = True):
        self.mock_mode = mock_mode
    
    def get_netflow(self) -> float:
        if self.mock_mode:
            # 生成逼真的测试数据
            return random.uniform(-3000, 3000)
        else:
            return self._fetch_real_data()
```

**测试网配置**：
```json
{
  "binance": {
    "api_key": "YOUR_TESTNET_KEY",
    "secret_key": "YOUR_TESTNET_SECRET",
    "testnet": true
  }
}
```

**测试清单**：
- [ ] 所有数据源在模拟模式下正常工作
- [ ] 状态持久化在进程重启后可恢复
- [ ] WebSocket 连接支持自动重连
- [ ] 订单在测试网上无错误执行
- [ ] 日志记录所有关键事件

## 项目结构模板

```
crypto_trading_system/
├── config/
│   └── config.json           # API 密钥、阈值
├── core/
│   ├── config.py             # 参数预设
│   ├── signal_aggregator.py  # 多指标逻辑
│   ├── aegis_executor.py     # 对冲策略
│   ├── spear_executor.py     # 方向性策略
│   ├── portfolio.py          # 资产管理
│   └── trading_engine.py     # 主协调器
├── data_sources/
│   ├── base.py               # DataSource 基类
│   ├── fear_greed.py
│   ├── funding_rate.py
│   └── whale_alert.py
├── indicators/
│   ├── atr.py                # ATR 止损
│   └── cvd_divergence.py     # 成交量背离
├── binance_utils/
│   ├── binance_base.py       # 基础客户端
│   ├── binance_client.py     # 现货交易
│   └── binance_futures_adapter.py
├── server/
│   ├── api.py                # FastAPI 接口
│   ├── scheduler.py          # 后台任务
│   ├── state_store.py        # JSON 持久化
│   └── history_store.py      # 历史数据
├── web/
│   └── index.html            # 监控仪表板
├── data/
│   ├── trading_state.json
│   └── history/
├── logs/
│   └── trading_YYYY-MM-DD.log
├── utils/
│   ├── log_util.py
│   └── common_utils.py       # 重试、辅助函数
├── requirements.txt
├── run_server.py
└── README.md
```

## 核心依赖

```txt
# 交易所集成
ccxt>=4.0.0

# Web 框架
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
websockets>=12.0

# 调度
apscheduler>=3.10.4

# 数据获取
requests>=2.31.0
aiohttp>=3.9.0

# 工具库
python-dotenv>=1.0.0
pydantic>=2.5.0
```

## 开发工作流

### 第一阶段：核心策略（第 1-2 周）
1. 实现带缓存的数据源基类
2. 构建各数据源（恐惧与贪婪指数、资金费率、巨鲸）
3. 创建信号聚合器（组合多个指标）
4. 使用模拟数据测试信号生成

### 第二阶段：执行层（第 2-3 周）
1. 配置 Binance 测试网凭证
2. 实现现货 + 合约适配器
3. 构建执行器（aegis + spear）
4. 在测试网上测试订单执行

### 第三阶段：状态管理（第 3-4 周）
1. 实现线程安全的 StateStore
2. 每次操作后添加状态保存
3. 测试重启后的恢复
4. 实现 HistoryStore 用于数据分析

### 第四阶段：监控（第 4-5 周）
1. 构建 FastAPI 后端
2. 添加 WebSocket 实时推送
3. 创建 Web 仪表板（HTML + Chart.js）
4. 集成 APScheduler 后台任务

### 第五阶段：生产加固（第 5-6 周）
1. 添加全面的错误处理
2. 实现限流
3. 配置日志轮转
4. 编写部署脚本
5. 使用类生产环境流量进行压力测试

## 必须避免的关键陷阱

### 0. 前端 JS 变量名冲突
**问题**：在 `indicators.js` 中添加新的指标卡片时，复用了已存在于同一函数作用域中的变量名（如 `const pcEl`）→ `SyntaxError: Identifier has already been declared` → **整个脚本在浏览器中静默失败**，所有指标卡片显示"--"

**症状**：
- 所有指标卡片显示"--"，即使 API 返回了有效数据
- K 线图可能仍能正常渲染（由不同的 JS 文件加载）
- 服务器日志中没有明显错误（200 OK 响应）
- 浏览器控制台显示：`SyntaxError: Identifier 'xxx' has already been declared`

**解决方案**：
1. 始终使用唯一的变量名（以功能名为前缀：`optPcEl`、`nfEl`、`scEl`）
2. 编辑 JS 文件后，运行 `node --check web/js/indicators.js` 检查语法错误
3. 每次修改 JS 后更新 `index.html` 中的 `?v=` 缓存破坏版本号
4. 为 `index.html` 的 FileResponse 添加 `Cache-Control: no-cache` 响应头

**前端修改预防清单**：
```bash
# 1. 语法检查
node --check web/js/indicators.js

# 2. 更新 index.html 中的版本号
# 将 ?v=20260520a 改为 ?v=20260520b

# 3. 重启服务
bash bin/restart.sh
```

### 1. 时钟同步
**问题**：Binance 拒绝订单并报错"Timestamp for this request is outside of the recvWindow"

**解决方案**：在 CCXT 选项中使用 `adjustForTimeDifference: True`

### 2. 线程安全
**问题**：FastAPI 后台任务 + 调度器 = 竞态条件

**解决方案**：共享状态始终使用 `Lock()`（StateStore、HistoryStore）

### 3. 小数精度
**问题**：Binance 拒绝精度错误的订单（如只允许 0.1234 BTC 时提交了 0.123456 BTC）

**解决方案**：查询交易所信息并正确四舍五入
```python
market = exchange.market(symbol)
amount = round(amount, market['precision']['amount'])
```

### 4. 仓位追踪
**问题**：重启后无法确定仓位是否仍然持有

**解决方案**：启动时始终查询交易所
```python
def reconcile_state():
    """重启后与交易所同步状态"""
    local_state = state_store.load()
    exchange_positions = exchange.fetch_positions()
    
    if local_state['position'] != exchange_positions:
        logger.warning("检测到状态不一致，正在同步...")
        # 从交易所更新本地状态
```

### 5. 密钥管理
**问题**：API 密钥被提交到 Git

**解决方案**：使用环境变量 + `.gitignore`
```python
from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv('BINANCE_API_KEY')
secret = os.getenv('BINANCE_SECRET_KEY')
```

## 性能优化

### 1. 批量 API 调用
```python
# 不推荐：多次请求
prices = {}
for symbol in symbols:
    prices[symbol] = exchange.fetch_ticker(symbol)

# 推荐：单次批量请求
prices = exchange.fetch_tickers(symbols)
```

### 2. 使用 WebSocket 获取实时数据
对于高频更新，使用 WebSocket 替代 REST 轮询：
```python
import asyncio
from binance import AsyncClient, BinanceSocketManager

async def price_stream():
    client = await AsyncClient.create(api_key, api_secret)
    bm = BinanceSocketManager(client)
    
    async with bm.trade_socket('BTCUSDT') as stream:
        while True:
            msg = await stream.recv()
            await broadcast_to_clients(msg)
```

### 3. 使用数据库存储历史数据
生产环境中，用 TimescaleDB 替代 JSON 历史文件：
```sql
CREATE TABLE price_history (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    price NUMERIC NOT NULL,
    volume NUMERIC
);

SELECT create_hypertable('price_history', 'time');
```

## 监控与告警

### 健康检查接口
```python
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "uptime": time.time() - startup_time,
        "last_update": last_update_time,
        "data_sources": {
            "fear_greed": check_data_source("fear_greed"),
            "funding_rate": check_data_source("funding_rate"),
            "whale_alert": check_data_source("whale_alert")
        }
    }
```

### 飞书/Slack 告警
```python
def send_alert(message: str, level: str = "info"):
    """发送告警到飞书 Webhook"""
    webhook_url = config['flybook_webhook_url']
    
    color_map = {
        "info": "blue",
        "warning": "yellow",
        "error": "red"
    }
    
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": f"交易告警 [{level.upper()}]"}},
            "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": message}}]
        }
    }
    
    requests.post(webhook_url, json=payload)
```

## 部署清单

- [ ] 环境变量已配置
- [ ] 测试网交易端到端验证通过
- [ ] 状态持久化经过服务重启测试
- [ ] 日志配置了按天轮转
- [ ] 健康检查接口正常响应
- [ ] WebSocket 重连已测试
- [ ] 限流已验证
- [ ] 告警 Webhook 已测试
- [ ] 状态文件有备份策略
- [ ] 监控仪表板可访问

## 附加资源

更多详情请参阅：
- [reference.md](reference.md) - 完整 API 文档
- [examples.md](examples.md) - 完整代码示例
- [troubleshooting.md](troubleshooting.md) - 常见问题与修复
