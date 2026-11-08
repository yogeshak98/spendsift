/**
 * Pure helpers for the shared period selection (Month | FY | Custom).
 *
 * Kept out of `period.tsx` so that file only exports the provider and hook
 * (react-refresh's only-export-components rule). Dates are ISO `YYYY-MM-DD`
 * strings and every range is inclusive at both ends, matching the backend's
 * `from` / `to` query params.
 */

export type Period = { year: number; month: number };

export type PeriodSelection =
  | { kind: "month"; year: number; month: number }
  | { kind: "fy"; fy: number }
  | { kind: "custom"; from: string; to: string };

export type DateRange = { from: string; to: string };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const pad = (n: number) => String(n).padStart(2, "0");

/** Local-calendar ISO date (no UTC shift). */
export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function lastDay(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

export function monthRange(year: number, month: number): DateRange {
  return { from: `${year}-${pad(month)}-01`, to: `${year}-${pad(month)}-${pad(lastDay(year, month))}` };
}

export function rangeOf(s: PeriodSelection): DateRange {
  if (s.kind === "month") return monthRange(s.year, s.month);
  if (s.kind === "fy") return { from: `${s.fy}-04-01`, to: `${s.fy + 1}-03-31` };
  return { from: s.from, to: s.to };
}

/** Indian FY (Apr–Mar) a calendar month falls in, named by its start year. */
export function fyOfMonth(year: number, month: number): number {
  return month >= 4 ? year : year - 1;
}

/**
 * The single month that stands for a selection on month-only screens (Budget,
 * Recurring, Dashboard). A month selection is its own anchor; otherwise the
 * month of min(range.to, today), never earlier than the range's first month.
 */
export function anchorOf(s: PeriodSelection, today: Date = new Date()): Period {
  if (s.kind === "month") return { year: s.year, month: s.month };
  const r = rangeOf(s);
  const t = isoDate(today);
  let d = r.to < t ? r.to : t;
  if (d < r.from) d = r.from;
  return { year: Number(d.slice(0, 4)), month: Number(d.slice(5, 7)) };
}

function dayMonth(iso: string): string {
  return `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]}`;
}

/** "Aug 2026" | "FY 2026-27" | "15 Aug – 14 Oct 2026" | "15 Dec 2025 – 14 Jan 2026". */
export function periodLabel(s: PeriodSelection): string {
  if (s.kind === "month") return `${MONTHS[s.month - 1]} ${s.year}`;
  if (s.kind === "fy") return `FY ${s.fy}-${String(s.fy + 1).slice(-2)}`;
  const fy = s.from.slice(0, 4);
  const ty = s.to.slice(0, 4);
  return fy === ty
    ? `${dayMonth(s.from)} – ${dayMonth(s.to)} ${ty}`
    : `${dayMonth(s.from)} ${fy} – ${dayMonth(s.to)} ${ty}`;
}

export function rangeQuery(r: DateRange): string {
  return `from=${r.from}&to=${r.to}`;
}

const ISO = /^\d{4}-\d{2}-\d{2}$/;
const isInt = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);

/**
 * Parse a stored selection. A legacy `{year, month}` value (no `kind`, written
 * before the period toggle existed) migrates to a month selection. Anything
 * else malformed returns null.
 */
export function parseSelection(raw: unknown): PeriodSelection | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const validMonth = isInt(o.year) && isInt(o.month) && o.month >= 1 && o.month <= 12;
  if (o.kind === undefined || o.kind === "month") {
    return validMonth ? { kind: "month", year: o.year as number, month: o.month as number } : null;
  }
  if (o.kind === "fy") return isInt(o.fy) ? { kind: "fy", fy: o.fy } : null;
  if (o.kind === "custom") {
    const { from, to } = o;
    if (typeof from === "string" && typeof to === "string" && ISO.test(from) && ISO.test(to) && from <= to) {
      return { kind: "custom", from, to };
    }
  }
  return null;
}
