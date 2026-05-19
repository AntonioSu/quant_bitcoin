#!/bin/bash
# 服务运维公共配置，被 start/stop/restart/status/logs 引用
export HTTPS_PROXY=http://gfw.in.zhihu.com:18080
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
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

mkdir -p "$PROJECT_DIR/logs"
