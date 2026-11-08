# SpendSift Release Checklist

## Purpose

This is the go/no-go checklist for the first production-usable local release of SpendSift.

Do not call the product ready until every required item below is true.

## Core Runtime

- [ ] Web app loads in the browser, served by FastAPI on 127.0.0.1
- [ ] FastAPI service starts and serves the built frontend
- [ ] Backend binds only to loopback
- [ ] App status loads through the real HTTP client
- [ ] Dashboard, Ledger (Transactions, Raw, Import), Net Worth (incl. Investments), Tax, and Settings screens load from the real backend

## Ingestion Trust

- [ ] HDFC bank fixture imports correctly
- [ ] HDFC card fixture imports correctly
- [ ] ICICI bank fixture imports correctly
- [ ] ICICI card fixture imports correctly
- [ ] IDFC bank fixture imports correctly
- [ ] IDFC card fixture imports correctly
- [ ] Groww orders fixture imports correctly
- [ ] Kite orders fixture imports correctly (Kite import is not built yet)
- [ ] Duplicate file imports do not duplicate journal entries
- [ ] Unknown shapes route to review instead of silent import
- [ ] Low-confidence categories route to review instead of silent import

## Data Safety

- [ ] Journal (`journal/*.beancount`) writes create a snapshot in `snapshots/` first
- [ ] A write that fails validation restores its snapshot
- [ ] Learned rules and templates (YAML) remain readable after repeated saves
- [ ] Nothing pending review is written to the journal before commit

## Derived Views

- [ ] Transaction list reads from canonical sources through read models
- [ ] Review list includes source context
- [ ] Groww activity flows into holdings views (Kite: not built)
- [ ] Tax workspace explains why items appear
- [ ] Dashboards recompute from the journal with no stale derived files

## Verification

- [ ] `scripts/verify_repo.sh` passes (backend pytest, frontend tests, frontend build)
- [ ] `uv run pytest -q` passes
- [ ] `uv run ruff check .` and `uv run ty check` pass
- [ ] `npm run lint`, `npm run test` and `npm run build` in `frontend/` pass

## Repo Hygiene

- [ ] Generated frontend build artifacts are ignored
- [ ] Local data artifacts are ignored
- [ ] Runbook is accurate

## Final Go / No-Go

- [ ] No known blocker remains in the critical path
- [ ] No known trust regression remains open
- [ ] Product is usable without prompting an agent for routine operation
