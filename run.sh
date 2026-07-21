#!/usr/bin/env bash
#
# HELIX — start all three services together.
#
#   1. KB server   (Flask, SAPBERT + lexical)   http://127.0.0.1:8765
#   2. API server  (FastAPI / uvicorn)          http://127.0.0.1:8000
#   3. Web app     (Vite dev server)            http://localhost:5173
#
# Usage:
#   ./run.sh                 # start everything (Ctrl+C stops all three)
#   API_PORT=9000 ./run.sh   # override a port
#
# Each service logs to logs/<name>.log; the combined stream is tailed live.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# macOS: faiss and torch each bundle an OpenMP runtime; without this they collide
# ("OMP: Error #15 … libomp.dylib already initialized") and the KB server crashes
# on the first query. This lets both coexist.
export KMP_DUPLICATE_LIB_OK="${KMP_DUPLICATE_LIB_OK:-TRUE}"

# ── pick a Python ─────────────────────────────────────────────────────────────
# Prefer an activated conda env ($CONDA_PREFIX), then a local venv, then PATH.
# Using $CONDA_PREFIX directly avoids PATH-ordering surprises (e.g. a system
# python3 shadowing the conda one).
if   [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python" ]; then PY="$CONDA_PREFIX/bin/python"
elif [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/venv/bin/python"  ]; then PY="$ROOT/venv/bin/python"
else PY="$(command -v python3 || command -v python)"; fi

API_PORT="${API_PORT:-8000}"
KB_PORT="${KB_PORT:-8765}"

# ── preflight: make sure the chosen Python has the backend deps ───────────────
if ! "$PY" -c "import flask, uvicorn, fastapi" >/dev/null 2>&1; then
  echo "ERROR: $PY is missing backend dependencies (flask / uvicorn / fastapi)."
  echo
  echo "Activate the right environment and install requirements, e.g.:"
  echo "    conda activate helix"
  echo "    pip install -r requirements.txt"
  echo
  echo "Then run:  ./run.sh   (use ./run.sh, not 'sh ./run.sh')"
  exit 1
fi

mkdir -p logs
pids=()

cleanup() {
  trap - INT TERM EXIT
  echo
  echo "Stopping HELIX services…"
  kill "${pids[@]}" 2>/dev/null
  pkill -P $$ 2>/dev/null      # sweep any direct children (e.g. tail)
  wait 2>/dev/null
  exit 0
}
trap cleanup INT TERM EXIT

start() {                       # start <name> <command...>
  local name="$1"; shift
  "$@" > "logs/$name.log" 2>&1 &
  pids+=($!)
  printf '  %-4s → logs/%s.log  (pid %s)\n' "$name" "$name" "$!"
}

echo "Starting HELIX with Python: $PY"
echo "  KB server : http://127.0.0.1:$KB_PORT"
echo "  API       : http://127.0.0.1:$API_PORT"
echo "  Web app   : http://localhost:5173"
echo

# 1. KB server (Flask)
start kb "$PY" kb/server.py --port "$KB_PORT"

# 2. API server (FastAPI / uvicorn, auto-reload)
start api "$PY" -m uvicorn api.main:app --port "$API_PORT" --reload

# 3. Web app (Vite). Run the binary directly when present so it isn't orphaned
#    behind an npm wrapper; fall back to `npm run dev` otherwise.
if [ -x "web/node_modules/.bin/vite" ]; then
  ( cd web && exec node_modules/.bin/vite ) > logs/web.log 2>&1 &
else
  ( cd web && exec npm run dev ) > logs/web.log 2>&1 &
fi
pids+=($!)
printf '  %-4s → logs/%s.log  (pid %s)\n' "web" "web" "$!"

echo
echo "All three started. Open http://localhost:5173  ·  Ctrl+C to stop."
echo "──────────────────────────────────────────────────────────────────"

# Live combined logs; Ctrl+C is caught by the trap above.
tail -n +1 -f logs/kb.log logs/api.log logs/web.log &
pids+=($!)
wait
