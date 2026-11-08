# SpendSift HTTP API

FastAPI loopback service (127.0.0.1:8765) — the sole interface between the React frontend and the Python financial core.

> Reflects the implementation as of 2026-11. Facts only.

## Purpose

SpendSift's HTTP API is a locally bound FastAPI service that owns all financial logic, ledger writes, and derived read models. The React frontend never touches the journal files directly — all reads and writes go through typed HTTP commands. `create_app()` in `backend/app/server/main.py` registers one `register_*_routes` function per module under `backend/app/server/routes/` (`ingest`, `agent`, `reports`, `networth`, `dashboard`, `transactions`, `tax`, `settings`, `entries`, `quotes`, `prices`, `liabilities`, `openings`) plus a health check in `main.py`, all under `/api/`. The built SPA (`frontend/dist`) is served from `/` after every API route.

---

## Endpoint Inventory

### Health

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/health` | — | `{status, storage_dir, journal_valid, errors[]}` | Ledger validity check; returns error list on parse failure |

---

### Import / Templates

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| POST | `/api/import/detect` | `multipart/form-data` (file) | `{matched, batch_id?, fingerprint, candidates?[], raw_rows?, suggested_*}` | Upload a bank/card export; if a saved template matches returns parsed candidates, otherwise returns raw rows + suggested column roles for template creation. When a template matches, the response also carries the additive `skipped_rows` (`{row_index, reason, cells}`, as on `POST /api/templates`), and a row that would parse but sits after the template's stop row is kept as a candidate with the flag `after the template's stop row — check this row`. A PDF (bytes start `%PDF-`) skips the CSV handling: a locked one returns `{matched: false, kind: "pdf_locked", filename}`; one no saved PDF template matches returns `{matched: false, kind: "pdf_unknown", filename, message: "New card layout. Run /ingest-stmt on this file in Claude Code once."}`; a match stages through the shared PDF validator and returns `{matched: true, batch_id, fingerprint, candidates[], skipped_rows[], statement, warnings[]}` (`statement` as on `/api/agent/stage`; `warnings` is the check-15 manual-sections message per declared panel, e.g. an EMI panel, whose rows import as ordinary rows; empty when there is none). 422 E1069 for a PDF with no text or a damaged file; 422 E1070 (`PDF_STATEMENT_MISMATCH`, the browser wording of E1081) when the parse fails validation (including a summary mismatch), with nothing staged |
| POST | `/api/import/unlock` | `multipart/form-data` (file, `password` body field) | the `/api/import/detect` response for the unlocked copy | Decrypts a locked PDF in memory and detects it as `<stem>-unlocked.pdf`; only the decrypted copy is written to `storage/imports/`. The password is never stored, logged or returned. 422 E1068 wrong, empty or absent password, 422 E1069 damaged file, 422 E1070 summary mismatch. Request-validation 422s on this route omit `input` and `ctx` from each `detail` entry |
| POST | `/api/import/classify` | `multipart/form-data` (file, `force_kind?` = `ledger`\|`statement`) | `{kind: "ledger", ledger}` or `{kind: "statement", statement}` | Routes one upload: a ledger-cli/hledger journal goes to the ledger-merge preview, anything else to `/api/import/detect`. An unknown `force_kind` is a coded 422 |
| POST | `/api/templates` | `TemplateDraft` JSON body | `{saved, batch_id?, fingerprint, candidates[], skipped_rows[]}` | Create or dry-run a new parse template; `save=false` previews without persisting. `skipped_rows` is additive: a list of `{row_index, reason, cells}` for rows the parser skipped |
| POST | `/api/templates/{fingerprint}/resolve-marker` | `MarkerResolution` | `{updated: int}` | Map one account-marker key (or default) to a beancount account, patch in-memory batch |
| DELETE | `/api/review/{batch_id}/{candidate_id}` | — | `{ok: true, dropped: int}` | Drop one candidate from a review batch; 404 E1006 / E1007 |
| GET | `/api/review/{batch_id}` | — | `{candidates[], statement}` | Fetch all candidates in an in-memory review batch. `statement` is the batch's PDF card-statement summary (`account`, `period_start`, `period_end`, `previous_balance`, `payments_credits`, `purchases_debits`, `finance_charges`, `cash_advances`, `other_charges`, `total_due`, `minimum_due`, `due_date`, `credit_limit`, `labels`) or `null`; on a layout that prints payments and other credits separately, `payments_credits` is their sum |
| GET | `/api/review/{batch_id}/learning` | — | `{will_learn[], not_learned[], would_open[]}` | Preview of what commit would learn (`will_learn: {pattern, category, candidate_ids[], replaces}`), what it will not and why (`not_learned: {candidate_id, pattern, reason}`), and the accounts it would open; read-only; 404 E1006 |
| POST | `/api/review/{batch_id}/{candidate_id}` | Patch dict (arbitrary fields + optional `splits[]`, `suggested_category`, `tags[]`, `learn`, `follows: null`; `remember` is ignored) | `{ok: true}` | Edit a single candidate; splits must sum to the transaction amount. `tags` must be a list of strings (else 422 E1037) and each is normalised to a beancount tag (422 E1072 if it cannot be). `suggested_category` must be a valid account name (422 E1017); an unopened account is allowed. A category set here spreads to same-merchant rows (`category_source: "taught_batch"`) and fee rows follow their charge (`follows`). `category_source` is app-set (422 E1037). 404 E1006 / E1007 |
| POST | `/api/commit/{batch_id}` | — | `{committed: int, uncategorized: int, unexplained: int, statement_recorded: bool, learned: [{pattern, category, replaces}], opened: string[]}` | Commit non-duplicate candidates to the ledger; rejects if any `needs_account` is unresolved. `statement_recorded` is `true` when the batch's PDF card-statement record (`custom "card-statement"`, dated `period_end`) was written in the same append as the rows; it is written at most once per card and `period_end`. A batch carrying a card statement is re-checked at commit (parsed debits against purchases + cash advances + other charges, credits against payments, every row on the statement's card; the same expected totals as the stage-time check); a batch that no longer reconciles after review edits or rejections is refused 422 E1070 (agent commit: E1081) and nothing is written. Rules for rows whose category was set in review are written to learned.yaml only after the journal write succeeds (learned); opened lists the accounts the commit opened. Unknown batch 404 E1006 |
| GET | `/api/review/{batch_id}/opening` | — | `OpeningPromptResponse` | The first-import opening-balance prompt for the batch's account |
| GET | `/api/schemes/search` | `?q=<str>&limit=<int>` | `SchemeSearchResponse` | Search the mutual-fund scheme master |
| POST | `/api/review/{batch_id}/{candidate_id}/resolve-scheme` | `{commodity, scheme_name}` | `{ok: true, resolved: int}` | Resolve an MF row's `unresolved_scheme` to a commodity |
| GET | `/api/categorize/export/{batch_id}` | — | `{text, index}` | Build an LLM copy-paste prompt for uncategorised candidates |
| POST | `/api/categorize/import/{batch_id}` | `{text, index}` | `{applied: int}` | Parse LLM categorisation response and apply it to the batch as categories set in review; the rules are learned at commit, not here. 422 E1017 for an invalid account, before any row is applied |
| POST | `/api/import/ledger/preview` | `multipart/form-data` (file) | `LedgerMergePreviewResponse` | Parse a ledger-cli/hledger journal into a merge preview (accounts and commodities to map, duplicates) |
| POST | `/api/import/ledger/commit` | `{batch_id, account_map, commodity_map, include_duplicates}` | `LedgerMergeCommitResponse` | Write the mapped preview to the beancount journal |
| DELETE | `/api/import/ledger/{batch_id}` | — | `{discarded}` | Drop an abandoned ledger-merge preview; 404 E1006 |
| GET | `/api/import/investment/cash-balance` | — | `{account, balance, currency}` | Balance of the broker wallet (cash) account |

