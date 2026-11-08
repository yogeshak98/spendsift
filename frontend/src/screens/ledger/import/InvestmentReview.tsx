import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CHECKLIST_PATH } from "../../../openings";
import { Callout } from "../../../components/Callout";
import { api } from "@/shared/api/http";
import { SchemeResolver } from "./SchemeResolver";

export type WalletLeg = {
  kind: "buy" | "refund";
  candidateId: string;
  date: string;
  narration: string;
  amount: string;
  commodity: string | null;
  units: string | null;
  unitCost: string | null;
  unresolvedScheme?: string | null;
  // Soft review flag, computed at PREVIEW time (import_service.detect) by the
  // same helper the commit path uses. The commit no longer refuses an
  // over-tolerance charge gap -- it books the remainder to a named account --
  // so this warning is the only thing standing between "never blocks" and a
  // silent import. `unexplainedChargeAmount` is how much; it can be null if
  // only the flag arrived.
  unexplainedCharge?: boolean;
  unexplainedChargeAmount?: string | null;
  // The backend's "possible duplicate: ..." flag: this row is identical to
  // an order already in the journal, but the file has more such rows than the
  // journal records, so it posts as new. Said here so it is never silent.
  possibleDuplicate?: string | null;
  // The backend's MF opening-lots flag: this scheme already has opening lots,
  // so the order may be counted in them. Soft -- it never blocks the commit.
  openingLots?: boolean;
};

export type CommitResult =
  | { ok: true }
  | { ok: false; code?: string; description?: string; resolution?: string };

export type InvestmentReviewProps = {
  batchId: string;
  legs: WalletLeg[];
  onCommit: () => Promise<CommitResult>;
  // Called when a row's identification hold is cleared, so the caller can drop
  // the flag from its own candidate list. Without it the row would stay
  // visibly blocked after the server had already accepted the resolution.
  onResolved: (candidateId: string, commodity: string, schemeName: string) => void;
};

function balChipClass(value: number): string {
  if (value < 0) return "neg";
  return "zero";
}

export function InvestmentReview(props: InvestmentReviewProps) {
  const { legs, onCommit } = props;
  const [seedBalance, setSeedBalance] = useState("0");
  const [commitResult, setCommitResult] = useState<CommitResult | null>(null);
  const [posting, setPosting] = useState(false);

  useEffect(() => {
    api
      .getJSON<{ balance: string }>("/api/import/investment/cash-balance")
      .then((res) => setSeedBalance(res.balance))
      .catch(() => setSeedBalance("0"));
  }, []);

  // Compute running balances after each leg
  const runningBalances: string[] = [];
  let running = parseFloat(seedBalance);
  for (const leg of legs) {
    const amt = parseFloat(leg.amount);
    if (leg.kind === "buy") {
      running -= amt;
    } else {
      running += amt;
    }
    runningBalances.push(running.toFixed(2));
  }

  const anyNegative = runningBalances.some((v) => parseFloat(v) < 0);

  async function handlePost() {
    setPosting(true);
    try {
      const result = await onCommit();
      setCommitResult(result);
    } finally {
      setPosting(false);
    }
  }

  return (
    <div>
      {anyNegative && (
        <Callout kind="warn">
          Wallet balance is negative — import the bank &quot;add money&quot; transfer from your bank
          statement.
        </Callout>
      )}
      <div>
        {legs.map((leg, index) => (
          <div className="flow-step" key={leg.candidateId}>
            <div className="fn">{index + 1}</div>
            <div className="ft">
              <div className="lab">{leg.kind === "buy" ? "BUY" : "REFUND"}</div>
              <div className="sub">
                {leg.date} · {leg.narration}
              </div>
              <div className="sub">
                {leg.units && leg.commodity
                  ? `${leg.units} units @ ₹${leg.unitCost} ${leg.commodity}`
                  : `₹${leg.amount}`}
              </div>
              {/* Name the row the commit will refuse over, and let the user fix
                  it there. E1012 lists candidate ids, which mean nothing on
                  screen; naming the row was C5c, and carrying the ACTION -- not
                  just the diagnosis -- is C5d. */}
              {/* NOT an error, and NOT a statutory charge -- it is the part
                  of this trade's cash that units x NAV and the known rates
                  together cannot account for. It commits either way, so the
                  warning has to arrive BEFORE the Post button, not after. */}
              {leg.unexplainedCharge && (
                <Callout kind="warn">
                  {leg.unexplainedChargeAmount
                    ? `₹${leg.unexplainedChargeAmount} of this trade's cash doesn't match units × NAV, and no charge we know explains it. `
                    : "Part of this trade's cash doesn't match units × NAV, and no charge we know explains it. "}
                  It will still be posted — to{" "}
                  <b>Expenses:Investment:Charges:Unexplained</b>, so the money stays visible
                  and nothing is dropped. Worth checking this row against your Groww statement
                  before you post.
                </Callout>
              )}
              {leg.possibleDuplicate && (
                <Callout kind="warn">
                  This order is identical to one already in the journal, and the file has
                  more of them than the journal records. It will be posted as a new order — check
                  it against your Groww statement first ({leg.possibleDuplicate}).
                </Callout>
              )}
              {leg.openingLots && (
                <Callout kind="warn">
                  This scheme has opening lots. Check this order is not already counted in
                  them before committing. <Link to={CHECKLIST_PATH}>Opening balances</Link>
                </Callout>
              )}
              {leg.unresolvedScheme && (
                <SchemeResolver
                  batchId={props.batchId}
                  candidateId={leg.candidateId}
                  unresolvedScheme={leg.unresolvedScheme}
                  commodity={leg.commodity}
                  narration={leg.narration}
                  onResolved={props.onResolved}
                />
              )}
              <span className={["bal-chip", balChipClass(parseFloat(runningBalances[index]))].join(" ")}>
                Groww Wallet ₹{runningBalances[index]}
              </span>
            </div>
          </div>
        ))}
      </div>
      {/* EVERY refusal is shown, whatever its code.
          An investment commit can be refused by at least seven distinct codes
          (E1010 unresolved accounts, E1012 unresolved schemes, E1013 uncovered
          redemptions, E1043 unreconciled lots, E1044 unbookable lots, E1045
          restated order, E1046 the order-id backfill gate). Only E1024 was
          ever rendered, so every other refusal left the user pressing Post and
          watching nothing happen -- the worst possible outcome on a screen
          whose entire job is to decide what gets written. E1024 itself is gone
          from this list: P5 stopped the charge gap refusing a commit at all,
          so the backend can no longer return it. Nothing is committed in any
          of these cases; the batch is intact and still in review. */}
      {commitResult && !commitResult.ok && (
        <Callout kind="bad">
          {commitResult.code ? (
            <>
              <b>{commitResult.code}</b> — {commitResult.description}
              {commitResult.resolution && <div>{commitResult.resolution}</div>}
            </>
          ) : (
            // No code came back at all: the request itself failed, or the body
            // was not the coded-error shape. Say exactly that rather than
            // inventing a reason.
            <>Could not post this batch, and the reason did not come back. Nothing was
            committed — the batch is unchanged and still in review. Check that the backend is
            running, then try again.</>
          )}
        </Callout>
      )}
      <button type="button" onClick={handlePost} disabled={posting}>
        Post
      </button>
    </div>
  );
}
