#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo "请先安装 Python 3.11+，创建 .venv 并安装项目，详见 README.md。"
  exit 1
fi
.venv/bin/python -m uvicorn tracelens.main:app --host 127.0.0.1 --port 8017 &
backend_pid=$!
trap 'kill "$backend_pid" 2>/dev/null || true' EXIT INT TERM
cd frontend
pnpm dev