**`TemplateDraft` fields (all required unless noted):**

| Field | Type | Notes |
|-------|------|-------|
| `filename` | string | Filename uploaded via `/api/import/detect` |
| `fingerprint` | string | Header fingerprint from detect |
| `institution` | string | Human label (e.g. `HDFC`) |
| `header_row_index` | int | 0-based row index of the CSV/Excel header |
| `date_column` | string | Column name |
| `date_format` | string | strptime format string |
| `narration_column` | string | Column name |
| `delimiter` | string | Default `","` |
| `debit_column` | string\|null | Optional |
| `credit_column` | string\|null | Optional |
| `amount_column` | string\|null | Optional (single-column amount) |
| `sign_column` | string\|null | Optional |
| `credit_indicators` | string[] | Default `["Cr","CR","Credit"]` |
| `account_marker_pattern` | string\|null | Regex for account disambiguation |
| `default_account` | string\|null | Fallback beancount account |
| `stop_pattern` | string\|null | Optional. Regex; parsing stops at the first row matching it **that would not itself parse as a transaction**. A parseable row is never taken for a footer. The pattern is matched against the row's cells joined by spaces and stripped |
| `skip_row_patterns` | string[] | Optional, default `[]`. Regexes; matching rows are skipped (same stripped-text matching and never-swallows-a-parseable-row rule as `stop_pattern`). Blank and catch-all entries (`.*`, `.+`, `^.*$`) are refused |
| `skip_exact_narrations` | string[] | Optional, default `[]`. Narrations to skip verbatim |
| `amount_sign` | string | Optional, default `"indicator"` |
| `balance_column` | string\|null | Optional. Running-balance column, used by agent validation |
| `account_marker_map` | object | Optional, default `{}`. Marker key to beancount account |
| `opening_balance` | object\|null | Optional. Where the statement prints its opening balance outside the data rows: `{label, row_offset}`. `label` is a regex (must compile, must not match the empty string) matched against each cell's stripped text; exactly one cell may match. `row_offset` is 0 to 5, default 0: 0 reads the first parseable cell to the right of the label, n reads the cell n rows below it. Rows dropped by `skip_exact_narrations` count as data rows |
| `save` | bool | `true` = persist template; `false` = dry-run preview only |

