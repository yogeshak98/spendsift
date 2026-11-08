# Liabilities Domain — Frontend Product Documentation

Documents the four Liabilities views in the SpendSift React/TS frontend: credit-card tracking, interest analytics, loan-repayment amortization, and a balance tree-table.

> Facts only.

---

## Purpose

The Liabilities domain surfaces all accounts whose beancount path begins with `Liabilities:`. It is split into four views shown as chips under the **Liabilities** tab of the Net Worth screen (see `assets.md`); the "Liabilities" (◇) rail entry is a shortcut to that tab. The old `/liabilities/*` URLs redirect to the matching `/networth/liabilities/*` view. Credit cards (`Liabilities:CreditCard:*`) and loans (`Liabilities:Loan:*`) are treated separately from the aggregated balance view.

---

## Screens

| Nav label    | Route key                    | Source file                                              | What it shows                                                                                     |
|--------------|------------------------------|----------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| Credit Cards | `networth/liabilities`       | `frontend/src/screens/liabilities/CreditCards.tsx`       | Card tiles (balance, payments awaiting the statement, cycle, expiry), card spend for the selected period, statement lines |
| Interest     | `networth/liabilities/interest` | `frontend/src/screens/liabilities/Interest.tsx`          | All-time interest paid: stat blocks, monthly bar chart, per-account ranked bars                   |
| Repayment    | `networth/liabilities/repayment` | `frontend/src/screens/liabilities/Repayment.tsx`         | Loan list with outstanding balance; in-app loan-terms form; amortization schedule and declining-balance chart when terms are known; EMI-plan split review |
| Balance      | `networth/liabilities/balance` | `frontend/src/screens/liabilities/Balance.tsx`           | Collapsible tree-table of all `Liabilities:*` accounts: I owe, owed to me, interest, APR; card payments awaiting the statement |

---

## Behavior per Screen

### Credit Cards (`CreditCards.tsx`)

The Net Worth tab bar shows the shared **Month | FY | Custom** period (shared with Expense, Income and Cash
Flow). The tiles always show current balances; the period scopes the card spend and statement
lines below them.

**Card tiles**
- One tile per open `Liabilities:CreditCard:*` account: the card name (with `••last4` when set),
  the full account path, the outstanding balance and the last activity date. An overpaid card
  shows "₹X in credit".
- **Cycle**: "Next statement ~15 Nov · due 3 Dec", "Current bill due …" while that bill is still
  ahead, and a "Statement expected (~15 Oct). Import it" nudge when a statement is overdue. Without
  a statement day, a "Set statement day" link goes to Settings → Accounts.
- **Expiry**: "Expires 08/29" when an expiry month is stored, with an **Expiring soon** pill
  inside the warning window (60 days by default) and an **Expired** pill after it.
- **Payment awaiting the statement**: when a bill payment from the bank has been imported but the
  card statement's "payment received" row has not, the tile reads "paid ₹X on 20 Oct · ₹Y left"
  ("₹Y in credit" when the payment was larger than the balance).
- Click a tile to pick that card below; click it again to go back to All cards.

**Payments awaiting the card statement**
- A bill payment from the bank sits in the card clearing account (`Assets:Transfer:CreditCard` by
  default) until the card statement's "payment received" row is imported. Until then the card's
  ledger balance overstates what you owe.
- While such payments exist, a line above the tiles reads "Outstanding ₹X · Paid, awaiting card
  statement: ₹Y · Net owed ₹Z". When the payments exceed what the cards owe it reads "Net ₹Z in
  credit". Payments that could not be matched to exactly one card are noted as "(₹W not matched to
  one card)".
- A payment is matched to a card when exactly one card's balance on the payment date is within
  ₹10 (or 0.1%, whichever is larger) of the amount.
- The line disappears once the card statement with the "payment received" row is imported. A
  payment that is still unmatched 60 days later is no longer counted.
- Entries typed into the journal by hand (no transaction id) count too, on both sides.
- Net worth is unaffected: the paid amount is already counted in the clearing account.

**Card picker and card spend**
- A **Card** select (All cards, or one card) sits next to the tiles.
- Below, **Card spend · \<period\>** is the Expense overview limited to spending paid by card (and
  to the picked card): headline, chart, categories and the list.
  There is no All/Card/Bank/Other toggle here.

**Statement lines**
- A collapsed **Statement lines** section at the bottom. With a card picked, it lists that card's
  statement rows inside the period (Date, Narration, Amount, Type: purchase, charge, payment,
  refund, EMI, adjustment). With All cards it says "Pick a card to see its statement lines."

