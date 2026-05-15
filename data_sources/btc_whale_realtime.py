"""
BTC 巨鲸实时监控器
- WebSocket aggTrade 流：实时捕获大单
- 阈值过滤（默认 >= 10 BTC），统计净买入
"""

import asyncio
import json
import time
import os
from datetime import datetime
from collections import deque
import aiohttp

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
SYMBOL          = "BTCUSDT"
WHALE_THRESHOLD = 10.0          # BTC，单笔 >= 此值视为大单
MAX_RECORDS     = 100           # 内存保留最近 N 条大单

WS_URL_9443     = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@aggTrade"
WS_URL_443      = f"wss://stream.binance.com/ws/{SYMBOL.lower()}@aggTrade"  # 备用端口


def get_proxy_url():
    """从环境变量获取代理 URL"""
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")


# ─────────────────────────────────────────────
# 状态（全局）
# ─────────────────────────────────────────────
stats = {
    "whale_buy_btc":  0.0,   # 累计主动买入（大单）
    "whale_sell_btc": 0.0,   # 累计主动卖出（大单）
    "total_trades":   0,     # 总聚合成交笔数
    "whale_trades":   0,     # 命中阈值的大单笔数
    "start_time":     time.time(),
}
recent_whales: deque = deque(maxlen=MAX_RECORDS)   # 最近大单记录


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────
def ts_to_str(ts_ms: int) -> str:
    """毫秒时间戳 → 本地时间字符串"""
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M:%S")


def fmt_btc(val: float) -> str:
    return f"{val:,.4f} BTC"


def net_flow() -> float:
    return stats["whale_buy_btc"] - stats["whale_sell_btc"]


def print_separator(char="─", width=80):
    print(char * width)


# ─────────────────────────────────────────────
# 打印当前统计面板
# ─────────────────────────────────────────────
def print_stats_panel():
    elapsed = time.time() - stats["start_time"]
    nf = net_flow()
    direction = "🟢 净流入（买压）" if nf > 0 else "🔴 净流出（卖压）" if nf < 0 else "⚪ 中性"

    print_separator("─")
    print(f"  ⏱  运行时长:   {elapsed/60:.1f} 分钟")
    print(f"  📈 总聚合成交:  {stats['total_trades']:,} 笔")
    print(f"  🐳 大单命中:   {stats['whale_trades']:,} 笔  (阈值 ≥ {WHALE_THRESHOLD} BTC)")
    print(f"  ─")
    print(f"  🟢 累计大单买入: {fmt_btc(stats['whale_buy_btc'])}")
    print(f"  🔴 累计大单卖出: {fmt_btc(stats['whale_sell_btc'])}")
    print(f"  ⚡ 净流入:       {fmt_btc(nf)}  {direction}")
    print_separator("─")


# ─────────────────────────────────────────────
# 处理单条 aggTrade 消息
# ─────────────────────────────────────────────
def handle_agg_trade(msg: dict):
    qty    = float(msg["q"])          # 成交量 BTC
    price  = float(msg["p"])          # 成交价
    ts_ms  = int(msg["T"])            # 时间戳（毫秒）
    is_sell = bool(msg["m"])          # True = buyer is maker = 主动卖出

    stats["total_trades"] += 1

    if qty < WHALE_THRESHOLD:
        return   # 不是大单，跳过

    stats["whale_trades"] += 1
    usd_val = qty * price

    if is_sell:
        stats["whale_sell_btc"] += qty
        side_label = "🔴 卖出"
    else:
        stats["whale_buy_btc"] += qty
        side_label = "🟢 买入"

    record = {
        "time":    ts_ms,
        "side":    "sell" if is_sell else "buy",
        "qty":     qty,
        "price":   price,
        "usd_val": usd_val,
    }
    recent_whales.append(record)

    # 实时打印大单
    t_str  = ts_to_str(ts_ms)
    nf     = net_flow()
    nf_str = f"+{fmt_btc(nf)}" if nf >= 0 else fmt_btc(nf)

    print(
        f"  [{t_str}] {side_label}  "
        f"{fmt_btc(qty):>16}  "
        f"@ ${price:>10,.1f}  "
        f"(${usd_val/1e6:.2f}M)  "
        f"净流入: {nf_str}"
    )


# ─────────────────────────────────────────────
# WebSocket 主循环
# ─────────────────────────────────────────────
async def ws_loop():
    reconnect_delay = 3
    proxy_url = get_proxy_url()
    
    # 根据是否有代理选择 URL 和 SSL 配置
    if proxy_url:
        import ssl
        ws_url = WS_URL_443  # 代理通常只支持 443 端口
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        print(f"  🔌 连接 WebSocket: {ws_url}")
        print(f"  🌐 使用代理: {proxy_url} (SSL验证已禁用)\n")
    else:
        ws_url = WS_URL_9443  # 直连用 9443 端口
        ssl_context = None
        print(f"  🔌 连接 WebSocket: {ws_url}\n")

    while True:
        try:
            # 创建 session，配置代理和 SSL
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.ws_connect(
                    ws_url,
                    proxy=proxy_url,
                    ssl=ssl_context,
                    heartbeat=20,
                ) as ws:
                    reconnect_delay = 3   # 成功连接后重置
                    print(f"  ✅ 已连接，监听 {SYMBOL} aggTrade 流...")
                    print(f"  🐳 大单阈值: ≥ {WHALE_THRESHOLD} BTC\n")
                    print_separator()
                    print(f"  {'时间':^10}  {'方向':^6}  {'数量':>16}  {'价格':>14}  {'USD':>10}  {'净流入'}")
                    print_separator()

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            handle_agg_trade(data)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            print(f"\n  ⚠️  WebSocket 错误: {ws.exception()}")
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            print(f"\n  ⚠️  连接已关闭")
                            break

        except aiohttp.ClientError as e:
            print(f"\n  ⚠️  连接错误: {e}，{reconnect_delay}s 后重连...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)

        except Exception as e:
            print(f"\n  ❌ 意外错误: {e}，{reconnect_delay}s 后重连...")
            await asyncio.sleep(reconnect_delay)


# ─────────────────────────────────────────────
# 定时打印统计面板
# ─────────────────────────────────────────────
async def stats_loop():
    while True:
        await asyncio.sleep(300)  # 每 5 分钟打印一次
        try:
            print("\n")
            print_stats_panel()
            print()
        except Exception as e:
            print(f"  ⚠️  打印统计失败: {e}")


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────
async def main():
    print("═" * 80)
    print("       🐳  BTC 巨鲸实时监控器  🐳")
    print(f"       交易对: {SYMBOL}  |  阈值: ≥ {WHALE_THRESHOLD} BTC")
    print("═" * 80)
    print()

    # 并发运行：WebSocket 监听 + 定时统计
    await asyncio.gather(
        ws_loop(),
        stats_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n  👋 已停止监控")
        print_stats_panel()
        print()
