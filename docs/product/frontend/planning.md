# Planning Domain — Frontend Product Documentation

Six client-side financial calculators reachable from the Planning nav section: goal funding simulation, SIP projection, lumpsum projection, CAGR lookup, India-specific in-hand salary estimation, and a 5-bucket FIRE plan.

> Facts only.

---

## Purpose

The Planning domain provides standalone, browser-only calculation tools for personal financial decision-making. All computation is pure TypeScript — no backend calls are made. State is persisted per-calculator in `localStorage` and survives page reload.

---

## Calculators

| Nav label | Screen file | Calc module | What it computes |
|---|---|---|---|
| Goal Planner | `GoalPlanner.tsx` | `goalProjection.ts` | Year-by-year pool depletion as goals are funded from a savings pool with growing monthly contributions |
| SIP Calculator | `Sip.tsx` | `sipCalc.ts` | Month-by-month corpus growth from a recurring monthly SIP with optional step-up and optional initial lumpsum |
| Lumpsum Calculator | `Lumpsum.tsx` | `lumpsumCalc.ts` | Year-by-year compounded growth of a single one-time investment |
| CAGR Calculator | `Cagr.tsx` | `cagrCalc.ts` | Implied annualised return between a start and end value over a given period |
| In-Hand Salary | `Salary.tsx` | `salaryCalc.ts` + `taxSlabs.ts` | Take-home pay and income-tax liability under India FY2025-26 New or Old regime |
| FIRE Calculator | `Fire.tsx` | `fireCalc.ts` | Retirement corpus target split into five buckets, the SIP needed to reach it, and a month-by-month drawdown simulation |

---

## Behavior per Calculator

### Goal Planner

**Inputs** (persisted under `spendsift.goalPlanner.v1`):

| Input | Type | Notes |
|---|---|---|
| Total available now (pool) | ₹ number | Starting savings pool |
| Avg monthly investment toward goals | ₹ number | Added annually: `investMo × 12`; grows by `raisePct` each year |
| Annual growth of investment | % slider 0–30 | Applied once per year |
| Avg monthly expense (reference only) | ₹ number | Tracked but not deducted from pool |
| Annual inflation of expenses | % slider 0–25 | Reference only; does not reduce pool |
| Years to project | integer slider 3–15 | Projection window |
| Goals list | name, ₹ amount, due year | Up to N goals; drag-reorderable; persisted |

**Outputs:**
- Stat blocks: Starting Pool, Pool After Year N, Goals Funded (x/y), Total Goal Value
- Goal outcome cards: each goal shows funded/not-funded, the year it was funded, and whether it slipped vs due year
- Yearly projection table: Year, Investment/mo, Expense/mo (ref), Added (yr), Pool start, Events (goal funded/short pills), Pool end

**Chart:** None. No AreaSeries on this screen.

**Goal interaction:** Goals are drag-reordered by reassigning sorted due-year values to the new sequence. The sort button re-sorts by `dueYear` ascending. Goals are processed in due-year order; a goal that cannot be funded halts processing for that year.

---

### SIP Calculator

**Inputs** (persisted under `spendsift.sipCalc.v1`):

| Input | Default | Notes |
|---|---|---|
| Current Corpus (lumpsum) | 0 | Seed value for month 0 |
| Monthly SIP Amount | ₹10,000 | Applied every month |
| Expected Return Rate p.a. | 12% | Annualised; converted to monthly internally |
| Time Period | 10 years | Integer; min 1, max 50 |
| Step-up (Annual Increase) | disabled | If enabled: % increase applied to SIP amount every 12 months |

**Outputs:**
- Stat blocks: Invested Amount, Est. Returns, Total Value (at end of projection)
- AreaSeries chart: annual snapshots of Invested vs Total Value
- Monthly breakdown table: Month, SIP Amount, Invested Amount, Wealth Gained, Total Value

---

### Lumpsum Calculator

**Inputs** (persisted under `spendsift.lumpsumCalc.v1`):

| Input | Default |
|---|---|
| Total Investment | ₹1,00,000 |
| Expected Return Rate p.a. | 12% |
| Time Period | 10 years (min 1, max 50) |

**Outputs:**
- Stat blocks: Invested Amount, Est. Returns, Total Value
- AreaSeries chart: year-by-year Invested (flat) vs compounded Total Value

---

### CAGR Calculator

**Inputs** (persisted under `spendsift.cagrCalc.v1`):

| Input | Notes |
|---|---|
| Beginning Value | ₹ |
| Ending Value | ₹ |
| Period Type | Toggle: "Years" (direct input) or "Dates" (start + end date) |
| Number of Years | Visible when Period Type = Years; min 0.1, step 0.1 |
| Start Date / End Date | Visible when Period Type = Dates; duration derived as `days / 365.25` |

**Outputs:**
- Single stat block: CAGR % (green if ≥ 0, red if negative)

**Chart:** None.

---

### In-Hand Salary

**Inputs** (persisted under `spendsift.salaryCalc.v1`):

