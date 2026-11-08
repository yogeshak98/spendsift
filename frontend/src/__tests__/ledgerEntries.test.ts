import { describe, expect, test } from "vitest";
import tagCases from "@/__tests__/fixtures/importReview/tag-normalize-cases.json";
import {
  buildAcronymSet,
  canonicalizeAccount,
  computeRemaining,
  normalizeTag,
  parseBuffer,
  serializeEntries,
  serializeEntry,
  type ParsedEntry,
} from "@/screens/ledger/import/ledgerEntries";

describe("serializeEntry / parseBuffer round-trip", () => {
  test("serializes a two-posting entry and parses it back identically", () => {
    const entry: ParsedEntry = {
      date: "2026/06/12",
      narration: "SWIGGY ORDER #4521 BANGALORE",
      ref: null,
      postings: [
        { account: "Expenses:Food:Delivery", amount: "420.00" },
        { account: "Assets:Bank:HDFC", amount: "-420.00" },
      ],
      tags: [],
    };
    const text = serializeEntry(entry);
    expect(parseBuffer(text)).toEqual([entry]);
  });

  test("keeps a ref comment on the header line through the round-trip", () => {
    const entry: ParsedEntry = {
      date: "2026/06/13",
      narration: "UPI-LITE TOPUP 9821XXXX41",
      ref: "ref 500000001",
      postings: [
        { account: "Assets:IDFC:UPILite", amount: "2000.00" },
        { account: "Assets:Bank:HDFC", amount: "-2000.00" },
      ],
      tags: [],
    };
    expect(parseBuffer(serializeEntry(entry))).toEqual([entry]);
  });

  test("parses multiple entries separated by a blank line, preserving order", () => {
    const a: ParsedEntry = {
      date: "2026/06/12",
      narration: "SWIGGY ORDER",
      ref: null,
      postings: [
        { account: "Expenses:Food", amount: "100.00" },
        { account: "Assets:Bank:HDFC", amount: "-100.00" },
      ],
      tags: [],
    };
    const b: ParsedEntry = {
      date: "2026/06/13",
      narration: "RZRPAY MERCHANT",
      ref: null,
      postings: [
        { account: "Expenses:Shopping", amount: "200.00" },
        { account: "Assets:Bank:HDFC", amount: "-200.00" },
      ],
      tags: [],
    };
    const text = serializeEntries([a, b]);
    expect(parseBuffer(text)).toEqual([a, b]);
  });

  test("supports more than two postings (an added split line)", () => {
    const entry: ParsedEntry = {
      date: "2026/06/14",
      narration: "MIXED CART ORDER",
      ref: null,
      postings: [
        { account: "Expenses:Food", amount: "150.00" },
        { account: "Expenses:Shopping", amount: "150.00" },
        { account: "Assets:Bank:HDFC", amount: "-300.00" },
      ],
      tags: [],
    };
    expect(parseBuffer(serializeEntry(entry))).toEqual([entry]);
  });

  test("preserves an MF lot's commodity and @ cost through the round-trip", () => {
    const entry: ParsedEntry = {
      date: "2020/01/16",
      narration: "MF Purchase: Axis Large Cap Fund Direct Growth",
      ref: null,
      postings: [
        {
          account: "Assets:Investment:MutualFund:Groww:AXISLARGECAP",
          amount: "742.61",
          commodity: "AXISLARGECAP",
          cost: "83.48",
          costCurrency: "INR",
        },
        { account: "Assets:Investment:MutualFund:Groww:Cash", amount: "-61993.69" },
      ],
      tags: [],
    };
    const text = serializeEntry(entry);
    expect(text).toContain("742.61 AXISLARGECAP @ 83.48 INR");
    expect(parseBuffer(text)).toEqual([entry]);
  });

  test("serializes an elided posting as account-only and parses it back", () => {
    const entry: ParsedEntry = {
      date: "2025/03/31",
      narration: "Initial Balance",
      ref: null,
      postings: [
        { account: "Assets:Checking:HDFC", amount: "50000.00" },
        { account: "Equity:OpeningBalance", amount: "" },
      ],
      tags: [],
    };
    const text = serializeEntry(entry);
    expect(text).toContain("    Equity:OpeningBalance");
    expect(text).not.toContain("Equity:OpeningBalance  ");
    expect(parseBuffer(text)).toEqual([entry]);
  });

  test("a transaction-level tag line survives serialize/parse and isn't mistaken for a posting", () => {
    const entry: ParsedEntry = {
      date: "2026/06/12",
      narration: "SWIGGY ORDER #4521 BANGALORE",
      ref: null,
      postings: [
        { account: "Expenses:Food:Delivery", amount: "420.00" },
        { account: "Assets:Bank:HDFC", amount: "-420.00" },
      ],
      tags: ["#fy2025-26"],
    };
    expect(parseBuffer(serializeEntry(entry))).toEqual([entry]);
  });
});

describe("canonicalizeAccount", () => {
  test("title-cases each segment by default", () => {
    const acronyms = buildAcronymSet(["Assets:Bank:HDFC"]);
    expect(canonicalizeAccount("expenses:food:delivery", acronyms)).toBe(
      "Expenses:Food:Delivery",
    );
  });

  test("preserves a known acronym segment in upper case regardless of typed case", () => {
    const acronyms = buildAcronymSet(["Assets:Bank:HDFC", "Assets:IDFC:UPILite"]);
    expect(canonicalizeAccount("assets:bank:hdfc", acronyms)).toBe("Assets:Bank:HDFC");
    expect(canonicalizeAccount("assets:idfc:upilite", acronyms)).toBe(
      "Assets:Idfc:Upilite",
    );
  });

  test("leaves already-correct casing untouched", () => {
    const acronyms = buildAcronymSet(["Assets:Bank:HDFC"]);
    expect(canonicalizeAccount("Assets:Bank:HDFC", acronyms)).toBe("Assets:Bank:HDFC");
  });
});

describe("computeRemaining", () => {
  test("returns 0 when category postings sum to the transaction amount", () => {
    expect(
      computeRemaining("420.00", [{ account: "Expenses:Food", amount: "420.00" }]),
    ).toBe(0);
  });

  test("returns the unallocated amount when postings don't sum to the total", () => {
    expect(
      computeRemaining("420.00", [{ account: "Expenses:Food", amount: "300.00" }]),
    ).toBe(120);
  });
});

describe("normalizeTag", () => {
  test("mirrors the backend rule", () => {
    expect(normalizeTag("travel/Madikeri Oct-2026")).toBe("travel/madikeri-oct-2026");
    expect(normalizeTag("#Trip")).toBe("trip");
    expect(normalizeTag("café")).toBeNull();
    expect(normalizeTag("trip:x")).toBeNull();
    expect(normalizeTag("a#b")).toBeNull();
    expect(normalizeTag("travel//x")).toBeNull();
  });

  test("agrees with every case in the backend parity fixture", () => {
    expect(tagCases.cases.length).toBeGreaterThan(15);
    for (const c of tagCases.cases) {
      expect(normalizeTag(c.raw), JSON.stringify(c.raw)).toBe(c.tag);
    }
  });
});
