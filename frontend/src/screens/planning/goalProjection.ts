export type Goal = { name: string; amount: number; dueYear: number };

export type GoalOutcome = Goal & {
  funded: boolean;
  fundedYear: number | null;
};

export type YearEvent = { name: string; amount: number; ok: boolean; short?: number };

export type YearRow = {
  year: number;
  investMo: number;
  addedAnnual: number;
  poolStart: number;
  poolEnd: number;
  investedCum: number;
  returnsCum: number;
  events: YearEvent[];
};

export type ProjectionInput = {
  poolTotal: number;
  investStart: number;
  raisePct: number;
  roiPct: number;
  years: number;
  goals: Goal[];
};

export type ProjectionResult = {
  rows: YearRow[];
  goalOutcomes: GoalOutcome[];
  startPool: number;
  endPool: number;
  totalInvested: number;
  totalReturns: number;
};

export function projectGoals(input: ProjectionInput): ProjectionResult {
  const { poolTotal, investStart, raisePct, roiPct, years } = input;
  const sortedGoals = [...input.goals].sort((a, b) => a.dueYear - b.dueYear);
  const pending: GoalOutcome[] = sortedGoals.map((g) => ({ ...g, funded: false, fundedYear: null }));

  let pool = poolTotal;
  const startPool = pool;
  let invest = investStart;
  let investedCum = poolTotal;
  let returnsCum = 0;
  const rows: YearRow[] = [];

  for (let y = 0; y <= years; y++) {
    const poolStart = pool;
    let addedAnnual = 0;
    let investMo = invest;

    if (y > 0) {
      const growth = pool * roiPct;
      pool += growth;
      returnsCum += growth;
      invest = invest * (1 + raisePct);
      investMo = invest;
      addedAnnual = investMo * 12;
      pool += addedAnnual;
      investedCum += addedAnnual;
    }

    const events: YearEvent[] = [];
    for (const g of pending) {
      if (g.funded) continue;
      if (g.dueYear > y) continue;
      if (pool >= g.amount) {
        pool -= g.amount;
        g.funded = true;
        g.fundedYear = y;
        events.push({ name: g.name, amount: g.amount, ok: true });
      } else {
        events.push({ name: g.name, amount: g.amount, ok: false, short: g.amount - pool });
        break;
      }
    }

    rows.push({
      year: y,
      investMo: y === 0 ? investStart : investMo,
      addedAnnual,
      poolStart,
      poolEnd: pool,
      investedCum,
      returnsCum,
      events,
    });
  }

  return { rows, goalOutcomes: pending, startPool, endPool: pool, totalInvested: investedCum, totalReturns: returnsCum };
}