| Input | Notes |
|---|---|
| Financial Year | Dropdown; only "FY2025-26" is available |
| Input Mode | CTC-derived (auto) or Custom breakdown |
| City Tier | Metro / Non-Metro (affects HRA exemption ratio: 50% metro, 40% non-metro) |
| Tax Regime | New / Old |
| CTC (mode=ctc) | Annual; basic = 40% CTC; HRA = 50% or 40% basic; special = remainder |
| Custom breakdown (mode=custom) | Basic, HRA, Special Allowance, LTA, Books & Periodicals, Tel & Internet, Employer NPS %, Other taxable allowances |
| Old-regime deductions (shown only when regime=old) | Monthly Rent, Other 80C investments, Employee NPS 80CCD(1B), 80D Health Insurance, LTA/Books/Tel exemptions (CTC mode only) |

**Outputs:**
- Stat blocks: Monthly Take-Home, Annual Take-Home, Income Tax (annual), Effective Tax Rate
- Callout if rebate u/s 87A applies (zero tax)
- Callout if surcharge applies (>₹50L taxable)
- Salary Structure table: Basic, HRA, Special/Other Allowances, Employer PF, Gross Salary
- Deductions from Gross table: Employee PF, Professional Tax, Income Tax (TDS), Total Deductions
- Tax working (collapsible `<details>`): full itemised working
- New vs Old Regime Comparison table: Monthly Take-Home, Annual Tax, Effective Rate, Taxable Income — with a callout indicating which regime saves more for the current inputs

**Chart:** None.

---

### FIRE Calculator

**Inputs** (persisted under `spendsift.fireCalc.v1`): Monthly Expense, Current Corpus, Years to
Retire, Inflation, Corpus Multiple (years of expenses); on the Accumulation tab Expected ROI and
Step-Up SIP %; on the Decumulation tab Inflation (Retirement) and Sim Years.

**Tabs:**
- **Summary**: Total Target Corpus with a progress bar (current corpus as % funded), future
  monthly and annual expense, and how many years of expenses Buckets 2–4 cover.
- **Bucket Allocation**: a `BucketBar` of the 5-bucket plan (B1 Safety, B2 Liquid, B3 Low Risk,
  B4 Med Risk, B5 Growth) with each bucket's instrument and logic.
- **Growth Breakdown**: the B5 equity split (flexi / mid / small cap).
- **Accumulation Phase**: flat and step-up SIP required, an `AreaSeries` of yearly growth, and a
  monthly projection table (opening, SIP, returns, closing, % of target).
- **Decumulation Phase**: a `StackedAreaSeries` of bucket balances over the simulation and a
  monthly drawdown table per bucket with notes.

---

## Calculation Cores

All math lives in pure TypeScript functions with no side effects; screens call them inside `useMemo`.

### `goalProjection.ts` — `projectGoals(input: ProjectionInput): ProjectionResult`

Iterates year 0 to `years`. Year 0 uses the starting pool with no addition. For years ≥ 1:
- `invest` is multiplied by `(1 + raisePct)` each year
- `addedAnnual = investMo × 12` is added to pool
- Goals due in the current year are funded in order from the pool; the first unfunded shortfall halts further goal payments for that year
- Expense inputs are tracked and inflated but never deducted from pool

Returns per-year rows and `GoalOutcome[]` with `funded` flag and `fundedYear`.

### `sipCalc.ts` — `projectSip(input: SipInput): SipMonthRow[]`

Monthly loop over `years × 12` months:
```
currentValue = currentValue × (1 + annualRate/12) + currentInvestment
```
Step-up (if `stepUpPct > 0`) is applied to `currentInvestment` every 12 months:
```
currentInvestment = currentInvestment × (1 + stepUpPct/100)
```
Initial lumpsum seeds `currentValue` at month 0.

### `lumpsumCalc.ts` — `projectLumpsum(input: LumpsumInput): LumpsumYearRow[]`

Standard compound interest, year 0 to `years`:
```
value = investment × (1 + rate)^year
```

### `cagrCalc.ts` — `calcCagr(input: CagrInput): number`

```
CAGR = (endValue / startValue)^(1 / years) − 1
```
Returns 0 if `startValue` or `years` is 0.

`Cagr.tsx` resolves `years` from either a direct numeric input or `(endDate − startDate) / 365.25` (calendar days).

### `salaryCalc.ts` — `computeSalary(input: SalaryInput, rules: FyTaxRules): SalaryResult`

**Regime: New**
- Standard deduction: ₹75,000
- Taxable = `gross − stdDed − employerNpsDed`
- Tax = progressive slab function on taxable income
- Rebate u/s 87A if taxable ≤ ₹12,00,000 (base tax zeroed)

**Regime: Old**
- Standard deduction: ₹50,000
- HRA exemption = `min(hra, ratio×basic, max(0, annualRent − 0.1×basic))`; ratio 0.5 metro / 0.4 non-metro
- 80C cap: ₹1,50,000 (employeePF + other80c)
- 80CCD(1B) NPS cap: ₹50,000
- 80D cap: ₹75,000
- Taxable = `gross − stdDed − hraEx − ltaEx − booksEx − telEx − 80C − 80CCD1B − 80D − employerNPS − profTax`
- Rebate u/s 87A if taxable ≤ ₹5,00,000

