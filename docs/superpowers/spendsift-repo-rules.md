# SpendSift — Repo Rules (harness rules file)

The concrete rules the loop-engineering harness agents must follow in this repo.
This file makes the generic architect/builder/critiq/code-reviewer concrete.
On conflict: **user instructions > this file > the generic agent definitions.**
Ground-truth product docs (`CLAUDE.md`, `VISION/PRODUCT/TRD/SCHEMA/API_CONTRACT/RUNBOOK`) override this file on product/architecture questions.

## Stack

- Backend: Python, managed by `uv`. FastAPI app via `create_app()` in
  `backend/app/server/main.py`, bound to `127.0.0.1:8765`, also serves `frontend/dist`.
- Frontend: React 19 + TypeScript + Vite + vitest, in `frontend/`.
- Ledger: Beancount-style double-entry; canonical account taxonomy.

## Where things live (build a dashboard = touch these, in this order)

**1. Backend / API first**
- Read model / query: `backend/app/service/reports/<area>/<domain>.py`
  (existing: `dashboard, networth/networth, cashflow/cashflow, cashflow/expense,
  liabilities/liabilities, liabilities/interest, liabilities/amortize,
  health/doctor, health/ledger_view`).
- Endpoint: register in `backend/app/server/routes/reports.py` inside
  `register_report_routes(app)` as `@router.get("/<path>")` (router prefix `/api/reports`).
- Contract: document the endpoint shape in `API_CONTRACT.md`.
- Tests: `tests/` (pytest). Amounts are decimal **strings**; liabilities are
  **negative**. Match the existing report read-model test patterns.

**2. Frontend second**
- Screen: `frontend/src/screens/<domain>/<Name>.tsx`.
- Route: add the path-key → component to `SCREENS` in `frontend/src/routes.tsx`.
- Nav: entry already declared in `frontend/src/nav.ts` (NAV) — verify it matches.
- Header picker suppression (full-timeline screens): `frontend/src/shell/ContentHeader.tsx`.
- Component test: `frontend/src/__tests__/screens.test.tsx` (or a sibling).

## Reusable frontend kit (use these; do not reinvent)

- `frontend/src/components/`: `Panel` (title, right, children, glow, fill),
  `StatBlock` (label, value, delta, tone, deltaTone; Tone = green|red|amber|blue|dim|text),
  `DataTable` (+ `cell()` in `cell.ts`), `TreeTable`, `Callout`, `Pill`.
- `useReport<T>(url)` → `{data, error}`; `usePeriod()` → `{year, month, setPeriod}`.
- Charts in `frontend/src/charts/` (AreaSeries, StackedAreaSeries, MonthlyCombo,
  Pies, RankedBars, Waterfall) and `frontend/src/shared/charts/` (PeriodHeatmap,
  PeriodBars, Sankey, Segmented, ChartSwitches). Inline a chart only when no shared one fits.

## Theme (midnight)

- Dark/mono. CSS vars in `frontend/src/theme.css`
  (`--bg #0f0f0f, --surface #1a1a1a, --border #2e2e2e, --text #e8e8e8,
  --green #4ade80, --amber #fbbf24, --red #f87171, --blue #60a5fa,
  --violet #a78bfa, --teal #2dd4bf, --pink #f472b6`; fonts IBM Plex Sans/Mono).
- Component styles live next to their screens/components (e.g. `shared/charts/charts.css`,
  `screens/tax/tax.css`). Match existing selectors; watch specificity collisions.

## Verification baseline (done-gates)

Backend:
```
uv run pytest -q
uv run ruff check .
uv run ty check
```
Frontend (run from `frontend/`):
```
npm run lint     # eslint
npm run test     # vitest run
npm run build    # tsc -b && vite build
```
Full: `scripts/verify_repo.sh`. A unit is not DONE until the relevant gates are
green with reproducible evidence.

## Non-negotiables (trust)

- Local-first. File-based storage. No SaaS / no user-facing DB workflow.
- **No invented financial data.** A dashboard shows only what a real endpoint
  returns or the sanctioned demo persona produces. If data is missing, build the
  endpoint to derive it or flag BLOCKED — never fabricate numbers.
- No silent import of ambiguous data; review-before-import on low confidence.
- Snapshot the beancount journal (`<storage_dir>/journal/`) before every write.
- **Demo persona: Jane Doe / fictional names only.** The real `storage/`
  and the real ledger backup are sensitive and off-limits to demo work.
- **No real PII anywhere** (code, demo, fixtures, tests, reports, docs): the
  owner's real name, PAN, phone, bank/card account numbers, masked card digits,
  or address from real statements. Use only the fictional Jane Doe persona with
  invented numbers. The real-token denylist the demo-build guard scans against
  lives in the gitignored `scripts/.pii-denylist.txt` (never committed).

## Workflow / docs

- Specs, build plans and harness progress are local working files and are never
  committed (`docs/` holds usage docs only; see `.gitignore`).

## Commit policy

- Commit **per unit**, conventional messages (e.g. `feat(frontend): ...`,
  `feat(reports): ...`).
- Pre-commit hooks run ruff / ruff-format / gitleaks / ty.
- **Agents never push**; the owner pushes.
