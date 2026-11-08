# Income & Doctor — Product Documentation

The Income screens show money received for any period, overall and by tag; the Doctor screen
surfaces ledger health and categorization diagnostics.

## Income

Nav: **Income → Overview · By Tag**. The old `/income` URL redirects to `/income/overview`.

Both screens follow the shared **Month | FY | Custom** period in the header, shared with Expense,
Cash Flow and Credit Cards (see `expense.md`): pick FY 2026-27 on Expense and Income shows the
same FY.

### Overview (`income/overview`)

Title: **Net Income · \<period\>**. The same layout as Expense · Overview, for `Income:*` accounts.

- **Funding filter** (All | Card | Bank | Other) scopes the whole screen by the account the money
  arrived in. Card covers cashback credited to a card.
- **Headline**:
  - **Net** — money received after deductions, with the transaction count.
  - **Received** — money received before deductions.
  - **Deducted/Reversed** — tax deducted at source and other reversals. For a salary of ₹1,00,000
    with ₹10,000 TDS, Received is ₹1,00,000, Deducted is ₹10,000 and Net is ₹90,000.
  - Under **All**, **Via card / Via bank / Via other** split Net by where the money arrived.
- **Chart**: the same **Heatmap | Bars** panel as Expense, in green (money in green, a reversal
  red). Click a day, month or bar to open the detail modal.
- **By source**: one bar per income category (Salary, Interest, …). Hover for the top payers; click
  to open the detail modal with that source's transactions.
- **Transactions**: every income line in the period, newest first. A line from an entry typed into
  the journal by hand is tagged **hand-written**.
- No one-time spends switch or panel (Expense only).
- **States**: `Loading…`, the dimmed previous figures while a new period loads, an inline error on
  failure, and "No income in this period." when empty.

### By Tag (`income/tags`)

Title: **Income by tag · \<period\>**. The Expense By Tag rollup over `Income:*` postings only:
tagged income per `#tag`, with `/`-nested tags rolled up under their prefix. Empty state: "No
tagged income yet in this period. Add #tags to income transactions to see it rolled up by source,
client, or bonus."

### API endpoints consumed

| Endpoint | Method | Used by |
|---|---|---|
| `/api/reports/income/overview?from=<date>&to=<date>&funding=<all\|card\|bank\|other>` | GET | Overview |
| `/api/reports/cashflow/rows?…&side=income` | GET | Overview's detail modal |
| `/api/reports/expense/tags?from=<date>&to=<date>&kind=income` | GET | By Tag |
| `/api/reports/available-years` | GET | the FY picker |

Response shapes are in `API_CONTRACT.md`.

## Doctor

Source: `frontend/src/screens/Doctor.tsx`

### What it shows

Up to four panels:

| Panel | Display |
|---|---|
| `Doctor` | `✓/✗ All transactions balance` with `{transaction_count} txns` (green/red, from `journal_valid`); `✓/✗ No asset holds a negative balance` (`clean` or the count of accounts); `Uncategorized` with `{uncategorized_count}` (amber) |
| `Opening balances` | Whether every active account has an opening balance; otherwise each uncovered account with its first posting date and a link to Settings → Opening balances. Shown when the payload carries `openings` |
| `Uncategorized backlog` | Clear, or the pending transactions and merchants with a link to Expense → Uncategorized. Shown when the payload carries `uncategorized` |
| `Negative asset balances` | Only when some asset account is below zero: a warning that each is a modelling defect (nothing is corrected automatically) and each account with its balance |

- **Loading state**: `Loading…` while `data` is null.
- Missing optional keys are tolerated rather than crashing the screen.
- The response type declares `errors: string[]`, but the screen does **not** render the `errors`
  field anywhere.

### API endpoints consumed

| Endpoint | Method | When | Returns (fields) |
|---|---|---|---|
| `/api/reports/doctor` | GET | On mount | `journal_valid`, `errors`, `transaction_count`, `uncategorized_count`, `uncategorized` (`transactions`, `merchants`), `negative_assets` (`account`, `balance`), `openings` (`missing_count`, `missing`) |

### Implementation notes

- Fetched via `useReport` → `api.getJSON`; no query parameters.
- Backend `doctor.checks()` validates the beancount ledger (`journal_valid` is `not errors`),
  counts transactions and `Expenses:Uncategorized` postings, lists asset accounts with a negative
  balance, and reports accounts whose activity is not covered by an opening balance.

## Known gaps / divergences

- **`errors` is fetched but never shown.** The UI surfaces only a pass/fail icon; individual
  validation errors are not displayed (the Net Worth journal badge links here for them).
- **Type mismatch on `errors`.** The backend returns beancount error objects, not the strings the
  frontend type asserts; masked only because the field is never rendered.
- **No `response_model` on `/doctor`.** The response shape is not validated at the API boundary;
  the frontend `Resp` type is hand-maintained.
