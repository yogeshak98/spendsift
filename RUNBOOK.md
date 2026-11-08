# SpendSift Runbook

## Purpose

This is the canonical operator runbook for the production harness and local development.

If a command path differs from this file, this file wins until updated.

## Environment

- Python environment: `./.venv` (create with `uv sync`)
- Frontend root: [`frontend/`](frontend) (React/TS, served by the backend — see below)
- Default data root: `storage/` (gitignored; override with `SPENDSIFT_STORAGE_DIR`; layout in [SCHEMA.md](SCHEMA.md))

## Canonical Checks

### Backend tests

Run the full backend suite:

    uv run pytest -q

Lint + type check:

    uv run ruff check .
    uv run ty check

All three must pass before a unit is marked complete. CI (`.github/workflows/ci.yml`) runs `ruff check` and `pytest`.

### Frontend tests

```bash
cd frontend && npm run test
```

### Frontend build

```bash
cd frontend && npm run build
```

Builds to `frontend/dist`, which the backend serves directly (see below). Run from the repo root or `frontend/`.

### Generated API client (`npm run gen:api`)

```bash
cd frontend && npm run gen:api
```

Regenerates the typed frontend API client from the backend's OpenAPI schema:

1. `../.venv/bin/python -m backend.app.server.openapi openapi.json` — builds the
   FastAPI app against a throwaway temp data dir (never `storage/`) and dumps
   `app.openapi()` to `frontend/openapi.json`.
2. `openapi-typescript openapi.json -o src/shared/api/schema.d.ts` — emits the TS types
   consumed by `frontend/src/shared/api/client.ts` (an `openapi-fetch` client).

Both outputs (`frontend/openapi.json`, `frontend/src/shared/api/schema.d.ts`) are
**gitignored** and regenerated on demand. `gen:api` is wired as `predev` and
`prebuild`, so `npm run dev` and `npm run build` regenerate the types first.

Requires the Python venv at `.venv/` (same venv `scripts/run_app.sh` uses). If it
is missing, `gen:api` — and therefore `npm run dev` / `npm run build` — will fail.

## Canonical Run Paths

### Start the backend (serves the API and the built frontend)

```bash
./.venv/bin/python -m backend.app
```

This runs `backend/app/server/main.py` (`create_app()`) under uvicorn, bound to `127.0.0.1:8765`. The same FastAPI app:

- serves `/api/*` routes (ingest, reports, transactions, tax, settings, liabilities, prices, health, …)
- serves files from `frontend/dist` through a catch-all route
- falls back to `frontend/dist/index.html` for any other non-`/api` path (SPA routing), so deep links survive a reload

On startup it refreshes MF NAVs and equity prices in the background (best-effort; offline is fine).

The frontend must be built first (`cd frontend && npm run build`) or the backend serves a placeholder page instead.

Notes:

- binds to loopback only
- uses `SPENDSIFT_STORAGE_DIR` if set (see `backend/app/core/settings.py`)

### Build + run in one step

```bash
scripts/run_app.sh
```

Installs frontend dependencies with `npm ci` only when `frontend/node_modules` is missing or
`frontend/package-lock.json` or `frontend/package.json` is newer than the last install
(`node_modules/.package-lock.json`);
otherwise it prints `Frontend dependencies up to date` and skips the install. Next it builds the
frontend, unless nothing changed since the last successful build: it content-hashes the build
inputs (`frontend/src` minus the generated `src/shared/api/schema.d.ts`, `public/`, `index.html`,
`package.json`, `package-lock.json`, `tsconfig*.json`, `vite.config.*`, `.env*`) and compares
the result with `frontend/dist/.build-hash`, which is written only after a build succeeds. On a
match it prints `Frontend up to date (build skipped)`. Finally it starts the backend in the
foreground.

Force a build with `scripts/run_app.sh --rebuild` (or `SPENDSIFT_REBUILD=1`). Only content counts:
touching a file without changing it does not trigger a rebuild.

### Run against the demo ledger

```bash
scripts/run_demo.sh                    # build frontend + demo ledger, serve on 127.0.0.1:8766
scripts/demo [--skip-build]            # always rebuild the demo ledger, serve via the demo factory on 8766 (DEMO_PORT)
uv run python scripts/build_demo.py   # (re)build the gitignored demo ledger by hand
```

