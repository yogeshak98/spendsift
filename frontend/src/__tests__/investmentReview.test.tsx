import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, test, expect, vi, afterEach } from "vitest";
import { InvestmentReview, type WalletLeg } from "@/screens/ledger/import/InvestmentReview";

const BUY: WalletLeg = {
  kind: "buy", candidateId: "c1", date: "2024-01-05", narration: "Purchase — ELSS Fund",
  amount: "4997.50", commodity: "ACMEELSS", units: "59.88", unitCost: "83.45",
};
const REFUND: WalletLeg = {
  kind: "refund", candidateId: "c2", date: "2024-01-07", narration: "Redeem — ELSS Fund",
  amount: "5200.00", commodity: "ACMEELSS", units: "59.88", unitCost: "86.83",
};

function mockBalance(balance: string) {
  vi.spyOn(global, "fetch").mockImplementation((url) => {
    if (String(url).includes("cash-balance")) {
      return Promise.resolve(new Response(JSON.stringify({ balance }), { status: 200 }));
    }
    return Promise.resolve(new Response("{}", { status: 200 }));
  });
}

afterEach(() => vi.restoreAllMocks());

describe("InvestmentReview", () => {
  test("balance seeds from the cash-balance endpoint", async () => {
    mockBalance("10000");
    render(<InvestmentReview batchId="b1" legs={[BUY]} onCommit={vi.fn().mockResolvedValue({ ok: true })} onResolved={vi.fn()} />);
    // After seed 10000 and a buy of 4997.50 → 5002.50, positive (zero class)
    await waitFor(() => expect(screen.getByText(/Groww Wallet ₹5002.50/)).toBeInTheDocument());
  });

  test("running balance updates per leg", async () => {
    mockBalance("5000");
    render(<InvestmentReview batchId="b1" legs={[BUY, REFUND]} onCommit={vi.fn().mockResolvedValue({ ok: true })} onResolved={vi.fn()} />);
    // seed 5000 - 4997.50 (buy) = 2.50 ; + 5200 (refund) = 5202.50
    await waitFor(() => expect(screen.getByText(/Groww Wallet ₹2.50/)).toBeInTheDocument());
    expect(screen.getByText(/Groww Wallet ₹5202.50/)).toBeInTheDocument();
  });

  test("negative running balance shows soft funding warn callout (not E1024)", async () => {
    mockBalance("0"); // seed 0, then a buy → negative
    render(<InvestmentReview batchId="b1" legs={[BUY]} onCommit={vi.fn().mockResolvedValue({ ok: true })} onResolved={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByText(/import the bank .*add money.* transfer/i)).toBeInTheDocument()
    );
    // It is a warn hint, NOT an E1024 label
    expect(screen.queryByText(/E1024/)).not.toBeInTheDocument();
  });

  test("Post is enabled even when balance is negative (soft hint does not block)", async () => {
    mockBalance("0");
    render(<InvestmentReview batchId="b1" legs={[BUY]} onCommit={vi.fn().mockResolvedValue({ ok: true })} onResolved={vi.fn()} />);
    await waitFor(() => screen.getByText(/Groww Wallet/));
    const post = screen.getByRole("button", { name: /post|commit/i }) as HTMLButtonElement;
    expect(post.disabled).toBe(false);
  });

  // Was mocked as E1024 ("charge violation"). P5 removed that refusal -- an
  // over-tolerance charge gap is now booked to a named account and flagged,
  // never raised -- so the backend cannot return that code and the test
  // passed only because the response was a mock. E1013 is a refusal the
  // backend still produces on this screen.
  test("Post calls commit; a coded refusal renders the bad callout", async () => {
    mockBalance("10000");
    const onCommit = vi.fn().mockResolvedValue({
      ok: false,
      code: "E1013",
      description: "These redemptions sell more units than the known lots can cover: c1",
    });
    render(<InvestmentReview batchId="b1" legs={[BUY]} onCommit={onCommit} onResolved={vi.fn()} />);
    await waitFor(() => screen.getByText(/Groww Wallet/));
    fireEvent.click(screen.getByRole("button", { name: /post|commit/i }));
    await waitFor(() => expect(onCommit).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/E1013/)).toBeInTheDocument());
  });
});

