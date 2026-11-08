import { useState } from "react";
import { Callout } from "@/components/Callout";
import { OpeningForm } from "@/components/OpeningForm";
import { ZeroMarkConfirm } from "@/components/ZeroMarkConfirm";
import type { OpeningPromptItem, OpeningPromptResponse } from "@/openings";

export type OpeningResolution = "recorded" | "zeroed" | "skipped";

type Props = {
  batchId: string;
  prompt: OpeningPromptResponse;
  /** One resolution per prompt account; owned by the wizard so it survives step navigation. */
  resolutions: Record<string, OpeningResolution>;
  onResolve: (account: string, how: OpeningResolution) => void;
  onContinue: () => void;
  /** True while the commit started by Continue is in flight. */
  continueBusy?: boolean;
};

const NO_SUGGESTION = "This statement does not print an opening balance. Enter it from your previous statement, or skip.";

function ItemCard(props: {
  batchId: string;
  item: OpeningPromptItem;
  resolution: OpeningResolution | undefined;
  onResolve: (how: OpeningResolution) => void;
}) {
  const { item, resolution } = props;
  const [confirmingZero, setConfirmingZero] = useState(false);
  const first = item.suggestion;
  const initial = {
    amount: first?.amount ?? "",
    direction: first?.direction ?? item.directions[0],
    date: item.suggested_date,
  };

  return (
    <section className="opening-item">
      <h3>{item.account_display}: first import</h3>
      {!first && <p>{NO_SUGGESTION}</p>}
      {item.note && <Callout kind="info">{item.note}</Callout>}
      {item.mismatch && (
        <Callout kind="warn">The printed opening and the one worked out from the rows differ. Check both before recording.</Callout>
      )}
      {resolution ? (
        <p>
          {resolution === "recorded" && "Opening balance recorded."}
          {resolution === "zeroed" && "Marked as started at zero."}
          {resolution === "skipped" && "Skipped for now. You can add it later in Settings · Opening balances."}
        </p>
      ) : null}
      {resolution !== "zeroed" && resolution !== "skipped" && (
        <OpeningForm
          key={item.account}
          mode="create"
          batchId={props.batchId}
          account={item.account}
          accountDisplay={item.account_display}
          kind={item.kind}
          directions={item.directions}
          initial={initial}
          arithmetic={first?.arithmetic ?? null}
          aprilAdvice={item.april_advice}
          alternatives={item.alternatives}
          onWritten={() => props.onResolve("recorded")}
        />
      )}
      {!resolution && confirmingZero && (
        <ZeroMarkConfirm
          account={item.account}
          zero
          onDone={() => props.onResolve("zeroed")}
          onCancel={() => setConfirmingZero(false)}
        />
      )}
      {!resolution && !confirmingZero && (
        <div className="import-template-actions">
          <button type="button" className="btn sm" onClick={() => setConfirmingZero(true)}>
            Started at zero
          </button>
          <button type="button" className="btn sm" onClick={() => props.onResolve("skipped")}>
            Skip for now
          </button>
        </div>
      )}
    </section>
  );
}

/** Import wizard step between Review and Commit: asks for the opening balance
 *  of each account this batch is the first import into. Nothing here blocks the
 *  commit: every account can be recorded, marked zero, or skipped. */
export function OpeningStep({ batchId, prompt, resolutions, onResolve, onContinue, continueBusy = false }: Props) {
  const done = prompt.prompts.every((p) => resolutions[p.account] !== undefined);
  return (
    <div className="opening-step">
      {prompt.prompts.map((item) => (
        <ItemCard
          key={item.account}
          batchId={batchId}
          item={item}
          resolution={resolutions[item.account]}
          onResolve={(how) => onResolve(item.account, how)}
        />
      ))}
      <button type="button" className="primary" disabled={!done || continueBusy} onClick={onContinue}>
        Continue to commit
      </button>
    </div>
  );
}
