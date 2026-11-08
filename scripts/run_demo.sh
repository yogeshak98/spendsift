#!/usr/bin/env bash
# Run SpendSift against the demo ledger on a separate port.
#
# Mirrors scripts/run_app.sh but points SPENDSIFT_STORAGE_DIR at demo/data and
# serves on a different port (default 8766) so it can run alongside a normal
# instance on 8765 without clashing. The demo ledger is a gitignored build
# artifact — (re)build it with scripts/build_demo.py before serving.
#
# Usage:
#   scripts/run_demo.sh            # builds frontend, serves demo on :8766
#   scripts/run_demo.sh 9000       # serves demo on :9000
#   PORT=9000 scripts/run_demo.sh  # same, via env

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
DATA_DIR="$ROOT_DIR/demo/data"
PORT="${1:-${PORT:-8766}}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "error: expected Python runtime at $PYTHON_BIN (run 'uv sync' first)" >&2
  exit 1
fi

# Rebuild the demo ledger when it is missing OR was built in an earlier month.
#
# build_demo.py anchors its 25-month window to the build date, so a ledger left
# over from a previous month ends before "this month" and the demo opens on
# emptiness -- the exact decay that made the window relative in the first
# place. Rebuilding is cheap and the directory is a gitignored artifact, so
# staleness is detected rather than left to the reader to notice.
STAMP_FILE="$DATA_DIR/.built-for-month"
CURRENT_MONTH="$(date +%Y-%m)"

if [[ ! -d "$DATA_DIR/journal" ]]; then
  echo "==> Demo ledger not found — building it"
  NEEDS_BUILD=1
elif [[ "$(cat "$STAMP_FILE" 2>/dev/null)" != "$CURRENT_MONTH" ]]; then
  echo "==> Demo ledger was built for a different month — rebuilding"
  NEEDS_BUILD=1
else
  NEEDS_BUILD=0
fi

if [[ "$NEEDS_BUILD" == "1" ]]; then
  "$PYTHON_BIN" "$ROOT_DIR/scripts/build_demo.py" --data-dir "$DATA_DIR"
  printf '%s' "$CURRENT_MONTH" > "$STAMP_FILE"
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "error: npm is not installed or not on PATH" >&2
  exit 1
fi

echo "==> Installing frontend dependencies"
cd "$FRONTEND_DIR"
npm install

echo "==> Building frontend (tsc -b && vite build -> frontend/dist)"
npm run build

echo "==> Starting demo backend on http://127.0.0.1:$PORT (data: $DATA_DIR)"
cd "$ROOT_DIR"
export SPENDSIFT_STORAGE_DIR="$DATA_DIR"
exec "$PYTHON_BIN" -m uvicorn backend.app.server.main:create_app --factory \
  --host 127.0.0.1 --port "$PORT"
