export type Regime = {
  /** The declared `exempt_hra_lta` actually applied under this regime: "0"
   *  under a regime whose rules withdraw HRA/LTA (the new one, s.115BAC), and
   *  capped at the declared gross salary. Optional for the same reason
   *  `provenance` is -- see the note on the summary below -- and read with a
   *  `?? "0"` fallback so a dropped field renders a zero line, not `₹NaN`. */
  exempt_allowances?: string;
  total_deductions: string;
  taxable_income: string;
  slab_tax: string;
  rebate: string;
  surcharge: string;
  cess: string;
  capital_gains_tax: string;
  total: string;
};

export type Estimate = {
  new_regime: Regime;
  old_regime: Regime;
  lower_tax_regime: "new" | "old";
  balance_due: string;
  /** The regime `balance_due` was computed against: the regime the user files
   *  under when the inputs file names one, otherwise `lower_tax_regime`. A
   *  balance shown without it looks unconditional. Optional: an absent value
   *  falls back to `lower_tax_regime`, which is what it means. */
  balance_due_regime?: "new" | "old";
} | null;

export type Item = {
  kind: string;
  amount: string;
  source_refs: string[];
  /** How the bucket is treated for tax, and the ONLY thing the Income screen
   *  groups on -- never a kind list this client keeps of its own, or a kind
   *  added in `backend/app/service/tax/report.py` leaves the client behind. `null` means
   *  "not income at all": a tax payment (`prepaid_tax`, `tax_paid`), which has
   *  no place in an income grouping. `unknown` is deliberately NOT
   *  `not_income` -- it means we cannot say whether it should be taxed. See
   *  `API_CONTRACT.md` ss`TaxRow`. */
  treatment: "taxed" | "exempt" | "not_income" | "unknown" | null;
};

export type Deduction = {
  section: string;
  declared: string;
  applied_old: string;
  applied_new: string;
};

/** Which source a figure came from, and what the other source said.
 *  `ignored_postings` counts ledger TRANSACTIONS (distinct txn-ids). */
export type ProvenanceFigure = {
  source: "declared" | "ledger";
  value: string;
  ignored_amount: string;
  ignored_postings: number;
  /** Only meaningful on `tds`: the ledger's advance-tax component, which a
   *  declared TDS never replaces. `value` is the TDS component ALONE, so the
   *  whole prepaid figure is `value + advance_tax`. Optional on this side for
   *  the same reason `provenance` itself is -- see the note below. */
  advance_tax?: string;
  advance_tax_postings?: number;
};

/** A direct-tax payment counted in this FY purely from its posting DATE: it
 *  carries no `tax-fy` metadata saying which year it was paid FOR. Carries no
 *  claim about which year it belongs to, and the notice that renders it makes
 *  none. */
export type UntaggedTaxPayment = {
  date: string;
  amount: string;
  txn_id: string;
};

export type Resp = {
  period: string;
  items: Item[];
  deductions: Deduction[];
  summary: {
    gross_ordinary_income: string;
    capital_gains: { ltcg: string; stcg: string; crypto: string; debt: string; unclassified: string };
    prepaid_tax: string;
    tax_paid_direct: string;
    estimate: Estimate;
    rules_configured: boolean;
    uncovered: string[];
    // Optional on purpose, and the one stance applied everywhere they are read.
    // The backend declares both as REQUIRED response fields, so a dropped field
    // is a loud 500 there -- but on this side an attribution line must never be
    // able to take the tax workspace down, the same rule the loader follows when
    // it refuses to raise over an unusable `income` block. Marking them optional
    // makes that degradation visible to the type system instead of hiding it in
    // scattered `?.`, and makes an unguarded read (TaxPrepaid had one) a compile
    // error rather than a runtime crash.
    provenance?: { gross_salary: ProvenanceFigure; tds: ProvenanceFigure };
    salary_is_net: boolean;
    declared_tds_at_or_above_gross: boolean;
    unrecognised_income_keys?: string[];
    /** The regime the user says they file under, from the inputs file. Absent
     *  and null both mean "not stated"; an unrecognised value in the file
     *  reads as null rather than failing the workspace. */
    declared_regime?: "new" | "old" | null;
    /** True when there is salary income in play at all (salary postings, or a
     *  declared gross salary). False means the regime question is not about
     *  this user, so neither a verdict nor a bound belongs on screen.
     *
     *  These two are REQUIRED, unlike `provenance` above: they do not decorate
     *  a figure, they decide whether a recommendation may be printed as a
     *  verdict at all. An unguarded read must be a compile error, and the
     *  runtime value of a dropped field (`undefined`) degrades towards saying
     *  less: `comparisonState` reaches the challan-granting `not-applicable`
     *  only on an EXPLICIT `false`, because a falsy test there made a dropped
     *  field print a pay-this-amount instruction. */
    comparison_applicable: boolean;
    /** True only when the comparison applies AND both a `gross_salary` and an
     *  `exempt_hra_lta` are declared. When false the old-regime figure stands
     *  on a lower bound on deductions, so it is an UPPER bound on tax, and no
     *  regime may be named the winner. See `comparisonState` in `utils.ts`. */
    comparison_complete: boolean;
    /** Direct-tax payments in this FY with no `tax-fy` tag, so the Prepaid
     *  screen can offer the tag. Optional on this side for the same reason
     *  `provenance` is: a discoverability notice must never be able to take
     *  the workspace down, and an absent field degrades to showing no notice,
     *  which says LESS rather than something wrong. */
    untagged_tax_payments?: UntaggedTaxPayment[];
  };
};

/** One declared figure as `GET /api/tax/inputs` reports it.
 *
 * Only keys PRESENT in the file appear, because absence ("not declared") and a
 * declared `"0"` are different answers and `comparison_complete` depends on
 * telling them apart. A value the backend could not read as an amount comes
 * back `invalid: true` with the file's own text in `raw` and a null `value`,
 * so the form shows what must be fixed instead of a figure it cannot save. */
export type TaxInputField = {
  key: string;
  value: string | null;
  raw: string | null;
  invalid: boolean;
};

/** Response of `GET /api/tax/inputs`, `PATCH /api/tax/inputs` and
 * `POST /api/tax/inputs/replace` -- the form's own endpoint, which answers 200
 * in the states that make the workspace route fail. */
export type TaxInputsResponse = {
  fy: number;
  path: string;
  exists: boolean;
  /** sha256 of the file's bytes, or "absent". Echoed on the next write. */
  revision: string;
  regime: string | null;
  income: TaxInputField[];
  deductions: TaxInputField[];
  /** Set when the file is present but will not parse; `raw_text` carries it
   *  verbatim so the form can repair it through `/replace`. Data, not an
   *  error: the GET is a 200. */
  parse_error: string | null;
  raw_text: string | null;
  /** Top-level or in-block keys nothing reads, so the form can say why a
   *  declaration had no effect. `taxpayer` is never listed. */
  unknown_keys: string[];
  /** The catalogs the form renders its fields from -- never a hardcoded list,
   *  so a section added to the rules file is declarable. */
  income_fields: string[];
  deduction_sections: string[];
  rules_configured: boolean;
  rules_error: string | null;
};
