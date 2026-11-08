import type { JSX } from "react";
import { Link } from "react-router-dom";
import { REGIME_LABEL, type ComparisonState } from "./utils";
import { inrWhole } from "@/shared/format/money";
import type { ProvenanceFigure } from "./types";
import "./tax.css";

/** The declared-income overlay, stated on screen.
 *
 * Every string a user reads about where a tax figure came from lives in this
 * file. The copy is single-sourced deliberately: the same sentence appears on
 * more than one tax screen, and two drifting copies of "this number is net of
 * TDS" would be worse than one.
 *
 * Copy rules this file keeps, learned from a review that found the opposite:
 *  - the action comes first, before any explanation;
 *  - a YAML key is only ever named the way the user typed it, in the two-line
 *    snippet below -- never as a dot path like `income.gross_salary`, which
 *    appears nowhere in the file the user edits;
 *  - every notice says WHICH figure to enter (payslip year-to-date gross, or
 *    Form 16), because that is the reader's next question.
 */

const INPUTS_FILE = (fy: number): string => `config/tax/${fy}-inputs.yaml`;

/** The FY's tax-RULES file -- slabs, caps and regime switches. A different file
 * from `INPUTS_FILE`, which holds the user's declared figures and is the only
 * one the form writes: sending a user to the wrong one is a dead end, and the
 * two names differ by nothing but the `-inputs` suffix, so any notice that
 * names one has to make the difference explicit in words too.
 *
 * Data-dir-relative, like every path these notices print: the storage dir is
 * configurable (`create_app(storage_dir=...)`, which demo mode points at
 * `demo/data`), so a `storage/` prefix would be true only for the default. */
const RULES_FILE = (fy: number): string => `config/tax/${fy}.yaml`;

/** The one canonical statement of what the `income` block looks like. Shared by
 * every notice that asks the user to edit it, so no two notices can disagree
 * about how the keys are spelled or nested. */
const INPUTS_SNIPPET = [
  "income:",
  '  gross_salary: "1200000"   # payslip year-to-date gross, or Form 16',
  '  tds: "150000"             # TDS withheld against it so far',
  '  exempt_hra_lta: "240000"  # HRA s.10(13A) + LTA s.10(5), for the',
  "                            # old-regime comparison",
].join("\n");

/** The form that writes the file these notices are about. Every notice points
 * at the form FIRST and names the file second: the file is still the storage
 * format and still hand-editable, but it is no longer the only way in. */
export function InputsFormLink({ text }: { text: string }): JSX.Element {
  return <Link to="/tax/overview">{text}</Link>;
}

function InputsSnippet(): JSX.Element {
  return <pre className="tax-yaml">{INPUTS_SNIPPET}</pre>;
}

/** Which of the two overridable figures a provenance line is about. */
export type FigureKind = "gross_salary" | "tds";

// The label is derived from the kind AND the source, and is deliberately NOT a
// caller-supplied prop. A review found "Gross salary ₹X, from the ledger."
// rendering next to a sibling notice that said the same figure was net of TDS:
// `source: "ledger"` means no gross was declared, which is exactly the
// condition that makes the ledger figure net. Deriving the word here makes
// "gross" unspellable for a ledger figure from any call site.
const DECLARED_LABEL: Record<FigureKind, string> = {
  gross_salary: "Gross salary",
  tds: "TDS",
};
const LEDGER_LABEL: Record<FigureKind, string> = {
  gross_salary: "Salary",
  tds: "TDS",
};

const hasAdvance = (figure: ProvenanceFigure): boolean =>
  Number(figure.advance_tax ?? 0) !== 0;

/** Where one figure came from, rendered immediately under the figure itself.
 *
 * Not a separate panel: read apart from the number, the number keeps being
 * trusted on its own. When the figure is ledger-derived there is nothing
 * ignored, so no "0 transactions ignored" clause is rendered -- and the salary
 * is named plainly, with the fact that it is net of TDS said out loud.
 */
