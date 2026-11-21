#!/usr/bin/env bash
# Sets up the venv if needed, installs backend deps, builds the frontend,
# runs pytest (which includes the build-output secret check), then repeats the
# secret grep in shell as an independent check.
#
# Order note: the frontend is built *before* pytest so that the pytest secret
# hygiene test inspects a fresh build of the current sources.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "== creating venv"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
fi
echo "== installing backend deps"
.venv/bin/pip install -q -r backend/requirements.txt

echo "== building frontend"
if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install --no-audit --no-fund)
fi
(cd frontend && npm run build)

echo "== pytest"
(cd backend && ../.venv/bin/python -m pytest -q)

echo "== phase 3 (macro) tests, listed separately"
(cd backend && ../.venv/bin/python -m pytest -q tests/test_fred_ingest.py tests/test_macro_calc.py tests/test_sector_macro.py)

echo "== secret grep on frontend/dist"
status=0
# Phase 3: FRED is fetched by the server only; the browser bundle must not
# reference its host or CSV endpoint either (要件 7.1: ブラウザは自前の /api/* のみ).
for needle in "api.jquants.com" "JQUANTS_API_KEY" "fred.stlouisfed.org" "fredgraph"; do
  if grep -rIl --fixed-strings "$needle" frontend/dist; then
    echo "FAIL: '$needle' found in build output"
    status=1
  else
    echo "ok: '$needle' not in build output"
  fi
done
if grep -rIli "x-api-key" frontend/dist; then
  echo "FAIL: 'x-api-key' found in build output"
  status=1
else
  echo "ok: 'x-api-key' not in build output"
fi
if [ -n "$(git ls-files .env 2>/dev/null || true)" ]; then
  echo "FAIL: .env is tracked by git"
  status=1
fi
[ "$status" -eq 0 ] && echo "== ALL CHECKS PASSED"
exit "$status"
