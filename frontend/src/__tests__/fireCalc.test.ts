import { computeFireSummary, solveAccumulation, simulateDecumulation } from "@/screens/planning/fireCalc";

test("bucket allocation splits future annual expense into the 5-bucket plan", () => {
  const summary = computeFireSummary({
    currentMonthlyExpense: 50000,
    currentCorpus: 0,
    yearsToRetirement: 0,
    inflationRatePct: 6,
    corpusMultiple: 35,
  });
  // No years to retirement means future expense == current expense.
  expect(summary.futureMonthlyExpense).toBeCloseTo(50000, 5);
  expect(summary.annualExpense).toBeCloseTo(600000, 5);
  expect(summary.totalCorpus).toBeCloseTo(600000 * 35, 5);

  const byKey = Object.fromEntries(summary.buckets.map((b) => [b.key, b.amount]));
  expect(byKey.safety).toBeCloseTo(600000, 5);
  expect(byKey.cash).toBeCloseTo(600000, 5);
  expect(byKey.low).toBeCloseTo(1200000, 5);
  expect(byKey.med).toBeCloseTo(1800000, 5);
  // Growth bucket absorbs whatever remains after the fixed 7-year allocation.
  expect(byKey.growth).toBeCloseTo(summary.totalCorpus - 4200000, 5);

  const totalPct = summary.buckets.reduce((sum, b) => sum + b.pctOfTotal, 0);
  expect(totalPct).toBeCloseTo(100, 5);
});

test("equity split within the growth bucket is 60/30/10", () => {
  const summary = computeFireSummary({
    currentMonthlyExpense: 50000,
    currentCorpus: 0,
    yearsToRetirement: 5,
    inflationRatePct: 6,
    corpusMultiple: 35,
  });
  const growth = summary.bucketAmounts.growth;
  expect(summary.equitySplit.flexi).toBeCloseTo(growth * 0.6, 5);
  expect(summary.equitySplit.mid).toBeCloseTo(growth * 0.3, 5);
  expect(summary.equitySplit.small).toBeCloseTo(growth * 0.1, 5);
});

test("accumulation flat SIP grows current corpus plus contributions to exactly the target corpus", () => {
  const totalCorpus = 1000000;
  const result = solveAccumulation({
    totalCorpus,
    currentCorpus: 0,
    yearsToRetirement: 10,
    expectedRoiPct: 12,
    stepUpSipPct: 0,
  });
  const last = result.rows[result.rows.length - 1];
  expect(last.closingBalance).toBeCloseTo(totalCorpus, 0);
  // With no step-up, the flat SIP formula should match the solved step-up SIP.
  expect(result.stepUpSip).toBeCloseTo(result.flatSip, 0);
});

test("accumulation step-up search converges on a SIP that reaches the target corpus", () => {
  const totalCorpus = 5000000;
  const result = solveAccumulation({
    totalCorpus,
    currentCorpus: 200000,
    yearsToRetirement: 15,
    expectedRoiPct: 10,
    stepUpSipPct: 10,
  });
  const last = result.rows[result.rows.length - 1];
  expect(last.closingBalance).toBeCloseTo(totalCorpus, -1);
});

test("decumulation refills buckets top-down and reports a survival buffer when funds last", () => {
  const result = simulateDecumulation({
    buckets: { safety: 600000, cash: 600000, low: 1200000, med: 1800000, growth: 20000000 },
    futureMonthlyExpense: 50000,
    inflationRetirementPct: 6,
    simYears: 30,
  });
  expect(result.ranOutMonth).toBeNull();
  expect(result.finalBalance).toBeGreaterThan(0);
  expect(result.bufferYears).toBeGreaterThan(0);
  expect(result.rows).toHaveLength(30 * 12);
});

test("decumulation reports the depletion month when the corpus is too small", () => {
  const result = simulateDecumulation({
    buckets: { safety: 50000, cash: 50000, low: 0, med: 0, growth: 0 },
    futureMonthlyExpense: 50000,
    inflationRetirementPct: 6,
    simYears: 5,
  });
  expect(result.ranOutMonth).not.toBeNull();
  expect(result.finalBalance).toBe(0);
});
