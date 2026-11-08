# Dashboard Screen — Product Documentation

Documents the Dashboard screen (`frontend/src/features/dashboard/Dashboard.tsx`) as implemented.

---

## Purpose

The Dashboard is the app's home screen and answers "where do I stand today". It is always
**as of today**: the shared Month | FY | Custom period does not apply here, and the header
has no period picker. Every figure comes from one call, and the net-worth figures are the same
ones Net Worth › Overview shows, so the two screens never disagree.

Everything on it is a link: a row, a bar or an item takes you to the screen that explains it.
Nothing opens a pop-up.

---

## What it shows (top to bottom)

### Header

- **Dashboard · as of _date_**: today's date.
- **Needs action** pill (red, with a bell and a count) on the right. It appears only when
  there is something to do. Click it for a list; each item has a button that takes you to
  the place to resolve it. Esc or a click outside closes the list. Items:

| Item | When it shows | Takes you to |
|------|---------------|--------------|
| _Card_ card overdue by N days | The latest statement's due date has passed and the payments since (including ones paid from your bank that the card statement does not show yet) fall short of the statement balance. Always listed first. | Net Worth › Liabilities (Credit cards) |
| _Card_ card due in N days | An amount is outstanding and the next due date is within 7 days. If you have already paid from your bank, it says so ("₹X paid on _date_, awaiting the statement"). | Net Worth › Liabilities (Credit cards) |
| N transactions need a category | Rows booked to Uncategorized this month. | Expense › Uncategorized |
| N card statements expected | Cards whose next statement date (from the statement day set in Settings › Accounts) is within 21 days. | Ledger › Import |
| _Bank_ import is N days old | An open bank account with a balance and no posting for more than 14 days. A closed account is never listed. | Ledger › Import |

### Opening-balance warning

Under the header, a warning callout appears when some accounts have no opening balance, or
an opening dated after earlier activity, with a link to Settings › Opening balances. It is
hidden when every account is covered.

### Net worth

The figure, its change over 12 months (with the percentage) and in the current month, and a
small line of the last 12 month-ends ending today.

Beside it, the breakdown as an equation that adds up to the figure exactly:

| Line | What it is | Extra facts |
|------|------------|-------------|
| Investments | Every investment vehicle at cost (mutual funds, stocks, FD, PPF, NPS, insurance) | XIRR (over the lots you hold now), gain, number of vehicles |
| + Bank and cash | Bank accounts and cash | **Months of cover**: the balance divided by your average monthly spending over the last 3 complete months; how many imports are stale |
| + Other assets | Property and any other asset | Which groups it holds |
| − Liabilities | Loans, credit cards, money owed to people, other liabilities | EMIs per month and when the last one ends; what the cards owe after payments already made but not yet on a statement |
| = Net worth | | |

A bar under the equation shows each line's share of net worth. Clicking a line opens
Net Worth › Overview with that section's cards highlighted.

### Cash flow

**_Current month_ | FY so far | Last 12 months** switch. For the chosen period: **In**, **Spent**,
**Invested**, **Net cash flow** (in − spent − invested), **Savings rate**
((in − spent) ÷ in), and **Where the income went**: a bar splitting income into spent,
invested and kept. When you spent and invested more than you earned, it says how much came
from savings. The figures are the same as Cash Flow › Overview's for the same dates.

### Spending

Spending is every expense except investment charges (stamp duty, brokerage), which count
as invested.

- **Last 3 / 6 / 12 months**: the average monthly spending. A month with no spending is left
  out of the average, and when fewer months than the window have spending the tile says so
  ("avg over 4 months") instead of "avg per month". The 3-month average is the same pace
  months of cover uses.
- **Lifestyle drift**: the 3-month average against the 12-month average, as a percentage.
  It shows a dash until at least 6 months have spending.
- **All spending | Without one-offs** switch: the averages and the chart with or without
  one-time spends.
- A bar chart of the last 12 complete months with the three averages drawn across it; one-off
  amounts are marked inside each bar. Click a month to open Expense › Overview on it.

### One-time spends

Recent spends (from the start of the month two months back, to today) that are **not
recurring** and **stand out**, largest first:

- Not recurring: the same detector as Cash Flow › Recurring. A monthly, quarterly or yearly
  payment (a subscription, insurance premium) is never one-time, whatever its size. A large
  purchase whose description and category match a recurring payment is treated as recurring.
- Stands out: at least 3 times the usual amount for its category (the median row over the
  last 12 months and this month). A category with fewer than 3 rows is compared with your
  spending across all categories.
- A refund is never one-time.

Each row shows the date, description, amount, category, how it was paid, and how many times
the usual it is. Click a row to open Expense › Overview on its month.

---

## API endpoint consumed

| Method | Path | Parameters | What it provides |
|--------|------|------------|------------------|
| GET | `/api/reports/dashboard/v2` | none (an optional `as_of` exists, but the screen never sends it) | `as_of`, `net_worth` (figure, changes, 12-point series, breakdown), `cash_flow` (three periods), `spending` (months and averages, with and without one-time spends), `one_time_spends`, `needs_action` |

| GET | `/api/openings` | none | Opening-balance status per account, for the warning callout |

The breakdown and its facts (XIRR, gain, EMIs, months of cover, stale imports) are read from
the Net Worth overview (`/api/reports/networth/overview`), not computed a second way. With no
`as_of`, both endpoints count only what is dated on or before today; a posting dated in the
future counts once its date arrives.

---

## States

- **Loading**: "Loading…".
- **Error**: "Could not load the dashboard: _reason_".
- **Nothing to do**: no Needs action pill.
- **No one-time spends**: "No one-time spends since _date_."
- **No income in a cash-flow period**: the income split says "no income in this period".
