# quant_bitcoin 问题总结与修复说明

## 一、核心交易逻辑 Bug

### 1. 重复开仓 (多次开空/开多叠加)
- **现象**: Demo 盘出现已持空仓时又开新空仓，导致多次 ~500U 的重复开单
- **涉及文件**: `live_scheduler.py`, `sim_scheduler.py`
- **修复**: 在 `_open_long` / `_open_short` 入口加 `if self.position.is_active: return None` 守卫

### 2. 平仓后同 tick 立即重新开仓
- **现象**: 同一个调度周期内先平仓、紧接着又满足条件重新开仓
- **涉及文件**: `base.py` (`check_and_execute`)
- **修复**: 增加 `just_closed` 标记 + `OPEN_COOLDOWN_SEC` 冷却时间 + `_last_open_ts` 时间戳

### 3. 重启后不再开仓 (current_mode 恢复错误)
- **现象**: 重启后状态文件 `current_mode=short` 但无实际持仓，导致信号判断 `signal.mode == current_mode`，永远不触发新开仓
- **涉及文件**: `base.py` (`_apply_position_state`)
- **修复**: 恢复状态时，若 `position` 不活跃，则强制 `current_mode = IDLE`，使新信号能正常触发

### 4. 资金上限未执行
- **现象**: `max_capital=500` 但 Demo 虚拟余额很大，可能连续开多个 500U 仓位
- **涉及文件**: `live_scheduler.py`
- **修复**: 增加 `_check_capital_guard()` 检查，基于交易所同步余额 + `max_capital` 限制

---

## 二、API / 网络可靠性问题

### 5. Spot Ticker API 失败导致止损/止盈不生效
- **现象**: 代理 503 / 连接失败时 `fetch_price` 返回 0，止损逻辑被跳过
- **涉及文件**: `base.py`, `live_scheduler.py`
- **修复**: 先调用 `_sync_position()` 再获取价格；`fetch_price ≤ 0` 时走 `_btc_price_fallback()` 回退到期货 mark_price

### 6. get_portfolio 异常被误判为"空仓"
- **现象**: API 异常时 `get_portfolio` 返回空字典，`_sync_position` 以为交易所无仓位，把本地仓位清零
- **涉及文件**: `binance_adapter.py` (`get_portfolio`), `live_scheduler.py` (`_sync_position`)
- **修复**: 异常时返回带 `_error: True` 标记的字典；`_sync_position` 遇 error 标记不重置本地状态

### 7. 双向持仓模式设置失败
- **现象**: 启动时报 `binance markets not loaded`
- **涉及文件**: `binance_adapter.py`
- **修复**: 捕获异常后仅 warning，不阻塞启动（后续 load_markets 成功后自然生效）

---

## 三、交易所级止损/止盈增强

### 8. 仅依赖轮询止损，网络中断时漏止损
- **现象**: 60s 轮询间隔 + 网络不稳，价格可能远超止损位才被发现
- **涉及文件**: `binance_adapter.py`, `base.py`, `live_scheduler.py`
- **修复**: 新增交易所侧 `STOP_MARKET` / `TAKE_PROFIT_MARKET` 委托单，增加 `sl_order_id` / `tp1_order_id` 管理，开/平/同步时自动创建/取消/替换

---

## 四、信号层问题

### 9. 做空信号缺少顶背离 (CVD Bearish Divergence)
- **现象**: `check_short_conditions` 没有使用 CVD 顶背离作为做空条件之一
- **涉及文件**: `core/signal_aggregator.py`
- **修复**: 在 `check_short_conditions` 中加入 `DivergenceType.BEARISH` 检查，扩展 values 和 ok_reason

### 10. Dashboard 只显示底背离，缺少顶背离
- **现象**: API 仅暴露 `has_bullish_divergence`，前端没有顶背离显示
- **涉及文件**: `server/api.py`, `web/index.html`, `web/app.js`
- **修复**: 增加 `has_bearish_divergence` 字段；UI 分别显示 底背离(绿) / 顶背离(红) / 无背离

---

## 五、代码重构 / 工程化问题

### 11. `bin/` 目录迁移后路径全部断裂
- **现象**: 日志目录、`.env` 路径、模块路径、测试 `sys.path` 全部指向旧位置
- **涉及文件**: `bin/run_daemon.sh`, `bin/commit.sh`, `bin/run_server.py`, `tests/test_*.py`
- **修复**: 引入 `PROJECT_DIR`；`commit.sh` 修正 `cd`；`load_env()` 指向 `bin/` 上级；添加 `bin/__init__.py`；daemon 改用 `python -m bin.run_server`

### 12. 冗余 helper `get_fear_greed_class()` 函数
- **现象**: 重复了 `market.fear_greed.raw` 已有的逻辑
- **涉及文件**: `data_sources/market_data.py`, `data_sources/__init__.py`, `server/api.py`, `base.py`
- **修复**: 删除函数，内联为 `market.fear_greed.raw.get("classification", "Unknown")`

---

## 六、前端 UI 问题

### 13. Trade 列表样式问题
- **现象**: 交易列表靠右挤压 / PnL 列缩小 / 文字溢出换行
- **涉及文件**: `web/style.css`, `web/app.js`
- **修复**: 调整 `.trade-item` padding、flex 布局；重构 `.trade-main` / `.trade-left` / `.trade-right`；`white-space: nowrap`

### 14. 顶背离显示强度数值 "1.00" 多余
- **现象**: 背离行显示 "顶背离 (1.00)" 中的数值无实际意义
- **涉及文件**: `web/app.js`
- **修复**: 仅显示类型名，去掉强度数值