**Agent ingestion** (`/api/agent/*`, `AgentIngestService` in `backend/app/llm/flows/prove_template/service.py`; driven by `scripts/ingest_cli.py` / `backend/app/cli/ingest.py`; full contract in `API_CONTRACT.md`, "Agent ingestion"). Besides the PDF routes below: `GET /api/agent/peek/{prove_id}` (masked head/tail of the stored file), `POST /api/agent/dry-run` (`{prove_id, draft, claims}`, one table-template proving attempt), `GET /api/agent/dry-run/{prove_id}?rows=` (a window of the last dry-run) and `POST /api/agent/commit/{batch_id}` (the agent-side commit; refusals use E1081).

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| POST | `/api/agent/inspect` | `multipart/form-data` (file; `kind?`, `upgrade?`, `source_path?`) | `{prove_id, stored_name, sha256, format, total_rows, pages, template_match, guess, dedicated, encrypted, error, …}` | For a PDF: `format: "pdf"`, `total_rows` = text lines, `pages`, `template_match` = `{fingerprint, institution, origin}` from the saved PDF templates or `null`, `guess` and `dedicated` `null`. A locked PDF returns `encrypted: true` and an `error` with the terminal unlock command, naming `source_path` (the path the CLI was given; used only in this message, never stored) or else the uploaded filename, shell-quoted; a PDF with no text returns `error: "the PDF has no selectable text (scanned PDFs are not supported)"`. Other files gain `format: "table"`, `encrypted: false` |
| POST | `/api/agent/pdf/dry-run` | `{prove_id, draft}` (PDF template fields, no claims) | the `dry-run` output plus `statement` | One proving attempt (5 per run). A draft that cannot be built is a failed `template` check, not a 422. The card is derived from the file: with none or several card numbers check 17 fails and `fingerprint` is `null`. 422 E1079 on a table run |
| POST | `/api/agent/stage` | `{prove_id, use}` | `{batch_id, fingerprint, source: "pdf_template", report, statement, candidates[], candidate_rows[], skipped_rows[], template_saved, replaced?}` | On a PDF run: `proof` saves the PDF template and stages; `template_match` uses the saved one. Refuses an unknown (E1077) or non-card (E1079) account and any failing check (E1081, including a summary mismatch). `proof` refuses an upgrade proved on another card's statement (422 E1067) and an account a saved template for another card already uses (409 E1071), before anything is saved or deleted |

`statement` is `{period_start, period_end, roles: {role: {label, value}}}`: ISO dates, every value a string, `label` the text the value was read under. A PDF statement must name exactly one card number, the saved layout's own (check 17 `card_number`; messages name line numbers, never digits).

**`MarkerResolution` fields:**

