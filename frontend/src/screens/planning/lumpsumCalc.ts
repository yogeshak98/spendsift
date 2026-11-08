// frontend/src/screens/planning/lumpsumCalc.ts
export type LumpsumInput = { investment: number; returnRatePct: number; years: number };
export type LumpsumYearRow = { year: number; invested: number; value: number };

export function projectLumpsum(input: LumpsumInput): LumpsumYearRow[] {
  const rate = input.returnRatePct / 100;
  const rows: LumpsumYearRow[] = [];
  for (let year = 0; year <= input.years; year++) {
    rows.push({
      year,
      invested: input.investment,
      value: input.investment * Math.pow(1 + rate, year),
    });
  }
  return rows;
}
