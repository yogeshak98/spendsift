import type { JSX } from "react";
import { TaxLayout } from "./TaxLayout";
import { useTaxContext } from "./taxContext";
import { sectionLabel } from "./utils";
import { inrWhole } from "@/shared/format/money";

export function TaxDeductionsContent(): JSX.Element {
  const { data, error } = useTaxContext();
  // An error is not a loading state: `TaxLayout` has already rendered the
  // coded error above, so printing "Loading…" under it for ever would be a
  // second, wrong account of the same situation.
  if (!data) return error ? <></> : <div>Loading…</div>;

  const { deductions } = data;
  const hasDeductions = deductions.length > 0;

  return (
    <div>
      <div className="callout info" style={{ marginBottom: "1.0769rem" }}>
        Deductions are <b>user-declared</b> per FY in{" "}
        <b>config/tax/&lt;fy&gt;-inputs.yaml</b>. Transaction-level tagging (#80c, #80ccd-2)
        is deferred to a future release.
      </div>

      {!hasDeductions && (
        <div className="callout warn">
          No deductions declared for this FY. Edit the inputs YAML to add them.
        </div>
      )}

      {hasDeductions && (
        <div className="panel">
          <div className="panel-head">Declared Deductions</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Section</th>
                  <th className="num">Declared</th>
                  <th className="num">Applied (old)</th>
                  <th className="num">Applied (new)</th>
                </tr>
              </thead>
              <tbody>
                {deductions.map((d) => (
                  <tr key={d.section}>
                    <td className="label">{sectionLabel(d.section)}</td>
                    <td className="num mono">{inrWhole(d.declared)}</td>
                    <td className="num mono">{inrWhole(d.applied_old)}</td>
                    <td className="num mono">{inrWhole(d.applied_new)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export function TaxDeductions(): JSX.Element {
  return <TaxLayout><TaxDeductionsContent /></TaxLayout>;
}
