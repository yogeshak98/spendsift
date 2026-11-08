# Expense Domain — Frontend Product Documentation

Tracks spending from the beancount journal: a period overview, tag rollups, a monthly budget, and the
uncategorized queue.

---

## Purpose

The Expense domain has four sub-views under the `expense/` nav group. They read spending postings
from the loaded ledger. Overview, By Tag and Budget are read-only; Uncategorized is where you
categorize spending.

**What counts as spending.** Every `Expenses:*` account except `Expenses:Investment:*`. Charges
booked under `Expenses:Investment:*` (broker charges, stamp duty, the NAV rounding residual on a
mutual-fund purchase) are part of the cost of investing: Cash Flow counts them under **Invested**,
and no spending figure includes them.

---

## Screens

| Nav label | Route key | What it shows |
|-----------|-----------|---------------|
| Overview | `expense/overview` | Spending for the selected period: headline, heatmap or bars, categories, one-time spends, transaction list |
| By Tag | `expense/tags` | Spending for the selected period rolled up by `#tag` |
| Budget | `expense/budget` | Checking balance summary, budget vs. actual cards per category, projected month-end |
| Uncategorized | `expense/uncategorized` | Merchants still booked to `Expenses:Uncategorized`, to categorize |

The old **Monthly**, **Breakdown** and **Yearly** screens are folded into Overview. Their URLs
(`/expense/monthly`, `/expense/breakdown`, `/expense/yearly`) redirect to `/expense/overview`.

---

## The period selector

Overview and By Tag (and Income, Cash Flow · Overview and Liabilities · Credit Cards) share one
period control in the header: **Month | FY | Custom**.

- **Month** picks a calendar month. **FY** picks an Indian financial year (1 Apr – 31 Mar).
  **Custom** takes a From and a To date, both inclusive; a From after the To is not applied and
  shows a message until fixed.
- The choice is shared: pick FY 2026-27 on Expense and Income, Cash Flow and Credit Cards show the
  same FY. It is remembered across reloads.
- Month-only screens (Budget, Recurring, Dashboard) keep a month picker. They use the month the
  shared period points at (for an FY or a custom range, its last month up to today).

---

## Behavior per screen

### Overview (`expense/overview`)

Title: **Net Expense · \<period\>** (for example `Net Expense · Aug 2026`, `Net Expense · FY 2026-27`).

- **Funding filter** (All | Card | Bank | Other) in the headline panel. It scopes the **whole
  screen**: headline, chart, categories, one-time spends and the list. A transaction counts as
  Card, Bank or Other by the account that paid for it (a credit card, a bank/checking account, or
  anything else). A purchase split between a card and a bank counts wholly as Card.
- **Headline**:
  - **Net** — spending minus refunds, with the transaction count.
  - **Spent** — spending before refunds. **Refunds** — refunds and reversals.
  - Under **All**, **Via card / Via bank / Via other** split Net by funding; the three always add
    up to Net.
- **All spending | Without one-offs** switch (remembered per viewer). Without re-reads the period
  with one-time spends left out and states the total left out. In All mode, chart cells and bars
  holding a one-off get a dashed outline.
- **Chart**: **Heatmap | Bars**, **Linear | Compressed** (remembered per viewer).
  - A period of up to 92 days shows one calendar grid per month, one cell per day (days outside
    the range are greyed). Hover a day to see its merchants.
  - A longer period (an FY, a long custom range) shows one cell per month.
  - A day or month with more refunds than spending is tinted green.
  - Click a day, month or bar to open the detail modal listing its transactions.
- **By category**: one bar per category, largest first. Hover for the top merchants; click to
  open the detail modal with that category's transactions. A category with more refunds than
  spending has an empty bar and a green amount.
- **One-time spends**: the period's spends that are not recurring and at least 3× their
  category's usual amount (the Dashboard's rule). A row opens the detail modal on its day and
  category.
- **Transactions**: every spending line in the period, newest first, with the paying account as a
  badge. A line from an entry typed into the journal by hand (no transaction id) is tagged
  **hand-written**: it counts in every figure, but you edit it in the journal file.
- **States**: `Loading…` on first load; while a new period or filter loads, the previous figures
  stay on screen, dimmed. A failed load shows an inline error. A period with no spending shows
  "No expenses in this period."

### By Tag (`expense/tags`)

