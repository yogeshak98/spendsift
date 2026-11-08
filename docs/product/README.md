# SpendSift — Product Documentation

> Factual product documentation of the implementation as it stands. **Facts only** — what exists now, not what is planned.

SpendSift is a **local-first personal-finance product**. It turns exported bank, card, and broker files into a trustworthy [beancount](https://beancount.github.io/) journal plus Paisa-class dashboards, with minimal manual work and no SaaS. The UI is a local browser web app (React/TS) served by FastAPI on `127.0.0.1:8765`.

## Non-negotiables

- **Local-first, file-based.** No SaaS, no user-facing database. Storage and interchange are files under a runtime data folder (`storage/`, gitignored).
- **No silent import of ambiguous financial data.** Review-before-import when confidence is low.
- **Snapshot the journal before every write.**
- Tax planning is a first-class product surface.

## How the docs are organized

The product splits into a **frontend** (the screens a user operates, one doc per nav domain) and a **backend** (the FastAPI service + read models + ingestion).

### Frontend — one doc per nav domain

| Domain | Doc | Covers |
|---|---|---|
| (cross-cutting) | [frontend/architecture.md](frontend/architecture.md) | Shell, nav/routing, data layer (typed OpenAPI client + legacy fetch), component kit, charts, theme |
| Dashboard | [frontend/dashboard.md](frontend/dashboard.md) | Home, as of today: needs-action list, net-worth breakdown, cash flow, spending averages, one-time spends |
| Net Worth | [frontend/assets.md](frontend/assets.md) | One screen with tabs: Overview, Investments (vehicles, benchmark, prices), Liabilities, Allocation |
| Cash Flow | [frontend/cash-flow.md](frontend/cash-flow.md) | Overview (period chart, Sankey, statement; Compare FYs view), Recurring |
| Expense | [frontend/expense.md](frontend/expense.md) | Overview, By Tag, Budget, Uncategorized |
| Liabilities | [frontend/liabilities.md](frontend/liabilities.md) | Credit Cards, Interest, Repayment, Balance (Net Worth's Liabilities tab) |
| Income & Doctor | [frontend/income-and-doctor.md](frontend/income-and-doctor.md) | Income (Overview, By Tag), Doctor (ledger health checks) |
| Ledger | [frontend/ledger.md](frontend/ledger.md) | Transactions, Raw, Import (stepped wizard, PDF unlock, opening balances, CodeMirror review editor, ledger-file merge), Quick add |
| Planning | [frontend/planning.md](frontend/planning.md) | Goal Planner, SIP, Lumpsum, CAGR, In-Hand Salary, FIRE (client-side calculators) |
| Tax | [frontend/tax.md](frontend/tax.md) | Tax workspace: Overview (declared inputs), Income, Capital Gains, Deductions, Regime Compare, Prepaid Tax |

### Backend

| Doc | Covers |
|---|---|
| [backend/architecture.md](backend/architecture.md) | Package layout, app factory + serving, data folder, enforced principles, request→data flow |
| [backend/ingestion.md](backend/ingestion.md) | Import pipeline: detect → fingerprint → template → parse → normalize → candidate → dedupe/transfer-match → commit |
| [backend/ledger-storage.md](backend/ledger-storage.md) | beancount journal lifecycle, snapshots/safety, account taxonomy, raw year-file edit, optimistic locking |
| [backend/reports.md](backend/reports.md) | Read models feeding every dashboard endpoint; money-as-decimal-string conventions |
| [backend/categorization.md](backend/categorization.md) | Merchant dictionary, learned rules (last-wins), copy-paste export/import |
| [backend/api.md](backend/api.md) | Complete HTTP endpoint inventory + the OpenAPI contract |

## Day-one institutions

HDFC, ICICI, IDFC (banks/cards), Groww and Kite (brokers). Watched-folder ingestion is the preferred path; manual upload feeds the same pipeline.

## Known divergences tracked across the docs

Each doc carries its own "Known gaps / divergences" section. The one cross-cutting correctness item to be aware of: a **TDS account-convention split** between `service/reports/cashflow/cashflow.py` (`Income:*:TDS`) and `service/tax/report.py` (`Assets:Receivable:TDS`) — see [backend/reports.md](backend/reports.md).
