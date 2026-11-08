import type { JSX } from "react";
import { TaxLayout } from "./TaxLayout";
import { useTaxContext } from "./taxContext";
import { KIND_LABELS } from "./utils";
import { inrWhole } from "@/shared/format/money";
import { TaxItemRow } from "./SourceTxns";

const CG_KINDS = [
  "capital_gain_ltcg",
  "capital_gain_stcg",
  "capital_gain_crypto",
  "capital_gain_debt",
  "capital_gain_unclassified",
];

/** Why a row is outside the tax figure, in one clause under its label. Same
 * pattern as the Income screen's `WHY_OUTSIDE`: keyed by KIND, and claims only
 * that the treatment is undecided -- never that the gain is exempt or not
 * income. */
const WHY_OUTSIDE: Record<string, string> = {
  capital_gain_unclassified:
    "AMFI does not classify this fund as equity or debt, so its holding-period treatment is undecided and it is not in the tax figure. If it is taxable, the figure above is too low.",
};

export function TaxCapitalGainsContent(): JSX.Element {
  const { data, error } = useTaxContext();
  // An error is not a loading state: `TaxLayout` has already rendered the
  // coded error above, so printing "Loading…" under it for ever would be a
  // second, wrong account of the same situation.
  if (!data) return error ? <></> : <div>Loading…</div>;

  const { capital_gains } = data.summary;
  const cgItems = data.items.filter((it) => CG_KINDS.includes(it.kind));
  const hasDebt = Number(capital_gains.debt) > 0;
  // Optional chaining: an older response without the field must not take the
  // screen down.
  const unclassified = capital_gains.unclassified ?? "0";

  return (
    <div>
      <div className="stats" style={{ marginBottom: "1.2308rem" }}>
        {[
          { label: "LTCG", value: capital_gains.ltcg },
          { label: "STCG", value: capital_gains.stcg },
          { label: "Crypto", value: capital_gains.crypto },
          { label: "Debt", value: capital_gains.debt },
          { label: "Undecided", value: unclassified },
        ].map(({ label, value }) => (
          <div className="stat" key={label}>
            <div className="l">{label}</div>
            <div className="n">{inrWhole(value)}</div>
          </div>
        ))}
      </div>

      <div className="callout info" style={{ marginBottom: "1.0769rem" }}>
        Realized gains from equity and stock sales are not yet included in this view; they are
        recorded in the journal with per-lot dates preserved, simply not yet classified here,
        and nothing is being wrongly slab-taxed as a result. For the gains shown, cost basis
        comes from the broker&apos;s order value, which excludes brokerage, STT, stamp duty and
        exchange fees, so realized gains are marginally overstated.
      </div>

      {hasDebt && (
        <div className="callout warn" style={{ marginBottom: "1.0769rem" }}>
          Debt capital gains (post-2023 slab treatment) are surfaced but not included in the
          regime estimate — treatment varies. Review manually.
        </div>
      )}

      <div className="panel">
        <div className="panel-head">Capital Gains Items</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Kind</th>
                <th className="num">Amount</th>
                <th className="num">Source txns</th>
              </tr>
            </thead>
            <tbody>
              {cgItems.length === 0 && (
                <tr>
                  <td colSpan={3} style={{ color: "var(--text3)", textAlign: "center", padding: "1.5385rem" }}>
                    No capital gains recorded for this FY.
                  </td>
                </tr>
              )}
              {cgItems.map((it) => (
                <TaxItemRow
                  key={it.kind}
                  label={KIND_LABELS[it.kind] ?? it.kind}
                  amount={it.amount}
                  refs={it.source_refs}
                  note={WHY_OUTSIDE[it.kind]}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export function TaxCapitalGains(): JSX.Element {
  return <TaxLayout><TaxCapitalGainsContent /></TaxLayout>;
}
