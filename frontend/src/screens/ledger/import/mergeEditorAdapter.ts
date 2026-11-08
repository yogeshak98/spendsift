import type { CandidateMeta } from "./LedgerReviewEditor";
import type { ParsedEntry, Posting } from "./ledgerEntries";

// A merge transaction as surfaced by POST /api/import/ledger/preview.
// Mirrors LedgerFileMerge's local MergeTxn / MergePosting types.
export type MergePosting = {
  account: string;
  amount: string | null;
  commodity: string | null;
  cost?: string | null;
  cost_currency?: string | null;
};

export type MergeTxn = {
  id: string;
  date: string;
  narration: string;
  postings: MergePosting[];
  duplicate: boolean;
  unbookable_sell: boolean;
  // Present once a source transaction is split into a main-journal leg and an
  // investments-journal leg (e.g. an MF trade); both halves share the same
  // `id` since they represent one logical transaction.
  target?: "main" | "investment";
};

// The INR cash amount that keys the candidate row's balance hint. We take the
// first plain-INR (or uncommodified) leg's magnitude; when every leg is elided
// or non-INR-priced we fall back to "0" — the checkbox is the point here, not a
// precise balance (the server re-derives everything on re-preview).
function inrLegAmount(txn: MergeTxn): string {
  for (const p of txn.postings) {
    if (p.amount == null || p.amount === "") continue;
    if (p.commodity == null || p.commodity === "INR") {
      const n = Number(p.amount);
      return Number.isNaN(n) ? "0" : Math.abs(n).toFixed(2);
    }
  }
  return "0";
}

// Map a merge batch's transactions onto the shared ledger editor's review-mode
// inputs: one ParsedEntry per txn (so the text renders) and one CandidateMeta
// per txn keyed by txn.id (so the gutter checkbox renders). Status semantics are
// benign — matchSource "unknown", needsAccount false — because editing here is
// free-text and the server re-checks balances/mappings on re-preview.
export function toEditorState(txns: MergeTxn[]): {
  entries: ParsedEntry[];
  candidates: CandidateMeta[];
} {
  const entries: ParsedEntry[] = txns.map((t) => ({
    date: t.date.replace(/-/g, "/"),
    // A split MF trade renders as two adjacent text blocks sharing one id;
    // annotate the investments-journal half so it reads as an intentional
    // pair, not an accidental duplicate.
    narration: t.target === "investment" ? `${t.narration} [→ investments.beancount]` : t.narration,
    ref: null,
    postings: t.postings.map((p) => {
      // Elided legs carry no amount in the ledger text (beancount balances them).
      const posting: Posting = {
        account: p.account,
        amount: p.amount == null || p.amount === "" ? "" : p.amount,
      };
      // Preserve MF unit symbol + cost basis so the seeded text is a faithful
      // ledger lot (`742.61 ACMEELSS @ 83.48 INR`), not a lossy `742.61 INR`.
      if (p.commodity && p.commodity !== "INR") posting.commodity = p.commodity;
      if (p.cost) {
        posting.cost = p.cost;
        posting.costCurrency = p.cost_currency ?? "INR";
      }
      return posting;
    }),
    tags: [],
  }));
  const candidates: CandidateMeta[] = txns.map((t) => ({
    id: t.id,
    amount: inrLegAmount(t),
    matchSource: "unknown",
    needsAccount: false,
    rawSource: t.narration,
  }));
  return { entries, candidates };
}
