import { projectGoals } from "@/screens/planning/goalProjection";

test("funds goals in due-year order when the pool covers them", () => {
  const result = projectGoals({
    poolTotal: 500000,
    investStart: 0,
    raisePct: 0,
    roiPct: 0,
    years: 2,
    goals: [
      { name: "Bike", amount: 300000, dueYear: 0 },
      { name: "Car", amount: 150000, dueYear: 1 },
    ],
  });
  expect(result.goalOutcomes.find((g) => g.name === "Bike")?.fundedYear).toBe(0);
  expect(result.goalOutcomes.find((g) => g.name === "Car")?.fundedYear).toBe(1);
});

test("stops at the first unaffordable goal and reports the shortfall", () => {
  const result = projectGoals({
    poolTotal: 100000,
    investStart: 0,
    raisePct: 0,
    roiPct: 0,
    years: 1,
    goals: [
      { name: "Car", amount: 200000, dueYear: 0 },
      { name: "Cheap goal", amount: 50000, dueYear: 0 },
    ],
  });
  const yearZero = result.rows[0];
  expect(yearZero.events).toEqual([
    { name: "Car", amount: 200000, ok: false, short: 100000 },
  ]);
  expect(result.goalOutcomes.find((g) => g.name === "Cheap goal")?.funded).toBe(false);
});

test("year 0 applies no compounding but still funds due-now goals", () => {
  const result = projectGoals({
    poolTotal: 50000,
    investStart: 10000,
    raisePct: 1,
    roiPct: 1,
    years: 1,
    goals: [{ name: "Now goal", amount: 50000, dueYear: 0 }],
  });
  expect(result.rows[0].investMo).toBe(10000);
  expect(result.rows[0].addedAnnual).toBe(0);
  expect(result.goalOutcomes[0].fundedYear).toBe(0);
});

test("investment compounds annually from year 1 onward", () => {
  const result = projectGoals({
    poolTotal: 0,
    investStart: 10000,
    raisePct: 0.1,
    roiPct: 0,
    years: 2,
    goals: [],
  });
  expect(result.rows[1].investMo).toBeCloseTo(11000, 5);
  expect(result.rows[1].addedAnnual).toBeCloseTo(11000 * 12, 5);
  expect(result.rows[2].investMo).toBeCloseTo(12100, 5);
});

test("ROI compounds the existing pool balance each year", () => {
  const result = projectGoals({
    poolTotal: 100000,
    investStart: 0,
    raisePct: 0,
    roiPct: 0.1,
    years: 2,
    goals: [],
  });
  expect(result.rows[1].poolEnd).toBeCloseTo(110000, 5);
  expect(result.rows[2].poolEnd).toBeCloseTo(121000, 5);
});

test("tracks invested principal separately from ROI returns", () => {
  const result = projectGoals({
    poolTotal: 100000,
    investStart: 1000,
    raisePct: 0,
    roiPct: 0.1,
    years: 2,
    goals: [],
  });
  // Invested = starting pool + contributions made (ROI growth excluded).
  expect(result.totalInvested).toBeCloseTo(100000 + 1000 * 12 * 2, 5);
  // Returns = sum of ROI growth applied on the pool balance each year.
  expect(result.totalReturns).toBeCloseTo(result.endPool - result.totalInvested, 5);
  expect(result.rows[0].investedCum).toBeCloseTo(100000, 5);
  expect(result.rows[0].returnsCum).toBe(0);
});
