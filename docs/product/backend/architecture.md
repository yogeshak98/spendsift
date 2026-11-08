# Backend Architecture

SpendSift's Python backend: a FastAPI loopback service that turns exported financial files into a validated beancount journal plus read-model dashboards, with all state stored as files on the local machine.

> Reflects the implementation as of 2026-11. Facts only.

## Purpose

The backend owns all financial logic and every on-disk write. The frontend never touches persistence files directly — it talks to the loopback HTTP API. The canonical finance model is a beancount journal under a single data directory; everything else (dashboards, tax workspace, liabilities) is derived from it on read.

## Package layout

| Package / dir | Responsibility |
|---|---|
| `backend/app/server/` | FastAPI app factory (`main.py`), server entry point (`__main__.py`), route modules under `routes/`, error middleware, OpenAPI export (`openapi.py`), demo factory (`demo.py`) |
| `backend/app/models/` | Pydantic shapes: `api/` (request/response schemas), `domain/` (account taxonomy, candidates, …), `storage/` (templates, budget, …) |
| `backend/app/service/ingest/` | Import pipeline: templates and fingerprinting, statement parsers, normalization, dedup, transfer matching, entry writing, ledger-cli/hledger merge, `ImportService` |
| `backend/app/service/ledger/` | Ledger-facing services: account admin and metadata, entries, openings, EMI splits, raw year-file access |
| `backend/app/service/reports/` | Read models: dashboard, cashflow/expense, networth, investments, liabilities, health (doctor, ledger view) |
| `backend/app/service/categorize/` | `Categorizer`, merchant dictionary, learned-rule patterns, fee linking, batch teach |
| `backend/app/service/tax/` | Tax rules loader, declared inputs, tax report |
| `backend/app/service/investments/` | MF NAV and equity price services, investment service |
| `backend/app/service/wiring.py` | `build_services()` — bootstraps both ledgers and builds every store and service |
| `backend/app/persistence/` | `Ledger` (`journal.py`), YAML stores, templates, learned rules, account aliases, in-memory review batches, NAV/price stores, seed data |
| `backend/app/llm/` | Copy-paste categorize prompts and the template-proving flow |
| `backend/app/clients/` | AMFI, mfapi, NSE/bhavcopy feed clients |
| `backend/app/core/` | `paths.py` (repo root), `settings.py`, error codes and exceptions |
| `backend/app/cli/` | Ingest CLI (used by `scripts/ingest_cli.py`) |

## App factory & serving

- **Entry point** — `backend/app/server/__main__.py` `main()` runs `uvicorn.run("backend.app.server.main:create_app", factory=True, host="127.0.0.1", port=8765, reload=False)` (factory mode: importing the module builds nothing). `python -m backend.app` delegates to it. Loopback only, port **8765**.
- **Factory** — `backend/app/server/main.py` `create_app(storage_dir=None, spa_dir=None, clock=date.today) -> FastAPI`. There is no module-level app. `clock` is the app's single source of "today".
- **Wiring** — `create_app` calls `build_services()` (`backend/app/service/wiring.py`), which bootstraps the main ledger and the mutual-fund lot ledger, seeds the copy-paste prompts, and builds every store and service. They are attached to `app.state` (`ledger`, `investment_ledger`, `categorizer`, `review_store`, `template_store`, `pdf_template_store`, `budget_store`, `booking_store`, `price_service`, …). The learned rules, aliases, template, budget, booking and review stores share the journal write lock.
- **Routers** — one `register_*_routes` function per module under `backend/app/server/routes/`: ingest, agent, reports, networth, dashboard, transactions, tax, settings, entries, quotes, prices, liabilities, openings. Most use explicit `/api/...` paths; reports, networth and dashboard use `/api/reports` prefixes.
- **Startup** — a lifespan hook refreshes MF NAVs and equity prices on a daemon thread, best-effort; the server serves at once from prices already on disk.
- **Health** — `GET /api/health` returns `{status, storage_dir, journal_valid, errors}`.
- **SPA mount** — default `dist` at `<repo>/frontend/dist`. If `index.html` exists there, a catch-all `GET /{full_path:path}` serves real files and falls back to `index.html` for client-side routes (paths under `api/` 404; a path-traversal guard requires the candidate to live under the SPA root). If no build is present, `GET /` returns a placeholder HTML string.
- **Demo factory** — `backend/app/server/demo.py` `create_demo_app()` calls `create_app(storage_dir=<repo>/demo/data)`, bypassing the env override. Served by `scripts/demo` via `uvicorn backend.app.server.demo:create_demo_app --factory`.

## Data folder & storage model

