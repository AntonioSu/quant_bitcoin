#!/bin/bash
# 启动后台服务
source "$(dirname "$0")/daemon_env.sh"

LISTENING_PID=$(ss -tlnp "sport = :$PORT" 2>/dev/null | awk '/LISTEN/{match($0,/pid=([0-9]+)/,a); print a[1]}')
if [ -n "$LISTENING_PID" ] && pgrep -f "bin.run_server" 2>/dev/null | grep -qw "$LISTENING_PID"; then
    echo "服务已在运行 (端口 $PORT, PID: $LISTENING_PID)"
    exit 1
fi

echo "启动服务..."
cd "$PROJECT_DIR"
ALL_FLAGS="$SIM_FLAG $DEMO_FLAG $LIVE_FLAG"
# PYTHONUNBUFFERED=1 避免重定向到文件时 stdout 被全缓冲，导致日志不实时更新
PYTHONPATH="$PROJECT_DIR" PYTHONUNBUFFERED=1 nohup python -m bin.run_server --port $PORT $ALL_FLAGS > "$LOG_FILE" 2>&1 &
STARTED_PID=$!

for i in {1..30}; do
    if ! ps -p $STARTED_PID > /dev/null 2>&1; then
        echo "❌ 启动失败，请查看日志: $LOG_FILE"
        exit 1
    fi

    LISTENING_PID=$(ss -tlnp "sport = :$PORT" 2>/dev/null | awk '/LISTEN/{match($0,/pid=([0-9]+)/,a); print a[1]}')
    if [ "$LISTENING_PID" = "$STARTED_PID" ]; then
        echo "✅ 服务已启动 (PID: $STARTED_PID)"
        echo "📊 访问: http://localhost:$PORT"
        exit 0
    fi

    sleep 1
done

echo "⚠️  服务进程已启动但端口 $PORT 尚未监听 (PID: $STARTED_PID)"
echo "📄 请查看日志: $LOG_FILE"
