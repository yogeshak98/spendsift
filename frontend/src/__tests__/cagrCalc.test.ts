import { calcCagr } from "@/screens/planning/cagrCalc";

test("computes the annualized growth rate between two values", () => {
  expect(calcCagr({ startValue: 10000, endValue: 20000, years: 5 })).toBeCloseTo(0.148698, 5);
});

test("returns 0 when the start value is 0", () => {
  expect(calcCagr({ startValue: 0, endValue: 20000, years: 5 })).toBe(0);
});

test("returns 0 when the period is 0 years", () => {
  expect(calcCagr({ startValue: 10000, endValue: 20000, years: 0 })).toBe(0);
});