**Help link**
- "How are credit cards tracked?" opens the Paisa liabilities documentation on GitHub in a new tab.

**Loading / empty states**
- Before data loads: `Loading…`.
- No cards: the tile grid is empty.

---

### Interest (`Interest.tsx`)

**Stat blocks**
- "Total Interest Paid" (red tone): sum of all `amount` values across all rows.
- "Accounts Charged" (text tone): count of distinct accounts with any interest charge.

**Interest by Month panel**
- Custom inline bar chart (class `combo` / `bars`), height 240 px.
- One bar per month (chronological). Bar height is proportional to that month's interest relative to the monthly maximum.
- Each bar has a `Tip` tooltip showing `YYYY-MM · ₹<amount>`.
- Month label shows the two-digit month suffix (e.g. `"06"` from `"2024-06"`).

**Interest by Account panel**
- `RankedBars` component: accounts sorted descending by total interest, each showing label, formatted amount, percentage of total, and a colored bar.
- Color palette cycles through: `--blue`, `--amber`, `--red`, `--green`, `--teal`, `--pink` (no violet).
- Account labels are shortened to everything after the first `:` segment.

**Loading / empty states**
- Before data loads: `Loading…` text.
- Zero rows: renders a single "No interest charges recorded." message inside a Panel. Stat blocks and charts are not shown.

---

### Repayment (`Repayment.tsx`)

**Loan list**
- Fetches `/api/reports/liabilities/repayment` on mount. Only `Liabilities:Loan:*` accounts appear.
- Renders a `data-row` per loan with a clickable account-name button and outstanding balance.
- On first data load, auto-selects the first loan. Clicking a row changes the selected loan.

- Card EMI plans show their card beside the name. When EMI postings on a card are not yet split
  into plans, a warning banner offers **Review split**, which opens `EmiSplitReview`: the proposed
  plans (`GET /api/liabilities/emi-plans/proposal`), editable names and labels, and a split action
  (`POST /api/liabilities/emi-plans/split`).

**Terms known: amortization dashboard**
When `loan.terms_set === true`:

- A badge says where the terms came from: **Set by you** (in-app form), **From your terms file**
  (`rules/loan-terms.yaml`), or **Inferred from N instalments · nominal rate**. **Edit terms** opens
  the form.
- **Stat blocks**: Outstanding (amber), Monthly EMI (blue), Rate, Total Interest (red), Payoff Month (text).
- **Outstanding Balance panel**: inline `BalanceCurve` SVG — a filled-area declining-balance curve over the full amortization schedule. Single legend entry "Outstanding balance" (blue dot). Aria-label: "Outstanding balance over time".
- **Amortization Schedule panel**: `DataTable` with columns Month, EMI, Principal, Interest, Balance (all right-aligned numeric), plus a Status column marking paid instalments when any are paid.

**Terms not known: terms form**
When `loan.terms_set === false`, a panel titled with the loan's label (or account leaf) says the terms are not known yet and shows `LoanTermsForm` (rate, term in months, original principal, label). Saving sends `PUT /api/accounts/loan-terms`, which writes the values to the account's own `open` directive; the user never edits a file.

**Loading / empty states**
- Before data: `Loading…`.
- Zero loans: "No loans to repay." text (no Panel wrapper); the EMI split banner can still show.

**Loan terms sources**
Terms set in the form live on the account's `open` directive. The older `{data_dir}/rules/loan-terms.yaml` (YAML dict keyed by full account name: `annual_rate_pct`, `term_months`, optional `original_principal`) is still read; a missing file or YAML parse error silently yields no terms from it. Without either, the backend may infer terms from the paid instalments.

---

### Balance (`Balance.tsx`)

**Tree-table**
- Builds a tree from all `Liabilities:*` accounts: each colon-delimited path segment becomes a
  node, and a parent row sums its children. Columns: Account, **I owe**, **Owed to me**,
  **Interest**, and **APR** when any account has loan terms. The two directions are never netted.
- APR is shown on leaf rows only; parent rows show `—`.
- Settled accounts are hidden behind a "Show settled (N)" button.

**Payments awaiting the card statement**
- While a card bill payment awaits its statement (see Credit Cards), a line above the tree reads
  "Cards outstanding ₹X · Paid, awaiting card statement: ₹Y · Net owed ₹Z" ("Net ₹Z in credit" when
  negative). The tree keeps the ledger figure.