| Field | Type | Notes |
|-------|------|-------|
| `batch_id` | string | |
| `marker_key` | string\|null | `null` = update default account |
| `account` | string | Beancount account path |

---

### Transactions / Annotate

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/accounts` | — | `{accounts: string[]}` | All open beancount accounts |
| GET | `/api/transactions` | — | `{transactions[]}` | All committed transactions (unscoped) |
| GET | `/api/transactions/{txn_id}` | — | `LedgerTransaction` or 404 | Single transaction by ID |
| POST | `/api/transactions/{txn_id}/postings/{account}/annotate` | `{expected_hash, ...meta fields}` | `LedgerTransaction` | Append string metadata to one posting; `expected_hash` is required and must match the year-file hash (409 on mismatch) |
| POST | `/api/entries` | `EntryIn` (`{date, narration, postings[], tags[]}`) | `{txn_id}` | Quick add: append one hand-entered transaction |

**`LedgerTransaction` shape** (also `LedgerTransactionsResponse.transactions[]`):

| Field | Type | Notes |
|-------|------|-------|
| `txn_id` | string | |
| `date` | string | ISO `YYYY-MM-DD` |
| `narration` | string | |
| `year_file_hash` | string | SHA of the year file; used as `expected_hash` in annotate |
| `postings[]` | array | See below |
| `postings[].account` | string | |
| `postings[].amount` | string | Decimal string |
| `postings[].tags` | string[] | Sorted |
| `postings[].meta` | dict[str,str] | String-valued only; beancount internal keys (`lineno`, `filename`) are stripped |

---

### Settings — Accounts / Templates

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| POST | `/api/accounts` | `{accounts[]}` | `AccountsCreateResponse` | Open new accounts |
| POST | `/api/accounts/rename` | `{account, new_name}` | `AccountRenameResponse` | Rename an account across the journal and the stores that name it; 404 when not open |
| POST | `/api/accounts/close` | `{account}` | `AccountCloseResponse` | Close an account |
| GET | `/api/accounts/card-properties` | — | `{cards[]}` | Per-card statement day, due day, last 4 digits, expiry |
| PUT | `/api/accounts/card-properties` | `{account, statement_day, due_day, last4, expiry?}` | `CardProperties` | Set one card's properties (omitting `expiry` keeps it) |
| PUT | `/api/accounts/card-properties/batch` | `{cards[]}` | `{cards[]}` | Set several cards at once |
| PUT | `/api/accounts/loan-terms` | `{account, annual_rate_pct, term_months, original_principal, label}` | `LoanTermsResponse` | Store loan terms on the account |
| GET | `/api/templates` | — | `{templates[]}` | Saved CSV/XLS templates |
| GET / PUT / DELETE | `/api/templates/{fingerprint}` | PUT: `Template` | `Template` / 204 | Read, replace (body fingerprint must match the path) or delete one template |

### Openings

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/openings` | — | `OpeningsStatusResponse` | Opening-balance status per balance-sheet account |
| POST / PUT | `/api/openings` | `OpeningCreateRequest` / `OpeningCorrectRequest` | `OpeningWriteResponse` | Add or correct an opening balance |
| POST | `/api/openings/zero` | `OpeningZeroRequest` | `OpeningZeroResponse` | Mark an account as opening at zero |
| POST | `/api/openings/mf-lots` | `MfOpeningRequest` | `MfOpeningResponse` | Opening mutual-fund lots |

### Liabilities — Card EMI plans

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/liabilities/emi-plans/proposal` | — | `EmiSplitProposalResponse` | Read-only proposal to split the lumped card-EMI account into per-plan accounts |
| POST | `/api/liabilities/emi-plans/split` | `{plans[]}` | `EmiSplitResponse` | Apply an approved split (`{moved, opened, plans, closed}`) |

### Tax inputs

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/tax/inputs` | `?fy=<int>` | `TaxInputsResponse` | The user-declared `config/tax/<fy>-inputs.yaml` (regime, income, deductions) |
| PATCH | `/api/tax/inputs` | `{fy, revision, regime?, income?, deductions?}` | `TaxInputsResponse` | Patch fields; `revision` guards concurrent edits |
| POST | `/api/tax/inputs/replace` | `{fy, revision, text}` | `TaxInputsResponse` | Replace the file's raw text |

