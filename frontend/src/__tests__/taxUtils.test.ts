import { describe, it, expect } from "vitest";
import { KIND_LABELS } from "@/screens/tax/utils";
import type { Resp } from "@/screens/tax/types";

describe("KIND_LABELS", () => {
  it("has salary label", () => {
    expect(KIND_LABELS["salary"]).toBe("Salary");
  });
  it("has ltcg label", () => {
    expect(KIND_LABELS["capital_gain_ltcg"]).toBeTruthy();
  });
});

describe("Resp type", () => {
  it("is structurally valid (compile-time only)", () => {
    const sample: Resp = {
      period: "FY 2025-26",
      items: [{ kind: "salary", amount: "500000", source_refs: ["t1"] }],
      deductions: [],
      summary: {
        gross_ordinary_income: "500000",
        capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "0" },
        prepaid_tax: "0",
        tax_paid_direct: "0",
        estimate: null,
        rules_configured: false,
        uncovered: [],
      },
    };
    expect(sample.period).toBe("FY 2025-26");
  });
});
