/**
 * Screens whose header shows the shared Month | FY | Custom `PeriodToggle`
 * instead of the month-only `MonthPicker`. A screen listed here reads
 * `usePeriod().selection` / `.range`. Kept out of ContentHeader.tsx so that
 * file only exports components.
 */
export const PERIOD_SCREENS: ReadonlySet<string> = new Set([
  "expense/overview",
  "expense/tags",
  "income/overview",
  "income/tags",
  "cashflow/overview",
  // Net Worth · Liabilities · Credit cards also reads the period, but its
  // toggle sits in the Net Worth tab bar (features/networth/netWorthTab.tsx).
]);

/**
 * Cash Flow · Overview's "Compare FYs" view lives in the URL (`?view=compare`)
 * so the header and the screen agree on it: in that view the FY range picker
 * governs and the header hides the PeriodToggle.
 */
export const COMPARE_VIEW_PARAM = "view";
export const COMPARE_VIEW_VALUE = "compare";

export function isCompareView(key: string, search: string): boolean {
  return key === "cashflow/overview" && new URLSearchParams(search).get(COMPARE_VIEW_PARAM) === COMPARE_VIEW_VALUE;
}
