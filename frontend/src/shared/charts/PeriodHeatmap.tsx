/**
 * PeriodHeatmap: a full-width calendar heatmap for a period series.
 *
 * `<PeriodHeatmap granularity from to cells positive legend tip? onPick? today? />`
 * - `granularity="day"`: one 7-column month grid per calendar month touching
 *   [from, to], with leading blanks for the weekday of the 1st. Days outside
 *   the range are dashed (`.out`), days after today too (`.future`).
 * - `granularity="month"`: a 6-column grid of month cells.
 * - Colour carries direction, never a minus sign: a cell's value > 0 means
 *   `positive` ("in" = money in, green; "out" = money out, red), a value < 0
 *   the other way. The shade deepens with |value| (square root of its share
 *   of the largest |value|). The cell prints |value| compactly.
 * - `legend`: "magnitude" (less → more, in the `positive` colour) or
 *   "direction" (out ← → in).
 * - `onPick(cell)` on click / Enter; `tip(cell)` is the hover body.
 * - `outline(key)` (optional, off by default): a note for a cell that gets a
 *   dashed outline (e.g. "includes one-off ₹82,700" on Expense · Overview),
 *   appended to its accessible name; `undefined` leaves the cell plain.
 * Every grid shares one colour scale.
 */
import { useContext, type ReactNode } from "react";
import { TooltipContext } from "@/shared/theme/tooltipContext";
import { inrWhole } from "@/shared/format/money";
import { compactK, directionOf, type Direction } from "./chartScale";
import { bucketTitle, dayKey, isFuture, monthName, monthsBetween, todayKey } from "./periodBuckets";

export type HeatCell = { key: string; label: string; value: number };

const DOW = ["S", "M", "T", "W", "T", "F", "S"];

function shade(value: number, max: number, dir: Direction): string | undefined {
  if (!value) return undefined;
  const pct = Math.round(18 + 62 * Math.sqrt(Math.min(1, Math.abs(value) / (max || 1))));
  return `color-mix(in srgb, var(${dir === "in" ? "--green" : "--red"}) ${pct}%, var(--surface2))`;
}

function Legend({ legend, positive }: { legend: "magnitude" | "direction"; positive: Direction }) {
  const colour = positive === "in" ? "var(--green)" : "var(--red)";
  const ramp =
    legend === "direction"
      ? "linear-gradient(90deg,var(--red),var(--surface2),var(--green))"
      : `linear-gradient(90deg,var(--surface2),${colour})`;
  return (
    <span className="hm-key" aria-hidden="true">
      {legend === "direction" ? "out" : "less"}
      <span className="hm-ramp" style={{ background: ramp }} />
      {legend === "direction" ? "in" : "more"}
    </span>
  );
}

export function PeriodHeatmap({
  granularity,
  from,
  to,
  cells,
  positive,
  legend = "magnitude",
  tip,
  onPick,
  outline,
  today = todayKey(),
}: {
  granularity: "day" | "month";
  from: string;
  to: string;
  cells: HeatCell[];
  positive: Direction;
  legend?: "magnitude" | "direction";
  tip?: (cell: HeatCell) => ReactNode;
  onPick?: (cell: HeatCell) => void;
  outline?: (key: string) => string | undefined;
  today?: string;
}) {
  const setTip = useContext(TooltipContext);
  const byKey = new Map(cells.map((c) => [c.key, c]));
  const max = Math.max(0, ...cells.map((c) => Math.abs(c.value)));

  const cell = (key: string, corner: string): ReactNode => {
    if (key < from.slice(0, key.length) || key > to.slice(0, key.length)) {
      return (
        <div className="hm-day out" key={key} aria-hidden="true">
          <span className="dn">{corner}</span>
        </div>
      );
    }
    if (isFuture(key, today)) {
      return (
        <div className="hm-day future" key={key} aria-hidden="true">
          <span className="dn">{corner}</span>
        </div>
      );
    }
    const c = byKey.get(key) ?? { key, label: corner, value: 0 };
    const dir = directionOf(c.value, positive);
    const body = tip?.(c);
    const note = outline?.(key);
    const show = (e: { clientX: number; clientY: number }) => {
      if (body !== undefined) setTip({ content: body, x: e.clientX, y: e.clientY });
    };
    return (
      <button
        type="button"
        key={key}
        className={note ? "hm-day outlined" : "hm-day"}
        data-dir={c.value ? dir : undefined}
        style={{ background: shade(c.value, max, dir) }}
        aria-label={`${bucketTitle(key)}${c.value ? ` · ${inrWhole(Math.abs(c.value))} ${dir}` : ""}${note ? ` · ${note}` : ""}`}
        onClick={onPick ? () => (setTip(null), onPick(c)) : undefined}
        onMouseEnter={show}
        onMouseMove={show}
        onMouseLeave={() => setTip(null)}
      >
        <span className="dn">{corner}</span>
        {c.value !== 0 && <span className="amt">{compactK(Math.abs(c.value))}</span>}
      </button>
    );
  };

  const key = <Legend legend={legend} positive={positive} />;

  if (granularity === "month") {
    const label = (k: string) => byKey.get(k)?.label ?? monthName(Number(k.slice(5, 7)));
    const months = monthsBetween(from, to).map(({ year, month }) => dayKey(year, month, 1).slice(0, 7));
    return (
      <div className="hm">
        <div className="hm-title">
          <span>{`${bucketTitle(months[0])} – ${bucketTitle(months[months.length - 1])}`}</span>
          {key}
        </div>
        <div className="hm-grid year">{months.map((k) => cell(k, label(k)))}</div>
      </div>
    );
  }

  return (
    <div className="hm">
      {monthsBetween(from, to).map(({ year, month }, i) => {
        const total = new Date(year, month, 0).getDate();
        const lead = new Date(year, month - 1, 1).getDay();
        return (
          <div className="hm-month" key={`${year}-${month}`}>
            <div className="hm-title">
              <span>{`${monthName(month)} ${year}`}</span>
              {i === 0 && key}
            </div>
            <div className="hm-grid">
              {DOW.map((d, j) => (
                <div className="hm-dow" key={`dow-${j}`}>
                  {d}
                </div>
              ))}
              {Array.from({ length: lead }, (_, j) => (
                <div className="hm-day blank" key={`blank-${j}`} aria-hidden="true" />
              ))}
              {Array.from({ length: total }, (_, d) => cell(dayKey(year, month, d + 1), String(d + 1)))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
