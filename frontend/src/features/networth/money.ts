// Gain/percent helpers shared by the asset screens. Rupee text itself comes
// from @/shared/format/money.

import { DASH, inrExact } from "@/shared/format/money";

export type Tone = "green" | "red" | "dim";

export function pct(s: string | null): string {
  return s === null ? DASH : `${s}%`;
}

// Signed ₹ with a ▲/▼ marker; null/0 stays neutral.
// Passes the ABSOLUTE value to inr() to avoid a double-negative in the loss case.
export function gain(s: string): { text: string; tone: Tone } {
  const n = Number(s);
  const abs = s.startsWith("-") ? s.slice(1) : s;
  if (n > 0) return { text: `▲ ${inrExact(abs)}`, tone: "green" };
  if (n < 0) return { text: `▼ ${inrExact(abs)}`, tone: "red" };
  return { text: inrExact(abs), tone: "dim" };
}

// Gain as a percentage of cost, derived on the client because the equity
// read-model does not return one.
//
// Returns null for a zero (or unparseable) cost basis rather than Infinity or
// NaN: a bonus-only holding has a real gain but no meaningful percentage, and
// showing "∞%" next to a rupee value would read as a bug in the numbers.
// Number() is acceptable here only because the result is a display percentage;
// the rupee amounts it derives from are never round-tripped through it.
export function gainPct(gainStr: string, costStr: string): string | null {
  const cost = Number(costStr);
  const g = Number(gainStr);
  if (!Number.isFinite(cost) || !Number.isFinite(g) || cost === 0) return null;
  const sign = g >= 0 ? "+" : "";
  return `${sign}${((g / cost) * 100).toFixed(2)}%`;
}
