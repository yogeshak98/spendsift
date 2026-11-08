import { useState } from "react";
import { api } from "@/shared/api/http";
import { parseCodedError } from "@/shared/api/codedError";
import { triggerReload } from "@/shared/api/useReport";
import { Callout } from "./Callout";
import {
  DIRECTION_LABEL,
  OPENINGS_URL,
  type OpeningDirection,
  type OpeningKind,
  type OpeningSuggestion,
  type OpeningWriteResponse,
} from "@/openings";

type OpeningFormCommon = {
  account: string;
  accountDisplay: string;
  kind: OpeningKind;
  directions: OpeningDirection[];
  initial: { amount: string; direction: OpeningDirection; date: string };
  arithmetic?: string | null;
  aprilAdvice?: string | null;
  alternatives?: OpeningSuggestion[];
  batchId?: string;
  onWritten: (res: OpeningWriteResponse) => void;
};

export type OpeningFormProps = OpeningFormCommon &
  (
    | { mode: "create"; expected?: undefined }
    | { mode: "correct"; expected: { date: string; ledger_amount: string } }
  );

function directionLabel(kind: OpeningKind, d: OpeningDirection): string {
  return kind === "loan" && d === "owe" ? "Outstanding principal (I owe)" : DIRECTION_LABEL[d];
}

/** Approval form for one opening balance. Nothing is written until the user
 *  ticks the confirmation; the server snapshots the journal before writing.
 *  Any edit clears the tick. State is seeded from `initial` once: a parent that
 *  reuses this form for another account must pass `key={account}`. */
export function OpeningForm(props: OpeningFormProps) {
  const [amount, setAmount] = useState(props.initial.amount);
  const [direction, setDirection] = useState<OpeningDirection>(props.initial.direction);
  const [date, setDate] = useState(props.initial.date);
  const [balanceCheck, setBalanceCheck] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<{ description: string; resolution: string | null } | null>(null);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const id = `opening-${props.account}`;
  // Any change to what would be sent invalidates the user's approval.
  const edit = (fn: () => void) => {
    fn();
    setConfirmed(false);
  };

  async function write() {
    setError(null);
    setBusy(true);
    try {
      let res: OpeningWriteResponse;
      if (props.mode === "correct") {
        res = await api.putJSON<OpeningWriteResponse>(OPENINGS_URL, {
          account: props.account,
          amount: amount.trim(),
          direction,
          date,
          balance_check: balanceCheck,
          expected: props.expected,
        });
      } else {
        res = await api.postJSON<OpeningWriteResponse>(OPENINGS_URL, {
          account: props.account,
          amount: amount.trim(),
          direction,
          date,
          ...(props.batchId ? { batch_id: props.batchId } : {}),
          balance_check: balanceCheck,
        });
      }
      setSnapshot(res.snapshot);
      triggerReload();
      props.onWritten(res);
    } catch (e: unknown) {
      const coded = parseCodedError(e);
      setError(coded ?? { description: String(e), resolution: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="opening-form"
      onSubmit={(e) => {
        e.preventDefault();
        void write();
      }}
    >
      <div>
        <strong>{props.accountDisplay}</strong>
      </div>
      {props.aprilAdvice && <Callout kind="info">{props.aprilAdvice}</Callout>}
      {props.arithmetic && <pre style={{ fontFamily: "var(--mono, monospace)" }}>{props.arithmetic}</pre>}
      {(props.alternatives ?? []).map((alt, i) => (
        <button
          key={i}
          type="button"
          className="btn sm"
          onClick={() => {
            edit(() => {
              setAmount(alt.amount);
              setDirection(alt.direction);
            });
          }}
        >
          Use {alt.amount} ({DIRECTION_LABEL[alt.direction]}): {alt.arithmetic}
        </button>
      ))}
      <div className="field">
        <label htmlFor={`${id}-amount`}>Amount</label>
        <input
          id={`${id}-amount`}
          inputMode="decimal"
          value={amount}
          onChange={(e) => edit(() => setAmount(e.target.value))}
        />
      </div>
      <fieldset>
        <legend>Direction</legend>
        {props.directions.map((d) => (
          <label key={d}>
            <input
              type="radio"
              name={`${id}-direction`}
              checked={direction === d}
              onChange={() => edit(() => setDirection(d))}
            />
            {directionLabel(props.kind, d)}
          </label>
        ))}
      </fieldset>
      <div className="field">
        <label htmlFor={`${id}-date`}>Date</label>
        <input id={`${id}-date`} type="date" value={date} onChange={(e) => edit(() => setDate(e.target.value))} />
      </div>
      <label>
        <input type="checkbox" checked={balanceCheck} onChange={(e) => setBalanceCheck(e.target.checked)} />
        Also add a balance check
      </label>
      <label>
        <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
        I&apos;ve checked this amount and date
      </label>
      {error && (
        <Callout kind="bad">
          {error.description}
          {error.resolution && <> {error.resolution}</>}
        </Callout>
      )}
      {snapshot && <div>Recorded. Restore point: {snapshot}</div>}
      <button type="submit" className="btn sm" disabled={!confirmed || amount.trim() === "" || busy || snapshot !== null}>
        {props.mode === "correct" ? "Save correction" : "Record opening"}
      </button>
    </form>
  );
}