### Prices / Quotes

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| POST | `/api/prices/refresh`, `/api/prices/backfill` | — | untyped dict | Fetch latest / historical mutual-fund NAVs (AMFI) |
| GET | `/api/prices/status` | — | untyped dict | MF NAV store status |
| POST / GET | `/api/prices/equity/refresh`, `/api/prices/equity/status` | — | untyped dict | Equity closes (NSE bhavcopy) |
| GET | `/api/prices/freshness` | — | untyped dict | One row per price feed: how current each part of the portfolio is |
| GET | `/api/quotes/random` | — | `{quote}` | A random quote for the UI |

---

### Ledger / Raw

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/ledger/raw` | `?year=<int>` | `RawLedgerResponse` | Read the verbatim text of one calendar-year ledger file + its SHA hash |
| PUT | `/api/ledger/raw` | `RawLedgerWriteRequest` | `RawLedgerWriteResponse` | Write new text to a year file; 409 on hash mismatch, 422 on beancount validation error |

**Models:**

`RawLedgerResponse` — `{year: int, text: string, hash: string}`

`RawLedgerWriteRequest` — `{year: int, text: string, expected_hash: string}`

`RawLedgerWriteResponse` — `{year: int, hash: string}`

---

### Reports — Available Years

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/available-years` | — | `AvailableYearsResponse` | Calendar and Indian fiscal years present in the ledger |

`AvailableYearsResponse` — `{calendar_years: int[], fiscal_years: int[]}`. Both lists are ascending; empty when ledger has no data.

---

### Reports — Dashboard

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/dashboard` | `?year=<int>&month=<int>` | `DashboardResponse` | Home-screen headline figures: net worth, liquidity, investments, spent this month, per-account balances |
| GET | `/api/reports/dashboard/v2` | `[?as_of=<date>]` | `DashboardV2Response` | Dashboard v2 body: `as_of`, net worth, cash flow, spending, one-time spends, needs-action items; built on the Net Worth overview read model |

`DashboardResponse` fields: `cash_net_worth`, `liabilities`, `spent_this_month`, `liquid`, `investments` (all decimal strings); `checking_count`, `investment_kinds` (ints); `accounts[]` (`{account: string, amount: string}`). Liabilities are sign-flipped positive.

---

### Reports — Expense

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/expense/monthly` | `?year=<int>&month=<int>` | `{by_category[], by_day{}, postings[], trailing[]}` | Monthly expense breakdown by category, by calendar day, individual postings, and 6-month trailing trend |
| GET | `/api/reports/expense/overview` | `?from=<date>&to=<date>[&funding=all\|card\|bank\|other][&account=<str>][&exclude_one_time=<bool>]` | `ExpenseOverviewResponse` | Expense Overview for a date range: headline, series, categories with merchants, trailing months, lines; filterable by funding source. Returns `ExpenseOverviewResponse` (adds `exclude_one_time` and `one_time`) |
| GET | `/api/reports/income/overview` | same as `expense/overview`, without `exclude_one_time` | `FlowOverviewResponse` | Income Overview: the same read model on the `Income:` side |
| GET | `/api/reports/expense/tags` | `[?from=<date>&to=<date>][&kind=expense\|income]` | `TagsResponse` | Spending (or income) aggregated by transaction tag, and grouped by `/` prefix; all-time without a range |
| GET | `/api/reports/expense/budget` | `?year=<int>&month=<int>` | `{checking_balance, total_budgeted, total_spent, available_for_budgeting, available_for_spending, projected_month_end, targets[]}` | Budget tracker: joins `config/budget.yaml` targets with actual ledger spend |

`fy` convention: Indian fiscal year start (`fy=2026` = Apr 2026–Mar 2027). All amounts are decimal strings.

---

