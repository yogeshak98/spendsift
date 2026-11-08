#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

echo "==> Verifying SpendSift repo"
echo "==> Root: $ROOT_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "error: expected Python runtime at $PYTHON_BIN" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "error: npm is not installed or not on PATH" >&2
  exit 1
fi

echo
echo "==> Running backend test suite"
cd "$ROOT_DIR"
"$PYTHON_BIN" -m pytest -q

echo
echo "==> Running frontend test suite"
cd "$FRONTEND_DIR"
npm run test

echo
echo "==> Building frontend"
npm run build

echo
echo "==> Verification passed"
