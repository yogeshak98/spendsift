import { useRef, useState } from "react";
import { api } from "@/shared/api/http";
import { parseCodedError } from "@/shared/api/codedError";
import { triggerReload } from "@/shared/api/useReport";
import { Callout } from "./Callout";
import type { OpeningZeroResponse } from "@/openings";

const TEXT = {
  mark: {
    tick: "I've checked: this account started at zero",
    button: "Mark started at zero",
  },
  undo: {
    tick: "I've checked: remove the started-at-zero mark",
    button: "Remove the mark",
  },
} as const;

/** Approval for the started-at-zero mark (`zero`) or its removal. Like the
 *  opening form, nothing is written until the user ticks the confirmation and
 *  presses the button; the server snapshots the journal before writing. One
 *  write at a time: a second click while one is in flight does nothing. */
export function ZeroMarkConfirm(props: {
  account: string;
  zero: boolean;
  onDone: (res: OpeningZeroResponse) => void;
  onCancel: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const text = props.zero ? TEXT.mark : TEXT.undo;

  async function send() {
    if (!confirmed || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const res = await api.postJSON<OpeningZeroResponse>("/api/openings/zero", {
        account: props.account,
        zero: props.zero,
      });
      triggerReload();
      props.onDone(res);
    } catch (e: unknown) {
      const coded = parseCodedError(e);
      setError(coded ? [coded.description, coded.resolution ?? ""].filter(Boolean).join(" ") : String(e));
      inFlight.current = false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="opening-zero-confirm">
      <label>
        <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
        {text.tick}
      </label>
      {error && <Callout kind="bad">{error}</Callout>}
      <div className="import-template-actions">
        <button type="button" className="btn sm" disabled={!confirmed || busy} onClick={() => void send()}>
          {text.button}
        </button>
        <button type="button" className="btn sm" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
