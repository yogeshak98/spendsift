export type CagrInput = { startValue: number; endValue: number; years: number };

export function calcCagr(input: CagrInput): number {
  if (input.startValue === 0 || input.years === 0) return 0;
  return Math.pow(input.endValue / input.startValue, 1 / input.years) - 1;
}
