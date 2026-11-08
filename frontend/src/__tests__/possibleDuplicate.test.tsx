import { render, screen, waitFor } from "@testing-library/react";
import { describe, test, expect, vi, afterEach } from "vitest";
import { StatementReview, possibleDuplicateFlag } from "@/screens/ledger/import/StatementReview";
import { InvestmentReview, type WalletLeg } from "@/screens/ledger/import/InvestmentReview";

// A row identical to one already in the journal, beyond the number the journal
// records, is imported as NEW (a second same-day charge is usually real) and
// flagged. The flag is the only thing between that and a silent double-book,
// so the review screens must show it -- and must stay quiet on a clean row.

const FLAG =
  "possible duplicate: 2 identical rows in this import, 1 already recorded; " +
  "reject this row unless it is a separate transaction";

const ROW = {
  id: "c1",
  date: "2026-09-04",
  raw_narration: "CAFE COFFEE DAY",
  suggested_category: "Expenses:Food",
  amount: "120.00",
  direction: "debit",
  dedup_status: "new",
  needs_account: false,
  source_account: "Liabilities:CreditCard:Test",
  flags: [] as string[],
};

afterEach(() => vi.restoreAllMocks());

function renderStatement(rows: (typeof ROW)[]) {
  vi.spyOn(global, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
  return render(
    <StatementReview
      batch="b1"
      templateFingerprint={null}
      rows={rows}
      previewCandidates={[]}
      accounts={[]}
      showTemplateLink={false}
      onBackToMap={vi.fn()}
      onCommit={vi.fn()}
      onRowsChange={vi.fn()}
    />,
  );
}

describe("possible-duplicate flag", () => {
  test("possibleDuplicateFlag finds only the dedup flag", () => {
    expect(possibleDuplicateFlag({ flags: ["UPI-Lite top-up — expensed on load", FLAG] })).toBe(FLAG);
    expect(possibleDuplicateFlag({ flags: ["exact duplicate of an existing transaction"] })).toBeUndefined();
    expect(possibleDuplicateFlag({})).toBeUndefined();
  });

  test("statement review warns about a flagged twin and names it", () => {
    renderStatement([
      { ...ROW, dedup_status: "exact_dup", flags: ["exact duplicate of an existing transaction"] },
      { ...ROW, flags: [FLAG] },
    ]);
    expect(screen.getByText(/one row matches/i)).toBeInTheDocument();
    expect(screen.getByText(/will be imported as new/i)).toBeInTheDocument();
    expect(screen.getByText(/1 already recorded/)).toBeInTheDocument();
  });

  test("statement review stays quiet without the flag", () => {
    renderStatement([ROW]);
    expect(screen.queryByText(/already in the journal/i)).toBeNull();
  });

  test("investment review warns on a flagged order", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ balance: "100000" }), { status: 200 }),
    );
    const leg: WalletLeg = {
      kind: "buy",
      candidateId: "m1",
      date: "2026-03-18",
      narration: "Purchase — Example Flexi Cap",
      amount: "1000",
      commodity: "MF100001",
      units: "10.0",
      unitCost: "100.0",
      possibleDuplicate: FLAG,
    };
    render(<InvestmentReview batchId="b1" legs={[leg, { ...leg, candidateId: "m2", possibleDuplicate: null }]} onCommit={vi.fn()} onResolved={vi.fn()} />);
    await waitFor(() => expect(screen.getAllByText(/identical to one already in the journal/i)).toHaveLength(1));
  });
});