### Reports — Cashflow

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/cashflow/overview` | `?from=<date>&to=<date>` | `CashflowOverviewResponse` | Cash Flow Overview for a date range: totals (income, expenses, invested, saved, savings rate), by-month series, statement |
| GET | `/api/reports/cashflow/monthly` | `?fy=<int>` | `CashflowMonthlyResponse` | 12-point FY series: income, expenses, liabilities, investment, tax, balance (all decimal strings) |
| GET | `/api/reports/cashflow/sankey` | `?fy=<int>` or `?from=<date>&to=<date>` | `SankeyResponse` | Income→Checking→Expense flow links for a Sankey diagram |
| GET | `/api/reports/cashflow/sankey/contributions` | `?source=<str>&target=<str>` plus `fy` or `from`+`to` | `LedgerTransactionsResponse` | Ledger transactions that make up one Sankey link |
| GET | `/api/reports/cashflow/rows` | `fy` or `from`+`to`, plus `node`, `source`+`target`, or `category`; `side`, `depth` | `CashflowRowsResponse` | Detail rows behind a Sankey node/link or a range's income/spending lines (`{start, end, totals, by_category[], rows[]}`); a bad selector combination is 422 E1098 |
| GET | `/api/reports/cashflow/pnl` | `?fys=<CSV ints>` | `PnlResponse` | Multi-FY P&L matrix: Income and Expenses sections, per-column totals and net |
| GET | `/api/reports/cashflow/recurring` | `?year=<int>&month=<int>` | `{summary{}, items[]}` | Detected recurring flows (subscriptions, EMIs, salary) with status for the selected month |

**`CashflowMonthlyResponse`** — `{series: [{month, income, expenses, liabilities, investment, tax, balance}]}` (12 points, Apr→Mar, all decimal strings).

**`SankeyResponse`** — `{links: [{source, target, value}]}` — value is decimal string.

**`PnlResponse`** — `{fys[], columns[{fy,label}], sections[{key,label,line_items[{category,amounts[]}],totals[]}], net[]}` — all amounts decimal strings.

**Recurring `items[]` shape** — `{key, label, account, cadence, status, direction, amount, expected_amount, expected_day, expected_date, last_seen, actual_dates[]}`. `cadence` ∈ `monthly|quarterly|yearly`; `status` ∈ `cleared|cleared_late|past_due|upcoming`; `direction` ∈ `out|in`.

---

### Reports — Net Worth

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/networth` | — | `NetworthResponse` | `{series[], current}`: all-time monthly `{month, assets, liabilities, net}` series plus today's position (prepaid tax excluded, assets grouped by second account segment) |
| GET | `/api/reports/networth/overview` | `[?as_of=<date>&benchmark=<str>&equity_index=<str>]` | `NetworthOverviewResponse` | Net Worth overview: Balance-screen totals, account-tree groups, investment value/gain/XIRR, liability detail, optional benchmarks from `benchmarks.yaml` (a bad file costs only the benchmark parts) |
| GET | `/api/reports/assets/balance` | — | `{balances[]}` | Per-asset-account balances (untyped) |

---

### Reports — Investments

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/investments` | — | `InvestmentsResponse` | Portfolio at cost basis: total, per-account holdings, aggregated by vehicle and issuer |
| GET | `/api/reports/investments/summary` | — | `InvestmentHubResponse` | Investments hub: per-vehicle cost basis and current value (MF from the investment sub-ledger, other vehicles from the main ledger), partial-valuation flags |
| GET | `/api/reports/investments/gains` | — | `InvestmentGainsResponse` | Mutual-fund holdings valued at stored NAVs: `{as_of, totals, holdings[], unpriced[], unpriced_count}` |
| GET | `/api/reports/investments/equity` | — | `EquityHoldingsResponse` | Stock holdings valued at stored closes |
| GET | `/api/reports/investments/changes` | — | `InvestmentChangesResponse` | Value change over windows (portfolio and per fund) |
| GET | `/api/reports/investments/vehicles/{vehicle_key}/holdings` | — | `AtCostHoldingsResponse` | Holdings of an at-cost vehicle (e.g. FD); 404 for unknown or priced vehicles |
| GET | `/api/reports/investments/vehicles/{vehicle_key}/value-history` | — | `ValueHistoryResponse` | Invested-vs-value series for a whole vehicle (`stock`, `mutual_fund`) |
| GET | `/api/reports/investments/{vehicle_key}/{instrument}` | — | `InstrumentDetailResponse` | One instrument's detail; 404 for an unknown vehicle or instrument |
| GET | `/api/reports/investments/{vehicle_key}/{instrument}/history` | — | `ValueHistoryResponse` | One instrument's value history |

`InvestmentsResponse` — `{total, holdings[{account,vehicle,issuer,instrument|null,amount}], by_vehicle[{vehicle,amount}], by_issuer[{vehicle,issuer,amount}]}`. Amounts are decimal strings. This route is at cost basis only; market value comes from the `summary`/`equity`/instrument routes, which read NAVs and closes stored by the price routes.

---

### Reports — Liabilities

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/liabilities/balance` | — | `LiabilitiesBalanceResponse` | `{balances[], in_transit}`: per-liability-account drawn, repaid, outstanding, interest, APR with its source; `in_transit` = card payments booked to the clearing account whose card-side row is not imported yet |
| GET | `/api/reports/liabilities/credit-cards` | — | `CreditCardSummaryResponse` | All-time credit card summary `{cards[], in_transit}` |
| GET | `/api/reports/liabilities/credit-cards/monthly` | `?fy=<int>&account=<str?>` | `CreditCardMonthlyResponse` | FY monthly credit card: purchases, payments, outstanding per month |
| GET | `/api/reports/liabilities/credit-cards/{account}` | — | `{account, transactions[]}` | Transaction-level detail for one credit card account |
| GET | `/api/reports/liabilities/repayment` | — | `RepaymentResponse` | `{loans[], emi_pool}`: per-loan amortisation schedule; `terms_set: false` when no terms resolve |
| GET | `/api/reports/liabilities/interest` | — | `{rows[]}` | Interest charges by `(account, month)` pair (positive magnitudes, `YYYY-MM`) |

