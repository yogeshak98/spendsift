# SpendSift — Reports / Read-Models Layer

Pure read models computed over the beancount journal; they feed the dashboard API endpoints and produce no side effects.

> Reflects the implementation as of 2026-11. Facts only.

---

## Purpose

`backend/app/service/reports/` is the read-model layer that sits between the raw beancount journal and the HTTP API (`backend/app/server/routes/reports.py`, `networth.py`, `dashboard.py`). Most modules accept pre-loaded `PostingRow` or `TransactionRow` objects (produced by `health/ledger_view.py`); the newer overview models take a `Ledger` and read it themselves. All return plain `dict` / `list[dict]` shapes whose money fields are decimal strings. No module writes to the journal or to storage.

The package is split into `cashflow/`, `networth/`, `liabilities/`, `investments/`, `dashboard_v2/` and `health/`, plus the legacy `dashboard.py`. The tax workspace lives beside it in `backend/app/service/tax/`.

---

## Read Models

| Module | Purpose | Key public function(s) | Returns |
|---|---|---|---|
| `health/ledger_view` | Shared base — iterates journal entries into typed rows | `all_postings`, `all_transactions`, `all_flow_transactions`, `transactions_in_fy`, `transactions_in_range`, `get_transaction`, `open_accounts`, `open_meta`, `closed_accounts`, `commodity_names`, `in_month`, `top_category` | `list[PostingRow]`, `list[TransactionRow]`, `list[str]` |
| `dashboard` | Legacy homepage summary card | `summary(rows, year, month)`, `account_balances(rows)` | `dict` with `cash_net_worth`, `liabilities`, `spent_this_month`, `liquid`, `investments`, `checking_count`, `investment_kinds`, `accounts` (all money as decimal strings) |
| `dashboard_v2/overview` | Dashboard v2 | `dashboard_overview(src, today, as_of=)` | `dict` with `as_of`, `net_worth`, `cash_flow`, `spending`, `one_time_spends`, `needs_action`; built on `networth_overview` |
| `cashflow/cashflow` | FY income / expense / flow series, P&L, overview totals | `available_years`, `income_statement_pnl`, `monthly_series`, `monthly_series_range`, `statement`, `flow_series`, `overview` | `dict` with decimal-string money; `monthly_series` returns 12-entry FY list; `income_statement_pnl` is multi-FY column table |
| `cashflow/sankey` | Income → accounts → spending flow graph | `sankey`, `contributions` | `{links: [{source, target, value}]}`; contributions as ledger transactions |
| `cashflow/overview`, `cashflow/detail_rows` | Expense / Income Overview and the detail-modal rows | `flow_overview`, `expense_overview`, `rows` | Overview `dict` (headline, series, categories with merchants, trailing months, lines); detail rows with totals |
| `cashflow/expense` | Expense drilldown | `by_category`, `by_day`, `postings`, `trailing_months`, `by_tag`, `by_tag_groups` | `list[dict]`; pct as float; money as decimal strings |
| `cashflow/recurring` | Detect and status recurring income/expense streams | `monthly_recurring`, `recurring_summary`, `stream_key` | `list[dict]` or summary `dict`; status values: `cleared`, `cleared_late`, `past_due`, `upcoming` |
| `cashflow/budget` | Budget-vs-actual for user-configured category targets | `report(rows, config, year, month)` | `dict` with `checking_balance`, `total_budgeted`, `total_spent`, `available_for_budgeting`, `available_for_spending`, `projected_month_end`, `targets` (per-category cards) |
| `cashflow/period` | Date-range helpers for range-based routes | `validate_range`, `range_or_fy`, `fy_range`, `in_range` | `DateRange`; a bad range raises `E1087` |
| `networth/networth` | Cumulative asset / liability / net-worth series and current position | `monthly_series(rows)`, `current_position(rows)` | series keyed by `YYYY-MM` with `assets`, `liabilities`, `net`; prepaid tax excluded from assets |
| `networth/overview` | Net Worth overview | `networth_overview(src, today, as_of=, benchmark=, equity_index=)` | Balance-screen totals, account-tree groups, investment value/gain/XIRR, liability detail, optional benchmarks |
| `networth/assets` | Per-asset-account balances | `balance_rows(rows)` | `list[dict]` |
| `networth/value_history` | Invested-vs-value history | `value_history`, `vehicle_value_history` | series `dict` per instrument or vehicle (`stock`, `mutual_fund`) |
| `liabilities/liabilities` | Liability balances, credit-card drilldowns, FY monthly CC activity | `balances`, `balance_rows`, `credit_card_summary`, `credit_card_detail`, `credit_card_monthly` | `list[dict]`; `balance_rows` takes APR from resolved loan terms only — never derived |
| `liabilities/in_transit` | Card payments booked to the clearing account whose card-side row is not imported yet | `payments_in_transit`, `summary` | `dict` |
| `investments/investments` | Investment holdings at cost basis by vehicle / issuer | `holdings(rows)` | `dict` with `total`, `holdings` (per-account), `by_vehicle`, `by_issuer`; all amounts decimal strings; no market value |
| `investments/investment_hub`, `instrument_detail`, `equity_holdings`, `price_change`, `xirr` | Valued investments | `investment_hub`, `at_cost_holdings`, `instrument_detail`, `equity_holdings`, `portfolio_windows`, `xirr` | Cost basis vs current value from stored NAVs/closes, partial-valuation flags, change windows |
| `liabilities/interest` | Per-account monthly interest charged (attributed from `Expenses:Interest` postings) | `by_account_by_month(transactions)` | `list[dict]` with `account`, `month` (`YYYY-MM`), `amount` |
| `liabilities/amortize` | EMI calculation and full amortization schedule | `emi(principal, annual_rate_pct, months)`, `schedule(principal, annual_rate_pct, months)` | `Decimal` (emi); `list[dict]` with `month`, `emi`, `principal`, `interest`, `balance` |
| `liabilities/repayment` | Per-loan amortization schedule combining ledger balance with resolved terms | `schedules(rows, terms, today, account_meta=, pool_account=, ...)` | `{loans, emi_pool}`; `terms_set: False` when no terms resolve |
| `liabilities/loan_terms` | Loan-terms resolution | `LoanTerms.from_file(path)`, `LoanTerms.get(account)`, `resolve_terms`, `resolve_all` | Terms with `source` = `account` (open metadata), `file` (`rules/loan-terms.yaml`) or `inferred` (from postings), or `None` |
| `health/doctor` | Journal health check | `checks(ledger, investment_ledger, today=)` | `dict` with `journal_valid`, `errors`, `transaction_count`, `uncategorized_count`, `uncategorized`, `negative_assets`, `openings`, plus investment-ledger validity and the MF reconciliation gap |
| `health/uncategorized` | Journal-wide `Expenses:Uncategorized` backlog by merchant | `merchant_backlog`, `backlog_summary` | merchant groups with counts |
| `service/tax/report` | Combined tax workspace: income classification, deductions, old/new regime estimate | `tax_workspace`, `classify_items`, `estimate_regime`, `total_deductions`, `applied_deductions`, `slab_tax`, `rebate`, `surcharge`, `capital_gains_tax`, `gratuity_ceiling` | `dict` with `period`, `items`, `deductions`, `summary` (includes regime estimate when `TaxRules` is configured) |

