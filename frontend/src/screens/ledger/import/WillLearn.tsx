import type { components } from "@/shared/api/schema";
import type { Candidate } from "./StatementReview";

// The learning preview's shapes come from the backend's OpenAPI schema
// (`npm run gen:api`), so a field the route returns cannot drift from here.
export type WillLearnItem = components["schemas"]["WillLearnItem"];
export type NotLearnedItem = components["schemas"]["NotLearnedItem"];
export type ReviewLearning = components["schemas"]["ReviewLearningResponse"];
export const REASON_VETO = "you said don't learn this";

export type WillLearnProps = {
  learning: ReviewLearning;
  rows: Candidate[];
  onSetLearn: (candidateIds: string[], learn: boolean) => Promise<void>;
};

/** What committing this batch will teach, with a per-pattern "don't learn" veto. */
export function WillLearn({ learning, rows, onSetLearn }: WillLearnProps) {
  const { will_learn, not_learned, would_open } = learning;
  if (will_learn.length === 0 && not_learned.length === 0 && would_open.length === 0) return null;
  // Candidate ids are content-derived, so twin rows share one: the FIRST row wins.
  const narration = (id: string) => rows.find((r) => r.id === id)?.raw_narration;
  return (
    <div className="will-learn">
      {will_learn.length > 0 && (
        <>
          <div className="note">Committing will teach these rules:</div>
          <ul>
            {will_learn.map((w) => (
              <li key={w.pattern}>
                <b>{w.pattern}</b> → {w.category}
                {w.replaces && <span className="note"> (was {w.replaces})</span>}{" "}
                {w.warning && (
                  <div className="note warning" role="alert">
                    {w.warning}
                  </div>
                )}
                <label>
                  <input
                    type="checkbox"
                    aria-label={`don't learn ${w.pattern}`}
                    checked={false}
                    onChange={() => void onSetLearn(w.candidate_ids, false)}
                  />{" "}
                  don't learn
                </label>
              </li>
            ))}
          </ul>
        </>
      )}
      {not_learned.length > 0 && (
        <ul>
          {not_learned.map((n, i) => (
            // Twin rows share a content-derived id, so the index keeps keys unique.
            <li key={`${n.candidate_id}:${i}`}>
              <span className="note">
                not learned: {n.pattern || narration(n.candidate_id) || "(no merchant words)"} — {n.reason}
              </span>
              {n.reason === REASON_VETO && (
                <>
                  {" "}
                  <button type="button" className="link-button" onClick={() => void onSetLearn([n.candidate_id], true)}>
                    learn it
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {would_open.length > 0 && <div className="note">New accounts: {would_open.join(", ")}</div>}
    </div>
  );
}

export type LearnedRule = { pattern: string; category: string; replaces: string | null };
export type CommitResult = {
  committed: number;
  uncategorized: number;
  unexplained: number;
  statement_recorded: boolean;
  learned: LearnedRule[];
  opened: string[];
};