**Empty state**
- No liabilities: "Hurray! You have no liabilities." When every account is settled: "Everything is
  settled."

---

## API Endpoints Consumed

Served by FastAPI on `127.0.0.1:8765`. Reads go through the `useReport` hook (plain `fetch` GET with no auth); writes use `api.postJSON` / `api.putJSON`.

| Method | Path                                          | Used by               | Notes                                                          |
|--------|-----------------------------------------------|-----------------------|----------------------------------------------------------------|
| GET    | `/api/reports/liabilities/credit-cards`       | `CreditCards`         | The card tiles, the payments awaiting the statement (per card and in total), outstanding and net owed |
| GET    | `/api/reports/expense/overview?from=…&to=…&funding=card[&account=…]` | `CreditCards` (card spend) | The Expense overview limited to card-paid spending |
| GET    | `/api/reports/liabilities/credit-cards/{account}` | `CreditCards` (statement lines) | URL-encoded full account path; returns that card's statement rows |
| GET    | `/api/reports/liabilities/interest`           | `Interest`            | Returns `{ rows: Row[] }` where each row has `account`, `month` (YYYY-MM), `amount` |
| GET    | `/api/reports/liabilities/repayment`          | `Repayment`           | Returns `{ loans: Loan[] }` — only `Liabilities:Loan:*` accounts; includes amortization schedule when terms configured |
| GET    | `/api/reports/liabilities/balance`            | `Balance`             | All `Liabilities:*` accounts (direction, amount, interest, APR) plus the card payments awaiting the statement |
| PUT    | `/api/accounts/loan-terms`                    | `LoanTermsForm`       | Writes a loan's terms to its `open` directive; `null` removes a term |
| GET    | `/api/liabilities/emi-plans/proposal`         | `EmiSplitReview`      | Proposed split of a card's EMI postings into plans |
| POST   | `/api/liabilities/emi-plans/split`            | `EmiSplitReview`      | Applies the split |

---

## Implementation Notes

- **Account classification**: Credit cards are identified by the prefix `Liabilities:CreditCard:`; loans by `Liabilities:Loan:`. The Balance screen covers all `Liabilities:*` regardless of sub-prefix.
- **Sign convention**: The ledger stores liabilities as negative amounts. The endpoints return what is owed as a positive amount; a negative card balance or net owed is a credit and is shown as "₹X in credit".
- **Loan amortization source**: `repayment.py` calls `amortize.schedule(principal, rate, months)`. The `original_principal` term lets the user specify the original loan amount rather than having the backend derive it from current outstanding (which reflects partial repayments).
- **`LoanTerms` file path**: hardcoded to `{ledger.storage_dir}/rules/loan-terms.yaml`. The file does not need to exist; a missing or invalid file is silently treated as no terms from it.
- **No auth / no CORS**: all API calls are same-origin loopback; `api/client.ts` sets `baseUrl: ""`.
- **`useReport` hook**: shared data-fetching hook used by all four screens. Re-fetches when the URL string changes (the period and card picker on Credit Cards); a saved loan-terms form triggers a reload.

---

## Known Gaps / Divergences

- **No empty-card-grid message**: when there are no cards, the tile grid renders empty with no placeholder text.
- **One bank payment for several cards**: a single bank debit that pays several cards never matches their separate "payment received" rows; it shows as not matched to one card until it is 60 days old.
- **No pagination on card transaction table**: all transactions for a card are returned and rendered in a single `DataTable` with no pagination or virtual scroll.
- **Balance TreeTable collapse state is not persisted**: the `TreeTable` expand/collapse state resets on navigation away and back.
- **APR shown only on leaf nodes in Balance**: synthetic parent nodes (e.g. `Liabilities:Loan`) always show `—` for APR even if all children share the same rate. This is a display limitation of the current `buildTree` aggregation, not a backend issue.
- **Repayment scope is `Liabilities:Loan:*` only**: accounts under other sub-prefixes (e.g. a hypothetical `Liabilities:Mortgage:`) do not appear in the Repayment screen; they appear only in Balance.
- **Interest endpoint returns all-time data only**: there is no FY filter on `/api/reports/liabilities/interest`. The Interest screen shows a lifetime aggregate, not a per-year view.
- **Payoff month is forward-projected from today's date** (except for inferred terms, which count from the first instalment): `_payoff_month` adds `term_months` to today, not to the loan origination date. If repayment is mid-term, the displayed payoff month will be incorrect unless `term_months` is manually adjusted to the remaining term.
