// frontend/src/__tests__/lumpsumCalc.test.ts
import { projectLumpsum } from "@/screens/planning/lumpsumCalc";

test("returns one row per year from 0 through the target year, invested amount held flat", () => {
  const rows = projectLumpsum({ investment: 100000, returnRatePct: 12, years: 10 });
  expect(rows).toHaveLength(11);
  expect(rows[0].year).toBe(0);
  expect(rows[0].value).toBe(100000);
  expect(rows.every((r) => r.invested === 100000)).toBe(true);
});

test("compounds the investment by the annual return rate", () => {
  const rows = projectLumpsum({ investment: 100000, returnRatePct: 12, years: 10 });
  expect(rows[10].value).toBeCloseTo(100000 * Math.pow(1.12, 10), 5);
});

test("zero return rate leaves the value unchanged across years", () => {
  const rows = projectLumpsum({ investment: 50000, returnRatePct: 0, years: 5 });
  expect(rows.every((r) => r.value === 50000)).toBe(true);
});
