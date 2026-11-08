#!/usr/bin/env bash
# Build the React frontend and start the FastAPI backend that serves it.
#
# The backend (backend.app, uvicorn on 127.0.0.1:8765) mounts the built
# frontend/dist output directly — there is no separate frontend server in
# production use. This script builds the frontend (skipped when its inputs are
# unchanged since the last successful build), then starts the backend in the
# foreground.
#
# Usage: scripts/run_app.sh [--rebuild]
#   --rebuild (or SPENDSIFT_REBUILD=1) forces the frontend build.

set -euo pipefail

REBUILD="${SPENDSIFT_REBUILD:-0}"
for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=1 ;;
    *)
      echo "error: unknown argument: $arg (usage: $0 [--rebuild])" >&2
      exit 2
      ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "error: expected Python runtime at $PYTHON_BIN (run 'uv sync' first)" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "error: npm is not installed or not on PATH" >&2
  exit 1
fi

cd "$FRONTEND_DIR"
# `npm ci` wipes node_modules (and the incremental tsc buildinfo inside it), so
# run it only when dependencies are missing or package.json / the lockfile
# changed since the last install (npm records that install in
# node_modules/.package-lock.json). A package.json edited without updating the
# lockfile makes `npm ci` stop with an error, which is the right outcome.
if [ ! -d node_modules ] || [ ! -f node_modules/.package-lock.json ] \
  || [ package-lock.json -nt node_modules/.package-lock.json ] \
  || [ package.json -nt node_modules/.package-lock.json ]; then
  echo "==> Installing frontend dependencies"
  npm ci
else
  echo "==> Frontend dependencies up to date"
fi

if command -v shasum >/dev/null 2>&1; then
  sha256() { shasum -a 256; }
  sha256_files() { xargs -0 shasum -a 256; }
else
  sha256() { sha256sum; }
  sha256_files() { xargs -0 sha256sum; }
fi

# One content hash over every build input, in a stable (sorted) order. Left
# out: node_modules and dist, and src/shared/api/schema.d.ts -- it is generated from
# the backend by the build itself (gen:api) and imported type-only, so it never
# changes the bundle. A backend API change that breaks the frontend's types is
# caught by `tsc -b` in the test gates, not at startup.
frontend_build_hash() {
  local roots="src"
  if [ -d public ]; then
    roots="src public"
  fi
  {
    # shellcheck disable=SC2086  # roots is a fixed list of plain dir names
    find $roots -type f ! -name .DS_Store ! -path src/shared/api/schema.d.ts -print0
    find . -maxdepth 1 -type f \( -name index.html -o -name package.json \
      -o -name package-lock.json -o -name 'tsconfig*.json' \
      -o -name 'vite.config.*' -o -name '.env*' \) -print0
  } | LC_ALL=C sort -z | sha256_files | sha256 | cut -d' ' -f1
}

BUILD_HASH="$(frontend_build_hash)"
if [ "$REBUILD" != "1" ] && [ -f dist/index.html ] && [ -f dist/.build-hash ] \
  && [ "$(cat dist/.build-hash)" = "$BUILD_HASH" ]; then
  echo "==> Frontend up to date (build skipped)"
else
  echo "==> Building frontend (tsc -b && vite build -> frontend/dist)"
  npm run build
  # Recorded only after a successful build (set -e stops on failure), so the
  # stored hash always describes the sources of the dist/ actually on disk.
  printf '%s\n' "$BUILD_HASH" >dist/.build-hash
fi

echo "==> Starting backend on http://127.0.0.1:8765 (serves API + built frontend)"
cd "$ROOT_DIR"
exec "$PYTHON_BIN" -m backend.app
