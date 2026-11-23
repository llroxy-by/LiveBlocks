#!/usr/bin/env bash
# 本地检查脚本：与 CI（.github/workflows/ci.yml）跑同一套检查。
# 用法：bash scripts/check.sh   （建议先激活 python3 -m venv .venv）
# 退出码：全部通过为 0；任一步失败即为非零（可被 CI / 预提交钩子直接使用）。
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

echo "==> 解释器：$("$PYTHON" -V 2>&1)"

echo "==> 语法编译检查：$PYTHON -m compileall -q src tests"
"$PYTHON" -m compileall -q src tests

echo "==> 全量测试：$PYTHON -m pytest"
"$PYTHON" -m pytest

echo "==> 检查通过"