**`CreditCardMonthlyResponse`** — `{fy, account|null, cards[], opening, months[{month,purchases,payments,outstanding}]}`. All amounts decimal strings.

**Balance row** — `{account, drawn, repaid, balance, interest, apr: string|null, apr_source, direction, amount}`. `amount` is the magnitude of the ledger net and `direction` its sense; `balance` is the legacy `-net` and may be negative. APR is never derived from postings by this route: account-set or inferred terms win, else the `rules/loan-terms.yaml` entry (`apr_source: "file"`), else null.

**Repayment loan** — `{account, label, card, terms_set, terms_source, outstanding, original_principal, annual_rate_pct, term_months, paid_instalments, start_month, emi, total_interest, payoff_month, schedule[{month,emi,principal,interest,balance}]}` (nullable except `account`, `terms_set`, `outstanding`, `schedule`). Terms resolve from the account's `open` metadata (set via `PUT /api/accounts/loan-terms`), then `rules/loan-terms.yaml`, then inference from the account's postings (`terms_source` = `account`|`file`|`inferred`). `schedule` is empty when `terms_set` is false. `emi_pool` reports the lumped card-EMI account, which is never inferred.

---

### Reports — Tax Workspace

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/tax/workspace` | `?fy=<int>` | `TaxWorkspaceResponse` | Ledger-derived tax items, declared Chapter VI-A deductions, old/new regime tax estimates |
| GET | `/api/reports/tax/gratuity-ceiling` | — | `GratuityCeilingResponse` | All-time recorded gratuity against the s.10(10) ceiling from the current FY's rules file (null ceiling when not configured) |

`TaxWorkspaceResponse` — `{period, items[{kind,amount,source_refs[]}], deductions[{section,declared,applied_old,applied_new}], summary{gross_ordinary_income, capital_gains{ltcg,stcg,crypto,debt}, prepaid_tax, tax_paid_direct, estimate|null, rules_configured, uncovered[], provenance{gross_salary,tds -> {source,value,ignored_amount,ignored_postings}}, salary_is_net, declared_tds_at_or_above_gross, unrecognised_income_keys[]}}`. `estimate` is null when no `config/tax/<fy>.yaml` exists. The `income` map of `config/tax/<fy>-inputs.yaml` (`gross_salary`, `tds`) REPLACES the matching ledger-derived figure outright, and `provenance` names the source used and the ledger amount/transaction count it overrode; an unreadable declared value is a coded 422 `E1051`. All amounts decimal strings.

---

### Reports — Ledger Transactions (FY-scoped)

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/ledger/transactions` | `?fy=<int>` | `LedgerTransactionsResponse` | All transactions in one Indian fiscal year; strips internal beancount meta keys |

---