Same build+run path as `run_app.sh`, but points `SPENDSIFT_STORAGE_DIR` at `demo/data` and
serves on a **separate port (default 8766)** so it can run alongside a normal instance on
8765. Pass a port to override: `scripts/run_demo.sh 9000` (or `PORT=9000 scripts/run_demo.sh`).
The demo ledger is a build artifact. Its 25-month window is anchored to the **build date**,
so it always ends in the current month — `run_demo.sh` rebuilds it automatically when it is
missing or was built in an earlier month (tracked by `demo/data/.built-for-month`). Rebuild
by hand after any change to the demo data or the accounts it seeds.

Determinism is per-month: `SEED` fixes every amount and category choice, so two builds in
the same month are identical, but the figures differ between months because the dates move.
This is deliberately the opposite of `tests/fixtures/statements/*.csv`, which stay fixed —
those are asserted against exact parsed output and encode chosen holding periods.

### One-time: before the FIRST Groww stock import

Stock imports dedup on the broker's Exchange Order Id. Stock transactions entered by hand
before this importer existed carry none, so an import cannot tell which of them the export
already covers. Stamp them first:

```bash
uv run python scripts/backfill_stock_order_ids.py            # dry run — read the table
uv run python scripts/backfill_stock_order_ids.py --apply    # snapshots, then stamps
```

The dry run refuses on any ambiguity or unmatched transaction; read *why* before applying.
Until it has run, a stock commit is refused with **E1046** rather than double-booking the
history. A journal with no hand-entered stock transactions needs none of this.

### Card bill payments: import each account from one source

Card bill-payment rows are de-duplicated only on the exact key (date, signed amount,
wording, account). Re-importing the same account from a differently-worded source (its PDF
statement after its CSV, say) can book the same payment twice — check review before
committing.

### How import dedup decides a row is already recorded

A row is `exact_dup` only when it matches a journal posting on the same date, account and
wording **with the same sign** — a debit is the negative posting on its source account (bank
or card), a credit the positive one. So a same-day refund of a recorded charge is a new row,
not a duplicate. Mutual-fund orders match on date, scheme and signed units (a buy adds units,
a redemption removes them) as well as on content.

Matching is counted: each recorded transaction covers one row. If a statement has two
identical rows on a day and the journal records one, one row is `exact_dup` and the other is
imported as new with a `possible duplicate: N identical rows in this import, M already
recorded` flag, which the review screen shows as a warning. Check it against the statement
before committing: two identical same-day charges are usually real, but a statement that
printed one charge twice would book it twice. The ledger-cli/hledger merge preview counts
the same way.

Because the sign is part of the match, changing a row's direction in review (debit to credit
or back, through the review patch or the CLI) changes its identity: a later re-import of the
original file will show that row as **new**, with the parser's original direction. Reject it
in review rather than committing it again, or fix the template so the parser reads the
direction correctly in the first place.

### Frontend-only dev loop

```bash
cd frontend && npm run dev
```

Starts the Vite dev server for fast frontend iteration with hot reload. It proxies `/api` to `http://127.0.0.1:8765` (`frontend/vite.config.ts`), so start the backend separately for real API calls.

## Canonical Full Verification

Run these in order:

1. backend tests
2. frontend tests
3. frontend build
4. unit-specific tests added by the current work

Or use (runs 1–3):

```bash
scripts/verify_repo.sh
```

Current baseline:

```bash
uv run pytest -q
```

```bash
cd frontend && npm run build
```

## Unit Procedure

1. read the unit's spec
2. plan, then execute within the unit's intended write scope
3. run verification (see baseline above)
4. record verdicts and evidence alongside the unit's plan
5. advance automatically unless blocked by a real decision

## Stop Conditions

Stop and escalate only when:

- product docs are in conflict
- a unit's dependencies are unsatisfied
- verification is failing without a clear local fix
- tooling, credentials, or network access are required and unavailable
- real finance semantics are ambiguous

## Notes

- `pytest` is part of the locked local environment (`dependency-groups.dev` in `pyproject.toml`) — use it, not `unittest`, for new work
- do not rely on generated frontend build artifacts (`frontend/dist`) being committed
- the Tauri desktop shell and Streamlit UI are retired; the long-term UI shell is the browser web app served by the backend, per [CLAUDE.md](CLAUDE.md)
