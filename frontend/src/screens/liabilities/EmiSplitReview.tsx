import { useState } from "react";
import { api } from "@/shared/api/http";
import { parseCodedError } from "@/shared/api/codedError";
import { triggerReload, useReport } from "@/shared/api/useReport";

// GET /api/liabilities/emi-plans/proposal (EmiSplitProposalResponse).
type PostingRef = { txn_id: string; date: string; amount: string };
type InstalmentRef = PostingRef & { seq: number | null };
type PlanProposal = {
  account: string;
  card: string;
  ref: string | null;
  conversion: PostingRef | null;
  instalments: InstalmentRef[];
  principal: string | null;
  repaid: string;
  outstanding: string | null;
  fully_repaid: boolean;
  label_hint: string | null;
  confident: boolean;
  reasons: string[];
};
type Unassigned = {
  txn_id: string;
  date: string;
  narration: string;
  amount: string;
  reason: string;
};
type Proposal = { parent: string; plans: PlanProposal[]; unassigned: Unassigned[] };

// POST /api/liabilities/emi-plans/split (EmiSplitRequest / EmiSplitResponse).
type SplitPlan = {
  account: string;
  card: string | null;
  ref: string | null;
  label: string | null;
  txn_ids: string[];
  close: boolean;
};
type SplitResponse = { moved: number; opened: string[]; plans: string[]; closed: string[] };

type Edit = { leaf: string; label: string; close: boolean };

const leafOf = (account: string) => account.slice(account.lastIndexOf(":") + 1);

// What the review starts from: the proposed name, the purchase narration as
// the label, and "will close" ticked for a fully repaid plan.
function defaultEdit(plan: PlanProposal): Edit {
  return { leaf: leafOf(plan.account), label: plan.label_hint ?? "", close: plan.fully_repaid };
}

function txnIds(plan: PlanProposal): string[] {
  return [
    ...(plan.conversion ? [plan.conversion.txn_id] : []),
    ...plan.instalments.map((i) => i.txn_id),
  ];
}

/** Review and apply the split of the lumped card EMI account into one
 *  sub-account per plan. Nothing is written until "Apply split". */
export function EmiSplitReview(props: { onApplied?: () => void }) {
  const { data, error: loadError } = useReport<Proposal>("/api/liabilities/emi-plans/proposal");
  // Only what the user changed; everything else reads from the proposal.
  const [edits, setEdits] = useState<Record<string, Partial<Edit>>>({});
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<SplitResponse | null>(null);

  if (loadError) return <div className="callout warn">{loadError}</div>;
  if (!data) return <div style={{ padding: "0.9231rem" }}>Loading proposal…</div>;
  if (data.plans.length === 0 && data.unassigned.length === 0) {
    return <div style={{ padding: "0.9231rem" }}>Nothing to split.</div>;
  }

  const editOf = (p: PlanProposal): Edit => ({ ...defaultEdit(p), ...edits[p.account] });
  const update = (account: string, patch: Partial<Edit>) =>
    setEdits((prev) => ({ ...prev, [account]: { ...prev[account], ...patch } }));

  const moving = data.plans.reduce((n, p) => n + txnIds(p).length, 0);
  const closing = data.plans.filter((p) => p.fully_repaid && editOf(p).close).length;

  async function apply() {
    if (!data) return;
    setError(null);
    setBusy(true);
    const plans: SplitPlan[] = data.plans.map((p) => {
      const edit = editOf(p);
      const label = edit.label.trim();
      return {
        account: `${data.parent}:${edit.leaf.trim()}`,
        card: p.card,
        ref: p.ref,
        label: label === "" ? null : label,
        txn_ids: txnIds(p),
        close: p.fully_repaid && edit.close,
      };
    });
    try {
      const res = await api.postJSON<SplitResponse>("/api/liabilities/emi-plans/split", {
        plans,
      });
      setDone(res);
      triggerReload();
      props.onApplied?.();
    } catch (e: unknown) {
      setError(parseCodedError(e)?.description ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="callout info">
        Moved {done.moved} postings into {done.plans.length} plan accounts
        {done.closed.length > 0 ? `; closed ${done.closed.length} repaid plans` : ""}.
      </div>
    );
  }

  return (
    <div className="emi-split-review">
      {error && <div className="callout warn">{error}</div>}
      {data.plans.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Plan account</th>
              <th>Label</th>
              <th>Card</th>
              <th className="num">Principal</th>
              <th className="num">Instalments</th>
              <th className="num">Outstanding</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {data.plans.map((p) => {
              const leaf = leafOf(p.account);
              const edit = editOf(p);
              return (
                <tr key={p.account}>
                  <td>
                    <input
                      aria-label={`${leaf} account`}
                      value={edit.leaf}
                      onChange={(e) => update(p.account, { leaf: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      aria-label={`${leaf} label`}
                      maxLength={60}
                      value={edit.label}
                      onChange={(e) => update(p.account, { label: e.target.value })}
                    />
                  </td>
                  <td>{leafOf(p.card)}</td>
                  <td className="num">{p.principal ?? "—"}</td>
                  <td className="num">{p.instalments.length}</td>
                  <td className="num">{p.outstanding ?? "—"}</td>
                  <td>
                    {!p.confident && (
                      <span className="pill unk" title={p.reasons.join("; ")}>
                        Check
                      </span>
                    )}
                    {p.fully_repaid && (
                      <label className="emi-close">
                        <input
                          type="checkbox"
                          aria-label={`${leaf} will close`}
                          checked={edit.close}
                          onChange={(e) => update(p.account, { close: e.target.checked })}
                        />{" "}
                        will close
                      </label>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {data.unassigned.length > 0 && (
        <>
          <div className="emi-split-sub">Not moved (no plan could take them)</div>
          <table className="data-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Narration</th>
                <th className="num">Amount</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {data.unassigned.map((u) => (
                <tr key={u.txn_id}>
                  <td>{u.date}</td>
                  <td>{u.narration}</td>
                  <td className="num">{u.amount}</td>
                  <td>{u.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {data.plans.length > 0 && (
        <div className="emi-split-confirm">
          <p>
            Moves {moving} postings into {data.plans.length} plan accounts. A journal snapshot is
            taken first; nothing else changes.
            {closing > 0 ? ` ${closing} fully repaid plans will be closed.` : ""}
          </p>
          <label>
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(e) => setReviewed(e.target.checked)}
            />{" "}
            I've reviewed these plans
          </label>{" "}
          <button
            type="button"
            className="btn sm"
            disabled={!reviewed || busy}
            onClick={() => void apply()}
          >
            Apply split
          </button>
        </div>
      )}
    </div>
  );
}
