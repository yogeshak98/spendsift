import { describe, expect, test } from "vitest";
import { buildHighlightSpans } from "@/screens/ledger/import/ledgerHighlight";

describe("buildHighlightSpans", () => {
  test("tags the date and narration on a header line", () => {
    const text = "2026/06/12 SWIGGY ORDER";
    const spans = buildHighlightSpans(text);
    expect(spans).toContainEqual({ from: 0, to: 10, className: "lg-date" });
    expect(spans).toContainEqual({ from: 11, to: 23, className: "lg-narr" });
  });

  test("tags account, amount, and currency on a posting line", () => {
    const text = "    Expenses:Food  420.00 INR";
    const spans = buildHighlightSpans(text);
    expect(spans).toContainEqual({ from: 4, to: 17, className: "lg-account" });
    expect(spans).toContainEqual({ from: 19, to: 25, className: "lg-amount" });
    expect(spans).toContainEqual({ from: 26, to: 29, className: "lg-currency" });
  });

  test("tags a ref comment on the header line", () => {
    const text = "2026/06/13 UPI TOPUP ; ref 500000001";
    const spans = buildHighlightSpans(text);
    expect(spans).toContainEqual({ from: 21, to: 36, className: "lg-comment" });
  });

  test("offsets spans correctly for a second line in a multi-line buffer", () => {
    const text = "2026/06/12 SWIGGY\n    Expenses:Food  420.00 INR";
    const spans = buildHighlightSpans(text);
    const secondLineStart = text.indexOf("\n") + 1;
    expect(spans).toContainEqual({
      from: secondLineStart + 4,
      to: secondLineStart + 17,
      className: "lg-account",
    });
  });
});