Title: **Spend by tag · \<period\>**.

- Follows the shared period. Spending postings carrying a beancount `#tag` are totalled per tag.
- Tags with `/` nest: `#travel/goa-2026` and `#travel/food` roll up under `#travel`.
  A group row shows the group total; click it to expand its tags. A tag with no `/` is a plain row.
- Group bars are a share of all tagged spend; a child's bar is a share of its group.
- **Empty state**: "No tagged spend yet in this period. Add #tags to transactions to see spend
  rolled up by trip, project, or reimbursable."
- While a new period loads, the previous rows stay dimmed; a failed load shows the error (with its
  code, for example an invalid custom range).

### Budget (`expense/budget`)

- **Period scoping**: the month from the header's month picker.
- **Summary stat blocks** (always shown, regardless of config):
  - Checking Current Balance — sum of `Assets:Checking:*` and `Assets:Cash*` postings.
  - Available for Budgeting / Budget Deficit — `checking_balance − total_budgeted`; label and tone
    flip red when negative.
  - Available for Spending — `total_budgeted − total_spent` (spent only counts budgeted categories);
    tone flips red when negative.
  - Projected Month-End Balance — `checking_balance − available_for_spending`.
- **No config state**: when `targets` is empty, a panel shows the expected YAML structure for
  `<data_dir>/config/budget.yaml` with an example snippet:
  ```yaml
  version: 1
  targets:
    "Expenses:Rent": "35000"
    "Expenses:Food": "12000"
    "Expenses:Shopping": "8000"
  ```
- **BudgetCard** (rendered per target category when config exists):
  - Header row: leaf category label · `spent / budgeted` (compact INR) · percentage.
  - Progress bar: green below 80 %, amber at 80–99 %, red when over-budget. Bar is capped at 100 %
    width visually.
  - Cards are sorted most-at-risk first: over-budget cards first, then by percentage descending.

### Uncategorized (`expense/uncategorized`)

Lists merchants whose spending is still booked to `Expenses:Uncategorized` (merchant, sample
narration, count, total, first and last date). Each row has a **Classify into** box (existing
categories offered; a new name is created) and a button that rewrites those postings and saves a
learned rule. **Classify every merchant at once** opens a copy-paste flow: copy the numbered
merchant list into any LLM, paste its numbered answers back, and apply; rejected lines are listed
with their reason. A hand-written row (no transaction id) is counted but cannot be rewritten, and
the screen says so. Empty state: "Nothing uncategorized — every transaction already has a
category."

---

## API endpoints consumed

| Method | Path | Query params | Used by |
|--------|------|--------------|---------|
| GET | `/api/reports/expense/overview` | `from`, `to`, `funding` (`all`/`card`/`bank`/`other`), `exclude_one_time` | Overview |
| GET | `/api/reports/cashflow/rows` | `from`, `to`, `side`, filters | Overview's detail modal |
| GET | `/api/reports/expense/tags` | `from`, `to` | By Tag |
| GET | `/api/reports/expense/budget` | `year`, `month` | Budget |
| GET | `/api/reports/uncategorized` | — | Uncategorized |
| GET | `/api/accounts` | — | Uncategorized's category list |
| POST | `/api/reports/uncategorized/classify` | — | Uncategorized: classify one merchant |
| GET | `/api/reports/uncategorized/export` | — | Uncategorized: the numbered merchant list to copy |
| POST | `/api/reports/uncategorized/import` | — | Uncategorized: apply the pasted answers |
| GET | `/api/reports/available-years` | — | the FY and month pickers |

`from` and `to` are ISO dates, inclusive. An invalid range (From after To, only one of the two, a
year outside 1900–2200) is rejected with `E1087 PERIOD_RANGE_INVALID`, which the screen shows.
Full response shapes are in `API_CONTRACT.md`.

---

## Notes

- **Category labels**: a category is the first two account segments (`Expenses:Rent`); the screen
  shows the last segment (`Rent`).
- **FY convention**: India financial year — 1 April of `fy` to 31 March of `fy+1`, labelled
  `FY 2026-27`.
- **Budget config**: `<data_dir>/config/budget.yaml`; there is no in-app editor, so targets are
  set by editing the file.
- **`total_spent` excludes unbudgeted categories**: "Available for Spending" only reflects
  categories named in `budget.yaml`.