---

## Conventions

### Decimal-string money

All monetary amounts cross function boundaries as `str(Decimal(...))`. No floats. Integer rounding is applied only inside `amortize` (`.quantize(Decimal("1"))`). The HTTP response models enforce this boundary via Pydantic `response_model` annotations in the router layer; the report functions themselves return plain dicts.

### Pydantic response_model shapes

Report functions return unvalidated `dict` / `list[dict]`. Pydantic validation happens at the HTTP boundary (FastAPI `response_model`, models in `backend/app/models/api/`), not inside the report functions. This means a report function can be called directly in tests without Pydantic overhead.

### `ledger_view` as shared base

Report modules read the journal through `health/ledger_view` rather than calling `beancount` directly. The two core row types are:

- `PostingRow` — immutable dataclass: `date`, `account`, `amount` (signed units), `narration`, `tags`, `meta`, `inr_amount` (INR cost basis; equals `amount` for INR postings).
- `TransactionRow` — immutable dataclass: `txn_id`, `date`, `narration`, `postings: list[PostingRow]`, `year_file_hash`, `tracked`.

`all_postings` walks the full journal; `all_transactions` covers entries with a `txn-id` metadata field and attaches a per-year file SHA-256 hash (the `expected_hash` for optimistic-concurrency writes). `all_flow_transactions` also includes hand-written entries without a `txn-id` (`tracked: False`).

### FY scoping

