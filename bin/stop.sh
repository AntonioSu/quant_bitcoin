#!/bin/bash
# 停止后台服务
source "$(dirname "$0")/daemon_env.sh"

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

ZOMBIE_PIDS=$(pgrep -f "bin.run_server" 2>/dev/null)
if [ -n "$ZOMBIE_PIDS" ]; then
    echo "🧹 发现残留进程，清理中: $ZOMBIE_PIDS"
    echo "$ZOMBIE_PIDS" | xargs kill -9 2>/dev/null
    sleep 1
fi

echo "✅ 服务已停止"
