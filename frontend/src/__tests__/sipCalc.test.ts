import { projectSip } from "@/screens/planning/sipCalc";

test("accumulates invested amount linearly when return rate is zero", () => {
  const rows = projectSip({
    monthlyInvestment: 10000,
    returnRatePct: 0,
    years: 1,
    initialInvestment: 0,
    stepUpPct: 0,
  });
  expect(rows).toHaveLength(12);
  expect(rows[0].investedAmount).toBe(10000);
  expect(rows[11].investedAmount).toBe(120000);
  expect(rows[11].totalValue).toBe(120000);
});

test("compounds monthly returns on top of contributions", () => {
  const rows = projectSip({
    monthlyInvestment: 10000,
    returnRatePct: 12,
    years: 1,
    initialInvestment: 0,
    stepUpPct: 0,
  });
  expect(rows[11].investedAmount).toBe(120000);
  expect(rows[11].totalValue).toBeGreaterThan(120000);
  expect(rows[11].wealthGained).toBeCloseTo(rows[11].totalValue - 120000, 5);
});

test("applies the annual step-up to the contribution amount from the 13th month", () => {
  const rows = projectSip({
    monthlyInvestment: 10000,
    returnRatePct: 0,
    years: 2,
    initialInvestment: 0,
    stepUpPct: 10,
  });
  expect(rows[11].sipAmount).toBe(10000);
  expect(rows[12].sipAmount).toBeCloseTo(11000, 5);
});

test("seeds totalValue and investedAmount from an initial lumpsum", () => {
  const rows = projectSip({
    monthlyInvestment: 0,
    returnRatePct: 12,
    years: 1,
    initialInvestment: 50000,
    stepUpPct: 0,
  });
  expect(rows[0].investedAmount).toBe(50000);
  expect(rows[11].totalValue).toBeGreaterThan(50000);
});
