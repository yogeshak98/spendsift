import { currentFY } from "./useAvailableYears";

export type FYSpan = { from: number; to: number };
export type FYPreset = "current" | "last3" | "last5";
export type FYMode = FYPreset | "custom";

/** Widest window the Income Statement will compare, in financial years. */
export const MAX_SPAN = 5;

const PRESET_WIDTH: Record<FYPreset, number> = { current: 1, last3: 3, last5: 5 };

/** The FY every preset ends on: the current FY when the ledger has it, else its latest. */
export function anchorFY(years: number[]): number | null {
  if (years.length === 0) return null;
  const cf = currentFY();
  return years.includes(cf) ? cf : years[years.length - 1];
}

/** Pull a span inside the ledger's FY range. Presets never fabricate pre-ledger columns. */
export function clampSpan(span: FYSpan, years: number[]): FYSpan {
  const lo = years[0];
  const hi = years[years.length - 1];
  const to = Math.min(Math.max(span.to, lo), hi);
  const from = Math.min(Math.max(span.from, lo), to);
  return { from, to };
}

export function presetSpan(preset: FYPreset, years: number[]): FYSpan | null {
  const anchor = anchorFY(years);
  if (anchor === null) return null;
  const width = PRESET_WIDTH[preset];
  return clampSpan({ from: anchor - width + 1, to: anchor }, years);
}

/**
 * Move one edge of a custom range. The cap pushes the opposite edge rather than
 * rejecting the change, so the control never refuses a click.
 */
export function adjustCustom(
  prev: FYSpan,
  edge: "from" | "to",
  value: number,
  years: number[],
): FYSpan {
  let { from, to } = prev;
  if (edge === "from") {
    from = value;
    if (to < from) to = from;
    if (to - from > MAX_SPAN - 1) to = from + MAX_SPAN - 1;
  } else {
    to = value;
    if (from > to) from = to;
    if (to - from > MAX_SPAN - 1) from = to - MAX_SPAN + 1;
  }
  return clampSpan({ from, to }, years);
}

/** Slide the whole window by `delta` FYs; a shift that would leave the ledger is a no-op. */
export function shiftSpan(span: FYSpan, delta: number, years: number[]): FYSpan {
  if (years.length === 0) return span;
  const lo = years[0];
  const hi = years[years.length - 1];
  if (span.from + delta < lo || span.to + delta > hi) return span;
  return { from: span.from + delta, to: span.to + delta };
}

/** Expand a span into contiguous FYs — an empty interior FY still gets its column. */
export function spanYears(span: FYSpan): number[] {
  const out: number[] = [];
  for (let y = span.from; y <= span.to; y++) out.push(y);
  return out;
}