### Reports — Doctor

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/doctor` | — | untyped dict | Ledger health checks (`backend/app/service/reports/health/doctor.checks`): validity, counts, uncategorized backlog, negative assets, openings, MF reconciliation |

---

### Reports — Uncategorized backlog

| Method | Path | Request | Response | Description |
|--------|------|---------|----------|-------------|
| GET | `/api/reports/uncategorized` | — | untyped dict | Journal-wide `Expenses:Uncategorized` backlog grouped by merchant |
| POST | `/api/reports/uncategorized/classify` | `{merchant, account}` | `ClassifyMerchantResponse` | Recategorise every backlog row of one merchant and learn the rule |
| GET | `/api/reports/uncategorized/export` | — | `MerchantExportResponse` | Copy-paste prompt numbering every backlog merchant |
| POST | `/api/reports/uncategorized/import` | `{text, index}` | `ClassifyMerchantsResponse` | Apply a pasted answer sheet; bad lines are reported, not fatal |

---

## Conventions

### Money representation

All monetary amounts are **decimal strings** throughout the API — never `float`. This prevents IEEE-754 precision loss on financial data. The Python boundary serialises with `str(decimal)`. TypeScript consumers should parse with a decimal library (e.g. `Decimal.js`), not `parseFloat`.

### Indian Fiscal Year (`fy` param)

`fy` is the April-start year integer. `fy=2026` covers 1 Apr 2026 – 31 Mar 2027. Routes that accept `fy` use the same windowing as `cashflow._in_fy` (`backend/app/service/reports/cashflow/cashflow.py`).

### `response_model` validation

Routes annotated with `response_model=` are validated by Pydantic before the response is sent. Model definitions live in `backend/app/models/api/`. Routes without a `response_model` annotation return raw dicts (no server-side shape guarantee to the frontend).

### Error mapping

| Status | When |
|--------|------|
| 404 | Resource not found (unknown batch_id, txn_id, template fingerprint, account) |
| 409 | Optimistic-concurrency conflict — `expected_hash` does not match current file hash; client must re-fetch and retry |
| 422 | Validation failure — malformed input, unresolved accounts, splits that don't sum, bad `fys` param |
| 500 | Unexpected server error |

Coded errors (`backend/app/core/errors/`) render as `{code, description, detail, resolution, resolvedByRefresh, data?}` via `backend/app/server/middleware/errors.py`; any unclaimed exception becomes `E0000` (500). FastAPI emits its own 422 for Pydantic request-model parse failures before route handlers run.

### Common query parameters

| Param | Used by | Notes |
|-------|---------|-------|
| `year` | expense/monthly, expense/budget, dashboard, cashflow/recurring | Calendar year int |
| `month` | expense/monthly, expense/budget, dashboard, cashflow/recurring | 1–12 |
| `fy` | cashflow/monthly, cashflow/sankey, cashflow/rows, cashflow/pnl (as `fys`), liabilities/credit-cards/monthly, reports/ledger/transactions, tax/workspace, tax/inputs | Indian FY start year int |
| `from`, `to` | expense/overview, income/overview, cashflow/overview, expense/tags, cashflow/sankey(/contributions), cashflow/rows | ISO dates, inclusive; both or neither where optional; a bad range is `E1087 PERIOD_RANGE_INVALID` (422) |
| `fys` | cashflow/pnl | Comma-separated FY start years |
| `as_of` | dashboard/v2, networth/overview | Optional ISO report date; nothing dated after it counts |

### Internal meta key stripping

The FY-scoped ledger transaction routes (`/api/reports/ledger/transactions`, `/api/reports/cashflow/sankey/contributions`) strip beancount parser internals (`lineno`, `filename`) and non-string meta values before serialising. The unscoped `/api/transactions` route does not strip these.

---

## OpenAPI Contract

`backend/app/server/openapi.py` exports the FastAPI OpenAPI schema without a running server:

```bash
python -m backend.app.server.openapi [outfile]
```

It creates a throwaway temp data dir (never the real `storage/`), calls `create_app()`, and dumps `app.openapi()` as JSON. `npm run gen:api` in `frontend/` writes it to `frontend/openapi.json` (gitignored).

The frontend uses the emitted schema for TypeScript type codegen (`frontend/src/shared/api/schema.d.ts`, also gitignored). Only routes with explicit `response_model=` annotations contribute typed response shapes to the schema; untyped routes appear with an `object` response type.

---

## Known Gaps / Divergences

`API_CONTRACT.md` is aligned with the generated OpenAPI schema (every route is
documented there by method and path). The schema
(`python -m backend.app.server.openapi`) remains the source of truth when the
two disagree.