export function FigureProvenance({
  kind,
  figure,
}: {
  kind: FigureKind;
  figure: ProvenanceFigure | undefined;
}): JSX.Element | null {
  if (!figure) return null;
  const advance = kind === "tds" && hasAdvance(figure);
  if (figure.source !== "declared") {
    if (advance) {
      return (
        <div className="figure-prov">
          {LEDGER_LABEL[kind]} {inrWhole(figure.value)} and advance tax{" "}
          {inrWhole(figure.advance_tax ?? "0")}, both from the ledger.
        </div>
      );
    }
    return (
      <div className="figure-prov">
        {LEDGER_LABEL[kind]} {inrWhole(figure.value)}, from the ledger
        {kind === "gross_salary" ? " — net of TDS." : "."}
      </div>
    );
  }
  const n = figure.ignored_postings;
  return (
    <div className="figure-prov">
      <span className="fp-src">
        {DECLARED_LABEL[kind]} {inrWhole(figure.value)}, declared
      </span>
      {n > 0 && (
        <>
          {" — "}
          {n === 1
            ? `1 ledger transaction of ${inrWhole(figure.ignored_amount)}`
            : `${n} ledger transactions totalling ${inrWhole(figure.ignored_amount)}`}
          {" ignored for this estimate"}
        </>
      )}
      {advance && (
        <>
          {"; plus advance tax "}
          {inrWhole(figure.advance_tax ?? "0")}
          {" from the ledger, counted in full"}
        </>
      )}
      .
    </div>
  );
}

/** Honest degradation: no gross was declared, so the salary on screen is the
 * bank credit after TDS and every estimate standing on it is too low. */
export function SalaryIsNetNotice({ fy }: { fy: number }): JSX.Element {
  return (
    <div className="callout warn">
      <span>
        Declare your gross salary in the{" "}
        <InputsFormLink text="declared-inputs form" />: the salary here is the
        bank credit, net of TDS, so the tax estimate built on it is understated.
        Enter the year-to-date gross from your latest payslip (or Form 16). It
        is saved to <span className="tax-key">{INPUTS_FILE(fy)}</span>, which
        stays hand-editable:
      </span>
      <InputsSnippet />
    </div>
  );
}

/** The two declared figures cannot both be right. Nothing has been corrected. */
export function DeclaredTdsNotice({
  fy,
  gross,
  tds,
}: {
  fy: number;
  gross: string;
  tds: string;
}): JSX.Element {
  return (
    <div className="callout warn">
      <span>
        Check the two declared figures in{" "}
        <span className="tax-key">{INPUTS_FILE(fy)}</span>: TDS ({inrWhole(tds)}) is
        not below gross salary ({inrWhole(gross)}). TDS is withheld out of the gross
        it is computed on, so the pair is likely reversed, or from different
        periods. Both are being used exactly as declared.
      </span>
    </div>
  );
}

/** A hand-typed `income` block the loader could not use. Without this the
 * screen shows the ledger figure and nothing explains why the declaration did
 * nothing -- the one failure mode this feature introduces.
 *
 * Two cases, because the backend reports both through one list. The single
 * entry `"income"` does not mean the key `income` is unrecognised (it is the
 * recognised block name): it means the block was not a key/value map at all --
 * a scalar, or the YAML list a hand-edit produces -- so the whole block was
 * dropped, possibly with both correct keys inside it. Telling that user to fix
 * an "unrecognised key `income`" would send them to the one thing that is
 * right.
 */
export function UnrecognisedIncomeKeysNotice({
  fy,
  keys,
}: {
  fy: number;
  keys: string[];
}): JSX.Element {
  const malformedBlock = keys.length === 1 && keys[0] === "income";
  return (
    <div className="callout warn">
      {malformedBlock ? (
        <span>
          Rewrite the <b>income</b> block in{" "}
          <span className="tax-key">{INPUTS_FILE(fy)}</span>: it is not a set of
          key/value lines, so the whole block was ignored and nothing was
          declared from it. It should read:
        </span>
      ) : (
        <span>
          <span className="tax-key">{INPUTS_FILE(fy)}</span> has an income{" "}
          {keys.length === 1 ? "key" : "keys"} this workspace does not
          recognise: <b>{keys.join(", ")}</b> —{" "}
          {keys.length === 1
            ? "ignored, so nothing was declared from it."
            : "ignored, so nothing was declared from them."}{" "}
          The recognised keys are:
        </span>
      )}
      <InputsSnippet />
    </div>
  );
}

/** Keys nothing reads, as reported by the form's own endpoint. The workspace
 * has its own narrower notice above for the `income` block; this one covers the
 * whole document, and names the file because the form cannot show a key it does
 * not render a field for. */
export function UnknownInputKeysNotice({
  path,
  keys,
}: {
  path: string;
  keys: string[];
}): JSX.Element {
  return (
    <div className="callout warn">
      <span>
        <span className="tax-key">{path}</span> has{" "}
        {keys.length === 1 ? "a key" : "keys"} nothing reads:{" "}
        <b>{keys.join(", ")}</b>. {keys.length === 1 ? "It is" : "They are"}{" "}
        left in the file untouched, and{" "}
        {keys.length === 1 ? "contributes" : "contribute"} nothing to any
        estimate — so a figure declared there has no effect. Move it to one of
        the fields below, or delete it.
      </span>
    </div>
  );
}