Indian fiscal year convention throughout: `date(fy, 4, 1)` to `date(fy+1, 3, 31)`. Monthly series are always emitted in FY order Apr to Mar. `cashflow._in_fy` implements this window and `ledger_view.transactions_in_fy` mirrors it. Range-based routes (`from`/`to`) use `cashflow/period` instead.

### Sign convention

- Income accounts carry **negative** numbers (beancount credits); report functions flip the sign to produce positive income totals.
- Liabilities accounts carry **negative** balances; outstanding amounts are reported as positive magnitudes.
- Investment and Asset accounts carry **positive** numbers.

---

## Loan Math

### `amortize`

`emi(principal, annual_rate_pct, months)` computes the standard fixed-payment EMI using the annuity formula. `schedule(...)` produces the full month-by-month amortization table: EMI, principal component, interest component, and remaining balance. Final month clears any rounding residual so the loan ends at exactly zero.

### `interest`

`by_account_by_month(transactions)` scans for transactions with exactly one `Expenses:Interest` or `Expenses:Interest:*` posting, then attributes the charge to the sibling `Liabilities:*` account. Grouped by account and calendar month (`YYYY-MM`).

### `repayment`

`schedules(...)` iterates all open `Liabilities:Loan:*` accounts with a non-zero ledger balance, resolves each account's terms (`loan_terms.resolve_all`), and runs `amortize.schedule`. When no terms resolve, the row is returned with `terms_set: False` and the computed fields as `None`. `original_principal` from the terms overrides the current ledger balance as the schedule starting point. The lumped card-EMI account (`Settings.card_emi_account`) is never inferred and is reported separately as `emi_pool`.

### `loan_terms`

`resolve_terms` takes the first of: terms stored on the account's `open` metadata (set via `PUT /api/accounts/loan-terms`), the legacy `rules/loan-terms.yaml` entry keyed by full account name (e.g. `Liabilities:Loan:HomeLoan`), or terms inferred from the account's own postings (`emi_terms.infer_terms`). `LoanTerms.from_file(path)` requires `annual_rate_pct` and `term_months` per entry, with optional `original_principal`; a missing file, YAML parse error, or invalid entry yields no terms — no fabricated defaults.

---

## Known Gaps / Divergences

### TDS account-convention split (confirmed divergence)

`cashflow/cashflow.py` and `backend/app/service/tax/report.py` use **incompatible account conventions for the same financial fact** (TDS withheld at source):

| Module | Account used | Role |
|---|---|---|
| `cashflow/cashflow.py` | `Income:*:TDS` (positive contra leg under `Income:`) | TDS is treated as a positive contra-credit within the income subtree. `_is_tds` checks `account.startswith("Income:")` and `"TDS" in account.split(":")`. Net income is already reduced. |
| `service/tax/report.py` | `Assets:Receivable:TDS` (and `Assets:Receivable:AdvanceTax`) | **Named explicitly as "the reconciliation source"** in a code comment. The workspace sums these prepaid-tax accounts (`PREPAID_TAX_PREFIXES`) as `prepaid_tax`. |

A journal that records TDS only under `Income:*:TDS` will produce a zero ledger `prepaid_tax` in the tax workspace unless a declared TDS in `config/tax/<fy>-inputs.yaml` replaces it, and the balance-due estimate will be overstated. A journal using only `Assets:Receivable:TDS` will have cashflow's income be gross (not net of TDS). These two conventions are not interchangeable.

### Investments: two prefix conventions

`investments.holdings` reports cost basis only; valuation lives in `investment_hub` / `instrument_detail` / `equity_holdings`, which depend on NAVs and closes fetched by the price routes. The `_INVESTMENT_PREFIX` in `investments/investments.py` uses a trailing colon (`"Assets:Investment:"`) whereas `cashflow._INVESTMENT_PREFIX` does not — a bare `Assets:Investment` leaf account is picked up by cashflow but not by `holdings`.

### Budget: targets from config only

`budget.report` only covers categories that have entries in `BudgetConfig.targets` (`config/budget.yaml`). Spend in categories not listed in targets is silently excluded from `total_spent` and all budget projections.

### `recurring`: narration-based detection

Stream identity (`stream_key`) is `(normalized narration, top_category_account)`. Narration variation (e.g. "AMAZON" vs "AMAZON PRIME") will fragment a stream into separate entries or prevent detection entirely.

### `repayment`: schedule from original principal, not current balance

When `original_principal` is set, the schedule runs from that value regardless of the actual current ledger balance. There is no reconciliation between the projected schedule and the actual repayment history in the ledger.
