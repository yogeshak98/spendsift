import { useState, type ReactNode } from "react";
import { ctxValue, currentMonth, PeriodContext } from "./periodContext";
import { parseSelection, type PeriodSelection } from "./periodRange";

// The pure helpers (rangeOf, anchorOf, periodLabel, rangeQuery, ...) live in
// ./periodRange, the context in ./periodContext and the hook in ./usePeriod,
// so this file only exports the provider.
export type { DateRange, Period, PeriodSelection } from "./periodRange";

const KEY = "ss-period";

function initial(): PeriodSelection {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return parseSelection(JSON.parse(raw)) ?? currentMonth();
  } catch {
    /* ignore malformed storage */
  }
  return currentMonth();
}

export function PeriodProvider({ children }: { children: ReactNode }) {
  const [selection, setSelectionState] = useState<PeriodSelection>(initial);
  const setSelection = (s: PeriodSelection) => {
    setSelectionState(s);
    try {
      localStorage.setItem(KEY, JSON.stringify(s));
    } catch {
      /* ignore storage failure */
    }
  };
  return <PeriodContext.Provider value={ctxValue(selection, setSelection)}>{children}</PeriodContext.Provider>;
}