/** Why the regime comparison is not a recommendation yet.
 *
 * One component for all three non-verdict states, so no screen can invent its
 * own account of the same gap. Returns null when the comparison is complete --
 * then the recommendation stands on its own and needs no caveat. */
export function ComparisonIncompleteNotice({
  state,
  fy,
}: {
  state: ComparisonState;
  fy: number;
}): JSX.Element | null {
  if (state === "complete") return null;
  if (state === "not-applicable") {
    return (
      <div className="callout info">
        <span>
          No salary income for this FY, declared or in the ledger, so the
          old/new regime choice is not what decides your tax here. Both columns
          are shown for completeness; neither is recommended.
        </span>
      </div>
    );
  }
  if (state === "no-declaration") {
    return (
      <div className="callout warn">
        <span>
          Without a declared gross salary the salary behind both columns is the
          bank credit, net of TDS, so <b>both</b> regimes are understated and
          neither can be recommended over the other — this is not a close call
          that one of them wins, it is a comparison of two numbers that are both
          too low. Declare your gross salary, TDS and exempt HRA + LTA in the{" "}
          <InputsFormLink text="declared-inputs form" /> (saved to{" "}
          <span className="tax-key">{INPUTS_FILE(fy)}</span>) to compare them.
        </span>
      </div>
    );
  }
  // Two different causes land here, and the workspace response cannot tell
  // them apart: it carries no signal of whether `exempt_hra_lta` was declared
  // (`provenance` covers gross salary and TDS only, and the APPLIED
  // `exempt_allowances` is "0" under both causes). So neither instruction may
  // be stated as THE next step -- each is written under the condition that
  // makes it true, and the reader can tell from their own inputs file which
  // one is theirs. The alternative, leading with "declare the figure", is a
  // dead end for the user who already has.
  return (
    <div className="callout warn">
      <span>
        HRA s.10(13A) and LTA s.10(5) are applied under the old regime and
        withdrawn under the new one (s.115BAC), and no figure for them is being
        applied here — so the old regime below is computed as if you claimed
        nothing. {UPPER_BOUND_SENTENCE} If you have not declared the figure
        yet, enter it in the{" "}
        <InputsFormLink text="declared-inputs form" /> (saved to{" "}
        <span className="tax-key">{INPUTS_FILE(fy)}</span>) — 0 is an answer
        too, if you claim neither. If you have already declared it, then this
        FY's tax-rules file is what dropped it: add{" "}
        <span className="tax-key">allows_exemptions: true</span> under{" "}
        <span className="tax-key">old_regime:</span> in{" "}
        <span className="tax-key">{RULES_FILE(fy)}</span> — the tax-rules file,
        which is the one WITHOUT{" "}
        <span className="tax-key">-inputs</span> in its name, and the one the
        form never writes.
      </span>
    </div>
  );
}

/** The one statement of what an incomplete comparison makes the old-regime
 * figure. Said the same way wherever that figure is shown. */
const UPPER_BOUND_SENTENCE =
  "Its deductions are a lower bound, so its tax is an upper bound — the real old-regime tax can only be lower, never higher.";

/** Rendered under an old-regime figure whose exemption term is missing, so the
 * number is never read as an estimate of the tax. */
export function UpperBoundNote(): JSX.Element {
  return (
    <div className="figure-prov">
      Upper bound on tax — no exempt HRA + LTA is being applied, so the old
      regime is computed without those reliefs.
    </div>
  );
}

/** Which regime a balance was computed against. A balance shown bare reads as
 * unconditional, and it is not: it is one regime's answer. */
export function BalanceRegimeNote({ regime }: { regime: "new" | "old" }): JSX.Element {
  return <div className="figure-prov">Computed against the {REGIME_LABEL[regime]}.</div>;
}

/** The user says they file under one regime and the comparison favours the
 * other. Only shown when the comparison is complete enough to be believed. */
export function RegimeMismatchNotice({
  declared,
  lower,
}: {
  declared: "new" | "old";
  lower: "new" | "old";
}): JSX.Element {
  return (
    <div className="callout info">
      <span>
        You file under the <b>{REGIME_LABEL[declared]}</b>, and this estimate
        makes the <b>{REGIME_LABEL[lower]}</b> the cheaper one for this FY.
        Figures here are quoted against the {REGIME_LABEL[declared]}, the one
        you file under — not against the cheaper one.
      </span>
    </div>
  );
}

/** The one sentence about what an undeclared exemption actually leaves open:
 * the regime CHOICE, which is not always the same thing as the amount. Shared
 * by the notice that withholds the challan instruction and by the notice that
 * keeps it, so the two cannot describe the same gap differently. */
