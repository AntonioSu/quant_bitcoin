#!/bin/bash
# 查看服务运行状态
source "$(dirname "$0")/daemon_env.sh"

LISTENING_PID=$(ss -tlnp "sport = :$PORT" 2>/dev/null | awk '/LISTEN/{match($0,/pid=([0-9]+)/,a); print a[1]}')
PROCESS_PIDS=$(pgrep -f "bin.run_server" 2>/dev/null)

if [ -n "$LISTENING_PID" ] && echo "$PROCESS_PIDS" | grep -qw "$LISTENING_PID"; then
    echo "✅ 服务运行中 (端口 $PORT, PID: $LISTENING_PID)"
    echo "📊 访问: http://localhost:$PORT"
    ELAPSED=$(ps -o etime= -p "$LISTENING_PID" | tr -d ' ')
    echo "⏱️  运行时间: $ELAPSED"
else
    echo "❌ 服务异常"
    [ -n "$LISTENING_PID" ] && echo "   ⚠️  端口 $PORT 被非目标进程占用 (PID: $LISTENING_PID)"
    [ -z "$LISTENING_PID" ] && echo "   ⚠️  端口 $PORT 无进程监听"
    [ -n "$PROCESS_PIDS" ] && echo "   ⚠️  存在残留 quant_bitcoin 进程: $PROCESS_PIDS"
fi
