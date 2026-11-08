import { computeSalary } from "@/screens/planning/salaryCalc";
import { TAX_RULES } from "@/screens/planning/taxSlabs";

const rules = TAX_RULES["FY2025-26"];

test("new regime applies the 87A rebate below the threshold", () => {
  const result = computeSalary(
    {
      regime: "new",
      gross: 900000,
      basic: 360000,
      hra: 0,
      metro: true,
      monthlyRent: 0,
      employerNpsPct: 0,
      employeeNps: 0,
      other80c: 0,
      lta: 0,
      books: 0,
      tel: 0,
      ded80d: 0,
    },
    rules,
  );
  expect(result.taxable).toBe(825000);
  expect(result.rebate).toBe(true);
  expect(result.incomeTax).toBe(0);
});

test("new regime computes slab tax, cess, and effective rate above the rebate threshold", () => {
  const result = computeSalary(
    {
      regime: "new",
      gross: 2000000,
      basic: 800000,
      hra: 0,
      metro: true,
      monthlyRent: 0,
      employerNpsPct: 0,
      employeeNps: 0,
      other80c: 0,
      lta: 0,
      books: 0,
      tel: 0,
      ded80d: 0,
    },
    rules,
  );
  expect(result.taxable).toBe(1925000);
  expect(result.baseTax).toBeCloseTo(185000, 5);
  expect(result.incomeTax).toBeCloseTo(192400, 5);
  expect(result.takeHomeAnnual).toBeCloseTo(1783600, 5);
});

test("new regime applies a surcharge above ₹50L taxable income", () => {
  const result = computeSalary(
    {
      regime: "new",
      gross: 6500000,
      basic: 0,
      hra: 0,
      metro: true,
      monthlyRent: 0,
      employerNpsPct: 0,
      employeeNps: 0,
      other80c: 0,
      lta: 0,
      books: 0,
      tel: 0,
      ded80d: 0,
    },
    rules,
  );
  expect(result.taxable).toBe(6425000);
  expect(result.surchargeRatePct).toBe(10);
  expect(result.incomeTax).toBeCloseTo(1724580, 5);
});

test("old regime applies HRA exemption and 80C deduction", () => {
  const result = computeSalary(
    {
      regime: "old",
      gross: 1200000,
      basic: 480000,
      hra: 240000,
      metro: true,
      monthlyRent: 20000,
      employerNpsPct: 0,
      employeeNps: 0,
      other80c: 0,
      lta: 0,
      books: 0,
      tel: 0,
      ded80d: 0,
    },
    rules,
  );
  expect(result.hraEx).toBeCloseTo(192000, 5);
  expect(result.taxable).toBeCloseTo(934000, 5);
  expect(result.rebate).toBe(false);
  expect(result.incomeTax).toBeCloseTo(103272, 5);
});

// ---- marginal relief (mirrors tests/service/tax/test_report.py) -------------------

// No deductions beyond the fixed ones: new taxable = gross - std deduction;
// old taxable = gross - std deduction - professional tax (basic 0 => no PF/80C).
function atTaxable(regime: "new" | "old", taxable: number) {
  const fixed =
    regime === "new"
      ? rules.newRegimeStdDeduction
      : rules.oldRegimeStdDeduction + rules.professionalTax;
  const r = computeSalary(
    {
      regime,
      gross: taxable + fixed,
      basic: 0,
      hra: 0,
      metro: true,
      monthlyRent: 0,
      employerNpsPct: 0,
      employeeNps: 0,
      other80c: 0,
      lta: 0,
      books: 0,
      tel: 0,
      ded80d: 0,
    },
    rules,
  );
  expect(r.taxable).toBe(taxable);
  return r;
}

const STEP = 10000;
const CESS = 1 + rules.cessRate;
const REGIMES = ["new", "old"] as const;

test("tax rises strictly across every surcharge threshold", () => {
  for (const regime of REGIMES) {
    for (const { threshold } of rules.surchargeBands) {
      const below = atTaxable(regime, threshold - STEP).incomeTax;
      const above = atTaxable(regime, threshold + STEP).incomeTax;
      expect(above).toBeGreaterThan(below);
    }
  }
});

test("tax rises strictly across the 87A rebate ceiling", () => {
  const ceilings = { new: rules.newRegimeRebateThreshold, old: rules.oldRegimeRebateThreshold };
  for (const regime of REGIMES) {
    const c = ceilings[regime];
    expect(atTaxable(regime, c + STEP).incomeTax).toBeGreaterThan(atTaxable(regime, c - STEP).incomeTax);
  }
});

// The monotonicity tests above also pass with no relief (the bug is a cliff UP,
// not a dip), so these bound the jump: crossing a threshold must cost no more
// than the income earned over it (plus cess).
test("crossing the 87A ceiling costs at most the income over it", () => {
  const ceilings = { new: rules.newRegimeRebateThreshold, old: rules.oldRegimeRebateThreshold };
  for (const regime of REGIMES) {
    const c = ceilings[regime];
    const jump = atTaxable(regime, c + STEP).incomeTax - atTaxable(regime, c).incomeTax;
    expect(jump).toBeLessThanOrEqual(STEP * CESS + 1e-6);
  }
});

test("crossing every surcharge threshold costs at most the income over it", () => {
  for (const regime of REGIMES) {
    for (const { threshold } of rules.surchargeBands) {
      const jump = atTaxable(regime, threshold + STEP).incomeTax - atTaxable(regime, threshold).incomeTax;
      expect(jump).toBeLessThanOrEqual(STEP * CESS + 1e-6);
    }
  }
});

test("87A marginal relief at 12,10,000: tax is capped at the income over the ceiling", () => {
  // slab tax: 400-800k @5% = 20000; 800k-1.2M @10% = 40000; 1.2M-1.21M @15% = 1500 -> 61500
  // income over the 12L ceiling = 10000, so rebate leaves tax = 10000 (not 61500)
  // cess 4% -> 10400
  const r = atTaxable("new", 1210000);
  expect(r.baseTax).toBeCloseTo(10000, 5);
  expect(r.surcharge).toBe(0);
  expect(r.incomeTax).toBeCloseTo(10400, 5);
});

test("surcharge marginal relief at 1,00,10,000 (new regime)", () => {
  // slab tax at 1 Cr: 20000+40000+60000+80000+100000 = 300000, +30% of 7600000 = 2280000 -> 2580000
  // slab tax at 1.001 Cr = 2580000 + 3000 = 2583000 (no rebate)
  // unrelieved 15% surcharge = 387450
  // baseline at 1 Cr carries the 10% band below: 2580000*1.10 = 2838000, + 10000 over = 2848000
  // relief = 2583000+387450-2848000 = 122450 -> surcharge 265000; pre-cess tax 2848000
  // cess 4% -> 2848000*1.04 = 2961920
  const r = atTaxable("new", 10010000);
  expect(r.baseTax).toBeCloseTo(2583000, 5);
  expect(r.surcharge).toBeCloseTo(265000, 5);
  expect(r.incomeTax).toBeCloseTo(2961920, 5);
});