function RegimeChoiceUndecided(): JSX.Element {
  return (
    <>
      which regime that should be is decided by a comparison missing your exempt
      HRA + LTA. <InputsFormLink text="Declare that figure" /> to settle it.
    </>
  );
}

/** Instead of "pay this amount by challan", on a balance known to be biased.
 *
 * Telling someone to pay a specific amount on a figure whose inputs are
 * incomplete is the worst thing any of these screens can do, so the amount
 * stays on screen as an estimate and the instruction is withheld. */
export function ChallanWithheldNotice({
  state,
  balance,
  regime,
}: {
  state: ComparisonState;
  balance: string;
  regime: "new" | "old";
}): JSX.Element {
  return (
    <div className="callout warn" style={{ marginBottom: "1.0769rem" }}>
      <span>
        <b>Not a challan amount yet.</b> The estimated balance of{" "}
        {inrWhole(balance)} is computed against the {REGIME_LABEL[regime]}, and{" "}
        {state === "no-declaration" ? (
          <>
            without a declared gross salary the salary it stands on is a bank
            credit net of TDS, so the balance is understated and paying it would
            leave tax outstanding. Declare your gross salary and TDS in the{" "}
            <InputsFormLink text="declared-inputs form" /> first.
          </>
        ) : (
          <>
            <RegimeChoiceUndecided /> Then pay against the regime you file
            under.
          </>
        )}
      </span>
    </div>
  );
}

/** The challan instruction stands, and the regime choice still does not.
 *
 * Rendered beside the pay-by-challan line for a declared new-regime filer whose
 * exemption is undeclared: s.115BAC grants no HRA/LTA exemption, so the figure
 * they owe under the regime they file under is exact -- withholding it would
 * cost them a correct number. What is still undecided is whether the new regime
 * is the one to be filing under at all, and that is said here rather than left
 * for the reader to infer from the comparison screen. */
export function ChallanRegimeCaveatNotice(): JSX.Element {
  return (
    <div className="callout info" style={{ marginBottom: "1.0769rem" }}>
      <span>
        <b>That amount stands.</b> You file under the new regime, which grants
        no HRA s.10(13A) or LTA s.10(5) exemption, so a figure you have not
        declared for them cannot change what you owe under it. What is not
        settled is the regime itself: <RegimeChoiceUndecided />
      </span>
    </div>
  );
}

/** The one canonical statement of how a direct-tax payment says which year it
 * was paid FOR. One line of posting metadata, spelled exactly as it is written
 * in the journal, and the AY bridge in the comment beside it -- a challan says
 * "AY 2026-27" and the user must never be left to work out the offset. */
const TAX_FY_SNIPPET = [
  "  Expenses:Tax   <amount> INR",
  '    tax-fy: "2025"   # the FY it was paid FOR, as its START',
  '                     # year: "2025" is FY2025-26, which a',
  "                     # challan calls AY 2026-27",
].join("\n");

/** Direct-tax payments attributed to this FY by their payment DATE alone.
 *
 * `info`, never `warn`: nothing here is known to be wrong. A payment made in
 * one year FOR another (self-assessment tax, paid while filing that year's
 * return) was being credited to the wrong year with no way to say otherwise,
 * and on the screen that tells a person to pay that inverted the sign of the
 * balance. The tag is the way to say otherwise, and nobody discovers posting
 * metadata from a changelog.
 *
 * This notice asserts NOTHING about which year these payments belong to. It
 * does not call them prior-year, does not rank them, and does not single out
 * any part of the calendar -- no date range distinguishes self-assessment tax
 * from advance tax, which is why the year has to be stated rather than
 * guessed. What is untagged, and what tagging does: that is all the data
 * supports. */
export function UntaggedTaxPaymentsNotice({
  period,
  payments,
}: {
  period: string;
  payments: { date: string; amount: string; txn_id: string }[];
}): JSX.Element {
  const one = payments.length === 1;
  return (
    <div className="callout info" style={{ marginBottom: "1.0769rem" }}>
      <span>
        Tax paid directly is counted against {period} from the date it was
        paid. {one ? "This payment says" : "These payments say"} nothing about
        the year {one ? "it was" : "they were"} paid <b>for</b>:{" "}
        {payments.map((p, i) => (
          <span key={`${p.txn_id}-${p.date}-${i}`}>
            {i > 0 && "; "}
            {inrWhole(p.amount)} on {p.date}
          </span>
        ))}
        . If a payment was made for a different year — self-assessment tax for
        one year is paid while filing its return, which falls in the next one —
        name that year on its posting and it will be counted against that year
        instead:
      </span>
      <pre className="tax-yaml">{TAX_FY_SNIPPET}</pre>
    </div>
  );
}
