#!/usr/bin/env bash
# One-command start: sets up the venv if needed, builds the frontend if needed,
# migrates the DB and serves everything on http://127.0.0.1:8000 (loopback only).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

if [ ! -x .venv/bin/python ]; then
  echo "[start] creating Python venv (.venv)"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r backend/requirements.txt
fi

if [ ! -f frontend/dist/index.html ] || [ -n "$(find frontend/src frontend/index.html -newer frontend/dist/index.html -print -quit 2>/dev/null)" ]; then
  echo "[start] building frontend"
  if [ ! -d frontend/node_modules ]; then
    (cd frontend && npm install --no-audit --no-fund)
  fi
  (cd frontend && npm run build)
fi

if [ ! -f .env ]; then
  echo "[start] note: .env not found — the app runs, but fetches will be recorded as 'APIキー未設定'."
  echo "        cp .env.example .env  and set JQUANTS_API_KEY to enable J-Quants fetches."
fi

cd backend
../.venv/bin/python -m stockapp.db
echo "[start] serving on http://127.0.0.1:${PORT}  (Ctrl+C to stop)"
exec ../.venv/bin/python -m stockapp.server --port "${PORT}"
