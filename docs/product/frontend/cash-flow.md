# Cash Flow

The Cash Flow domain shows where money comes from and where it goes: a period **Overview** (with a
**Compare FYs** view) and a **Recurring** payments tracker.

## Purpose

Show income, spending, investing and saving for any period, and how money flowed between accounts.
All screens are read-only; none change the journal.

## Screens

| Nav label | Route key | What it shows |
|-----------|-----------|---------------|
| Overview | `cashflow/overview` | Headline totals, a daily or monthly cash-flow chart, a Sankey with drill-down, and a one-column statement for the selected period. A **Compare FYs** switch shows FYs side by side. |
| Recurring | `cashflow/recurring` | Summary stats and cards for detected recurring payment streams, for one calendar month. |

The old **Income Statement**, **Monthly** and **Yearly** screens are folded into Overview. Their
URLs (`/cashflow/income-statement`, `/cashflow/monthly`, `/cashflow/yearly`) redirect to
`/cashflow/overview`.

## Behavior per screen

### Overview

A switch at the top picks the view: **Overview | Compare FYs**.

#### Overview view

Follows the shared **Month | FY | Custom** period in the header (shared with Expense, Income and
Credit Cards; see `expense.md`). Title: **Cash Flow · \<period\>**.

- **Headline**:
  - **Income** — money received, net of tax deducted at source.
  - **Expenses** — all spending (see `expense.md` for what counts), tax paid included. It equals
    Expense · Overview's Net for the same period.
  - **Invested** — money moved into investments, plus investment charges
    (`Expenses:Investment:*`).
  - **Saved** — Income − Expenses (red when negative).
  - **Savings rate** — Saved as a share of Income; `—` when there is no income.
- **Daily / Monthly cash flow**: one bucket per day (short periods) or per month, drawn as a
  **Heatmap** or **Bars** (Linear or Compressed axis), showing **Both**, **Income** or
  **Expenses**. In the heatmap colour carries direction (green in, red out); bars put income and
  expenses side by side, with the net in the hover. The chart choices are remembered per viewer.
  Click a cell or bar to open the detail modal listing that day's or month's transactions.
- **Flow**: a Sankey of income → accounts → spending and other outflows for the period, with a
  **Summary | Detailed** depth switch. Click a node or ribbon to open the detail modal listing
  the transactions behind it. Entries typed into the journal by hand are included. A failed load
  shows an error in the panel; an empty period shows "No flows in this period."
- **Statement**: Income and Expenses by top category for the period, each section with its total,
  and a **Net** row (green when ≥ 0, red when negative).
- **States**: `Loading…` on first load; while a new period loads, the previous figures stay on
  screen, dimmed. A failed load shows an inline error. A period with no activity shows "No cash
  flow in this period."

#### Compare FYs view

- One column per financial year across a span you pick with the FY range picker (for example the
  last 3 FYs), oldest to newest: Income and Expenses by top category, with a Net row.
- The header's period control is hidden here, because the FY range picker governs. Switching back
  to Overview restores the shared period as it was.
- The view is part of the URL (`/cashflow/overview?view=compare`), so it can be bookmarked.

### Recurring

- Renders a 4-up `StatBlock` summary header and a grid of `RecurringCard`s.
- **Period scoping**: the calendar month from the header's month picker.
- **Summary stats**: Recurring series (blue), Cleared this month (green, with amount), Past due (red
  when > 0, else dim), Upcoming (blue).
- **Legend**: color dots for Cleared (green), Cleared late (amber), Past due (red), Upcoming (blue
  outline).
- **Cards**: label, account chip, status dot and label, amount (actual if cleared, else expected),
  cadence, expected day and last-seen date.
- **Empty states (two tiers)**:
  1. No streams detected → "No recurring patterns detected yet — recurring items appear once a payee
     repeats on a regular cadence."
  2. Streams exist but none this month → summary stats with zeros and "No recurring items expected
     in {month} {year}."

## API endpoints consumed

All under `/api/reports`.

| Method | Path | Used by |
|--------|------|---------|
| GET | `/api/reports/cashflow/overview?from=<date>&to=<date>` | Overview (headline, cash-flow chart, Statement) |
| GET | `/api/reports/cashflow/sankey?from=<date>&to=<date>&depth=<summary\|detailed>` | Overview · Flow |
| GET | `/api/reports/cashflow/rows?from=<date>&to=<date>&…` | The detail modal behind every chart cell, bar, node and ribbon |
| GET | `/api/reports/cashflow/pnl?fys=<fy>,<fy>,…` | Compare FYs |
| GET | `/api/reports/cashflow/recurring?year=<year>&month=<month>` | Recurring |
| GET | `/api/reports/available-years` | the FY pickers |

Response shapes are in `API_CONTRACT.md`.

## Notes

- **FY definition**: month ≥ April → `year`; else `year − 1`. FY `N` covers 1 Apr `N` – 31 Mar
  `N+1`.
- Recurring is the only Cash Flow screen scoped by calendar month rather than the shared period.