// --- every refusal must be visible -----------------------------------------
//
// An investment commit can be refused by at least seven distinct codes. Only
// E1024 was ever rendered, so every other refusal left the user pressing Post
// and watching nothing happen -- on the one screen whose whole job is deciding
// what gets written to the journal. E1024 is not among them any more: P5
// stopped the charge gap refusing a commit, so nothing can return it.

describe("InvestmentReview refusals", () => {
  test.each([
    ["E1012", "Resolve the mutual-fund scheme for candidates: c1"],
    ["E1013", "These redemptions sell more units than the known lots can cover: c1"],
    ["E1043", "These stock orders' per-lot postings do not reconcile"],
    ["E1044", "These investment rows cannot be booked as lots"],
    ["E1045", "already exists in the journal attached to a transaction"],
    ["E1046", "stock transaction(s) with no broker order-id"],
  ])("renders %s rather than failing silently", async (code, description) => {
    mockBalance("100000");
    render(
      <InvestmentReview
        batchId="b1"
        legs={[BUY]}
        onCommit={vi.fn().mockResolvedValue({
          ok: false,
          code,
          description,
          resolution: "Nothing was committed.",
        })}
        onResolved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /post/i }));
    await waitFor(() => expect(screen.getByText(code)).toBeInTheDocument());
    // Assert against the callout's own text. A text query would match every
    // ancestor too, and building a RegExp from the description would treat
    // characters like the "(s)" in E1046 as a capture group.
    const callout = screen.getByText(code).closest("div");
    expect(callout?.textContent).toContain(description);
    // The resolution says what to DO. A diagnosis with no next step is half an answer.
    expect(callout?.textContent).toContain("Nothing was committed.");
  });

  test("a refusal with no code still says something, and never invents a reason", async () => {
    mockBalance("100000");
    render(
      <InvestmentReview
        batchId="b1"
        legs={[BUY]}
        onCommit={vi.fn().mockResolvedValue({ ok: false })}
        onResolved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /post/i }));
    await waitFor(() =>
      expect(screen.getByText(/the reason did not come back/i)).toBeInTheDocument(),
    );
    // It must still be unambiguous that the journal was NOT written.
    expect(screen.getByText(/Nothing was committed/i)).toBeInTheDocument();
  });

  test("names the row an unresolved scheme blocks, and puts the fix on that row", async () => {
    mockBalance("100000");
    const unnamed: WalletLeg = { ...BUY, commodity: null, units: null, unitCost: null,
      unresolvedScheme: "SOME UNKNOWN FUND - DIRECT" };
    render(
      <InvestmentReview batchId="b1" legs={[unnamed]} onCommit={vi.fn().mockResolvedValue({ ok: true })}
        onResolved={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByText("SOME UNKNOWN FUND - DIRECT")).toBeInTheDocument(),
    );
    // Naming the row was C5c. The row now carries the ACTION too, so the user
    // is not sent elsewhere -- or nowhere -- to clear a hold they can see.
    expect(screen.getByLabelText("search schemes")).toBeInTheDocument();
  });

  test("shows the lot a stock row will book, not just its rupee total", async () => {
    mockBalance("100000");
    const stock: WalletLeg = {
      kind: "buy", candidateId: "s1", date: "2026-02-10", narration: "SBIN",
      amount: "8000.00", commodity: "SBIN", units: "10", unitCost: "800.00",
    };
    render(
      <InvestmentReview batchId="b1" legs={[stock]} onCommit={vi.fn().mockResolvedValue({ ok: true })} onResolved={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getByText(/10 units @ ₹800.00 SBIN/)).toBeInTheDocument());
  });
});
