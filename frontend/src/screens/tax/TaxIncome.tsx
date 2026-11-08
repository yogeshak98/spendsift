import { Fragment, type JSX } from "react";
import { TaxLayout } from "./TaxLayout";
import { useTaxContext } from "./taxContext";
import { KIND_LABELS } from "./utils";
import { inrWhole } from "@/shared/format/money";
import { TaxItemRow } from "./SourceTxns";
import { GratuityCeilingPanel } from "./GratuityCeiling";
import type { Item } from "./types";
import {
  FigureProvenance,
  SalaryIsNetNotice,
  UnrecognisedIncomeKeysNotice,
} from "./DeclaredNotices";

type OutsideGroup = "exempt" | "not_income" | "unknown";

/** The excluded groups, in render order, each named by what it IS rather than
 * by the schema's word for it. "Not identified" is NOT "Not income": an
 * unrecognised `Income:*` account may well be fully taxable and is silently
 * untaxed today, so calling it "not income" would assert something the ledger
 * cannot support. See `API_CONTRACT.md` ss`TaxRow`. */
const OUTSIDE_GROUPS: { key: OutsideGroup; heading: string }[] = [
  { key: "exempt", heading: "Exempt on leaving a job" },
  { key: "not_income", heading: "Not income" },
  { key: "unknown", heading: "Not identified" },
];

/** Why this row is outside the tax figure, in one clause under its label.
 * Keyed by KIND, because the reason is specific (a section number, a reversal)
 * while the treatment is only the bucket. */
const WHY_OUTSIDE: Record<string, string> = {
  exempt_gratuity: "s.10(10)",
  exempt_leave_encashment:
    "s.10(10AA). Partly taxable — the exempt and taxable parts are not separated in the ledger, only described in a note on the transaction.",
  refund: "Reversals of money you paid, not earnings.",
  // The honest statement of a real defect: `unknown` means we cannot say. It
  // must never be softened into "not income" -- that is the one claim the data
  // does not support, and the one that would make an untaxed taxable account
  // look deliberate.
  unclassified_income:
    "No prefix rule names this account, so it is left out of the tax figure. If it is taxable income, the figure above is too low.",
};

function groupOf(it: Item): OutsideGroup {
  if (it.treatment === "exempt" || it.treatment === "not_income") return it.treatment;
  // Everything else that survived the filter is something this client cannot
  // name a treatment for -- `unknown` by contract, or a treatment added to the
  // backend since. It is listed under "Not identified", which claims only that
  // we cannot say, rather than dropped: a silently missing row is the defect
  // this screen exists to fix.
  return "unknown";
}

