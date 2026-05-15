#!/bin/bash

# 快速提交脚本
# 用法: ./commit.sh "提交信息"

if [ -z "$1" ]; then
    echo "用法: ./commit.sh \"提交信息\""
    exit 1
fi

# 仓库根目录为 bin 的上一级（本仓库为 quant_bitcoin）
cd "$(dirname "$0")/.." || exit 1

git add -A
git commit -m "$1"
git push origin wip-swy
