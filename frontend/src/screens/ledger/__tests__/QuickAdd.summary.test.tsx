import { describe, it, expect } from "vitest";
import { computeNetByAccount } from "@/screens/ledger/netByAccount";
import type { ParsedEntry } from "@/screens/ledger/import/ledgerEntries";

const e1: ParsedEntry = {
  date: "2026-06-30", narration: "Coffee", ref: null, tags: [],
  postings: [
    { account: "Expenses:Food", amount: "20" },
    { account: "Assets:Bank:HDFC", amount: "-20" },
  ],
};
const e2: ParsedEntry = {
  date: "2026-06-30", narration: "Lunch", ref: null, tags: [],
  postings: [
    { account: "Expenses:Food", amount: "150" },
    { account: "Assets:Bank:HDFC", amount: "-150" },
  ],
};

describe("computeNetByAccount", () => {
  it("returns empty array for no entries", () => {
    expect(computeNetByAccount([])).toEqual([]);
  });

  it("sums a single entry", () => {
    const result = computeNetByAccount([e1]);
    const food = result.find((r) => r.account === "Expenses:Food");
    const bank = result.find((r) => r.account === "Assets:Bank:HDFC");
    expect(food?.net).toBe(20);
    expect(bank?.net).toBe(-20);
  });

  it("sums multiple entries for same account", () => {
    const result = computeNetByAccount([e1, e2]);
    const food = result.find((r) => r.account === "Expenses:Food");
    expect(food?.net).toBe(170);
  });

  it("sorts by abs(net) descending", () => {
    const result = computeNetByAccount([e1, e2]);
    const nets = result.map((r) => Math.abs(r.net));
    for (let i = 1; i < nets.length; i++) {
      expect(nets[i - 1]).toBeGreaterThanOrEqual(nets[i]);
    }
  });

  it("NaN guard: a posting with a non-numeric amount contributes 0 and does not poison the net", () => {
    const badEntry: ParsedEntry = {
      date: "2026-06-30", narration: "Corrupt", ref: null, tags: [],
      postings: [
        { account: "Expenses:Food", amount: "not-a-number" },
        { account: "Assets:Bank:HDFC", amount: "-50" },
      ],
    };
    const result = computeNetByAccount([e1, badEntry]);
    const food = result.find((r) => r.account === "Expenses:Food");
    const bank = result.find((r) => r.account === "Assets:Bank:HDFC");
    // "not-a-number" must contribute 0, so food net = 20 (from e1 only)
    expect(food?.net).toBe(20);
    // -50 from badEntry + -20 from e1
    expect(bank?.net).toBe(-70);
    // Result must never contain NaN
    for (const row of result) {
      expect(Number.isNaN(row.net)).toBe(false);
    }
  });
});