**Both regimes:**
- Employee PF = `min(0.12 × basic, ₹21,600)`; Employer PF same cap
- Professional tax: ₹2,400/yr (fixed; not configurable)
- Surcharge bands: 10% above ₹50L, 15% above ₹1Cr, 25% above ₹2Cr taxable
- Cess: 4% on (base tax + surcharge)
- `incomeTax = baseTax + surcharge + cess`
- `takeHomeAnnual = gross − employeePF − profTax − incomeTax`
- `effRate = (incomeTax / gross) × 100`

### `fireCalc.ts`

`computeFireSummary` inflates the monthly expense to retirement, multiplies the annual expense by
the corpus multiple, and allocates the buckets (B1 and B2 one year of expenses each, B3 two years,
B4 three years, B5 the remainder). `solveAccumulation` finds the flat and step-up SIP that reach the target;
`simulateDecumulation` draws monthly expenses down through the buckets.

### `taxSlabs.ts` — `TAX_RULES`

Defines `FyTaxRules` per financial year. Only `"FY2025-26"` is present. The screen renders a dropdown of `Object.keys(TAX_RULES)`, so adding future FY entries extends the dropdown automatically.

### `AreaSeries.tsx` — shared chart

Used by SIP, Lumpsum and FIRE (Accumulation). Renders an inline SVG (600×200 viewBox) with:
- A filled area under the Total Value line
- A Total Value polyline
- An Invested Amount polyline (flat for Lumpsum; stepped for SIP annual snapshots)
- A legend (Invested / Total Value)
- X-axis labels below the SVG

No tooltips, no hover interaction, no axes with tick values — purely visual.

---

## API Endpoints Consumed

**None.** All six calculators are entirely client-side. No `fetch` or API calls are made from any planning screen or calc module. The FastAPI backend at `127.0.0.1:8765` is not involved.

| Screen | API calls |
|---|---|
| GoalPlanner | None |
| Sip | None |
| Lumpsum | None |
| Cagr | None |
| Salary | None |
| Fire | None |

---

## Implementation Notes

- **localStorage persistence**: Each calculator saves its full input state to a dedicated key (`spendsift.{calculator}.v1`) on every state change via `useEffect`. State is hydrated on mount with a `loadState()` function that merges saved state over defaults; JSON parse errors are silently ignored and fall back to defaults.
- **Pure-function design**: Calc modules export typed pure functions that take a plain input object and return a plain result. They import nothing from React or any UI layer. This makes them testable in isolation (Vitest, no DOM).
- **Test coverage**: Dedicated test files exist for all six calc modules under `frontend/src/__tests__/`: `cagrCalc.test.ts`, `sipCalc.test.ts`, `lumpsumCalc.test.ts`, `goalProjection.test.ts`, `salaryCalc.test.ts`, `fireCalc.test.ts`. Screen-level rendering is covered by `screens.test.tsx`.
- **FY hardcode**: Only `"FY2025-26"` rules exist in `taxSlabs.ts`. The FY dropdown in the Salary screen will only ever show one option until additional years are added.
- **CTC mode derivation**: In `ctc` mode, basic is fixed at 40% CTC, employer PF is capped at ₹21,600/yr, and special allowance is `CTC − basic − HRA − employerPF − employerNPS`. Gross credited = basic + HRA + special (employer PF is excluded from gross).
- **Goal Planner expense tracking**: Monthly expenses are tracked and inflated for reference display but are never subtracted from the pool in the projection. The projection is purely savings-in vs goals-out.

---

## Known Gaps / Divergences

- **No tax inflation**: Only FY2025-26 is defined. No FY2024-25 or earlier for backward comparison.
- **Old regime surcharge check**: The surcharge threshold check uses `taxable` income, not the gross income figure that ITACT specifies. Marginal relief is not implemented.
- **SIP step-up timing**: Step-up is applied after month 12, 24, etc. — i.e., the increased amount takes effect from month 13 onwards, not at the start of year 2.
- **Goal Planner no investment returns**: The monthly contributions are added linearly to the pool (no compounding on the pool balance itself). The pool does not earn returns between contributions.
- **AreaSeries no Y-axis**: The chart has no axis labels or tick values; the magnitude of values is not readable from the chart alone.
- **AreaSeries no tooltip/interaction**: Hovering over the chart provides no data readout.
- **Lumpsum no breakdown table**: Unlike SIP, the Lumpsum screen has no per-year data table — only the chart and the three stat blocks.
- **Professional tax fixed**: Professional tax is hardcoded at ₹2,400/yr (Karnataka slab). It is not configurable and is applied in both regimes for salary calculation (though for the new regime it is not deducted from taxable income).
- **80CCD(2) employer NPS deduction**: Applied in both new and old regimes in `computeSalary`, but the new regime does not currently display it in the tax-working table in `Salary.tsx` (old-regime block only shows it there). The calculation is correct; the display omits it for the new regime.
