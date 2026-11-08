import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { Doctor } from "@/screens/Doctor";

let _DATA: unknown = null;

vi.mock("@/shared/api/useReport", () => ({
  useReport: () => ({ data: _DATA }),
}));

function payload(over: Record<string, unknown> = {}) {
  return {
    journal_valid: true,
    errors: [],
    transaction_count: 42,
    uncategorized_count: 7,
    uncategorized: { transactions: 7, merchants: 3 },
    negative_assets: [],
    ...over,
  };
}

function renderDoctor() {
  return render(
    <MemoryRouter>
      <Doctor />
    </MemoryRouter>,
  );
}

describe("Doctor uncategorized backlog", () => {
  it("renders both the transaction count and the distinct-merchant count", () => {
    _DATA = payload();
    renderDoctor();
    expect(screen.getByText(/7 transactions/)).toBeInTheDocument();
    expect(screen.getByText(/3 merchants/)).toBeInTheDocument();
  });

  it("links to the screen that clears the backlog", () => {
    _DATA = payload();
    renderDoctor();
    const link = screen.getByRole("link", { name: /uncategorized/i });
    expect(link).toHaveAttribute("href", "/expense/uncategorized");
  });

  it("says the backlog is clear, not an alarm, when there is nothing uncategorized", () => {
    // Matched on the sentence, not on /clear/i: the NON-clear branch renders
    // "Clear the backlog" (the link row), which also matches /clear/i, so the
    // loose version passed even with the clear/non-clear condition inverted.
    _DATA = payload({ uncategorized_count: 0, uncategorized: { transactions: 0, merchants: 0 } });
    renderDoctor();
    expect(screen.getByText(/backlog is clear/i)).toBeInTheDocument();
    expect(screen.queryByText(/only spend-by-category is pending/i)).toBeNull();
  });

  it("suppresses the link to Expense -> Uncategorized once the backlog is clear", () => {
    _DATA = payload({ uncategorized_count: 0, uncategorized: { transactions: 0, merchants: 0 } });
    renderDoctor();
    expect(screen.queryByRole("link", { name: /uncategorized/i })).toBeNull();
  });

  it("reads the nested merchant-grouped count, not the legacy raw-posting count, when the two diverge", () => {
    // The legacy uncategorized_count (12) counts every raw posting against
    // Expenses:Uncategorized; the nested uncategorized.transactions (7) comes
    // from the merchant grouping, which drops postings whose normalized
    // narration is empty (see the comment in doctor.py). They can legitimately
    // differ -- this fixture pins that the panel reads the nested figure, not
    // the legacy one, by making them disagree.
    _DATA = payload({ uncategorized_count: 12, uncategorized: { transactions: 7, merchants: 3 } });
    renderDoctor();
    expect(screen.getByText(/7 transactions/)).toBeInTheDocument();
    expect(screen.queryByText(/12 transactions/)).not.toBeInTheDocument();
  });

  it("phrases the backlog as a condition, not a warning: these rows are already committed and counted", () => {
    _DATA = payload();
    renderDoctor();
    expect(screen.getByText(/counted in net worth and cash flow/i)).toBeInTheDocument();
  });

  it("styles the backlog as information, never as a warning or a failure", () => {
    // Asserted on the class that actually decides how this reads. The
    // previous version of this test looked for a text node equal to
    // "WARNING"/"ALARM", which no plausible implementation renders as its own
    // node -- it passed against every version, correct or broken.
    _DATA = payload();
    renderDoctor();
    const callout = document.querySelector(".callout.info") as HTMLElement;
    expect(callout).not.toBeNull();
    expect(callout.className).toContain("info");
    expect(callout.className).not.toContain("warn");
    expect(callout.className).not.toContain("bad");
  });
});

describe("Doctor opening balances", () => {
  it("lists missing openings", () => {
    _DATA = payload({
      openings: {
        missing_count: 2,
        missing: [
          { account: "Assets:Checking:Zeta", account_display: "Zeta Savings", first_posting: "2026-06-14" },
          { account: "Liabilities:People:Ravi", account_display: "Ravi", first_posting: null },
        ],
      },
    });
    renderDoctor();
    expect(screen.getByText("✗ 2 accounts have activity not covered by an opening balance")).toBeInTheDocument();
    expect(screen.getByText("Zeta Savings")).toBeInTheDocument();
    expect(screen.getByText("Ravi")).toBeInTheDocument();
    expect(screen.queryByText(/Every active account has an opening balance/)).toBeNull();
    expect(screen.getByRole("link", { name: /opening balances/i })).toHaveAttribute(
      "href",
      "/settings/openings",
    );
  });

  it("says every account has an opening when none is missing", () => {
    _DATA = payload({ openings: { missing_count: 0, missing: [] } });
    renderDoctor();
    expect(screen.getByText("✓ Every active account has an opening balance")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /opening balances/i })).toBeNull();
  });

  it("tolerates payload without openings", () => {
    _DATA = payload();
    expect("openings" in (_DATA as object)).toBe(false);
    renderDoctor();
    // The rest of the screen still renders, and no opening verdict is invented.
    expect(screen.getByText(/All transactions balance/)).toBeInTheDocument();
    expect(screen.queryByText(/opening balance/i)).toBeNull();
  });
});
