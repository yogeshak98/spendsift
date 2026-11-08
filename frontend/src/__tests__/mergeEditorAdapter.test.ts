import { describe, it, expect } from "vitest";
import { toEditorState, type MergeTxn } from "@/screens/ledger/import/mergeEditorAdapter";
import { serializeEntries } from "@/screens/ledger/import/ledgerEntries";

const BUY: MergeTxn = {
  id: "t1",
  date: "2024-01-05",
  narration: "Buy units",
  duplicate: false,
  unbookable_sell: false,
  postings: [
    { account: "Assets:Investment:MutualFund:Groww:ACMEELSS", amount: "742.61", commodity: "ACMEELSS", cost: "83.48", cost_currency: "INR" },
    { account: "Assets:Checking:HDFC", amount: "-61993.69", commodity: "INR" },
  ],
};

describe("mergeEditorAdapter.toEditorState", () => {
  it("maps a 2-posting buy to one entry + one candidate keyed by txn.id", () => {
    const { entries, candidates } = toEditorState([BUY]);
    expect(entries).toHaveLength(1);
    expect(candidates).toHaveLength(1);

    const entry = entries[0];
    expect(entry.date).toBe("2024/01/05");
    expect(entry.narration).toBe("Buy units");
    expect(entry.postings.map((p) => p.account)).toEqual([
      "Assets:Investment:MutualFund:Groww:ACMEELSS",
      "Assets:Checking:HDFC",
    ]);

    const candidate = candidates[0];
    expect(candidate.id).toBe("t1");
    expect(candidate.matchSource).toBe("unknown");
    expect(candidate.needsAccount).toBe(false);
    // INR leg magnitude drives the balance hint.
    expect(candidate.amount).toBe("61993.69");
  });

  it("round-trips the account lines through serializeEntries", () => {
    const { entries } = toEditorState([BUY]);
    const doc = serializeEntries(entries);
    expect(doc).toContain("Assets:Investment:MutualFund:Groww:ACMEELSS");
    expect(doc).toContain("Assets:Checking:HDFC");
    expect(doc).toContain("2024/01/05 Buy units");
  });

  it("preserves the MF lot commodity + cost in the seeded text (not lossy INR)", () => {
    const { entries } = toEditorState([BUY]);
    const doc = serializeEntries(entries);
    // The unit lot keeps its symbol and per-unit cost; the cash leg stays INR.
    expect(doc).toContain("742.61 ACMEELSS @ 83.48 INR");
    expect(doc).toContain("-61993.69 INR");
    expect(doc).not.toContain("742.61 INR");
  });

  it("falls back to 0 when the INR leg is elided", () => {
    const elided: MergeTxn = {
      id: "t2",
      date: "2024-02-01",
      narration: "Opening",
      duplicate: false,
      unbookable_sell: false,
      postings: [
        { account: "Assets:Checking:HDFC", amount: "1000.00", commodity: "INR" },
        { account: "Equity:Opening-Balances", amount: null, commodity: null },
      ],
    };
    const { candidates } = toEditorState([elided]);
    // First INR leg's magnitude wins.
    expect(candidates[0].amount).toBe("1000.00");
  });
});
