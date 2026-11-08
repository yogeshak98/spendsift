/** Field copy for the form, kept here with the notices that send users to it so
 * a field's label and the sentence pointing at it cannot drift apart.
 *
 * `exempt_hra_lta` is named for what it is, and its hint says it is used WHEN
 * COMPARING against the old regime -- never "old regime only". A new-regime
 * filer who reads "old regime only" skips it, and is then the user who can
 * never get a complete comparison, which is the one thing they wanted the old
 * regime's column for. */
export const INCOME_FIELD_COPY: Record<string, { label: string; hint: string }> = {
  gross_salary: {
    label: "Gross salary",
    hint: "Payslip year-to-date gross, or Form 16 — cumulative for the whole FY. Replaces the ledger's salary credits outright.",
  },
  tds: {
    label: "TDS on salary",
    hint: "Tax already withheld against that salary. Advance tax is always read from the ledger and is never replaced by this figure.",
  },
  exempt_hra_lta: {
    label: "Exempt HRA + LTA",
    hint: "HRA s.10(13A) and LTA s.10(5) together, as exempted on your Form 16. Used when comparing against the old regime — the old regime applies them and the new one does not, so without this figure the two cannot be compared. Enter 0 if you claim neither: absence and 0 are different answers here. A figure you enter is still dropped from the old regime if this FY's tax-rules file does not authorise exemptions — the regime-comparison notice says what to change when that happens. This figure is ignored until you also declare a gross salary above: an allowance paid as part of salary cannot shelter interest income, so with no declared salary there is nothing for it to come out of.",
  },
};

export function incomeFieldCopy(key: string): { label: string; hint: string } {
  return INCOME_FIELD_COPY[key] ?? { label: key, hint: "" };
}
