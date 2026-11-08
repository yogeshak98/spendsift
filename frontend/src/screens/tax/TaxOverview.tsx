import type { JSX } from "react";
import { TaxLayout } from "./TaxLayout";
import { useTaxContext } from "./taxContext";
import { comparisonState, REGIME_LABEL, verdictAllowed } from "./utils";
import { inrWhole } from "@/shared/format/money";
import { TaxInputsForm } from "./TaxInputsForm";
import {
  BalanceRegimeNote,
  ComparisonIncompleteNotice,
  FigureProvenance,
  RegimeMismatchNotice,
  SalaryIsNetNotice,
  UnrecognisedIncomeKeysNotice,
  UpperBoundNote,
} from "./DeclaredNotices";

export function TaxOverviewContent(): JSX.Element {
  const { data, fy, error } = useTaxContext();

  // The form is rendered ABOVE the null-workspace guard, and off its own
  // endpoint: a declared value that is not a number, or an inputs file that
  // will not parse, makes the workspace route a coded 422, so a form inside the
  // guard (or reading the workspace) would vanish in exactly the states it
  // exists to repair.
  return (
    <div>
      <TaxInputsForm key={fy} fy={fy} />
      {/* An error is not a loading state: `TaxLayout` renders the coded error
          above a form-fixable failure, and the form above is then the whole
          screen -- there is nothing still arriving to wait for. */}
      {data ? <TaxOverviewBody /> : error ? <></> : <div>Loading…</div>}
    </div>
  );
}

function TaxOverviewBody(): JSX.Element {
  const { data, fy } = useTaxContext();
  // Unreachable: the caller renders this only with data. Kept as a narrowing
  // guard rather than a non-null assertion.
  if (!data) return <></>;

  const { summary } = data;
  const est = summary.estimate;
  const lower = est
    ? est.lower_tax_regime === "new"
      ? est.new_regime
      : est.old_regime
    : null;
  const balance = est ? Number(est.balance_due) : null;
  const unrecognised = summary.unrecognised_income_keys ?? [];

  // Whether a regime may be named the winner at all. The engine applies
  // HRA/LTA under the old regime only, so without a declared exemption the
  // old-regime tax is an upper bound and "recommended" would be a guess
  // dressed as a finding.
  const state = comparisonState(summary);
  const verdict = verdictAllowed(state);
  // The regime the balance was computed against: the one the user files under
  // when they declared it, otherwise the cheaper one.
  const balanceRegime = est ? (est.balance_due_regime ?? est.lower_tax_regime) : null;
  const declaredRegime = summary.declared_regime ?? null;

  return (
    <div>
      {unrecognised.length > 0 && (
        <UnrecognisedIncomeKeysNotice fy={fy} keys={unrecognised} />
      )}

      {summary.salary_is_net && <SalaryIsNetNotice fy={fy} />}

      {!summary.rules_configured && (
        <div className="callout warn" style={{ marginBottom: "1.0769rem" }}>
          Tax rules not configured for {data.period}. Add{" "}
          <b>config/tax/{fy}.yaml</b> to see a liability estimate. Figures below
          are aggregated ledger amounts only.
        </div>
      )}

      {est !== null && <ComparisonIncompleteNotice state={state} fy={fy} />}

      {est !== null && verdict && declaredRegime !== null && declaredRegime !== est.lower_tax_regime && (
        <RegimeMismatchNotice declared={declaredRegime} lower={est.lower_tax_regime} />
      )}

      {est && lower && verdict && (
        <div className="callout info" style={{ marginBottom: "1.0769rem" }}>
          Estimated tax under the lower-tax regime ({est.lower_tax_regime} regime):{" "}
          <b>{inrWhole(lower.total)}</b>.{" "}
          {balance !== null && balance >= 0 ? (
            <>
              Estimated balance due: <b>{inrWhole(String(balance))}</b> (after prepaid tax).
            </>
          ) : (
            <>
              Estimated refund: <b>{inrWhole(String(-balance!))}</b>.
            </>
          )}{" "}
          {balanceRegime !== null && (
            <>Computed against the {REGIME_LABEL[balanceRegime]}. </>
          )}
          This is a planning estimate, not a filing.
        </div>
      )}

      {/* No verdict available: the same two figures, stated per regime and
          without a winner, so the screen stays useful rather than blank. */}
      {est && !verdict && balanceRegime !== null && (
        <div className="callout info" style={{ marginBottom: "1.0769rem" }}>
          Estimated tax: <b>{inrWhole(est.new_regime.total)}</b> under the new regime,{" "}
          <b>{inrWhole(est.old_regime.total)}</b> under the old one.{" "}
          {balance !== null && balance >= 0 ? (
            <>
              Estimated balance due <b>{inrWhole(String(balance))}</b> (after prepaid
              tax), computed against the {REGIME_LABEL[balanceRegime]}.
            </>
          ) : (
            <>
              Estimated refund <b>{inrWhole(String(-balance!))}</b>, computed against
              the {REGIME_LABEL[balanceRegime]}.
            </>
          )}{" "}
          This is a planning estimate, not a filing.
        </div>
      )}

      <div className="stats" style={{ marginBottom: "1.2308rem" }}>
        {/* Both of these are the declared-overridden figures, and this is the
            nav default -- the screen most likely to be the only one read. A
            number shown here without its source is a number trusted on its
            own. */}
        <div className="stat">
          <div className="l">Gross ordinary income</div>
          <div className="n">{inrWhole(summary.gross_ordinary_income)}</div>
          <FigureProvenance kind="gross_salary" figure={summary.provenance?.gross_salary} />
        </div>
        <div className="stat">
          <div className="l">Prepaid tax (TDS + advance)</div>
          <div className="n">{inrWhole(summary.prepaid_tax)}</div>
          <FigureProvenance kind="tds" figure={summary.provenance?.tds} />
        </div>
        {lower && verdict && (
          <div className="stat">
            <div className="l">Estimated tax (lower regime)</div>
            <div className="n">{inrWhole(lower.total)}</div>
          </div>
        )}
        {/* Without a verdict there is no "lower regime" tile to show: one tile
            per regime, and the old-regime figure marked for what it is. */}
        {est && !verdict && (
          <>
            <div className="stat">
              <div className="l">Estimated tax (new regime)</div>
              <div className="n">{inrWhole(est.new_regime.total)}</div>
            </div>
            <div className="stat">
              <div className="l">Estimated tax (old regime)</div>
              <div className="n">{inrWhole(est.old_regime.total)}</div>
              {state === "exemption-missing" && <UpperBoundNote />}
            </div>
          </>
        )}
        {est && balance !== null && (
          <div className="stat">
            <div className="l">{balance >= 0 ? "Balance due" : "Estimated refund"}</div>
            <div className="n">{inrWhole(String(Math.abs(balance)))}</div>
            {balanceRegime !== null && <BalanceRegimeNote regime={balanceRegime} />}
          </div>
        )}
      </div>

      {summary.uncovered.length > 0 && (
        <div className="callout warn">
          Not included in estimate (surfaced, never silently taxed):{" "}
          <b>{summary.uncovered.join(", ")}</b>
        </div>
      )}
    </div>
  );
}

export function TaxOverview(): JSX.Element {
  return <TaxLayout><TaxOverviewContent /></TaxLayout>;
}
