/**
 * The one place rupee amounts become text. Indian (lakh/crore) grouping
 * everywhere; a negative amount always renders as "-₹…" (sign before the
 * symbol); null/undefined/non-finite input renders as DASH.
 *
 * Variants by intent:
 *   inrWhole   ₹16,243          whole rupees, rounded
 *   inr2       ₹16,243.42       two decimals (columns that must align)
 *   inrExact   ₹13,247.63       backend Decimal string, no float round-trip;
 *                               more than 2dp rounds half-up to the paisa
 *   inrUnit    ₹98.123456       backend Decimal string, digits untouched
 *                               (per-unit prices: NAV, average cost)
 *   inrCompact ₹1.23Cr ₹4.56L   2-decimal Cr/L, whole rupees below 1L
 *   inrAbbr    ₹2.3L ₹24k       1-decimal chart-label abbreviation
 *   inrLakh    ₹12.50L          always lakhs, 2 decimals
 *   inrSigned  +₹500 / −₹500    direction-bearing (U+2212 minus), ₹0 for zero
 *   groupIN    16,243           grouped digits, no symbol
 */

export const DASH = "—";

const LOCALE = "en-IN";

type Amount = number | string | null | undefined;

function toNum(v: Amount): number | null {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Grouped digits with no symbol. `round` -> whole numbers; otherwise the locale default (up to 3 decimals). */
export function groupIN(v: Amount, opts: { round?: boolean } = {}): string {
  const n = toNum(v);
  if (n === null) return DASH;
  return (opts.round ? Math.round(n) : n).toLocaleString(LOCALE);
}

/** Whole rupees, rounded: 16243.422 -> "₹16,243"; -0.6415 -> "-₹1". */
export function inrWhole(v: Amount): string {
  const n = toNum(v);
  if (n === null) return DASH;
  const r = Math.round(n);
  return `${r < 0 ? "-" : ""}₹${Math.abs(r).toLocaleString(LOCALE)}`;
}

/** Two decimals, always: 1234.5 -> "₹1,234.50". */
export function inr2(v: Amount): string {
  const n = toNum(v);
  if (n === null) return DASH;
  const body = Math.abs(n).toLocaleString(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${n < 0 ? "-" : ""}₹${body}`;
}

/**
 * String-faithful: Indian grouping on the integer part and decimal-string
 * arithmetic on the fraction (no Number round-trip), so "13247.63" ->
 * "₹13,247.63" and "4999.999999603336" -> "₹5,000.00". Strings at most 2dp
 * pass through digit-for-digit; anything longer is rounded half-up (away
 * from zero) to the paisa, because a main-ledger amount can carry more than
 * 2dp (an opening mutual-fund lot is booked at an unrounded cost). Per-unit
 * prices (NAV, average cost) legitimately carry more precision: use inrUnit.
 */
export function inrExact(s: string | null | undefined): string {
  if (s === null || s === undefined) return DASH;
  return inrUnit(roundTo2dp(s));
}

/** As inrExact, but keeps every fractional digit given (per-unit prices). */
export function inrUnit(s: string | null | undefined): string {
  if (s === null || s === undefined) return DASH;
  const neg = s.startsWith("-");
  const abs = neg ? s.slice(1) : s;
  const [intPart, fracPart] = abs.split(".");
  const grouped = Number(intPart).toLocaleString(LOCALE); // intPart is pure digits -> no rounding
  const body = fracPart ? `${grouped}.${fracPart}` : grouped;
  return `${neg ? "-" : ""}₹${body}`;
}

// Half-up (away from zero) rounding of a plain decimal string to 2dp; strings
// already at most 2dp, and anything that is not a plain decimal, pass through.
function roundTo2dp(s: string): string {
  const m = /^(-?)(\d+)\.(\d{3,})$/.exec(s);
  if (!m) return s;
  const [, sign, intPart, frac] = m;
  const kept = BigInt(intPart + frac.slice(0, 2));
  const up = frac.charCodeAt(2) >= 53 ? 1n : 0n; // third digit >= "5"
  const total = (kept + up).toString().padStart(3, "0");
  const out = `${total.slice(0, -2)}.${total.slice(-2)}`;
  return out === "0.00" ? out : `${sign}${out}`;
}

/** Compact: ₹1.23Cr / ₹4.56L / ₹7,890. Pass Math.abs(n) for a magnitude-only figure. */
export function inrCompact(v: Amount): string {
  const n = toNum(v);
  if (n === null) return DASH;
  const a = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)}Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)}L`;
  return inrWhole(n);
}

/** Always in lakhs, 2 decimals: 1250000 -> "₹12.50L". */
export function inrLakh(v: Amount): string {
  const n = toNum(v);
  if (n === null) return DASH;
  return `${n < 0 ? "-" : ""}₹${Math.abs(n / 1e5).toFixed(2)}L`;
}

/** Direction-bearing amount: "+₹500" / "−₹500" (U+2212); zero is "₹0". Locale-default decimals. */
export function inrSigned(v: Amount): string {
  const n = toNum(v);
  if (n === null) return DASH;
  if (n === 0) return "₹0";
  return `${n > 0 ? "+" : "−"}₹${Math.abs(n).toLocaleString(LOCALE)}`;
}

/**
 * inrAbbr — compact Indian-style rupee abbreviation:
 *   ≥ 1,00,00,000 (1 Cr)  → "₹x.xCr"
 *   ≥ 1,00,000   (1 L)    → "₹x.xL"
 *   ≥ 1,000      (1 k)    → "₹x.xk"
 *   < 1,000               → "₹900"  (raw integer)
 *
 * Decimal policy is unified across every tier: one decimal place is shown,
 * but a trailing ".0" is suppressed when the value sits naturally inside its
 * tier. So 230000 → "₹2.3L" keeps its non-zero decimal while 100000 → "₹1L",
 * 24000 → "₹24k" and 10000000 → "₹1Cr" drop it.
 *
 * The tier is chosen from the ROUNDED mantissa, so a value just below a tier
 * boundary promotes into the next tier instead of printing a malformed
 * abbreviation: 99999 → "₹1.0L" (not "₹100.0k") and 9999999 → "₹1.0Cr"
 * (not "₹100.0L"). A promoted value keeps the ".0" to signal it is an
 * approximation that rounded up across the boundary.
 *
 * Non-finite input (NaN/Infinity) returns "₹0".
 */
export function inrAbbr(n: number): string {
  if (!Number.isFinite(n)) return "₹0";
  const a = Math.abs(n);
  const sign = n < 0 ? "-" : "";

  // round a mantissa to one decimal place
  const round1 = (m: number) => Math.round(m * 10) / 10;
  // suppress a trailing ".0" for values that sit naturally inside their tier
  const trim = (r: number) => (r % 1 === 0 ? `${r}` : r.toFixed(1));

  // Crore tier — naturally ≥ 1Cr, or promoted up from the lakh tier when the
  // rounded lakh mantissa reaches 100.
  if (a >= 1e7) return `${sign}₹${trim(round1(a / 1e7))}Cr`;
  if (round1(a / 1e5) >= 100) return `${sign}₹${round1(a / 1e7).toFixed(1)}Cr`;

  // Lakh tier — naturally ≥ 1L, or promoted up from the thousand tier when the
  // rounded thousand mantissa reaches 100.
  if (a >= 1e5) return `${sign}₹${trim(round1(a / 1e5))}L`;
  if (round1(a / 1e3) >= 100) return `${sign}₹${round1(a / 1e5).toFixed(1)}L`;

  // Thousand tier.
  if (a >= 1e3) return `${sign}₹${trim(round1(a / 1e3))}k`;

  return `${sign}₹${Math.round(a)}`;
}
