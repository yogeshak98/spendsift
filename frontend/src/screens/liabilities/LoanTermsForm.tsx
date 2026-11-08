import { useState } from "react";
import { api } from "@/shared/api/http";
import { parseCodedError } from "@/shared/api/codedError";

export type LoanTermsValues = {
  annual_rate_pct: string | null;
  term_months: number | null;
  original_principal: string | null;
  label: string | null;
};

// PUT /api/accounts/loan-terms: every key is sent; null removes the term.
type LoanTermsBody = LoanTermsValues & { account: string };

function textOrNull(value: string): string | null {
  const t = value.trim();
  return t === "" ? null : t;
}

/** The in-app terms form for one loan. Values are written to the account's
 *  own `open` directive; the user never edits a file. */
export function LoanTermsForm(props: {
  account: string;
  initial: LoanTermsValues;
  onSaved: () => void;
  // The loan carries terms the user set here, so "Clear my terms" applies.
  ownTerms?: boolean;
}) {
  const { initial } = props;
  const [rate, setRate] = useState(initial.annual_rate_pct ?? "");
  const [months, setMonths] = useState(
    initial.term_months === null ? "" : String(initial.term_months),
  );
  const [principal, setPrincipal] = useState(initial.original_principal ?? "");
  const [label, setLabel] = useState(initial.label ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send(body: LoanTermsBody) {
    setError(null);
    setBusy(true);
    try {
      await api.putJSON("/api/accounts/loan-terms", body);
      props.onSaved();
    } catch (e: unknown) {
      setError(parseCodedError(e)?.description ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  function save() {
    const m = months.trim();
    void send({
      account: props.account,
      annual_rate_pct: textOrNull(rate),
      term_months: m === "" ? null : Number(m),
      original_principal: textOrNull(principal),
      label: textOrNull(label),
    });
  }

  function clear() {
    void send({
      account: props.account,
      annual_rate_pct: null,
      term_months: null,
      original_principal: null,
      label: textOrNull(label),
    });
  }

  return (
    <form
      className="loan-terms-form"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      {error && <div className="callout warn">{error}</div>}
      <div className="loan-terms-fields">
        <div className="field">
          <label htmlFor="lt-rate">Annual rate %</label>
          <input
            id="lt-rate"
            inputMode="decimal"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="lt-months">Term (months)</label>
          <input
            id="lt-months"
            type="number"
            min={1}
            max={600}
            value={months}
            onChange={(e) => setMonths(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="lt-principal">Original principal (optional)</label>
          <input
            id="lt-principal"
            inputMode="decimal"
            value={principal}
            onChange={(e) => setPrincipal(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="lt-label">Label (optional)</label>
          <input
            id="lt-label"
            maxLength={60}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </div>
      </div>
      <div className="loan-terms-actions">
        <button type="submit" className="btn sm" disabled={busy}>
          Save terms
        </button>
        {props.ownTerms && (
          <button type="button" className="btn sm" disabled={busy} onClick={clear}>
            Clear my terms
          </button>
        )}
      </div>
    </form>
  );
}
