// Ported from legacy/spendsift/fire_view.py (5-Bucket FIRE Strategy Calculator).

export type FireSummaryInput = {
  currentMonthlyExpense: number;
  currentCorpus: number;
  yearsToRetirement: number;
  inflationRatePct: number;
  corpusMultiple: number;
};

export type BucketKey = "safety" | "cash" | "low" | "med" | "growth";

export type BucketAllocation = {
  key: BucketKey;
  label: string;
  instrument: string;
  logic: string;
  amount: number;
  pctOfTotal: number;
};

export type FireSummary = {
  futureMonthlyExpense: number;
  annualExpense: number;
  totalCorpus: number;
  buckets: BucketAllocation[];
  bucketAmounts: Record<BucketKey, number>;
  equitySplit: { flexi: number; mid: number; small: number };
  safetyYears: number;
};

export function computeFireSummary(input: FireSummaryInput): FireSummary {
  const futureMonthlyExpense =
    input.currentMonthlyExpense * (1 + input.inflationRatePct / 100) ** input.yearsToRetirement;
  const annualExpense = futureMonthlyExpense * 12;
  const totalCorpus = annualExpense * input.corpusMultiple;

  const safety = annualExpense * 1;
  const cash = annualExpense * 1;
  const low = annualExpense * 2;
  const med = annualExpense * 3;
  const fixedAllocation = safety + cash + low + med;
  const growth = totalCorpus - fixedAllocation;

  const pct = (amount: number) => (totalCorpus > 0 ? (amount / totalCorpus) * 100 : 0);

  const buckets: BucketAllocation[] = [
    { key: "safety", label: "1. Safety Net", instrument: "Arbitrage Funds", logic: "1 Yr (Do Not Touch)", amount: safety, pctOfTotal: pct(safety) },
    { key: "cash", label: "2. Cash Flow", instrument: "Savings / Liquid", logic: "1 Yr (Expenses)", amount: cash, pctOfTotal: pct(cash) },
    { key: "low", label: "3. Low Risk", instrument: "Arb / Debt Funds", logic: "2 Yrs Protection", amount: low, pctOfTotal: pct(low) },
    { key: "med", label: "4. Med Risk", instrument: "Equity Savings", logic: "3 Yrs Protection", amount: med, pctOfTotal: pct(med) },
    { key: "growth", label: "5. Growth", instrument: "Pure Equity", logic: "The Rest", amount: growth, pctOfTotal: pct(growth) },
  ];

  const safetyYears = annualExpense > 0 ? fixedAllocation / annualExpense - 1 : 0;

  return {
    futureMonthlyExpense,
    annualExpense,
    totalCorpus,
    buckets,
    bucketAmounts: { safety, cash, low, med, growth },
    equitySplit: { flexi: growth * 0.6, mid: growth * 0.3, small: growth * 0.1 },
    safetyYears,
  };
}

export type AccumulationInput = {
  totalCorpus: number;
  currentCorpus: number;
  yearsToRetirement: number;
  expectedRoiPct: number;
  stepUpSipPct: number;
};

export type AccumulationMonthRow = {
  month: number;
  openingBalance: number;
  sipInvestment: number;
  returnsEarned: number;
  closingBalance: number;
  targetAchievedPct: number;
};

export type AccumulationResult = {
  flatSip: number;
  stepUpSip: number;
  rows: AccumulationMonthRow[];
};

function finalCorpusFor(startSip: number, stepUpPct: number, roiPct: number, months: number, initialCorpus: number): number {
  const monthlyRate = roiPct / 12 / 100;
  let corpus = initialCorpus;
  let sip = startSip;
  for (let m = 1; m <= months; m++) {
    corpus = corpus * (1 + monthlyRate) + sip;
    if (m % 12 === 0) sip = sip * (1 + stepUpPct / 100);
  }
  return corpus;
}

export function solveAccumulation(input: AccumulationInput): AccumulationResult {
  const months = Math.round(input.yearsToRetirement * 12);
  const r = input.expectedRoiPct / 12 / 100;
  const gap = input.totalCorpus - input.currentCorpus * (1 + input.expectedRoiPct / 100) ** input.yearsToRetirement;

  const flatSip = months <= 0 ? 0 : r > 0 ? (gap * r) / ((1 + r) ** months - 1) : gap / months;

  let low = 0;
  let high = Math.max(input.totalCorpus, 0);
  for (let i = 0; i < 100; i++) {
    const mid = (low + high) / 2;
    const result = finalCorpusFor(mid, input.stepUpSipPct, input.expectedRoiPct, months, input.currentCorpus);
    if (result < input.totalCorpus) low = mid;
    else high = mid;
  }
  const stepUpSip = high;

  const rows: AccumulationMonthRow[] = [];
  let corpus = input.currentCorpus;
  const monthlyRate = input.expectedRoiPct / 12 / 100;
  let sip = Math.max(0, stepUpSip);
  for (let m = 1; m <= months; m++) {
    const opening = corpus;
    const returns = opening * monthlyRate;
    const closing = opening + returns + sip;
    rows.push({
      month: m,
      openingBalance: opening,
      sipInvestment: sip,
      returnsEarned: returns,
      closingBalance: closing,
      targetAchievedPct: input.totalCorpus > 0 ? (closing / input.totalCorpus) * 100 : 0,
    });
    corpus = closing;
    if (m % 12 === 0) sip = sip * (1 + input.stepUpSipPct / 100);
  }

  return { flatSip: Math.max(0, flatSip), stepUpSip: Math.max(0, stepUpSip), rows };
}

