/**
 * PeriodToggle: the shared Month | FY | Custom period control.
 *
 * Usage: `<PeriodToggle />`, with no props. It reads and writes the shared selection
 * through `usePeriod()` (`../period`), so any screen that reads
 * `usePeriod().selection` / `.range` follows it and the choice persists
 * across screens and reloads. ContentHeader renders it on the screens listed
 * in `PERIOD_SCREENS` (`../shell/periodScreens`); a screen can also render it
 * itself.
 *
 * - Month: the existing `MonthPicker`. FY: the existing `FYSelector`.
 *   Custom: two date inputs labelled "From" / "To".
 * - Switching modes: month → FY takes the FY of the current anchor month;
 *   FY/Custom → Month returns to the last month picked here when it lies in
 *   the current range, else the anchor (`anchorOf`); → Custom seeds from the
 *   current range.
 * - A custom edit with From after To is not committed; it shows a
 *   `role="alert"` message until fixed.
 */
import { useRef, useState } from "react";
import { usePeriod } from "./usePeriod";
import { anchorOf, fyOfMonth, monthRange, type DateRange, type Period, type PeriodSelection } from "./periodRange";
import { useAvailableYears } from "./useAvailableYears";
import { FYSelector } from "./FYSelector";
import { MonthPicker } from "./MonthPicker";

type Mode = PeriodSelection["kind"];
const MODES: { kind: Mode; label: string }[] = [
  { kind: "month", label: "Month" },
  { kind: "fy", label: "FY" },
  { kind: "custom", label: "Custom" },
];

export function PeriodToggle() {
  const { selection, setSelection, range } = usePeriod();
  const { calendarYears } = useAvailableYears();
  // The last month picked here, so Month → FY → Month lands back on it.
  const lastMonth = useRef<Period | null>(
    selection.kind === "month" ? { year: selection.year, month: selection.month } : null,
  );

  const anchor = anchorOf(selection);
  const pickMonth = (p: Period) => {
    lastMonth.current = p;
    setSelection({ kind: "month", year: p.year, month: p.month });
  };

  const switchTo = (kind: Mode) => {
    if (kind === selection.kind) return;
    if (kind === "fy") {
      setSelection({ kind: "fy", fy: fyOfMonth(anchor.year, anchor.month) });
    } else if (kind === "custom") {
      setSelection({ kind: "custom", from: range.from, to: range.to });
    } else {
      const last = lastMonth.current;
      const lastRange = last ? monthRange(last.year, last.month) : null;
      const inRange = lastRange !== null && lastRange.from >= range.from && lastRange.to <= range.to;
      pickMonth(inRange && last ? last : anchor);
    }
  };

  // Same navigable span as the month-only header picker: the ledger's years
  // plus the selected year and today's year.
  const years = [...new Set([...calendarYears, anchor.year, new Date().getFullYear()])].sort((a, b) => a - b);

  return (
    <div className="ptoggle">
      <div className="ptoggle-seg" role="group" aria-label="Period type">
        {MODES.map((m) => (
          <button
            key={m.kind}
            type="button"
            aria-pressed={selection.kind === m.kind}
            onClick={() => switchTo(m.kind)}
          >
            {m.label}
          </button>
        ))}
      </div>
      {selection.kind === "month" && (
        <MonthPicker
          year={selection.year}
          month={selection.month}
          minYear={years[0]}
          maxYear={years[years.length - 1]}
          onChange={pickMonth}
        />
      )}
      {selection.kind === "fy" && (
        <FYSelector value={selection.fy} onChange={(fy) => setSelection({ kind: "fy", fy })} />
      )}
      {selection.kind === "custom" && (
        <CustomRange
          key={`${range.from}|${range.to}`}
          range={range}
          onCommit={(r) => setSelection({ kind: "custom", from: r.from, to: r.to })}
        />
      )}
    </div>
  );
}

/** Draft From/To inputs; commits only a complete, ordered pair. Remounted (keyed) on each committed range. */
function CustomRange({ range, onCommit }: { range: DateRange; onCommit: (r: DateRange) => void }) {
  const [draft, setDraft] = useState<DateRange>(range);
  const invalid = draft.from !== "" && draft.to !== "" && draft.from > draft.to;

  const edit = (next: DateRange) => {
    setDraft(next);
    if (next.from && next.to && next.from <= next.to && (next.from !== range.from || next.to !== range.to)) {
      onCommit(next);
    }
  };

  return (
    <div className="ptoggle-custom">
      <input
        type="date"
        aria-label="From"
        value={draft.from}
        onChange={(e) => edit({ ...draft, from: e.target.value })}
      />
      <span className="ptoggle-dash">–</span>
      <input
        type="date"
        aria-label="To"
        value={draft.to}
        onChange={(e) => edit({ ...draft, to: e.target.value })}
      />
      {invalid && (
        <span role="alert" className="ptoggle-alert">
          From must be on or before To
        </span>
      )}
    </div>
  );
}
