import type { JSX } from "react";
import { TaxLayout } from "./TaxLayout";
import { useTaxContext } from "./taxContext";
import { comparisonState, verdictAllowed } from "./utils";
import { inrWhole } from "@/shared/format/money";
import { ComparisonIncompleteNotice, UpperBoundNote } from "./DeclaredNotices";
import type { Regime } from "./types";

const ROWS: { label: string; key: keyof Regime }[] = [
  { label: "Exempt allowances (HRA + LTA)", key: "exempt_allowances" },
  { label: "Deductions applied", key: "total_deductions" },
  { label: "Taxable income", key: "taxable_income" },
  { label: "Slab tax", key: "slab_tax" },
  { label: "Rebate (§87A)", key: "rebate" },
  { label: "Surcharge", key: "surcharge" },
  { label: "Cess (4%)", key: "cess" },
  { label: "Capital gains tax", key: "capital_gains_tax" },
  { label: "Total tax", key: "total" },
];

export function TaxRegimeCompareContent(): JSX.Element {
  const { data, fy, error } = useTaxContext();
  // An error is not a loading state: `TaxLayout` has already rendered the
  // coded error above, so printing "Loading…" under it for ever would be a
  // second, wrong account of the same situation.
  if (!data) return error ? <></> : <div>Loading…</div>;

  const est = data.summary.estimate;
  if (!est) {
    return (
      <div className="callout warn">
        Regime comparison not available — add a tax rules YAML for this FY to enable estimates.
      </div>
    );
  }

  // "recommended" is a claim about which regime wins. It is only printable
  // when both figures were computed from the same information: an undeclared
  // exempt HRA + LTA leaves the old-regime column computed without reliefs it
  // would actually get, so the pill would name a winner the figures do not
  // support.
  const state = comparisonState(data.summary);
  const verdict = verdictAllowed(state);

  return (
    <>
      <ComparisonIncompleteNotice state={state} fy={fy} />
      <div className="panel">
        <div className="panel-head">Regime Comparison — {data.period}</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Line item</th>
                <th className="num">
                  Old regime{verdict && est.lower_tax_regime === "old" && (
                    <span className="pill asset" style={{ marginLeft: "0.6154rem", fontSize: "0.7692rem" }}>recommended</span>
                  )}
                  {state === "exemption-missing" && <UpperBoundNote />}
                </th>
                <th className="num">
                  New regime{verdict && est.lower_tax_regime === "new" && (
                    <span className="pill asset" style={{ marginLeft: "0.6154rem", fontSize: "0.7692rem" }}>recommended</span>
                  )}
                </th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map(({ label, key }) => (
                <tr key={key} style={key === "total" ? { fontWeight: 600 } : undefined}>
                  <td className="label">{label}</td>
                  {/* `exempt_allowances` is optional on the type for the same
                      reason `provenance` is: a dropped field renders a zero
                      line, never `₹NaN`. */}
                  <td className="num mono">{inrWhole(est.old_regime[key] ?? "0")}</td>
                  <td className="num mono">{inrWhole(est.new_regime[key] ?? "0")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

export function TaxRegimeCompare(): JSX.Element {
  return <TaxLayout><TaxRegimeCompareContent /></TaxLayout>;
}
