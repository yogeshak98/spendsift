export type SipInput = {
  monthlyInvestment: number;
  returnRatePct: number;
  years: number;
  initialInvestment: number;
  stepUpPct: number;
};

export type SipMonthRow = {
  month: number;
  sipAmount: number;
  investedAmount: number;
  wealthGained: number;
  totalValue: number;
};

export function projectSip(input: SipInput): SipMonthRow[] {
  const months = input.years * 12;
  const monthlyRate = input.returnRatePct / 100 / 12;
  const rows: SipMonthRow[] = [];

  let currentInvestment = input.monthlyInvestment;
  let totalInvested = input.initialInvestment;
  let currentValue = input.initialInvestment;

  for (let month = 1; month <= months; month++) {
    currentValue = currentValue * (1 + monthlyRate);
    currentValue += currentInvestment;
    totalInvested += currentInvestment;

    rows.push({
      month,
      sipAmount: currentInvestment,
      investedAmount: totalInvested,
      wealthGained: currentValue - totalInvested,
      totalValue: currentValue,
    });

    if (month % 12 === 0) {
      currentInvestment = currentInvestment * (1 + input.stepUpPct / 100);
    }
  }

  return rows;
}
