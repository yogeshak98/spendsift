import { createContext } from "react";
import { anchorOf, rangeOf, type DateRange, type Period, type PeriodSelection } from "./periodRange";

/**
 * The one period selection shared by every period-aware screen (Expense,
 * Income, Cash Flow, Credit Cards). `selection` + `range` are the new API;
 * `{year, month, setPeriod}` stay for month-only screens (Budget, Recurring,
 * Dashboard): `year`/`month` are the selection's anchor month and `setPeriod`
 * selects that month.
 */
export type PeriodCtx = Period & {
  setPeriod: (p: Period) => void;
  selection: PeriodSelection;
  setSelection: (s: PeriodSelection) => void;
  range: DateRange;
};

export function currentMonth(): PeriodSelection {
  const now = new Date();
  return { kind: "month", year: now.getFullYear(), month: now.getMonth() + 1 };
}

export function ctxValue(selection: PeriodSelection, setSelection: (s: PeriodSelection) => void): PeriodCtx {
  return {
    ...anchorOf(selection),
    setPeriod: (p: Period) => setSelection({ kind: "month", year: p.year, month: p.month }),
    selection,
    setSelection,
    range: rangeOf(selection),
  };
}

export const PeriodContext = createContext<PeriodCtx>(ctxValue(currentMonth(), () => {}));
