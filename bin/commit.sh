#!/bin/bash

# 快速提交脚本
# 用法: bin/commit.sh "type(scope): 提交信息"
# 示例: bin/commit.sh "feat(ai): replace numeric confidence with 5-level labels"

usage='用法: bin/commit.sh "type(scope): 提交信息"'

if [ -z "$1" ]; then
    echo "$usage"
    exit 1
fi

cd "$(dirname "$0")/.." || exit 1

TYPES="feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert"

SCOPE='[^)]+'

if [[ "$1" =~ ^($TYPES)\($SCOPE\):\ .+ ]]; then
    MESSAGE="$1"
else
    echo "提交信息需使用 type(scope): message 格式。"
    echo "$usage"
    exit 1
fi

git add .
git commit -m "$MESSAGE"
git push origin wip-swy