export function TaxIncomeContent(): JSX.Element {
  const { data, fy, error } = useTaxContext();
  // An error is not a loading state: `TaxLayout` has already rendered the
  // coded error above, so printing "Loading…" under it for ever would be a
  // second, wrong account of the same situation.
  if (!data) return error ? <></> : <div>Loading…</div>;
  const { summary } = data;
  // What belongs on the INCOME screen, with exactly two documented exclusions.
  // Everything surviving is grouped on `treatment` alone.
  const income = data.items.filter(
    (it) =>
      // `treatment: null` is a tax PAYMENT (`prepaid_tax`, `tax_paid`): money
      // paid out, not income, and it has the Prepaid screen.
      it.treatment !== null &&
      // `capital_gain_*` carries `treatment: "taxed"`, so a treatment-only
      // grouping would pull capital gains onto this screen; they have their
      // own. The PREFIX test is deliberate and must not be "simplified" into a
      // list of capital-gain kinds: such a list is exactly the client-side
      // drift the `treatment` field was added to kill -- a gain class added in
      // `backend/app/service/tax/report.py` would then appear here as income.
      !it.kind.startsWith("capital_gain"),
  );
  const taxed = income.filter((it) => it.treatment === "taxed");
  const outside = income.filter((it) => it.treatment !== "taxed");
  const outsideTotal = outside.reduce((sum, it) => sum + Number(it.amount), 0);
  const outsideTxns = new Set(outside.flatMap((it) => it.source_refs)).size;
  const unrecognised = summary.unrecognised_income_keys ?? [];
  const grossDeclared = summary.provenance?.gross_salary.source === "declared";
  const label = (it: Item): string => KIND_LABELS[it.kind] ?? it.kind;
  return (
    <div>
      {/* A dropped `gross_salary` key is read HERE as well as on the overview:
          this is the screen whose salary row and total it was meant to change,
          and the net-salary notice beside it would otherwise tell a user who
          did declare gross to declare it again, with no hint that their key
          was discarded. */}
      {unrecognised.length > 0 && (
        <UnrecognisedIncomeKeysNotice fy={fy} keys={unrecognised} />
      )}
      {summary.salary_is_net && <SalaryIsNetNotice fy={fy} />}
      <div className="panel">
        <div className="panel-head">Reaches your tax figure</div>
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
              {taxed.length === 0 && (
                <tr>
                  <td colSpan={3} style={{ color: "var(--text3)", textAlign: "center", padding: "1.5385rem" }}>
                    No ordinary income recorded for this FY.
                  </td>
                </tr>
              )}
              {taxed.map((it) => (
                <TaxItemRow
                  key={it.kind}
                  label={label(it)}
                  amount={it.amount}
                  refs={it.source_refs}
                  note={
                    it.kind === "salary" && grossDeclared
                      ? "Ledger salary, net of TDS — the total below uses your declared gross instead, so these do not add up."
                      : undefined
                  }
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {outside.length > 0 && (
        <div className="panel" style={{ marginTop: "0.9231rem" }}>
          {/* The group's own total sits in its head, because the figure under
              the screen's first table deliberately excludes every rupee of
              it -- a table of amounts with no total of its own is how
              real money went unexplained. */}
          <div className="panel-head">
            Outside your tax figure <span className="tot">{inrWhole(String(outsideTotal))}</span>
          </div>
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
                {OUTSIDE_GROUPS.map(({ key, heading }) => {
                  const rows = outside.filter((it) => groupOf(it) === key);
                  if (rows.length === 0) return null;
                  return (
                    <Fragment key={key}>
                      <tr>
                        <td colSpan={3} className="treat-group">
                          {heading}
                        </td>
                      </tr>
                      {rows.map((it) => (
                        <TaxItemRow
                          key={it.kind}
                          rowClass="treat-row"
                          label={label(it)}
                          amount={it.amount}
                          refs={it.source_refs}
                          note={WHY_OUTSIDE[it.kind]}
                        />
                      ))}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {/* Two tiles, and NO arithmetic between them: the left is the DECLARED
          gross (plus ledger interest), the right is a ledger total, so they
          are not addends of each other and the UI must not imply a sum, an
          "of which", or a percentage. */}
      <div className="stats two" style={{ marginTop: "0.9231rem" }}>
        <div className="stat">
          <div className="l">Total gross ordinary income</div>
          <div className="n">{inrWhole(summary.gross_ordinary_income)}</div>
          <FigureProvenance kind="gross_salary" figure={summary.provenance?.gross_salary} />
        </div>
        {outside.length > 0 && (
          <div className="stat">
            <div className="l">Outside your tax figure</div>
            <div className="n">{inrWhole(String(outsideTotal))}</div>
            {/* "rows", not "accounts": each row aggregates every account a
                prefix arm matches (transfers from several people are one row),
                so an account count here would be a number nothing on screen
                supports. */}
            <div className="sub">
              {outside.length} {outside.length === 1 ? "row" : "rows"}, {outsideTxns}{" "}
              {outsideTxns === 1 ? "transaction" : "transactions"}
            </div>
          </div>
        )}
      </div>
      <GratuityCeilingPanel />
    </div>
  );
}

export function TaxIncome(): JSX.Element {
  return <TaxLayout><TaxIncomeContent /></TaxLayout>;
}
