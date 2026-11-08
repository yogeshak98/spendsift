import { useState, type JSX } from "react";
import { useReport } from "@/shared/api/useReport";
import { currentFY } from "@/shared/period/useAvailableYears";
import { FYSelector } from "@/shared/period/FYSelector";
import { parseCodedError } from "@/shared/api/codedError";
import type { Resp } from "./types";
import { TaxContext } from "./taxContext";
import "./tax.css";

/** The coded errors a user can fix FROM the declared-inputs form: a declared
 * value the engine cannot read as a number (E1051 income, E1055 deductions) and
 * an inputs file that will not parse at all (E1059). For these the error is
 * rendered ABOVE the sub-screen instead of in place of it, because the form on
 * the overview screen is the repair tool -- replacing the children with the
 * error hides the one control that ends the error. Every other coded error
 * (a broken rules file, a read failure) keeps the replace behaviour: nothing on
 * the screen below can fix it. */
const FORM_FIXABLE_CODES = new Set(["E1051", "E1055", "E1059"]);

/** The workspace could not be read.
 *
 * E1051 (a declared income value that is not a readable number -- Indian digit
 * grouping, a blank, a currency symbol) makes the WHOLE workspace a coded 422,
 * which is the loader's deliberate choice. Dropping `useReport`'s error left
 * every tax screen on "Loading…" for ever, which is the worst available
 * outcome for a hand-edit the backend diagnosed precisely: the envelope names
 * the key, the value, the file and what to type instead.
 */
function TaxLoadError({ error }: { error: string }): JSX.Element {
  const coded = parseCodedError(error);
  return (
    <div className="callout bad">
      <span>
        Could not load the tax workspace.{" "}
        {coded ? (
          <>
            {coded.description} {coded.resolution}
            {coded.code !== null && (
              <>
                {" "}
                <span className="tax-key">{coded.code}</span>
              </>
            )}
          </>
        ) : (
          <>
            The journal is unchanged — this is a read failure, not a data loss.
            Check that the backend is running, then reload.
          </>
        )}
      </span>
    </div>
  );
}

export function TaxLayout({ children }: { children: React.ReactNode }): JSX.Element {
  const [fy, setFy] = useState<number>(currentFY);
  // `useReport` has no loading flag: loading is `data === null && error === null`.
  // The FY selector stays mounted on the error path on purpose -- a malformed
  // declaration is per-FY, so switching year is the one control that can get
  // the user out of it without leaving the screen.
  const { data, error } = useReport<Resp>(`/api/reports/tax/workspace?fy=${fy}`);
  const coded = error === null ? null : parseCodedError(error);
  const fixableHere = coded !== null && coded.code !== null && FORM_FIXABLE_CODES.has(coded.code);

  // Sub-navigation lives in the main nav rail (consistent with every other
  // domain); this layout only owns the FY scope + shared report context.
  return (
    <TaxContext.Provider value={{ fy, setFy, data, error }}>
      <div style={{ display: "flex", alignItems: "center", padding: "0 1.6923rem 0.9231rem", gap: "0.9231rem" }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: "0.8462rem", color: "var(--text3)" }}>FY</span>
        <FYSelector value={fy} onChange={setFy} />
      </div>
      <div className="screen-main">
        {error !== null && <TaxLoadError error={error} />}
        {(error === null || fixableHere) && children}
      </div>
    </TaxContext.Provider>
  );
}
