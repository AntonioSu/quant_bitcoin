#!/bin/bash
# 查看服务实时日志
source "$(dirname "$0")/daemon_env.sh"

if [ -f "$LOG_FILE" ]; then
    tail -f "$LOG_FILE"
else
    echo "日志文件不存在: $LOG_FILE"
fi
