import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, test, expect, vi, afterEach } from "vitest";
import { InvestmentReview, type WalletLeg } from "@/screens/ledger/import/InvestmentReview";
import { LedgerImport } from "@/screens/ledger/Import";

// The commit no longer refuses an over-tolerance charge gap (P5 Task 2): the
// remainder is booked to Expenses:Investment:Charges:Unexplained and the row
// is flagged. This screen is therefore the ONLY place the user can learn
// about it before the journal is written. A badge that always renders would
// be no warning at all, so the clean-row case is asserted just as hard.

const CLEAN: WalletLeg = {
  kind: "buy",
  candidateId: "clean",
  date: "2026-03-18",
  narration: "Purchase — Parag Parikh Flexi Cap",
  amount: "21500",
  commodity: "MF122639",
  units: "100.0",
  unitCost: "215.0",
  unexplainedCharge: false,
  unexplainedChargeAmount: null,
};

const FLAGGED: WalletLeg = {
  ...CLEAN,
  candidateId: "flagged",
  date: "2026-03-19",
  amount: "21540",
  unexplainedCharge: true,
  unexplainedChargeAmount: "38.92",
};

function mockBalance(balance: string) {
  vi.spyOn(global, "fetch").mockImplementation((url) => {
    if (String(url).includes("cash-balance")) {
      return Promise.resolve(new Response(JSON.stringify({ balance }), { status: 200 }));
    }
    return Promise.resolve(new Response("{}", { status: 200 }));
  });
}

function renderLegs(legs: WalletLeg[]) {
  mockBalance("100000");
  return render(
    <InvestmentReview
      batchId="b1"
      legs={legs}
      onCommit={vi.fn().mockResolvedValue({ ok: true })}
      onResolved={vi.fn()}
    />,
  );
}

describe("InvestmentReview unexplained charges", () => {
  test("a flagged row warns, names the amount, and says where it is booked", async () => {
    renderLegs([FLAGGED]);
    await waitFor(() => expect(screen.getByText(/Groww Wallet/)).toBeInTheDocument());

    const warning = screen.getByText(/no charge we know explains it/i);
    // How much could not be explained.
    expect(warning.textContent).toContain("₹38.92");
    // That it is booked rather than dropped, and to WHERE.
    expect(warning.textContent).toContain("Expenses:Investment:Charges:Unexplained");
    expect(warning.textContent).toMatch(/nothing is dropped/i);
    // It is a warning, not a refusal and not a statutory charge.
    expect(warning.className).toContain("warn");
    expect(warning.textContent).not.toMatch(/error|statutory|refus/i);
  });

  test("a clean row shows no unexplained-charge warning at all", async () => {
    renderLegs([CLEAN]);
    await waitFor(() => expect(screen.getByText(/Groww Wallet/)).toBeInTheDocument());

    expect(screen.queryByText(/no charge we know explains it/i)).toBeNull();
    expect(screen.queryByText(/Expenses:Investment:Charges:Unexplained/)).toBeNull();
    expect(screen.queryByText(/38\.92/)).toBeNull();
  });

  test("in a mixed batch the warning appears once, on the flagged row only", async () => {
    renderLegs([CLEAN, FLAGGED]);
    await waitFor(() => expect(screen.getByText(/Groww Wallet ₹78500.00/)).toBeInTheDocument());

    // getByText throws on more than one match, so this IS the once-only
    // assertion -- and the flagged row is the one carrying it.
    const warning = screen.getByText(/no charge we know explains it/i);
    const row = warning.closest(".flow-step");
    expect(row?.textContent).toContain("2026-03-19");
    expect(row?.textContent).not.toContain("2026-03-18");
  });

  test("the flag still warns when only the flag arrived, with no amount", async () => {
    // Defensive: the commit path sets the boolean without an amount, and the
    // review patch route writes candidate attributes verbatim. Say what is
    // known; never print "₹null".
    renderLegs([{ ...FLAGGED, unexplainedChargeAmount: null }]);
    await waitFor(() => expect(screen.getByText(/Groww Wallet/)).toBeInTheDocument());

    const warning = screen.getByText(/no charge we know explains it/i);
    expect(warning.textContent).toContain("Expenses:Investment:Charges:Unexplained");
    expect(warning.textContent).not.toMatch(/null|undefined|₹\s*\./);
  });

  test("the Post button stays enabled — this flag informs, it does not block", async () => {
    renderLegs([FLAGGED]);
    await waitFor(() => expect(screen.getByText(/Groww Wallet/)).toBeInTheDocument());

    const post = screen.getByRole("button", { name: /post/i }) as HTMLButtonElement;
    expect(post.disabled).toBe(false);
  });
});

afterEach(() => vi.restoreAllMocks());

// --- the wiring, end to end -------------------------------------------------
//
// The component tests above are handed a WalletLeg. That leaves the half that
// actually runs in the app -- backend candidate field -> Candidate ->
// computeLegs -> WalletLeg -- uncovered, and a flag that never reaches the
// component is the same thing as no flag at all.

