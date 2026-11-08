import type { JSX } from "react";
import { TaxLayout } from "./TaxLayout";
import { useTaxContext } from "./taxContext";
import { challanAllowed, comparisonState, KIND_LABELS } from "./utils";
import { inrWhole } from "@/shared/format/money";
import { TaxItemRow } from "./SourceTxns";
import {
  BalanceRegimeNote,
  ChallanRegimeCaveatNotice,
  ChallanWithheldNotice,
  FigureProvenance,
  DeclaredTdsNotice,
  UnrecognisedIncomeKeysNotice,
  UntaggedTaxPaymentsNotice,
} from "./DeclaredNotices";

const PREPAID_KINDS = new Set(["prepaid_tax", "tax_paid"]);

export function TaxPrepaidContent(): JSX.Element {
  const { data, fy, error } = useTaxContext();
  // An error is not a loading state: `TaxLayout` has already rendered the
  // coded error above, so printing "Loading…" under it for ever would be a
  // second, wrong account of the same situation.
  if (!data) return error ? <></> : <div>Loading…</div>;

  const { summary } = data;
  const prepaidItems = data.items.filter((it) => PREPAID_KINDS.has(it.kind));
  const est = summary.estimate;
  const balance = est ? Number(est.balance_due) : null;
  const unrecognised = summary.unrecognised_income_keys ?? [];
  // Direct-tax payments counted here from their date alone. `?? []` for the
  // same reason every other optional summary field is read that way: a missing
  // field shows no notice rather than crashing the screen.
  const untaggedTaxPayments = summary.untagged_tax_payments ?? [];
  // This screen is the one that tells a person to pay a specific amount of
  // money. The engine applies HRA/LTA under the old regime only, so while the
  // comparison behind the regime choice is incomplete the balance is known to
  // be biased -- and "pay this by challan" is withheld rather than printed over
  // a figure that will move once the exemption is declared.
  const state = comparisonState(summary);
  const balanceRegime = est ? (est.balance_due_regime ?? est.lower_tax_regime) : null;
  const challan = challanAllowed(state, summary.declared_regime, balanceRegime);
  // The one case where the amount is exact while the regime choice is not: a
  // declared new-regime filer with no declared exemption. The instruction is
  // given AND the open question is stated, rather than one at the cost of the
  // other.
  const regimeCaveat = challan && state === "exemption-missing";

  return (
    <div>
      {/* A mistyped `tds` key (`tdss`, `tds_total`) is invisible everywhere
          else: this screen then shows "TDS ₹X, from the ledger", which is
          TRUE, and `salary_is_net` does not fire for TDS. Without this notice a
          dropped TDS declaration has no effect and no explanation -- exactly
          what the notice exists to prevent. */}
      {unrecognised.length > 0 && (
        <UnrecognisedIncomeKeysNotice fy={fy} keys={unrecognised} />
      )}
      <div className="stats" style={{ marginBottom: "1.2308rem" }}>
        <div className="stat">
          <div className="l">TDS + advance tax (prepaid)</div>
          <div className="n">{inrWhole(summary.prepaid_tax)}</div>
          <FigureProvenance kind="tds" figure={summary.provenance?.tds} />
        </div>
        <div className="stat">
          <div className="l">Tax paid directly</div>
          <div className="n">{inrWhole(summary.tax_paid_direct)}</div>
        </div>
        {balance !== null && (
          <div className="stat">
            <div className="l">{balance >= 0 ? "Estimated balance due" : "Estimated refund"}</div>
            <div className="n">{inrWhole(String(Math.abs(balance)))}</div>
            {balanceRegime !== null && <BalanceRegimeNote regime={balanceRegime} />}
          </div>
        )}
      </div>

      {summary.declared_tds_at_or_above_gross && summary.provenance && (
        <DeclaredTdsNotice
          fy={fy}
          gross={summary.provenance.gross_salary.value}
          tds={summary.provenance.tds.value}
        />
      )}

      {balance !== null && balance >= 0 && challan && (
        <div className="callout warn" style={{ marginBottom: "1.0769rem" }}>
          Estimated balance due: <b>{inrWhole(String(balance))}</b>. Pay via self-assessment challan
          before the filing deadline to avoid interest.
        </div>
      )}

      {balance !== null && balance >= 0 && regimeCaveat && <ChallanRegimeCaveatNotice />}

      {balance !== null && balance >= 0 && !challan && balanceRegime !== null && (
        <ChallanWithheldNotice
          state={state}
          balance={String(balance)}
          regime={balanceRegime}
        />
      )}

      {balance !== null && balance < 0 && (
        <div className="callout info" style={{ marginBottom: "1.0769rem" }}>
          Estimated refund: <b>{inrWhole(String(-balance))}</b>. File your return promptly to
          process the refund.
        </div>
      )}

      {untaggedTaxPayments.length > 0 && (
        <UntaggedTaxPaymentsNotice
          period={data.period}
          payments={untaggedTaxPayments}
        />
      )}

      <div className="panel">
        <div className="panel-head">Prepaid Tax Items</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th className="num">Amount</th>
                <th className="num">Source txns</th>
              </tr>
            </thead>
            <tbody>
              {prepaidItems.length === 0 && (
                <tr>
                  <td colSpan={3} style={{ color: "var(--text3)", textAlign: "center", padding: "1.5385rem" }}>
                    No TDS or advance tax recorded for this FY.
                  </td>
                </tr>
              )}
              {prepaidItems.map((it) => (
                <TaxItemRow
                  key={it.kind}
                  label={KIND_LABELS[it.kind] ?? it.kind}
                  amount={it.amount}
                  refs={it.source_refs}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export function TaxPrepaid(): JSX.Element {
  return <TaxLayout><TaxPrepaidContent /></TaxLayout>;
}
