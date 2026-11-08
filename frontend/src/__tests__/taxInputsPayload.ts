import type { TaxInputsResponse } from "@/screens/tax/types";

/** A `GET /api/tax/inputs` response, shaped exactly as API_CONTRACT.md's
 * `tax_inputs` section documents it.
 *
 * Shared because the declared-inputs form now renders on the tax overview, so
 * every test that mounts a real `TaxOverview` has to answer this endpoint too --
 * and a hand-rolled near-miss (a missing `unknown_keys`, say) would fail as a
 * crash in the form rather than as the test's own subject.
 *
 * Only keys PRESENT in the file appear in `income`/`deductions`: that is the
 * contract, and the whole declared-"0"-versus-absent distinction rests on it.
 */
export function inputsPayload(over: Partial<TaxInputsResponse> = {}): TaxInputsResponse {
  return {
    fy: 2026,
    path: "config/tax/2026-inputs.yaml",
    exists: true,
    revision: "rev-1",
    regime: null,
    income: [],
    deductions: [],
    parse_error: null,
    raw_text: null,
    unknown_keys: [],
    income_fields: ["gross_salary", "tds", "exempt_hra_lta"],
    deduction_sections: ["80c", "80ccd_1b", "80ccd_2", "80d", "80tta"],
    rules_configured: true,
    rules_error: null,
    ...over,
  };
}
