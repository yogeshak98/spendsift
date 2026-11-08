# SpendSift — Claude Code Guide

Local-first personal finance product. Turn exported bank/card/broker files into a trustworthy journal + dashboards with minimal manual work.

## Ground Truth Docs

Consult before product or architecture decisions:

- [VISION.md](./VISION.md)
- [PRODUCT.md](./PRODUCT.md)
- [TRD.md](./TRD.md)
- [SCHEMA.md](./SCHEMA.md)
- [API_CONTRACT.md](./API_CONTRACT.md)
- [RUNBOOK.md](./RUNBOOK.md)

## Non-Negotiables

- Local-first. File-based storage and interchange.
- No SaaS assumptions. No user-facing database workflow.
- No silent import of ambiguous financial data.
- Review-before-import when confidence is low.
- Snapshot the beancount journal (`<storage_dir>/journal/`) before every write.
- Tax planning is a first-class product surface.
- Long-term UI shell is a local browser web app (React/TS served by FastAPI on 127.0.0.1). Tauri/Streamlit are retired.
- **No stray files in the repo.** Before staging, committing, or pushing — *even when explicitly told to push* — confirm every changed/added file exists in service of a SpendSift goal. Personal notes, scratch plans, unrelated docs (e.g. a relocation plan) must never be tracked. If a file's relevance to the project is unclear, stop and confirm with the user before including it. Temporary working files belong in the scratchpad, not the repo.

## Product Priorities

1. Reliable ingestion from exported financial files.
2. Review-before-import on low confidence.
3. Categorization and tagging that improve over time.
4. Groww-first investment support, then Kite.
5. Unified dashboards: transactions, liabilities, investments, net worth, tax workspace.

## Repo Shape

- `backend/app/server/` — FastAPI loopback service: `main.py` (app factory + static serving on `127.0.0.1:8765`), route modules under `routes/` (`ingest`, `reports`, `transactions`, `tax`, …), `openapi.py`, `demo.py`.
- `backend/app/models/` — pydantic shapes: `api/` (request/response schemas), `domain/` (account taxonomy `accounts.py`, `candidate`, …), `storage/` (`template`, `budget`, …).
- `backend/app/service/ingest/` — import pipeline: `templates/` (`fingerprint`, …), `parsers/`, `pipeline/` (`normalize`, `dedup`, `transfer_match`, `entry_writer`), `validation/`, `ledger_merge/`, `service.py`.
- `backend/app/service/` — also `ledger/`, `tax/` (tax rules), `investments/`, `categorize/` (merchant dictionary, learned rules, copy-paste export/import) and `reports/` — read models feeding the dashboards (`cashflow`, `dashboard`, `liabilities`, `networth`, `investments`, `health`, …).
- `backend/app/persistence/` — beancount journal storage (`journal.py`), review batches (`review_batches.py`), `yaml_stores.py` (budget, booking), NAV/price stores, `seed_data/dictionary.seed.yaml`.
- `backend/app/llm/` — `prompts/categorize.md`, copy-paste prompting, the template-proving flow; `backend/app/clients/` — NAV/price feeds; `backend/app/core/` — paths, settings, errors; `backend/app/cli/` — ingest CLI.
- Import templates are user-created at runtime and saved as YAML under `<storage_dir>/templates/` (gitignored); day-one institution samples are CSV fixtures in `tests/fixtures/statements/` (HDFC, ICICI, IDFC, Groww, Kite).
- `frontend/` — React/TS + Vite app served by FastAPI (Vitest tests).
- `tests/` — pytest suites.
- `scripts/` — `run_app.sh`, `run_demo.sh`, `demo`, `verify_repo.sh`, `build_demo.py`, `ingest_cli.py`, one-off migration helpers.
- `docs/product/` — backend and frontend product docs; `docs/superpowers/spendsift-repo-rules.md` — repo rules the subagents follow.
- On-disk data layout (under `storage/` or `SPENDSIFT_STORAGE_DIR`): see [SCHEMA.md](./SCHEMA.md).

## Working Style

- Treat financial data conservatively.
- Plans, product output, and decisions go in Markdown.
- Prefer options that reduce recurring user effort.
- Keep changes inside the unit's intended scope. Do not revert unrelated files.
- Never mark a unit done without reproducible verification evidence.

## Verification Baseline

Canonical checks live in [RUNBOOK.md](./RUNBOOK.md). Leave evidence the checks can be reproduced.

## Existing Product Assumptions

- Single-user only for now.
- Day-one institutions: HDFC, ICICI, IDFC, Groww, Kite.
- Watched-folder ingestion is the preferred normal path; manual upload feeds the same pipeline.
- Cloud sync is an external plugin outside the app.
- Combined tax workspace is the first tax surface.

## Subagents

Work runs as a declarative reconciliation loop: drive each unit (spec) to its declared desired state.

- `loop-engineering` — orchestrates: dispatches the agents below until every done-gate passes; owns the ledger; never edits code itself.
- `architect` — decomposes a unit's desired-state spec into a build plan (API + data model first, then frontend); writes no implementation code.
- `builder` — implements one unit from its plan (backend/API + tests first, then frontend); commits when green, never pushes.
- `critiq` — judges whether the built unit is the RIGHT thing (approach, design, fit to intent); reports a verdict, fixes nothing.
- `code-reviewer` — judges whether the unit is built WELL (quality, regressions, contract drift, trust/security); reports findings, fixes nothing.
- `ingest-prover` — works out an import template for one statement file via `scripts/ingest_cli.py`; read-only toward the journal.