export type DecumulationInput = {
  buckets: Record<BucketKey, number>;
  futureMonthlyExpense: number;
  inflationRetirementPct: number;
  simYears: number;
};

export type DecumulationMonthRow = {
  month: number;
  monthlyExpense: number;
  b1: number;
  b2: number;
  b3: number;
  b4: number;
  b5: number;
  totalPortfolio: number;
  refillNote: string;
};

export type DecumulationResult = {
  rows: DecumulationMonthRow[];
  ranOutMonth: number | null;
  finalBalance: number;
  bufferYears: number;
};

const BUCKET_ANNUAL_RETURNS = { b1: 0.06, b2: 0.05, b3: 0.07, b4: 0.09, b5: 0.12 };

export function simulateDecumulation(input: DecumulationInput): DecumulationResult {
  let b1 = input.buckets.safety;
  let b2 = input.buckets.cash;
  let b3 = input.buckets.low;
  let b4 = input.buckets.med;
  let b5 = input.buckets.growth;

  const r1 = BUCKET_ANNUAL_RETURNS.b1 / 12;
  const r2 = BUCKET_ANNUAL_RETURNS.b2 / 12;
  const r3 = BUCKET_ANNUAL_RETURNS.b3 / 12;
  const r4 = BUCKET_ANNUAL_RETURNS.b4 / 12;
  const r5 = BUCKET_ANNUAL_RETURNS.b5 / 12;

  const rows: DecumulationMonthRow[] = [];
  let drawdown = input.futureMonthlyExpense;
  let ranOutMonth: number | null = null;

  const months = Math.round(input.simYears * 12);
  for (let m = 1; m <= months; m++) {
    const notes: string[] = [];

    if ((m - 1) % 12 === 0 && m > 1) {
      const annual = drawdown * 12;
      const targetB1 = annual;
      const targetB2 = annual;
      const targetB3 = 2 * annual;
      const targetB4 = 3 * annual;

      if (b1 < targetB1) {
        const moved = Math.min(b2, targetB1 - b1);
        b2 -= moved;
        b1 += moved;
        if (moved > 0) notes.push("B2→B1");
      }
      if (b2 < targetB2) {
        const moved = Math.min(b3, targetB2 - b2);
        b3 -= moved;
        b2 += moved;
        if (moved > 0) notes.push("B3→B2");
      }
      if (b3 < targetB3) {
        const moved = Math.min(b4, targetB3 - b3);
        b4 -= moved;
        b3 += moved;
        if (moved > 0) notes.push("B4→B3");
      }
      if (b4 < targetB4) {
        const moved = Math.min(b5, targetB4 - b4);
        b5 -= moved;
        b4 += moved;
        if (moved > 0) notes.push("B5→B4");
      }
    }

    b1 *= 1 + r1;
    b2 *= 1 + r2;
    b3 *= 1 + r3;
    b4 *= 1 + r4;
    b5 *= 1 + r5;

    let needed = drawdown;
    const take = (bucket: number) => {
      const used = Math.min(bucket, needed);
      needed -= used;
      return bucket - used;
    };
    b1 = take(b1);
    if (needed > 0) b2 = take(b2);
    if (needed > 0) b3 = take(b3);
    if (needed > 0) b4 = take(b4);
    if (needed > 0) b5 = take(b5);

    const totalPortfolio = b1 + b2 + b3 + b4 + b5;
    if (ranOutMonth === null && totalPortfolio <= 0) ranOutMonth = m;

    rows.push({
      month: m,
      monthlyExpense: drawdown,
      b1,
      b2,
      b3,
      b4,
      b5,
      totalPortfolio,
      refillNote: notes.join(", "),
    });

    if (m % 12 === 0) drawdown *= 1 + input.inflationRetirementPct / 100;
  }

  const finalBalance = rows.length > 0 ? rows[rows.length - 1].totalPortfolio : 0;
  const finalAnnualExpense = drawdown * 12;
  const bufferYears = ranOutMonth === null && finalAnnualExpense > 0 ? finalBalance / finalAnnualExpense : 0;

  return { rows, ranOutMonth, finalBalance, bufferYears };
}
