import { describe, it, expect } from "vitest";
import { NAV } from "@/nav";

describe("tax nav", () => {
  it("tax domain has 6 sub-views", () => {
    const tax = NAV.find((d) => d.id === "tax");
    expect(tax).toBeTruthy();
    const ids = tax!.subs.map((s) => s.id);
    expect(ids).toEqual(["overview", "income", "capital-gains", "deductions", "regime-compare", "prepaid"]);
  });
});