- **Resolver** — `backend/app/core/settings.py`: `Settings(BaseSettings)` with `env_prefix="SPENDSIFT_"`, field `storage_dir: Path = Path("storage")` (relative to CWD). Override with env var **`SPENDSIFT_STORAGE_DIR`**. Cached via `get_settings()` (`@lru_cache`).
- **Bootstrap** — `Ledger.bootstrap()` creates subdirs `journal/`, `templates/`, `rules/`, `imports/`, `snapshots/`, then seeds initial files if absent; `prompts/` is created when the prompts are seeded. Tax config is deliberately not seeded (the product ships no opinionated tax rules).

Runtime layout under the data root (full layout in [SCHEMA.md](../../../SCHEMA.md)):

| Path | Contents |
|---|---|
| `journal/main.beancount` | Journal entry point; sets `operating_currency INR`, title, and seed `open`/`commodity` directives |
| `journal/<year>.beancount` | Per-year transactions, `include`d from `main.beancount` |
| `journal/investments.beancount`, `investments-<year>.beancount` | Mutual-fund lot journal (a second `Ledger`) |
| `journal/prices.beancount`, `prices-equity.beancount` | Latest MF NAVs and equity closes, one file per feed |
| `snapshots/<UTC-timestamp>/journal/` | Full copy of `journal/` taken before every write |
| `imports/<filename>` | Uploaded statement files saved during detect |
| `templates/` | Import templates (`TemplateStore`); PDF templates under `templates/pdf/` |
| `rules/learned.yaml` | Learned categorization rules (an account rename rewrites their categories) |
| `rules/dictionary.yaml` | Seeded from `backend/app/persistence/seed_data/dictionary.seed.yaml` |
| `rules/account_aliases.yaml` | Renamed accounts, `old: new`. Written by an account rename, and an entry is dropped when its old name is opened again. Dictionary suggestions are resolved through it (see categorization.md). |
| `rules/loan-terms.yaml` | Loan terms read by liabilities/repayment reports |
| `prompts/categorize.md`, `prompts/categorize-merchants.md` | Seeded from `backend/app/llm/prompts/`; user-owned once copied |
| `config/` | `budget.yaml`, `merge_booking.yaml`, `benchmarks.yaml`, `tax/` |
| `prices/` | Cached AMFI feed and NAV/equity history (parquet) |

Beancount is loaded with beancount's loader (parse cached in memory, see ledger-storage.md) and entries are written with `beancount.parser.printer.format_entry`. Operating currency is INR. All monetary values cross the API as decimal **strings**, never floats.

## Core principles enforced in code

- **Local-first / loopback** — server binds `127.0.0.1:8765` only (`backend/app/server/__main__.py`); no external host, no SaaS dependency.
- **File-based storage** — all state lives under the data directory as beancount + YAML files. There is no database. The frontend cannot write files; it must use the API.
- **Snapshot-before-write** — every `Ledger` write method (`append`, `replace_entries`, `write_year_text`, `annotate_posting`, …) calls `snapshot()` (timestamped full copy of `journal/`) before mutating, validate the whole journal after, and roll back on any error (`LedgerValidationError`).
- **Hash-guarded edits** — raw-text and annotate writes require an `expected_hash`; a mismatch returns HTTP **409** (`HashMismatchError`), preventing blind overwrites.
- **Review-before-import** — `POST /api/import/detect` parses into candidates held in the in-memory `ReviewStore`; nothing reaches the journal until `POST /api/commit/{batch_id}`.
- **No silent ambiguous import** — commit blocks if any candidate is `needs_account`, and skips `exact_dup` / `transfer_already_recorded` candidates. `ImportService._postprocess` runs dedup and transfer matching, the categorizer, EMI routing and fee linking so duplicates and ambiguity surface in review rather than being committed.

## Request → data flow (high level)

Import path:

1. `POST /api/import/detect` — file saved to `imports/`, headers fingerprinted, template candidates matched, statement parsed into `CandidateTxn` rows held in `ReviewStore` under a batch id.
2. `_postprocess` marks dedup status, flags already-recorded transfers, and applies the categorizer.
3. `GET /api/review/{batch_id}` / `POST /api/review/{batch_id}/{candidate_id}` — user inspects and edits candidates (splits validated to sum to the candidate amount; optional `remember` persists a learned rule).
4. `POST /api/commit/{batch_id}` — `commit_candidates(ledger, rows)` snapshots, appends to the per-year journal file, validates, and clears the batch.

Read path:

- `GET /api/reports/*` and `GET /api/transactions*` load the journal (via helpers such as `backend/app/service/reports/health/ledger_view.py`) and compute read models on demand. Internal posting meta keys `lineno`/`filename` (and `txn-id` in serialization) are stripped from responses.

Direct-edit path:

- `GET/PUT /api/ledger/raw` and the posting annotate endpoint allow hash-guarded text edits to a year file, snapshotting and re-validating the whole journal on write.

## Known gaps / divergences

- **Review store is in-memory** — `ReviewStore` (`backend/app/persistence/review_batches.py`) is constructed with no path; review batches do not survive a server restart.
