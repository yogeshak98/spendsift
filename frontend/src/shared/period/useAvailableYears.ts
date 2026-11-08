import { useMemo } from "react";
import { useReport } from "@/shared/api/useReport";

type AvailableYears = { calendar_years: number[]; fiscal_years: number[] };

export function currentFY(): number {
  const now = new Date();
  const yr = now.getFullYear();
  const mo = now.getMonth() + 1; // 1-based
  return mo >= 4 ? yr : yr - 1;
}

// How many of an FY's 12 months (Apr..Mar) are NOT in the future relative to
// `now`. A future FY -> 0; a fully-elapsed FY -> 12; the in-progress FY -> the
// count up to and including the current month. Used to stop a running line
// (balance/outstanding) at the current month WHILE still drawing every month's
// bars — the months are chronological, so this is the line's point count.
export function realFyMonthCount(fy: number, now: Date = new Date()): number {
  const nowYr = now.getFullYear();
  const nowMo = now.getMonth() + 1; // 1-based
  let count = 0;
  for (const month of [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]) {
    const yr = month >= 4 ? fy : fy + 1;
    if (yr < nowYr || (yr === nowYr && month <= nowMo)) count += 1;
  }
  return count;
}

// D2: the /available-years endpoint returns only the FYs that actually have
// data (a sparse present-set). For the FY selector we deliberately render the
// CONTIGUOUS [min..max] range instead — a stray Jan–Mar posting would otherwise
// inject a fringe FY and leave gaps that make the dropdown jump
// non-monotonically. Filling the gaps gives a stable, predictable selector and
// lets the user navigate to an empty interior FY (which renders the sanctioned
// zero-state, not a crash). Empty input -> [].
function contiguous(years: number[]): number[] {
  if (years.length === 0) return [];
  const min = Math.min(...years);
  const max = Math.max(...years);
  const out: number[] = [];
  for (let y = min; y <= max; y++) out.push(y);
  return out;
}

export function useAvailableYears(): {
  calendarYears: number[]; // raw present-set, ascending (for the calendar picker)
  fiscalYears: number[]; // CONTIGUOUS min..max FY range (for FY selectors) — see D2
  loading: boolean;
} {
  const { data, error } = useReport<AvailableYears>("/api/reports/available-years");
  const loading = data === null && error === null;
  // On error or empty ledger: both arrays [] and loading=false (no throw).
  // Memoize on the fetched `data` so array identity is stable across renders —
  // FYSelector's default-selection effect is keyed on [fiscalYears] and would
  // otherwise re-run on every parent re-render.
  const calendarYears = useMemo(() => data?.calendar_years ?? [], [data]);
  const fiscalYears = useMemo(() => contiguous(data?.fiscal_years ?? []), [data]);
  return { calendarYears, fiscalYears, loading };
}
