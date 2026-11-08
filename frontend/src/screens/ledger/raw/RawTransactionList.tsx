import { type JSX } from "react";

export type RawPosting = {
  account: string;
  amount: string;
  tags: string[];
  meta: Record<string, string>;
};

export type RawTransaction = {
  txn_id: string;
  date: string;
  narration: string;
  year_file_hash: string;
  postings: RawPosting[];
};

/** Read-only raw transaction list for one fiscal year. Mirrors the import-review
 *  raw look: one `.data-row` per transaction with a `.data-label` header
 *  (`{date} · {narration}`) and each posting as `{account}` + a right-aligned
 *  `.data-val` amount string (verbatim). No edit/annotate affordances. */
export function RawTransactionList({
  transactions,
}: {
  transactions: RawTransaction[];
}): JSX.Element {
  if (transactions.length === 0) {
    return <p>No transactions for this year.</p>;
  }
  return (
    <>
      {transactions.map((t) => (
        <div key={t.txn_id} className="data-row" style={{ display: "block" }}>
          <span className="data-label">
            {t.date} · {t.narration}
          </span>
          {t.postings.map((p) => (
            <div
              key={`${t.txn_id}::${p.account}`}
              style={{ display: "flex", justifyContent: "space-between" }}
            >
              <span>{p.account}</span>
              <span className="data-val">{p.amount}</span>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
