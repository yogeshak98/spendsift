# SpendSift Frontend Architecture

Cross-cutting structure of the React/TS SPA served by FastAPI on `127.0.0.1:8765`.

> Facts only.

---

## Purpose

React 19 + TypeScript SPA bundled by Vite, served by FastAPI as static files. All data is fetched from the same loopback origin (`127.0.0.1:8765`). No external services.

---

## Source layout

| Folder | Holds |
|---|---|
| `src/shell/` | The app frame: `Rail.tsx` (left nav), `ContentHeader.tsx` (breadcrumb + period control) |
| `src/features/` | Screens built as a self-contained feature: `dashboard/` (the Dashboard) and `networth/` (the Net Worth screen and its tabs), each with its own API types, components, CSS and `__tests__/` |
| `src/screens/` | The other screens, one folder per rail domain (`cashflow/`, `expense/`, `income/`, `liabilities/`, `ledger/`, `planning/`, `tax/`, `settings/`), plus `overview/` (the period overview shared by Expense, Income and Credit cards) |
| `src/shared/` | Code every screen may use: `api/` (`http.ts`, `useReport.ts`, the typed `client.ts`, `codedError.ts`), `charts/` (period heatmap and bars, Sankey, chart switches), `format/money.ts`, `period/` (the shared period context, pickers, `periodScreens.ts`), `theme/` (themes, tooltip), `ui/` (`DetailModal`) |
| `src/components/` | Small layout components (`Panel`, `StatBlock`, `TreeTable`, `DataTable` + its `cell.ts` helper, …) |
| `src/charts/` | Older single-screen charts (planning and liabilities) |
| `nav.ts`, `routes.tsx`, `App.tsx` | Navigation model, screen table and redirects, app layout |

Imports use the `@/` alias for `src/` (for example `@/shared/format/money`).

---

## App shell & navigation

### Entry point

`frontend/src/main.tsx` mounts the app into `#root` under `StrictMode` + `BrowserRouter` (react-router-dom v7).

### App layout (`App.tsx`)

```
<TooltipProvider>          ← global floating tooltip
  <PeriodProvider>         ← global calendar month/year context
    <div style="display:flex; minHeight:100vh">
      <Rail />             ← 188px sticky left nav
      <div class="content">
        <ContentHeader />  ← sticky breadcrumb + optional period pickers
        <ScreenOutlet />   ← keyed off pathname, no <Routes>
      </div>
    </div>
  </PeriodProvider>
</TooltipProvider>
```

### Routing

There are **no `<Route>` elements**. `ScreenOutlet` reads `useLocation().pathname`, strips the leading `/`, and looks up the component in a flat `SCREENS` record defined in `routes.tsx`. An unmatched key renders a "Coming soon" stub.

```ts
// routes.tsx (abridged)
export const SCREENS: Record<string, () => JSX.Element> = {
  dashboard: Dashboard,
  "networth/overview": NetWorthOverview,
  "networth/investments": netWorthTab(InvestmentsOverview),
  // ...
};
```

One exception to exact keys: `INSTRUMENT_SCREENS` resolves a path with one trailing instrument
id (`networth/investments/mutual_fund/<id>`) when the exact lookup misses.

There is no `<Route>`-based fallback. The FastAPI server must serve `index.html` for all SPA paths.

### Rail (`shell/Rail.tsx`)

- Fixed 188px left column, sticky full-height.
- Iterates `NAV` from `nav.ts`. Each `Domain` entry has an `id`, `label`, icon glyph, and a `subs` array of `SubView`.
- Active domain is derived from `pathname.split("/")[1]`, except that `networth/liabilities/*` lights **Liabilities** rather than Net Worth.
- When a domain is active and has subs, the sub-list expands inline (CSS class `nav-sub show`); a sub stays lit on drill-downs nested under it.
- `.rail-foot` holds the **Quick add** button (→ `/ledger/quick-add`), the Settings domain (pinned there because it is last in `NAV`), and `ThemeSwitcher`.
- A global `n` keypress (outside inputs) also opens Quick add.

### Navigation model (`nav.ts`)

Top-level domains and their sub-views:

