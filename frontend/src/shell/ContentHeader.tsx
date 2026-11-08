import { useLocation } from "react-router-dom";
import { usePeriod } from "@/shared/period/usePeriod";
import { useAvailableYears } from "@/shared/period/useAvailableYears";
import { MonthPicker } from "@/shared/period/MonthPicker";
import { PeriodToggle } from "@/shared/period/PeriodToggle";
import { isCompareView, PERIOD_SCREENS } from "@/shared/period/periodScreens";

export function ContentHeader() {
  const { year, month, setPeriod } = usePeriod();
  // Rules of hooks: useAvailableYears must run unconditionally, before the
  // early-return denylist branch below.
  const { calendarYears } = useAvailableYears();
  const { pathname, search } = useLocation();
  const key = pathname.replace(/^\//, "") || "dashboard";
  const crumb = key.split("/").join(" · ");
  // The import screen reviews a whole uploaded statement, not a dashboard
  // period — showing this picker next to per-transaction dates in the
  // ledger buffer reads as if it filtered them, but it does nothing here.
  // The raw ledger view is FY-scoped and carries its own FY select; the global
  // month/year picker does not apply.
  // The Net Worth screen owns its own tab bar (features/networth/NetWorthTabs),
  // which states the location, lets you switch, and carries each tab's own
  // control (the period on Credit cards, the benchmark on Investments).
  // Rendering this bar too would put a second header above it. The tabs win: a
  // breadcrumb only tells you where you are.
  // The Dashboard is always "as of today" (ruling R30): no period applies, and
  // a crumb reading "DASHBOARD" on the landing page says nothing.
  if (key === "networth" || key.startsWith("networth/") || key === "dashboard") {
    return null;
  }
  // Cash Flow · Compare FYs: its FY range picker governs, not the period.
  if (
    isCompareView(key, search) ||
    key === "ledger/raw" ||
    key === "ledger/import"
  ) {
    return (
      <div className="cbar">
        <div className="crumb">{crumb}</div>
      </div>
    );
  }
  // Period-aware screens (Expense, Income, Cash Flow overviews, By Tag) take
  // the shared Month | FY | Custom selection.
  if (PERIOD_SCREENS.has(key)) {
    return (
      <div className="cbar">
        <div className="crumb">{crumb}</div>
        <div className="right">
          <PeriodToggle />
        </div>
      </div>
    );
  }
  // Always include the current period `year` so an empty `calendarYears`
  // (loading / empty ledger) still yields a usable [year, year] span and the
  // selected period is always inside the navigable range. Today's year is
  // included too, so the current month is always reachable.
  const years = [...new Set([...calendarYears, year, new Date().getFullYear()])].sort((a, b) => a - b);
  return (
    <div className="cbar">
      <div className="crumb">{crumb}</div>
      <div className="right">
        <MonthPicker
          year={year}
          month={month}
          minYear={years[0]}
          maxYear={years[years.length - 1]}
          onChange={setPeriod}
        />
      </div>
    </div>
  );
}
