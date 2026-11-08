# Net Worth Screen — Product Documentation

Documents the Net Worth screen (`frontend/src/features/networth/`) as implemented: one screen
with four tabs, and the backend endpoints it reads.

---

## Purpose

Net Worth answers "what do I own, what do I owe, and how are my investments doing". It is one
rail entry, **Net Worth**, with its own tab bar:

| Tab | Route | What it shows |
|-----|-------|---------------|
| Overview | `/networth/overview` | Headline totals, what your assets are made of, one card per account group |
| Investments | `/networth/investments` | Each investment vehicle, its value and return, judged against a benchmark |
| Liabilities | `/networth/liabilities` | Credit cards, Interest, Repayment and Balance views |
| Allocation | `/networth/allocation` | How your investments split by vehicle and by issuer |

The **Liabilities** rail entry is a shortcut to the Liabilities tab. Old links keep working:
`/networth` and `/networth/balance` open Overview, and the former `/liabilities/*` screens open
the matching view under the Liabilities tab.

The tab bar replaces the app-wide header on this screen. Its right side carries what belongs to
the open tab: the date and badges on Overview, the benchmark on Investments, the period on
Credit cards.

---

## Overview tab

Figures are **as of today**, like the Dashboard, and the two screens show the same net worth,
assets and liabilities. A transaction dated in the future counts once its date arrives.

**Header badges** (right of the tabs): "as of _date_"; **journal valid** (green) or **journal
has N errors** (red, opens Doctor); and a stale-import warning: a bank account with no activity
for more than 14 days is named with its last activity date, or with several, "N stale
imports". A closed account is never stale.

A warning callout (with a link to Settings › Opening balances) appears when some accounts lack
an opening balance; such an account's row carries a "No opening" or "Late opening" badge.

**Tiles**

| Tile | Shows |
|------|-------|
| Net worth | The figure, its change this FY and this month (▲ green / ▼ red) |
| Assets | Total, and how many accounts hold it |
| Liabilities | Total owed; EMIs per month when you have loans, else the number of accounts, or "nothing owed" |
| Investments return | XIRR over the lots you hold now, the gain, and the chosen benchmark's yearly return. "at cost, no return yet" when no investment has a price |

**What your assets are made of**: a bar split by group with a percentage legend. Click a
segment to highlight its card. Prepaid tax (TDS, advance tax) is listed as "Not counted"
because it settles against tax due at filing.

**Cards.** Under **Assets**: Investments, Property, Bank & cash, Other assets. Under
**Liabilities**: Loans & EMIs, Credit cards, People, Other liabilities. A group with nothing in
it is left out. Each card shows its total, a tag (XIRR, "at cost", months of cover and so on),
a few facts, and its three largest accounts. Clicking a card goes where its detail lives:

| Card | Opens |
|------|-------|
| Investments | Investments tab |
| Property, Bank & cash, Other assets | Ledger › Transactions filtered to that group |
| Loans & EMIs | Liabilities › Repayment |
| Credit cards | Liabilities › Credit cards |
| People, Other liabilities | Liabilities › Balance |

Money someone owes you (People in credit) shows in green.

**Section links.** Other screens link to a card with `?section=<group>`, for example the
Dashboard's breakdown rows. The Overview then scrolls to that card and highlights it briefly.
`?section=other` highlights Property and Other assets together, and `?section=liabilities`
every liability card. A `?section=` link to another tab opens Overview.

**Empty and error states**: "No accounts with a balance yet. Import a statement to see your net
worth." / "Could not load net worth. The journal is unchanged — this is a read failure, not a
data loss."

---

## Investments tab

The hub of investment vehicles (mutual funds, stocks, and at-cost vehicles such as FD), each
opening its own view, then **Return by vehicle**: invested, value, gain, XIRR, share and the
benchmark comparison for each vehicle.

**Benchmark** (tab bar, right): "XIRR _x_% vs _y_%" with the difference in points, and a
dropdown to choose what to compare against (Nifty 50 by default). **Blended** compares each
vehicle with its own benchmark, weighted by its share. Your choice is remembered in this
browser and also drives the Overview's return tile.

Benchmark returns are not fetched online. They come from `config/benchmarks.yaml` in your
data folder, which is written with rough starting figures the first time Net Worth opens.
Edit it to use the figures you trust, or add a benchmark (see `SCHEMA.md`). If the file cannot
be read, the screens say "Benchmark unavailable: check config/benchmarks.yaml" and everything
else still works.

The hub and each vehicle and instrument view carry a provenance strip: "Prices as of _date_"
(and the oldest priced date when they differ), the fetch state, and a **Refresh** control that
re-fetches mutual-fund NAVs and stock closes. The mutual-fund view offers **Run backfill** when
historical NAV data is too sparse for its 1M / 1Y / SI columns.

FD, PPF, NPS, insurance and property are shown at cost with no return, because there is no
price for them.

---

## Liabilities tab

Four views, as chips under the tab bar: **Credit cards** (the landing view, with the shared
Month | FY | Custom period on the right), **Interest**, **Repayment** and **Balance**. See
`liabilities.md`.

---

## Allocation tab

**Allocation by Vehicle** (a donut with amounts and percentages) and **By Issuer** (ranked
bars), at cost.

---

## API endpoints consumed

| Method | Path | Used by |
|--------|------|---------|
| GET | `/api/reports/networth/overview` | Overview tab; benchmark control and Return by vehicle. Sends `benchmark` for the chosen benchmark; no `as_of`, so it is as of today |
| GET | `/api/reports/doctor` | Overview's journal badge |
| GET | `/api/reports/investments/summary` | Investments hub |
| GET | `/api/reports/investments/gains`, `/changes`, `/equity`, `/vehicles/{key}/holdings`, `/{vehicle}/{instrument}` | The vehicle and instrument views |
| GET | `/api/prices/freshness` | Price freshness badges and the Refresh control's per-feed detail |
| POST | `/api/prices/refresh`, `/api/prices/equity/refresh` | Refresh control (mutual-fund NAVs, stock closes) |
| POST | `/api/prices/backfill` | Mutual-fund view's Run backfill |
| GET | `/api/openings` | Overview's opening-balance callout and badges |
| GET | `/api/reports/investments` | Allocation tab |
| GET | `/api/reports/liabilities/...` | Liabilities views |