| Domain | Sub-views |
|---|---|
| dashboard | — |
| networth | — (one screen with its own tabs: overview, investments, liabilities, allocation) |
| cashflow | overview, recurring |
| expense | overview, tags, budget, uncategorized |
| liabilities | — (a shortcut to Net Worth's Liabilities tab) |
| income | overview, tags |
| ledger | transactions, raw, import |
| planning | goals, sip, lumpsum, cagr, salary, fire |
| tax | overview, income, capital-gains, deductions, regime-compare, prepaid |
| doctor | — |
| docs | — |
| settings | accounts, templates, openings |

Domain `id` + sub `id` map directly to `SCREENS` keys (e.g. `"expense/overview"`). The rail links a
domain to its first sub-view. `ledger/quick-add` is a screen with no nav entry (reached from the rail's Quick add button or `n`).

**Redirects.** `REDIRECTS` in `routes.tsx` sends retired URLs to their successors (the query string
is kept): `expense/monthly`, `expense/breakdown` and `expense/yearly` → `expense/overview`;
`income` → `income/overview`; `cashflow/income-statement`, `cashflow/monthly` and
`cashflow/yearly` → `cashflow/overview`; `networth` and `networth/balance` →
`networth/overview`; `liabilities` and `liabilities/credit-cards` → `networth/liabilities`;
`liabilities/interest`, `liabilities/repayment` and `liabilities/balance` → the same view under
`networth/liabilities/`.

**Net Worth tabs.** `features/networth/netWorthTab.tsx` wraps each Net Worth view in the tab bar
(`NetWorthTabs`), adds the tab's own control on the right, and the view chips under Liabilities.
A `?section=<group>` link on any tab moves to Overview, which highlights that group's cards.

### ContentHeader (`shell/ContentHeader.tsx`)

Sticky bar below the Rail, full-width at top of the content column.

- Always renders a breadcrumb (pathname segments joined with ` · `).
- On the Dashboard (always as of today) and the whole `networth/*` subtree (its tab bar replaces the header) it renders nothing.
- On the period screens (`PERIOD_SCREENS` in `shared/period/periodScreens.ts`: `expense/overview`, `expense/tags`, `income/overview`, `income/tags`, `cashflow/overview`) it renders the shared **Month | FY | Custom** `PeriodToggle`. Net Worth › Liabilities › Credit cards reads the same period; its toggle sits in the Net Worth tab bar.
- On Cash Flow · Overview's Compare FYs view (`?view=compare`), `ledger/raw` and `ledger/import` it renders the breadcrumb only.
- Elsewhere it renders the month picker (`MonthPicker`, controlled via `usePeriod`).
- Year options come from `useAvailableYears()` (calendar years from the backend), always including the currently-selected year so the select is never empty.

---

## Data layer

### Global period context (`shared/period/period.tsx`)

`PeriodProvider` holds one shared period selection — a month, an FY, or a custom inclusive date range — persisted to `localStorage` under key `ss-period` (an older stored `{ year, month }` is read as a month). Initial value is the stored selection, else today's month. `usePeriod()` exposes `selection`, `setSelection` and its date `range` for the period screens, plus `{ year, month, setPeriod }` for month-only screens: the month the selection points at (for an FY or a custom range, its last month up to today).

### `useAvailableYears` (`shared/period/useAvailableYears.ts`)

Fetches `/api/reports/available-years` once. Returns:
- `calendarYears` — raw present-set of calendar years from the backend (for the ContentHeader year picker).
- `fiscalYears` — **contiguous** `min..max` range (gaps filled deliberately — see inline D2 comment), for `FYSelector` dropdowns.
- `loading` boolean.

Also exports `currentFY()` (April-based) and `realFyMonthCount(fy)` (used by charts to stop running-balance lines at the current month).

### `useReport<T>` (`shared/api/useReport.ts`)

Minimal fire-and-forget data hook. Calls `api.getJSON<T>(url)` on mount (or URL change), returns `{ data, error }`. Used by `useAvailableYears` and by most dashboard screens. Does not cache, deduplicate, or handle loading state beyond `data === null`.

### Two parallel API layers (migration in progress)

| Layer | File | Mechanism | Typed? | Used by |
|---|---|---|---|---|
| Legacy | `src/shared/api/http.ts` | hand-rolled `fetch` wrappers: `getJSON`, `postJSON`, `postForm`, `putJSON`, `del` | No runtime types; callers cast with generics | `useReport` and most screens (Dashboard, Net Worth, Ledger, Settings, Tax, …) |
| OpenAPI client | `src/shared/api/client.ts` | `openapi-fetch` v0.17 bound to `paths` from `schema.d.ts` | Full compile-time types from generated schema | Cash Flow · Overview (Sankey), `DetailModal` (drill-down), `LedgerRaw`; the generated types (`schema.d.ts`) are used by most screens, including Dashboard and Net Worth |

`schema.d.ts` is generated by `npm run gen:api` (see Build/codegen). Both layers are present simultaneously; no single migration date is set. New screens may use either layer.

---

## Component kit

Components live in `frontend/src/components/` (except `FYSelector`, in `shared/period/`). There is no per-kit stylesheet; their classes are styled in `theme.css` and the screen CSS files.

| Component | Role |
|---|---|
| `DataTable` | Flat `<table>` with column definitions (`key`, `label`, `num?`). All cells use monospace class. Sortable cells are built with `cell(sort, node)` from `components/cell.ts`. |
| `TreeTable` | Hierarchical table with expand/collapse via caret. Rows are pre-flattened with `depth` + `hasChildren`; visibility is computed by ancestor-collapse check. First column gets indentation + caret slot. |
| `Panel` | Titled card container. Optional `glow` (highlight border) and `fill` (stretch body) variants. Optional `right` slot in header. |
| `Pill` | Inline badge. Variants: `cat` (default), `unk`, `ok`, `short`. |
| `Callout` | Banner-style notice. Variants: `warn`, `bad`, `info`. |
| `StatBlock` | KPI tile: label + large value + optional delta. Tone variants map to CSS color tokens (`green`, `red`, `amber`, `blue`, `dim`, `text`). |
| `ProgressTrack` | Horizontal percentage bar. Tone sets fill color (`green`/`amber`/`red`). |
| `FYSelector` (`shared/period/FYSelector.tsx`) | Controlled `<select>` for financial-year ranges. Reads `fiscalYears` from `useAvailableYears`. Snaps to `currentFY()` or latest FY when the controlled value is out of range (guarded against loops). Shows a disabled placeholder while loading. |

---

## Charts

**No chart library**: charts use inline SVG, CSS flexbox, or the d3-sankey layout. The `d3` and `d3-sankey` packages are the only chart-related dependencies; all rendering is manual SVG/DOM.

Shared charts (`frontend/src/shared/charts/`), used by the period overviews:

| Component | What it renders |
|---|---|
| `PeriodHeatmap` | A full-width calendar heatmap for a period: one month grid per month for a range of days, or one cell per month. Colour carries direction (in green, out red), the shade the size. A cell click opens the detail modal. |
| `PeriodBars` | One bar group per day or month, Linear or Compressed (square-root) axis. A bar click opens the detail modal. |
| `ChartSwitches`, `Segmented` | The panel-header switches (Heatmap \| Bars, Linear \| Compressed, Both \| Income \| Expenses, Summary \| Detailed); choices are remembered per viewer (`chartPrefs.ts`). |
| `Sankey` | The account-graph cash-flow diagram (`/api/reports/cashflow/sankey`), d3-sankey layout in `sankeyLayout.ts`; back flows (refunds, repayments) drawn as loops under the diagram. A node or flow click opens the detail modal. |

`shared/ui/DetailModal.tsx` is the click-through behind every cell, bar, category row and Sankey
node or flow: it lists the transactions behind it from `/api/reports/cashflow/rows`.

Older single-screen charts (`frontend/src/charts/`):

| Component | What it renders |
|---|---|
| `AreaSeries`, `StackedAreaSeries` | Area charts as inline SVG, used in planning projections. |
| `MonthlyCombo` | Paired grouped bars (income green / expenses red) per month. |
| `Pies` | Row of CSS `conic-gradient` pie discs with label + value. No SVG. |
| `RankedBars` | Horizontal ranked bar list with label, fill bar, amount, and percentage. A row can expand to a detail list; a negative row draws an empty bar with a green amount. |
| `Waterfall` | Horizontal bar list with tone-colored fills; bars are proportional within an 88% track. |

The Dashboard's and Net Worth's own charts (net-worth line, spending bars, allocation donut)
live inside their feature folders.

---

## Theme & formatting

### CSS tokens (`src/theme.css`)

Three named themes, applied via `data-theme` on `<body>`:

| Theme | Character |
|---|---|
| `midnight` (default) | Very dark `#0f0f0f` background; blue accent `#60a5fa` |
| `carbon` | GitHub-dark palette; blue accent `#58a6ff` |
| `daylight` | Off-white `#f5f3ee` background; muted blue `#2b6cb0` |

Token set (same names across all themes): `--bg`, `--surface`, `--surface2`, `--border`, `--line`, `--text`, `--text2`, `--text3`, `--green`, `--amber`, `--red`, `--blue`, `--blue-border`, `--violet`, `--teal`, `--pink`, `--sans`, `--mono`.

Typefaces: IBM Plex Sans (UI) + IBM Plex Mono (data/labels). Loaded from Google Fonts (external request; blocked in offline environments).

### Theme switcher (`shared/theme/theme.tsx`, `useTheme.ts`)

`useTheme()` hook: reads from `localStorage` (`ss-theme`), writes to `data-theme` attribute on `<body>`. `ThemeSwitcher` is a `<select>` rendered inside Rail's `.rail-foot`.

### Formatting (`shared/format/money.ts`)

The one money formatter module: `inrWhole` (whole rupees, Indian grouping), `inr2` (two
decimals), `inrCompact` / `inrLakh` / `inrAbbr` (₹L / ₹Cr abbreviations), `inrSigned` and
`groupIN`. Negative amounts print as `-₹1,235`, and a missing or invalid value as `—`.

### Tooltip (`shared/theme/tooltip.tsx`)

Global floating tooltip implemented via React context (`TooltipContext`, `shared/theme/tooltipContext.ts`). `TooltipProvider` renders one `<div class="tip">` at a fixed pixel offset from cursor. Two usage patterns:

- `<Tip content={...}>` wrapper (HTML elements only) — attached via `onMouseEnter/Move/Leave` on a `<span>`.
- Direct `useContext(TooltipContext)` on SVG `<path>` / `<rect>` elements (Sankey) where wrapping in a `<span>` is invalid.

---

## Build / codegen

| Step | Command | Notes |
|---|---|---|
| Dev server | `npm run dev` | Vite HMR; `predev` hook runs `gen:api` first |
| Production build | `npm run build` | TypeScript compile + Vite bundle; `prebuild` hook runs `gen:api` |
| OpenAPI codegen | `npm run gen:api` | Runs `python -m backend.app.server.openapi openapi.json` then `openapi-typescript openapi.json -o src/shared/api/schema.d.ts` |
| Tests | `npm test` (= `vitest run`) | jsdom environment; `@testing-library/react`; globals enabled; setup in `setupTests.ts` |
| Lint | `npm run lint` | ESLint 10 + typescript-eslint + react-hooks + react-refresh |

**Generated files (gitignored):** `frontend/openapi.json` and `frontend/src/shared/api/schema.d.ts`. Both are regenerated on every `dev`/`build` invocation. `schema.d.ts` is present in the working tree when the app is running but is not tracked.

Vite `server.proxy` forwards `/api/*` to `http://127.0.0.1:8765` during development. In production, the FastAPI process serves both the API and the built static files on the same origin, so no proxy is needed.

---

## Known gaps / divergences

| Issue | Detail |
|---|---|
| Dual API layers | `shared/api/http.ts` (legacy) and `shared/api/client.ts` (typed OpenAPI) coexist. Most screens use the legacy layer (`http.ts` / `useReport`); only Cash Flow · Overview (Sankey), `DetailModal` (drill-down) and `LedgerRaw` call the typed client. No migration plan or timeline is established. |
| No router `<Route>` / fallback | Routing is a manual `SCREENS` lookup. There is no 404 screen; unknown paths render a "Coming soon" stub. The FastAPI SPA fallback must rewrite all paths to `index.html`. |
| `useReport` limitations | No request deduplication, no cache, no loading state differentiation between "loading" and "never fetched". Each component instance fetches independently. |
| Google Fonts dependency | `theme.css` imports from `fonts.googleapis.com`. Fails silently in offline environments (falls back to system sans-serif / monospace). |
| `schema.d.ts` gitignored but present | The typed schema file is generated on every dev/build but not committed. A clean checkout will fail TypeScript compilation until `gen:api` is run. |
