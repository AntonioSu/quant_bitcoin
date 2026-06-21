#!/bin/bash

# 快速提交脚本
# 用法:
#   bin/commit.sh feat "提交信息"
#   bin/commit.sh "fix: 提交信息"

if [ -z "$1" ]; then
    echo "用法: bin/commit.sh <feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert> \"提交信息\""
    echo "或:   bin/commit.sh \"fix(scope): 提交信息\""
    exit 1
fi

cd "$(dirname "$0")/.." || exit 1

TYPES="feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert"

if [[ "$1" =~ ^($TYPES)(\(.+\))?:\ .+ ]]; then
    MESSAGE="$1"
elif [[ "$1" =~ ^($TYPES)(\(.+\))?$ ]] && [ -n "${2:-}" ]; then
    MESSAGE="$1: $2"
else
    echo "提交信息需使用 conventional commit 格式。"
    echo "用法: bin/commit.sh <feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert> \"提交信息\""
    echo "或:   bin/commit.sh \"fix(scope): 提交信息\""
    exit 1
fi

git add .
git commit -m "$MESSAGE"
git push origin wip-swy
