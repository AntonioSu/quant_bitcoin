#!/bin/bash
# 服务运维公共配置，被 start/stop/restart/status/logs 引用
export HTTPS_PROXY=http://gfw.in.zhihu.com:18080
export https_proxy=http://gfw.in.zhihu.com:18080
export HTTP_PROXY=http://gfw.in.zhihu.com:18080
export http_proxy=http://gfw.in.zhihu.com:18080
export no_proxy="model.in.zhihu.com,localhost,127.0.0.1"
export NO_PROXY="model.in.zhihu.com,localhost,127.0.0.1"
export CRYPTOGRAPHY_OPENSSL_NO_LEGACY=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/.."
LOG_FILE="$PROJECT_DIR/logs/daemon.log"

# Python 环境：优先使用项目内虚拟环境，缺失时再回退到 conda base。
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
else
    CONDA_ENV="base"
    if command -v conda >/dev/null 2>&1; then
        eval "$(conda shell.bash hook 2>/dev/null)"
        conda activate "$CONDA_ENV"
    elif [ -x "/data1/suwenyuan/miniconda3/bin/conda" ]; then
        eval "$(/data1/suwenyuan/miniconda3/bin/conda shell.bash hook 2>/dev/null)"
        conda activate "$CONDA_ENV"
    else
        echo "⚠️  未找到项目 .venv 或 conda，将使用当前 PATH 中的 python"
    fi
fi

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
