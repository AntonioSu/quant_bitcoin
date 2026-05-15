#!/bin/bash
#
# 24小时后台运行脚本
#
# 用法:
#   ./run_daemon.sh start   # 启动
#   ./run_daemon.sh stop    # 停止
#   ./run_daemon.sh status  # 查看状态
#   ./run_daemon.sh logs    # 查看日志
#
export HTTPS_PROXY=http://gfw.in.zhihu.com:18080
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/.."
LOG_FILE="$PROJECT_DIR/logs/daemon.log"

PORT=8088

# 运行模式配置 (独立开关，可以同时启用多个)
# 模拟盘 (默认启用全部3个预设)
# SIM_FLAG="--no-sim"                    # 禁用模拟盘
# SIM_FLAG="--sim-preset aggressive"     # 启用单个预设

# Demo Trading (开关 + 预设)
DEMO_FLAG="--demo --demo-preset aggressive"

# 真实主网 (开关 + 预设 + 二次确认)
# LIVE_FLAG="--live --live-preset aggressive --mainnet --max-capital 1000"

# 确保日志目录存在
mkdir -p "$PROJECT_DIR/logs"

start() {
    LISTENING_PID=$(ss -tlnp "sport = :$PORT" 2>/dev/null | awk '/LISTEN/{match($0,/pid=([0-9]+)/,a); print a[1]}')
    if [ -n "$LISTENING_PID" ] && pgrep -f "stock_btc.bin.run_server" 2>/dev/null | grep -qw "$LISTENING_PID"; then
        echo "服务已在运行 (端口 $PORT, PID: $LISTENING_PID)"
        return 1
    fi
    
    echo "启动服务..."
    cd "$PROJECT_DIR/.."
    # PYTHONUNBUFFERED=1 避免重定向到文件时 stdout 被全缓冲，导致日志不实时更新
    # 拼接所有 FLAG
    ALL_FLAGS="$SIM_FLAG $DEMO_FLAG $LIVE_FLAG"
    PYTHONUNBUFFERED=1 nohup python -m stock_btc.bin.run_server --port $PORT $ALL_FLAGS > "$LOG_FILE" 2>&1 &
    STARTED_PID=$!
    
    sleep 2
    if ps -p $STARTED_PID > /dev/null 2>&1; then
        echo "✅ 服务已启动 (PID: $STARTED_PID)"
        echo "📊 访问: http://localhost:$PORT"
    else
        echo "❌ 启动失败，请查看日志: $LOG_FILE"
        return 1
    fi
}

stop() {
    # 通过端口找占用进程，比 PID 文件更可靠
    LISTENING_PID=$(ss -tlnp "sport = :$PORT" 2>/dev/null | awk '/LISTEN/{match($0,/pid=([0-9]+)/,a); print a[1]}')
    if [ -n "$LISTENING_PID" ]; then
        echo "停止服务 (端口 $PORT, PID: $LISTENING_PID)..."
        kill "$LISTENING_PID"
        sleep 1
        if ps -p "$LISTENING_PID" > /dev/null 2>&1; then
            echo "⚠️  进程未退出，强制 kill..."
            kill -9 "$LISTENING_PID" 2>/dev/null
        fi
    fi

    # 清理所有残留的同名服务进程
    ZOMBIE_PIDS=$(pgrep -f "stock_btc.bin.run_server" 2>/dev/null)
    if [ -n "$ZOMBIE_PIDS" ]; then
        echo "🧹 发现残留进程，清理中: $ZOMBIE_PIDS"
        echo "$ZOMBIE_PIDS" | xargs kill -9 2>/dev/null
        sleep 1
    fi

    echo "✅ 服务已停止"
}

status() {
    LISTENING_PID=$(ss -tlnp "sport = :$PORT" 2>/dev/null | awk '/LISTEN/{match($0,/pid=([0-9]+)/,a); print a[1]}')
    PROCESS_PIDS=$(pgrep -f "stock_btc.bin.run_server" 2>/dev/null)

    if [ -n "$LISTENING_PID" ] && echo "$PROCESS_PIDS" | grep -qw "$LISTENING_PID"; then
        echo "✅ 服务运行中 (端口 $PORT, PID: $LISTENING_PID)"
        echo "📊 访问: http://localhost:$PORT"
        ELAPSED=$(ps -o etime= -p "$LISTENING_PID" | tr -d ' ')
        echo "⏱️  运行时间: $ELAPSED"
    else
        echo "❌ 服务异常"
        [ -n "$LISTENING_PID" ] && echo "   ⚠️  端口 $PORT 被非目标进程占用 (PID: $LISTENING_PID)"
        [ -z "$LISTENING_PID" ] && echo "   ⚠️  端口 $PORT 无进程监听"
        [ -n "$PROCESS_PIDS" ] && echo "   ⚠️  存在残留 stock_btc 进程: $PROCESS_PIDS"
    fi
}

logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "日志文件不存在: $LOG_FILE"
    fi
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        stop
        sleep 1
        start
        ;;
    status)
        status
        ;;
    logs)
        logs
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
