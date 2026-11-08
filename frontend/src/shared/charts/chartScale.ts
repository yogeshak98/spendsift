/**
 * Pure helpers for the period charts: compact Indian labels, the nice axis
 * maximum, and the y-scale for Linear | Compressed (√).
 *
 * Kept local to shared/charts on purpose (shared/format/money.ts is read-only
 * for this lane): chart labels use upper-case "K" and no decimals past 10K,
 * as in the approved mock.
 */

export type YScale = "linear" | "sqrt";

/** "412", "4.2K", "36K", "2.7L", "27L", "1.2Cr": compact, sign kept with "−". */
export function compactK(v: number): string {
  const a = Math.abs(v);
  const s = v < 0 ? "−" : "";
  const f = (x: number, big: boolean) => x.toFixed(big ? 0 : 1).replace(/\.0$/, "");
  if (a >= 1e7) return s + f(a / 1e7, a >= 1e8) + "Cr";
  if (a >= 1e5) return s + f(a / 1e5, a >= 1e6) + "L";
  if (a >= 1e3) return s + f(a / 1e3, a >= 1e4) + "K";
  return s + Math.round(a);
}

/** "₹10K", "−₹2.5K". */
export function rupeeK(v: number): string {
  return (v < 0 ? "−" : "") + "₹" + compactK(Math.abs(v));
}

/** The smallest of 1, 2, 2.5, 5, 10 × 10ⁿ at or above `v` (1 for v ≤ 0). */
export function niceMax(v: number): number {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
}

/** The value transform behind each scale (sign-preserving square root). */
export function transform(scale: YScale): (v: number) => number {
  return scale === "sqrt" ? (v) => Math.sign(v) * Math.sqrt(Math.abs(v)) : (v) => v;
}

/** Candidate ticks on the compressed scale: ₹500 → ₹2.5K → ₹10K → ₹25K → ₹50K → ₹1L … */
export const SQRT_STEPS = [
  500, 2_500, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 2_500_000, 5_000_000, 10_000_000,
  25_000_000, 50_000_000, 100_000_000,
];

/**
 * Positive ticks for the compressed scale: 0, then each step up to `hi`,
 * dropping any step whose pixel position `y(t)` sits within `minGap` px of the
 * previous kept tick (y grows downward).
 */
export function sqrtTicks(hi: number, y: (v: number) => number, minGap: number): number[] {
  const ticks = [0];
  let last = y(0);
  for (const t of SQRT_STEPS) {
    if (t > hi) break;
    if (last - y(t) >= minGap) {
      ticks.push(t);
      last = y(t);
    }
  }
  return ticks;
}

/** Money in (green) or out (red). */
export type Direction = "in" | "out";

/** The direction a signed value reads as, given what a positive value means. */
export function directionOf(value: number, positive: Direction): Direction {
  return value > 0 === (positive === "in") ? "in" : "out";
}
