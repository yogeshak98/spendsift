import { type FyTaxRules, type SlabBracket, type SurchargeBand } from "./taxSlabs";

export type SalaryInput = {
  regime: "new" | "old";
  gross: number;
  basic: number;
  hra: number;
  metro: boolean;
  monthlyRent: number;
  employerNpsPct: number;
  employeeNps: number;
  other80c: number;
  lta: number;
  books: number;
  tel: number;
  ded80d: number;
};

export type SalaryResult = {
  basic: number;
  hra: number;
  employerPf: number;
  employerNps: number;
  gross: number;
  employeePf: number;
  profTax: number;
  stdDed: number;
  hraEx: number;
  ltaEx: number;
  booksEx: number;
  telEx: number;
  ded80c: number;
  ded80ccd1b: number;
  ded80d: number;
  employerNpsDed: number;
  taxable: number;
  baseTax: number;
  surcharge: number;
  surchargeRatePct: number;
  cess: number;
  incomeTax: number;
  rebate: boolean;
  takeHomeAnnual: number;
  takeHomeMonthly: number;
  effRate: number;
};

function slabTax(income: number, slabs: SlabBracket[]): number {
  if (income <= 0) return 0;
  let tax = 0;
  let prev = 0;
  for (const { ceiling, rate } of slabs) {
    if (income <= prev) break;
    tax += (Math.min(income, ceiling) - prev) * rate;
    prev = ceiling;
  }
  return tax;
}

function surchargeRateFor(taxable: number, bands: SurchargeBand[]): number {
  let rate = 0;
  for (const band of bands) {
    if (taxable > band.threshold) rate = band.rate;
  }
  return rate;
}

// §87A rebate with marginal relief; mirrors backend/app/service/tax/report.py `rebate`.
function rebateAmount(slabTaxAmt: number, taxable: number, ceiling: number, max: number): number {
  if (taxable <= ceiling) return Math.min(slabTaxAmt, max);
  return Math.max(0, slabTaxAmt - (taxable - ceiling));
}

// Surcharge with marginal relief; mirrors backend/app/service/tax/report.py `surcharge`,
// including the lower band's surcharge in the baseline at the threshold.
function surchargeAmount(
  taxAfterRebate: number,
  taxable: number,
  rules: FyTaxRules,
  slabs: SlabBracket[],
  ceiling: number,
  rebateMax: number,
): number {
  let rate = 0;
  let prevRate = 0;
  let threshold: number | null = null;
  for (const band of rules.surchargeBands) {
    if (taxable > band.threshold) {
      prevRate = rate;
      rate = band.rate;
      threshold = band.threshold;
    }
  }
  if (threshold === null || rate === 0) return 0;
  const base = rate * taxAfterRebate;
  if (!rules.surchargeMarginalRelief) return base;
  const slabAtThreshold = slabTax(threshold, slabs);
  let taxAtThreshold = slabAtThreshold - rebateAmount(slabAtThreshold, threshold, ceiling, rebateMax);
  taxAtThreshold += prevRate * taxAtThreshold;
  const relief = Math.max(0, taxAfterRebate + base - (taxAtThreshold + (taxable - threshold)));
  return Math.max(0, base - relief);
}

export function computeSalary(input: SalaryInput, rules: FyTaxRules): SalaryResult {
  const employerPf = Math.min(0.12 * input.basic, rules.pfCapAnnual);
  const employerNps = (input.employerNpsPct / 100) * input.basic;
  const employeePf = Math.min(0.12 * input.basic, rules.pfCapAnnual);
  const profTax = rules.professionalTax;
  const npsDed = employerNps;

  let stdDed: number;
  let taxable: number;
  let baseTax: number;
  let rebate: boolean;
  let slabs: SlabBracket[];
  let ceiling: number;
  let rebateMax: number;
  let hraEx = 0;
  let ltaEx = 0;
  let booksEx = 0;
  let telEx = 0;
  let ded80c = 0;
  let ded80ccd1b = 0;
  let ded80dVal = 0;

  if (input.regime === "new") {
    stdDed = rules.newRegimeStdDeduction;
    taxable = Math.max(0, input.gross - stdDed - npsDed);
    slabs = rules.newRegimeSlabs;
    ceiling = rules.newRegimeRebateThreshold;
    rebateMax = rules.newRegimeRebateMax;
    baseTax = slabTax(taxable, slabs);
    rebate = taxable <= ceiling;
  } else {
    stdDed = rules.oldRegimeStdDeduction;
    const annualRent = input.monthlyRent * 12;
    const hraRatio = input.metro ? 0.5 : 0.4;
    hraEx =
      annualRent > 0
        ? Math.min(input.hra, hraRatio * input.basic, Math.max(0, annualRent - 0.1 * input.basic))
        : 0;
    ltaEx = input.lta;
    booksEx = input.books;
    telEx = input.tel;
    ded80c = Math.min(employeePf + input.other80c, 150000);
    ded80ccd1b = Math.min(input.employeeNps, 50000);
    ded80dVal = Math.min(input.ded80d, 75000);
    taxable = Math.max(
      0,
      input.gross - stdDed - hraEx - ltaEx - booksEx - telEx - ded80c - ded80ccd1b - ded80dVal - npsDed - profTax,
    );
    slabs = rules.oldRegimeSlabs;
    ceiling = rules.oldRegimeRebateThreshold;
    rebateMax = rules.oldRegimeRebateMax;
    baseTax = slabTax(taxable, slabs);
    rebate = taxable <= ceiling;
  }

  baseTax -= rebateAmount(baseTax, taxable, ceiling, rebateMax);
  const surcharge = surchargeAmount(baseTax, taxable, rules, slabs, ceiling, rebateMax);
  const surchargeRate = surchargeRateFor(taxable, rules.surchargeBands);
  const cess = (baseTax + surcharge) * rules.cessRate;
  const incomeTax = baseTax + surcharge + cess;

  const takeHomeAnnual = input.gross - employeePf - profTax - incomeTax;
  const effRate = input.gross > 0 ? (incomeTax / input.gross) * 100 : 0;

  return {
    basic: input.basic,
    hra: input.hra,
    employerPf,
    employerNps,
    gross: input.gross,
    employeePf,
    profTax,
    stdDed,
    hraEx,
    ltaEx,
    booksEx,
    telEx,
    ded80c,
    ded80ccd1b,
    ded80d: ded80dVal,
    employerNpsDed: npsDed,
    taxable,
    baseTax,
    surcharge,
    surchargeRatePct: surchargeRate * 100,
    cess,
    incomeTax,
    rebate,
    takeHomeAnnual,
    takeHomeMonthly: takeHomeAnnual / 12,
    effRate,
  };
}
