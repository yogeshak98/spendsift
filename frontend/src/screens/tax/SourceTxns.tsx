import { useEffect, useState, type JSX } from "react";
import { api } from "@/shared/api/http";
import { inrWhole } from "@/shared/format/money";
import "./tax.css";

type Posting = { account: string; amount: string };
type TxnDetail = { txn_id: string; date: string; narration: string; postings: Posting[] };

/** Expanded drill-down for a tax item's `source_refs`: fetches each transaction
 * by id and lists its date + narration, so every aggregate figure is traceable
 * to the journal entries behind it. Rendered inside a colspan detail row. */
export function SourceTxns({ refs }: { refs: string[] }): JSX.Element {
  // Keyed by the refs it answers, so a new `refs` reads as loading again.
  const [result, setResult] = useState<{ refs: string[]; txns: TxnDetail[] | null; error: boolean } | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all(refs.map((id) => api.getJSON<TxnDetail>(`/api/transactions/${id}`)))
      .then((rows) => {
        if (!cancelled) setResult({ refs, txns: rows, error: false });
      })
      .catch(() => {
        if (!cancelled) setResult({ refs, txns: null, error: true });
      });
    return () => {
      cancelled = true;
    };
  }, [refs]);

  const current = result?.refs === refs ? result : null;
  const txns = current?.txns ?? null;
  const error = current?.error ?? false;

  if (error) return <div className="note">Couldn't load source transactions.</div>;
  if (!txns) return <div className="note">Loading…</div>;
  if (txns.length === 0) return <div className="note">No source transactions.</div>;

  return (
    <table className="srcref">
      <tbody>
        {txns.map((t) => (
          <tr key={t.txn_id}>
            <td className="srcref-date">{t.date}</td>
            <td className="srcref-narr">{t.narration}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** One tax-item row (label · amount · source-txn count) where the count is a
 * toggle that expands the source transactions in a detail row below. Shared by
 * the Income / Capital Gains / Prepaid tables (all three columns: label, amount,
 * source txns). */
export function TaxItemRow({
  label,
  amount,
  refs,
  note,
  rowClass,
}: {
  label: string;
  amount: string;
  refs: string[];
  /** Optional class on the row itself, for the Income screen's treatment
   * groups: the rows under a group heading carry a left rule so the heading
   * visibly owns them. Presentation only -- it changes no figure. */
  rowClass?: string;
  /** Optional one-line caveat about this row's amount, shown under the label.
   * Exists for the case where the row's own figure is deliberately NOT the one
   * the totals use (declared gross salary overriding the ledger net), so the
   * visible numbers not adding up is explained where the gap is, not only in
   * the provenance line under the total. */
  note?: string;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const hasRefs = refs.length > 0;
  return (
    <>
      <tr className={rowClass}>
        <td className="label">
          {label}
          {note !== undefined && <div className="row-note">{note}</div>}
        </td>
        <td className="num mono">{inrWhole(amount)}</td>
        <td className="num">
          <button
            type="button"
            className="refs-toggle"
            disabled={!hasRefs}
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            {refs.length}
            {hasRefs && <span className="cv">{open ? "▾" : "▸"}</span>}
          </button>
        </td>
      </tr>
      {open && hasRefs && (
        <tr>
          <td className="srcref-cell" colSpan={3}>
            <SourceTxns refs={refs} />
          </td>
        </tr>
      )}
    </>
  );
}