describe("LedgerImport carries the unexplained-charge flag to the review row", () => {
  const CANDIDATE = {
    id: "c1",
    date: "2026-03-19",
    raw_narration: "Purchase — Parag Parikh Flexi Cap",
    suggested_category: null,
    amount: "21540",
    direction: "debit",
    dedup_status: "new",
    needs_account: false,
    source_account: "Assets:Investment:MutualFund:Groww:Cash",
    commodity: "MF122639",
    units: "100.0",
    unit_cost: "215.0",
    unexplained_charge: true,
    unexplained_charge_amount: "38.92",
  };

  async function uploadInvestment(candidate: Record<string, unknown>) {
    const responses: Record<string, unknown> = {
      "/api/import/detect": {
        matched: true,
        batch_id: "b1",
        fingerprint: "fp1",
        candidates: [candidate],
      },
      "/api/accounts": { accounts: ["Assets:Investment:MutualFund:Groww:Cash"] },
      "/api/import/investment/cash-balance": { balance: "100000" },
    };
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const key = String(url);
      if (key in responses) return new Response(JSON.stringify(responses[key]), { status: 200 });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });

    render(<LedgerImport />);
    fireEvent.click(screen.getByRole("button", { name: /investment/i }));
    const input = screen.getByLabelText("investment file") as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["x"], "groww.csv", { type: "text/csv" })] },
    });
    await waitFor(() => expect(screen.getByText(/Groww Wallet/)).toBeInTheDocument());
  }

  test("the flag and its amount survive the trip from the detect payload", async () => {
    await uploadInvestment(CANDIDATE);
    const warning = screen.getByText(/no charge we know explains it/i);
    expect(warning.textContent).toContain("₹38.92");
    expect(warning.textContent).toContain("Expenses:Investment:Charges:Unexplained");
  });

  test("a candidate the backend did not flag produces no warning", async () => {
    await uploadInvestment({
      ...CANDIDATE,
      amount: "21500",
      unexplained_charge: false,
      unexplained_charge_amount: null,
    });
    expect(screen.queryByText(/no charge we know explains it/i)).toBeNull();
  });
});

// --- the OTHER screen a flagged batch can land on ---------------------------
//
// `/api/import/detect` is header-driven and tab-agnostic: it recognises a
// Groww MF export by its header row, not by which dropzone sent it, and the
// statement dropzone's own sub-text advertises "Groww". So the same flagged
// candidates can land on StatementReview instead of InvestmentReview. The
// commit no longer refuses an over-tolerance charge gap, so a screen that
// renders no warning is the one route by which money reaches
// Expenses:Investment:Charges:Unexplained with nothing ever shown.

describe("the statement tab warns about an unexplained charge too", () => {
  const GROWW_CANDIDATE = {
    id: "c1",
    date: "2026-03-19",
    raw_narration: "Purchase — Parag Parikh Flexi Cap",
    suggested_category: null,
    amount: "21540",
    direction: "debit",
    dedup_status: "new",
    needs_account: false,
    source_account: "Assets:Investment:MutualFund:Groww:Cash",
    commodity: "MF122639",
    units: "100.0",
    unit_cost: "215.0",
    unexplained_charge: true,
    unexplained_charge_amount: "38.92",
  };

  async function uploadStatement(candidate: Record<string, unknown>) {
    const responses: Record<string, unknown> = {
      "/api/import/detect": {
        matched: true,
        source: "groww",
        batch_id: "b1",
        fingerprint: "fp1",
        candidates: [candidate],
      },
      "/api/accounts": { accounts: ["Assets:Investment:MutualFund:Groww:Cash"] },
    };
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const key = String(url);
      if (key in responses) return new Response(JSON.stringify(responses[key]), { status: 200 });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });

    render(<LedgerImport />);
    const input = screen.getByLabelText("statement file") as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["x"], "groww.csv", { type: "text/csv" })] },
    });
    await waitFor(() =>
      expect(screen.getByText(/Parag Parikh Flexi Cap/)).toBeInTheDocument(),
    );
  }

  test("a Groww export uploaded through the statement dropzone still warns", async () => {
    await uploadStatement(GROWW_CANDIDATE);

    const warning = screen.getByText(/no charge we know explains it/i);
    expect(warning.className).toContain("warn");
    expect(warning.textContent).toContain("Expenses:Investment:Charges:Unexplained");
    expect(warning.textContent).toMatch(/nothing is dropped/i);
    // Names the row and the amount, so "which one?" is answerable on screen.
    expect(warning.textContent).toContain("₹38.92");
    expect(warning.textContent).toContain("2026-03-19");
  });

  test("and a row the backend did not flag produces no warning here either", async () => {
    await uploadStatement({
      ...GROWW_CANDIDATE,
      amount: "21500",
      unexplained_charge: false,
      unexplained_charge_amount: null,
    });

    expect(screen.queryByText(/no charge we know explains it/i)).toBeNull();
    expect(screen.queryByText(/Expenses:Investment:Charges:Unexplained/)).toBeNull();
  });

  test("the Commit button stays enabled — this flag informs, it does not block", async () => {
    await uploadStatement(GROWW_CANDIDATE);
    expect(screen.getByRole("button", { name: "Commit" })).not.toBeDisabled();
  });
});

