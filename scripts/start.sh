#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f frontend/dist/index.html ]]; then
  echo "请先运行 cd frontend && pnpm install && pnpm build"
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "请先创建 .venv 并安装依赖，详见 README.md"
  exit 1
fi
exec .venv/bin/python -m uvicorn tracelens.main:app --host 127.0.0.1 --port "${TRACELENS_PORT:-8017}"
