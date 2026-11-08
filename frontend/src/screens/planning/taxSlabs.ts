export type SlabBracket = { ceiling: number; rate: number };
export type SurchargeBand = { threshold: number; rate: number };

export type FyTaxRules = {
  newRegimeSlabs: SlabBracket[];
  oldRegimeSlabs: SlabBracket[];
  newRegimeStdDeduction: number;
  oldRegimeStdDeduction: number;
  newRegimeRebateThreshold: number;
  oldRegimeRebateThreshold: number;
  newRegimeRebateMax: number;
  oldRegimeRebateMax: number;
  pfCapAnnual: number;
  professionalTax: number;
  surchargeBands: SurchargeBand[];
  surchargeMarginalRelief: boolean;
  cessRate: number;
};

export const TAX_RULES: Record<string, FyTaxRules> = {
  "FY2025-26": {
    newRegimeSlabs: [
      { ceiling: 400000, rate: 0 },
      { ceiling: 800000, rate: 0.05 },
      { ceiling: 1200000, rate: 0.1 },
      { ceiling: 1600000, rate: 0.15 },
      { ceiling: 2000000, rate: 0.2 },
      { ceiling: 2400000, rate: 0.25 },
      { ceiling: Infinity, rate: 0.3 },
    ],
    oldRegimeSlabs: [
      { ceiling: 250000, rate: 0 },
      { ceiling: 500000, rate: 0.05 },
      { ceiling: 1000000, rate: 0.2 },
      { ceiling: Infinity, rate: 0.3 },
    ],
    newRegimeStdDeduction: 75000,
    oldRegimeStdDeduction: 50000,
    newRegimeRebateThreshold: 1200000,
    oldRegimeRebateThreshold: 500000,
    // Source: storage/config/tax/2026.yaml (new_regime/old_regime `rebate_max`,
    // `surcharge.marginal_relief`). Duplicates the YAML; tracked separately.
    newRegimeRebateMax: 60000,
    oldRegimeRebateMax: 12500,
    pfCapAnnual: 21600,
    professionalTax: 2400,
    surchargeBands: [
      { threshold: 5000000, rate: 0.1 },
      { threshold: 10000000, rate: 0.15 },
      { threshold: 20000000, rate: 0.25 },
    ],
    surchargeMarginalRelief: true,
    cessRate: 0.04,
  },
};

export const DEFAULT_FY = "FY2025-26";
