import type { Resp } from "./types";

type Summary = Resp["summary"];

/** A Chapter VI-A section key as it is written in the Act: `80ccd_1b` ->
 * `80CCD(1B)`. The form and the deductions table share it so the same section
 * is not spelled two ways on two screens. */
export const sectionLabel = (key: string): string => {
  const [head, ...rest] = key.split("_");
  const base = head.toUpperCase();
  return rest.length > 0 ? `${base}(${rest.join(" ").toUpperCase()})` : base;
};

export const KIND_LABELS: Record<string, string> = {
  salary: "Salary",
  interest: "Interest",
  capital_gain_ltcg: "Capital gains (LTCG)",
  capital_gain_stcg: "Capital gains (STCG)",
  capital_gain_crypto: "Capital gains (crypto)",
  capital_gain_debt: "Capital gains (debt)",
  // Not "debt" and not "other": the label says only that the treatment is
  // undecided, which is all the data supports.
  capital_gain_unclassified: "Capital gains (treatment undecided)",
  exempt_gratuity: "Gratuity",
  exempt_leave_encashment: "Leave encashment",
  refund: "Refunds",
  transfers_from_people: "Transfers from people",
  // "Not identified", not the schema's "unclassified": the user is reading a
  // tax screen, not a kind enum -- and the row means only that no prefix rule
  // names the account, which is not a claim that it is untaxable.
  unclassified_income: "Not identified",
  prepaid_tax: "Prepaid tax (TDS / advance)",
  tax_paid: "Tax paid directly",
};

/** How much the old/new regime comparison can honestly be said to show.
 *
 * The engine applies HRA s.10(13A) + LTA s.10(5) under the old regime only
 * (s.115BAC withdraws them), so without a declared `exempt_hra_lta` the
 * old-regime deductions are a LOWER bound and its tax an UPPER bound. The
 * arithmetic cannot fix that; only withholding the verdict can.
 *
 *  - `not-applicable` — no salary at all, declared or posted: the regime
 *    question is not about this user. Neither a verdict nor a bound.
 *  - `complete`       — gross salary and exemption both declared: the
 *    recommendation stands as a verdict.
 *  - `exemption-missing` — a gross salary is declared and no exemption figure
 *    is being applied under the old regime. Two causes reach this state, and
 *    the response does not distinguish them: no `exempt_hra_lta` was declared,
 *    or one was and the FY's rules file does not authorise exemptions under
 *    the old regime (`allows_exemptions`), so it was dropped. Both leave the
 *    old regime missing the same term, hence one state: no winner, and the
 *    old-regime figure is an upper bound on tax. They need DIFFERENT
 *    instructions, which is `ComparisonIncompleteNotice`'s problem, not this
 *    function's.
 *  - `no-declaration` — nothing declared, so the salary base is a net-of-TDS
 *    bank credit and BOTH regimes are understated. No winner and no bound
 *    either: a bound would contradict `SalaryIsNetNotice`, which is already on
 *    screen saying the whole estimate is too low.
 */
export type ComparisonState =
  | "not-applicable"
  | "complete"
  | "exemption-missing"
  | "no-declaration";

export function comparisonState(summary: Summary): ComparisonState {
  // `not-applicable` is the one incomplete state that GRANTS the challan
  // instruction, so it is reached only by an EXPLICIT `false` -- never by a
  // falsy value. Both fields are typed required, but a field dropped at runtime
  // arrives as `undefined`, and that must degrade towards saying less, not
  // towards printing a payment instruction. An absent flag therefore reads as
  // `no-declaration`, the state that claims the least.
  if (summary.comparison_applicable === false) return "not-applicable";
  if (summary.comparison_applicable !== true) return "no-declaration";
  if (summary.comparison_complete === true) return "complete";
  // Which of the two incomplete states this is turns on whether a gross salary
  // was declared at all. `provenance` is optional, and an absent one cannot
  // evidence a declaration, so it reads as "nothing declared" -- the state that
  // claims the least.
  return summary.provenance?.gross_salary.source === "declared"
    ? "exemption-missing"
    : "no-declaration";
}

/** A regime may be named the winner only when the comparison is complete. */
export const verdictAllowed = (state: ComparisonState): boolean => state === "complete";

/** "Pay this by challan" may only be said about a balance that is not known to
 * be biased.
 *
 * Three ways a balance is not biased:
 *  - `complete`       — both regimes were computed from the same information.
 *  - `not-applicable` — no salary at all, so no withdrawn-allowance term is
 *    missing from either regime.
 *  - the user has DECLARED they file under the new regime AND the balance was
 *    computed against the new regime. s.115BAC grants no HRA/LTA exemption, so
 *    an undeclared `exempt_hra_lta` cannot move a new-regime figure by a rupee:
 *    the amount is exact and withholding it costs the user a correct number.
 *    This is the product's primary user, so it is the common case, not an edge.
 *
 * Both halves of that third condition are required:
 *  - `balance_due_regime === "new"` alone is NOT enough. With no declared
 *    regime the balance is the cheaper regime's, and "cheaper" came out of the
 *    very comparison that is incomplete -- the old regime may genuinely win
 *    once the exemption is declared, in which case the figure on screen is the
 *    wrong regime's answer. Only an explicit declaration makes it safe.
 *  - and the state must be `exemption-missing`, i.e. a gross salary IS
 *    declared. Under `no-declaration` the salary base is a net-of-TDS bank
 *    credit, so the balance is understated whatever the regime: declaring the
 *    regime does not cure a missing gross.
 *
 * Permission is never derived from a falsy `comparison_applicable`: only an
 * explicit `false` reaches `not-applicable` (see `comparisonState`), so a
 * dropped field cannot buy the instruction.
 */
export const challanAllowed = (
  state: ComparisonState,
  declaredRegime: "new" | "old" | null | undefined,
  balanceRegime: "new" | "old" | null | undefined,
): boolean =>
  state === "complete" ||
  state === "not-applicable" ||
  (state === "exemption-missing" && declaredRegime === "new" && balanceRegime === "new");

export const REGIME_LABEL: Record<"new" | "old", string> = {
  new: "new regime",
  old: "old regime",
};
