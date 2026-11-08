/**
 * Day / month buckets of a period series: the date range a bucket covers
 * (clipped to the period), its long title, and whether it is in the future.
 * Keys are `YYYY-MM-DD` (day) or `YYYY-MM` (month), as the overview series use.
 */
import type { DateRange } from "@/shared/period/periodRange";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n: number) => String(n).padStart(2, "0");

export const monthName = (m: number) => MONTHS[m - 1];

/** The inclusive range a bucket covers, clipped to [from, to]. */
export function bucketRange(key: string, from: string, to: string): DateRange {
  if (key.length === 10) return { from: key, to: key };
  const y = Number(key.slice(0, 4));
  const m = Number(key.slice(5, 7));
  const first = `${key}-01`;
  const last = `${key}-${pad(new Date(y, m, 0).getDate())}`;
  return { from: first < from ? from : first, to: last > to ? to : last };
}

/** "3 Aug 2026" for a day, "Aug 2026" for a month. */
export function bucketTitle(key: string): string {
  const y = key.slice(0, 4);
  const m = monthName(Number(key.slice(5, 7)));
  return key.length === 10 ? `${Number(key.slice(8, 10))} ${m} ${y}` : `${m} ${y}`;
}

/** Local today as `YYYY-MM-DD`. */
export function todayKey(d = new Date()): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** A bucket strictly after today (a month bucket: after today's month). */
export function isFuture(key: string, today: string): boolean {
  return key > today.slice(0, key.length);
}

/** (year, month) pairs from `from`'s month through `to`'s month, chronological. */
export function monthsBetween(from: string, to: string): { year: number; month: number }[] {
  const out: { year: number; month: number }[] = [];
  let y = Number(from.slice(0, 4));
  let m = Number(from.slice(5, 7));
  const ey = Number(to.slice(0, 4));
  const em = Number(to.slice(5, 7));
  while (y < ey || (y === ey && m <= em)) {
    out.push({ year: y, month: m });
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  return out;
}

export const dayKey = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`;
