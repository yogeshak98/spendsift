import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { EditorView } from "@codemirror/view";

// The typed openapi-fetch client builds requests with `new URL`, which throws
// on the relative ("") base URL under jsdom. Stub GET with a controllable spy
// (default: empty links) for any screen here that reaches it.
const clientGet = vi.fn(async () => ({ data: { links: [] }, error: undefined }));
vi.mock("@/shared/api/client", () => ({
  client: { GET: (...args: unknown[]) => clientGet(...args) },
}));
import { TooltipProvider } from "@/shared/theme/tooltip";
import { Doctor } from "@/screens/Doctor";
import { GoalPlanner } from "@/screens/planning/GoalPlanner";
import { Sip } from "@/screens/planning/Sip";
import { Lumpsum } from "@/screens/planning/Lumpsum";
import { Cagr } from "@/screens/planning/Cagr";
import { Salary } from "@/screens/planning/Salary";
import { Fire } from "@/screens/planning/Fire";
import { PeriodProvider } from "@/shared/period/period";
import { LedgerImport } from "@/screens/ledger/Import";
import { LedgerTransactions } from "@/screens/ledger/Transactions";
import { LiabilitiesBalance } from "@/screens/liabilities/Balance";
import { LiabilitiesInterest } from "@/screens/liabilities/Interest";
import { LiabilitiesRepayment } from "@/screens/liabilities/Repayment";
import { CashflowRecurring } from "@/screens/cashflow/Recurring";
import { CashflowCompareFYs } from "@/screens/cashflow/CompareFYs";
import { ExpenseBudget } from "@/screens/expense/Budget";
import App from "@/App";

beforeEach(() => localStorage.clear());
// Each test installs its own fetch spy; without a restore, every spy in this
// file shares one call log, so `toHaveBeenCalledWith` can be satisfied by an
// earlier test's request.
afterEach(() => vi.restoreAllMocks());

test("CashflowCompareFYs (Income Statement) renders the multi-FY P&L tree with a net row and no tax", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        fys: [2023, 2024, 2025, 2026],
        columns: [
          { fy: 2023, label: "FY 2023-24" },
          { fy: 2024, label: "FY 2024-25" },
          { fy: 2025, label: "FY 2025-26" },
          { fy: 2026, label: "FY 2026-27" },
        ],
        sections: [
          {
            key: "income",
            label: "Revenue",
            line_items: [
              { category: "Income:Salary", amounts: ["0", "0", "0", "280000"] },
            ],
            totals: ["0", "0", "0", "280000"],
          },
          {
            key: "expenses",
            label: "Expenses",
            line_items: [
              { category: "Expenses:Rent", amounts: ["0", "0", "0", "35000"] },
              { category: "Expenses:Food", amounts: ["0", "0", "0", "1300"] },
            ],
            totals: ["0", "0", "0", "36300"],
          },
        ],
        net: ["0", "0", "0", "239401"],
      }),
      { status: 200 },
    ),
  );
  render(
    <TooltipProvider>
      <CashflowCompareFYs />
    </TooltipProvider>,
  );
  await waitFor(() => expect(screen.getByText("Revenue")).toBeInTheDocument());
  expect(screen.getByText("Expenses")).toBeInTheDocument();
  expect(screen.getByText("Salary")).toBeInTheDocument(); // leaf label (prefix stripped)
  expect(screen.getByText("FY 2026-27")).toBeInTheDocument(); // last column header
  expect(screen.getByText("₹2,39,401.00")).toBeInTheDocument(); // net, always 2dp
  expect(screen.queryByText(/tax/i)).toBeNull(); // no fabricated tax row
});

test("CashflowCompareFYs (Income Statement) period presets drive the FY column window", async () => {
  // Hermetic against a flake root-caused to FYRangePicker's own
  // /available-years fetch resolving independently of -- and sometimes after
  // -- the pnl fetch the first assertion waits for. The preset <select>
  // renders DISABLED until years land, and a fireEvent.change fired too early
  // silently no-ops on it; the `not.toBeDisabled` wait below is the fix.
  // cleanup() force-unmounts anything a prior test left mounted.
  cleanup();
  withFY2026(() => {}); // currentFY() === 2026
  let rendered: ReturnType<typeof render> | undefined;
  try {
    const pnl = {
      fys: [2024, 2025, 2026],
      columns: [
        { fy: 2024, label: "FY 2024-25" },
        { fy: 2025, label: "FY 2025-26" },
        { fy: 2026, label: "FY 2026-27" },
      ],
      sections: [
        {
          key: "income",
          label: "Revenue",
          line_items: [{ category: "Income:Salary", amounts: ["0", "0", "280000"] }],
          totals: ["0", "0", "280000"],
        },
        {
          key: "expenses",
          label: "Expenses",
          line_items: [{ category: "Expenses:Rent", amounts: ["0", "0", "35000"] }],
          totals: ["0", "0", "35000"],
        },
      ],
      net: ["0", "0", "245000"],
    };
    const spy = vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const key = String(url);
      if (key.includes("available-years")) {
        return new Response(
          JSON.stringify({
            calendar_years: [2023, 2024, 2025, 2026],
            fiscal_years: [2023, 2024, 2025, 2026],
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify(pnl), { status: 200 });
    });

    rendered = render(
      <TooltipProvider>
        <CashflowCompareFYs />
      </TooltipProvider>,
    );

    // Default preset is Last 3 FYs, anchored at currentFY -> 2024..2026.
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        expect.stringContaining("fys=2024,2025,2026"),
        expect.anything(),
      ),
    );
    expect(screen.getByLabelText("Period")).toBeInTheDocument();
    expect(screen.getByText("Salary")).toBeInTheDocument();
    // The range edges belong to custom mode only -- a preset shows one control.
    expect(screen.queryByLabelText("Range start")).not.toBeInTheDocument();

    await waitFor(() => expect(screen.getByLabelText("Period")).not.toBeDisabled());

    // Last 5 FYs over a 4-FY ledger clamps to the earliest FY -- four columns,
    // never a fabricated pre-ledger fifth.
    spy.mockClear();
    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "last5" } });
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        expect.stringContaining("fys=2023,2024,2025,2026"),
        expect.anything(),
      ),
    );

    // Current FY is a single column.
    spy.mockClear();
    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "current" } });
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        expect.stringContaining("fys=2026"),
        expect.anything(),
      ),
    );

    // Custom carries the visible span in, then the arrow slides it a year back.
    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "custom" } });
    expect(await screen.findByLabelText("Range start")).toBeInTheDocument();
    spy.mockClear();
    fireEvent.click(screen.getByLabelText("Shift range earlier"));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        expect.stringContaining("fys=2025"),
        expect.anything(),
      ),
    );
  } finally {
    rendered?.unmount();
    vi.useRealTimers();
  }
});

test("CashflowCompareFYs (Income Statement) TreeTable structural alignment: tree-table class, caret slots, colgroup, tabular-nums", async () => {
  // Same fixture as the multi-FY test — one parent row (Revenue) + one leaf (Salary).
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        fys: [2026],
        columns: [{ fy: 2026, label: "FY 2026-27" }],
        sections: [
          {
            key: "income",
            label: "Revenue",
            line_items: [
              { category: "Income:Salary", amounts: ["280000"] },
            ],
            totals: ["280000"],
          },
        ],
        net: ["280000"],
      }),
      { status: 200 },
    ),
  );
  const { container } = render(
    <TooltipProvider>
      <CashflowCompareFYs />
    </TooltipProvider>,
  );
  await waitFor(() => expect(screen.getByText("Revenue")).toBeInTheDocument());

  // 1. The <table> carries the .tree-table class for scoped CSS to apply.
  const table = container.querySelector("table.tree-table");
  expect(table).not.toBeNull();

  // 2. A <colgroup> is present so table-layout:fixed can use explicit column widths.
  const colgroup = table!.querySelector("colgroup");
  expect(colgroup).not.toBeNull();
  // One <col> per header cell (IncomeStatement renders 1 label + N FY columns).
  expect(colgroup!.querySelectorAll("col").length).toBe(
    table!.querySelectorAll("thead tr th").length,
  );

  // 3. Fixed-width caret slot: every first-column <td> must contain a .tree-caret-slot
  //    span, regardless of whether the row is expandable.  This ensures leaf rows and
  //    parent rows share identical horizontal alignment.
  const firstTds = Array.from(table!.querySelectorAll("tbody tr td:first-child"));
  expect(firstTds.length).toBeGreaterThan(0);
  for (const td of firstTds) {
    const slot = td.querySelector(".tree-caret-slot");
    expect(slot).not.toBeNull();
    // The slot must be inline-block with a fixed pixel width.
    const style = (slot as HTMLElement).style;
    expect(style.display).toBe("inline-block");
    expect(style.width).toBeTruthy(); // e.g. "14px"
  }

  // 4. Numeric cells carry the num-cell class so .tree-table td.num-cell tabular-nums
  //    rule applies (structural assertion; jsdom doesn't compute CSS).
  const numCells = table!.querySelectorAll("tbody td.num-cell");
  expect(numCells.length).toBeGreaterThan(0);

  // 5. Leaf row (Salary) has a caret slot that renders no interactive button —
  //    confirms empty-slot pattern rather than null.
  await waitFor(() => expect(screen.getByText("Salary")).toBeInTheDocument());
  const salaryRow = Array.from(table!.querySelectorAll("tbody tr")).find((tr) =>
    tr.textContent?.includes("Salary"),
  );
  expect(salaryRow).toBeDefined();
  const leafSlot = salaryRow!.querySelector("td:first-child .tree-caret-slot");
  expect(leafSlot).not.toBeNull();
  // No role="button" inside the leaf's slot (it's just an empty spacer).
  expect(leafSlot!.querySelector("[role=button]")).toBeNull();

  // 6. Parent row (Revenue) has a caret slot with an interactive button.
  const revenueRow = Array.from(table!.querySelectorAll("tbody tr")).find((tr) =>
    tr.querySelector("td:first-child")?.textContent?.includes("Revenue"),
  );
  expect(revenueRow).toBeDefined();
  const parentSlot = revenueRow!.querySelector("td:first-child .tree-caret-slot");
  expect(parentSlot!.querySelector("[role=button]")).not.toBeNull();
});

test("Doctor shows journal validity", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        journal_valid: true,
        errors: [],
        transaction_count: 5,
        uncategorized_count: 0,
      }),
      { status: 200 },
    ),
  );
  render(<Doctor />);
  await waitFor(() => expect(screen.getByText(/All transactions balance/i)).toBeInTheDocument());
});

// A journal once carried Assets:Broker:Groww at -1,23,456.78 for months. It
// was found by a manual reconciliation against an external ledger, not by the
// product. These two tests are what make the product find the next one.
test("Doctor names each negative asset account and its exact balance", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        journal_valid: true,
        errors: [],
        transaction_count: 5,
        uncategorized_count: 0,
        negative_assets: [{ account: "Assets:Broker:Groww", balance: "-123456.78" }],
      }),
      { status: 200 },
    ),
  );
  render(<Doctor />);
  await waitFor(() =>
    expect(screen.getByText("Assets:Broker:Groww")).toBeInTheDocument(),
  );
  // Rendered from the Decimal string, never via a JS number.
  expect(screen.getByText("-₹1,23,456.78")).toBeInTheDocument();
  // It must not read as though the app fixed anything.
  expect(screen.getByText(/Nothing has been corrected automatically/i)).toBeInTheDocument();
});

test("Doctor says the balances are clean rather than staying silent", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        journal_valid: true,
        errors: [],
        transaction_count: 5,
        uncategorized_count: 0,
        negative_assets: [],
      }),
      { status: 200 },
    ),
  );
  render(<Doctor />);
  await waitFor(() =>
    expect(screen.getByText(/No asset holds a negative balance/i)).toBeInTheDocument(),
  );
  expect(screen.getByText("clean")).toBeInTheDocument();
});

test("navigating to /expense/monthly lands on Expense · Overview", async () => {
  localStorage.setItem("ss-period", JSON.stringify({ kind: "month", year: 2026, month: 8 }));
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    if (key.includes("available-years")) {
      return new Response(JSON.stringify({ calendar_years: [2026], fiscal_years: [2026] }), { status: 200 });
    }
    return new Response(
      JSON.stringify({
        side: "expense", start: "2026-08-01", end: "2026-08-31", funding: "all", account: null,
        granularity: "day",
        headline: { net: "412.00", gross: "412.00", reversals: "0.00", count: 1,
          funding_split: { card: "0.00", bank: "412.00", other: "0.00" } },
        series: [], lines: [],
        groups: [{ group: "Expenses:Food", amount: "412.00", pct: "100.0",
          items: [{ name: "Synth Diner", amount: "412.00", count: 1 }] }],
        exclude_one_time: false,
        one_time: { report_date: "2026-08-31", total: "0.00", count: 0, items: [] },
      }),
      { status: 200 },
    );
  });
  render(
    <MemoryRouter initialEntries={["/expense/monthly"]}>
      <App />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getAllByText("Food").length).toBeGreaterThanOrEqual(1));
  expect(screen.getByText("expense · overview")).toBeInTheDocument();
  expect(global.fetch).toHaveBeenCalledWith(
    "/api/reports/expense/overview?from=2026-08-01&to=2026-08-31&funding=all",
    expect.anything(),
  );
});

test("changing the period picker re-drives report URLs", async () => {
  // The Dashboard is always as of today (R30) and has no picker, so this runs
  // on Expense · Budget, a month-only screen that still follows the picker.
  const now = new Date();
  const month = now.getMonth() + 1;
  const spy = vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    if (key.includes("available-years")) {
      return new Response(
        JSON.stringify({ calendar_years: [2025, 2026], fiscal_years: [2025, 2026] }),
        { status: 200 },
      );
    }
    const budget = {
      checking_balance: "0", total_budgeted: "0", total_spent: "0", available_for_budgeting: "0",
      available_for_spending: "0", projected_month_end: "0", targets: [],
    };
    return new Response(JSON.stringify(budget), { status: 200 });
  });
  render(
    <MemoryRouter initialEntries={["/expense/budget"]}>
      <App />
    </MemoryRouter>,
  );
  // defaults to today's month/year, not a hardcoded one
  await waitFor(() =>
    expect(spy).toHaveBeenCalledWith(
      expect.stringContaining(`budget?year=${now.getFullYear()}&month=${month}`),
      expect.anything(),
    ),
  );
  // stepping back a month re-fetches with the new period (rolling into the
  // previous year in January)
  fireEvent.click(screen.getByLabelText("Previous month"));
  const prevYear = month === 1 ? now.getFullYear() - 1 : now.getFullYear();
  const prevMonth = month === 1 ? 12 : month - 1;
  await waitFor(() =>
    expect(spy).toHaveBeenCalledWith(
      expect.stringContaining(`budget?year=${prevYear}&month=${prevMonth}`),
      expect.anything(),
    ),
  );
});

test("GoalPlanner renders default goals and summary stats", () => {
  render(<GoalPlanner />);
  expect(screen.getByDisplayValue("Bike")).toBeInTheDocument();
  expect(screen.getByText("Goal Investment")).toBeInTheDocument();
  expect(screen.getByText("Goals Funded")).toBeInTheDocument();
});

test("GoalPlanner reassigns dueYear by drag order when a goal row is dragged to a new position", () => {
  const { container } = render(<GoalPlanner />);

  // Default order: Bike(due 0), Car(due 1), Marriage(due 2), Home construction(due 4).
  const rows = container.querySelectorAll(".goal-row");
  const bikeHandle = screen.getByLabelText("Reorder Bike");
  const marriageRow = rows[2];

  fireEvent.dragStart(bikeHandle);
  fireEvent.drop(marriageRow);

  // Bike moves after Marriage; the existing dueYear set [0,1,2,4] is reassigned by new
  // position, so Car/Marriage shift down to 0/1 and Bike takes Marriage's old slot (2).
  expect(screen.getByDisplayValue("Car")).toBeInTheDocument();
  const dueYearInputs = container.querySelectorAll(".goal-row input[type=number] + input[type=number]");
  expect((dueYearInputs[0] as HTMLInputElement).value).toBe("0");
  expect((dueYearInputs[2] as HTMLInputElement).value).toBe("2");
});

test("Sip renders projected totals for the default inputs", () => {
  render(<Sip />);
  expect(screen.getAllByText("Invested Amount").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Total Value").length).toBeGreaterThan(0);
});

test("Lumpsum renders projected totals for the default inputs", () => {
  render(<Lumpsum />);
  expect(screen.getAllByText("Total Value").length).toBeGreaterThan(0);
  expect(screen.getAllByText(/^Invested/).length).toBeGreaterThan(0);
});

test("Cagr computes the rate from the default inputs", () => {
  render(<Cagr />);
  expect(screen.getByText("CAGR")).toBeInTheDocument();
});

test("Salary renders the take-home summary for the default CTC", () => {
  render(<Salary />);
  expect(screen.getAllByText("Monthly Take-Home").length).toBeGreaterThan(0);
  expect(screen.getByText("New vs Old Regime Comparison")).toBeInTheDocument();
});

test("Fire walks through all five tabs and renders each one's readout", () => {
  render(<Fire />);

  expect(screen.getByText("Total Target Corpus")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Bucket Allocation" }));
  expect(screen.getByText("5-Bucket Plan")).toBeInTheDocument();
  expect(screen.getByText(/1\. Safety Net/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Growth Breakdown" }));
  expect(screen.getByText("Growth Bucket (B5) Breakdown")).toBeInTheDocument();
  expect(screen.getByText("Flexi Cap")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Accumulation Phase" }));
  expect(screen.getByText("Flat SIP")).toBeInTheDocument();
  expect(screen.getByText(/Required SIP/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Decumulation Phase" }));
  expect(screen.getByText(/YRS$|^Depleted Yr/)).toBeInTheDocument();
});

test("LedgerImport commits an uncategorized row instead of refusing the import", async () => {
  const responses: Record<string, unknown> = {
    "/api/import/detect": {
      matched: true,
      batch_id: "b1",
      fingerprint: "fp1",
      candidates: [
        {
          id: "c1",
          date: "2026-04-01",
          raw_narration: "CRED CARD BILL",
          suggested_category: null,
          amount: "8420.00",
          direction: "debit",
          dedup_status: "new",
          needs_account: false,
          source_account: "Assets:Bank:HDFC",
        },
      ],
    },
    "/api/accounts": { accounts: ["Assets:Bank:HDFC", "Expenses:Food", "Expenses:Fuel"] },
  };
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    if (key in responses) {
      return new Response(JSON.stringify(responses[key]), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  render(<LedgerImport />);

  const file = new File(["Date,Narration\n01/04/2026,CRED CARD BILL"], "hdfc.csv", {
    type: "text/csv",
  });
  const input = screen.getByLabelText("statement file") as HTMLInputElement;
  await waitFor(() => expect(input).toBeInTheDocument());
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() => expect(screen.getByText(/CRED CARD BILL/)).toBeInTheDocument());

  // suggested_category is null, so the real amount sits on
  // Expenses:Uncategorized. That row commits cleanly -- the backend has never
  // refused one -- and 96% of a real bank statement arrives this way, so
  // gating Commit on it meant ~250 hand-classifications before a single
  // statement could land. Counted now, not blocked.
  expect(screen.getByRole("button", { name: "Commit" })).not.toBeDisabled();
  expect(screen.getByText(/will land\s+uncategorized/i)).toBeInTheDocument();
});

test("LedgerImport still refuses to commit a row whose postings do not balance", async () => {
  // The gate that remains real: an imbalanced entry cannot be written to a
  // double-entry journal at all, unlike a missing category.
  const responses: Record<string, unknown> = {
    "/api/import/detect": {
      matched: true,
      batch_id: "b1",
      fingerprint: "fp1",
      candidates: [
        {
          id: "c1",
          date: "2026-04-01",
          raw_narration: "CRED CARD BILL",
          suggested_category: null,
          amount: "8420.00",
          direction: "debit",
          dedup_status: "new",
          needs_account: false,
          source_account: "Assets:Bank:HDFC",
        },
      ],
    },
    "/api/accounts": { accounts: ["Assets:Bank:HDFC", "Expenses:Food"] },
  };
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    if (key in responses) return new Response(JSON.stringify(responses[key]), { status: 200 });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  render(<LedgerImport />);
  const file = new File(["Date,Narration\n01/04/2026,CRED CARD BILL"], "hdfc.csv", {
    type: "text/csv",
  });
  const input = screen.getByLabelText("statement file") as HTMLInputElement;
  await waitFor(() => expect(input).toBeInTheDocument());
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(screen.getByText(/CRED CARD BILL/)).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Commit" })).not.toBeDisabled();

  const editable = document.querySelector(".ledger-review-code .cm-content") as HTMLElement;
  const view = EditorView.findFromDOM(editable);
  const docText = view!.state.doc.toString();
  // Break the balance: the category leg no longer matches the source leg.
  const at = docText.indexOf("8420.00");
  view!.dispatch({ changes: { from: at, to: at + "8420.00".length, insert: "8000.00" } });

  await waitFor(() => expect(screen.getByRole("button", { name: "Commit" })).toBeDisabled(), {
    timeout: 2000,
  });
});

test("LedgerImport saves a recategorization of the Uncategorized account", async () => {
  const responses: Record<string, unknown> = {
    "/api/import/detect": {
      matched: true,
      batch_id: "b1",
      fingerprint: "fp1",
      candidates: [
        {
          id: "c1",
          date: "2026-04-01",
          raw_narration: "CRED CARD BILL",
          suggested_category: null,
          amount: "8420.00",
          direction: "debit",
          dedup_status: "new",
          needs_account: false,
          source_account: "Assets:Bank:HDFC",
        },
      ],
    },
    "/api/accounts": { accounts: ["Assets:Bank:HDFC", "Expenses:Food", "Expenses:Fuel"] },
  };
  let lastPatchBody: Record<string, unknown> | null = null;
  vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
    const key = String(url);
    if (key in responses) {
      return new Response(JSON.stringify(responses[key]), { status: 200 });
    }
    if (key.startsWith("/api/review/") && init?.method === "POST") {
      lastPatchBody = JSON.parse(init.body as string);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  render(<LedgerImport />);

  const file = new File(["Date,Narration\n01/04/2026,CRED CARD BILL"], "hdfc.csv", {
    type: "text/csv",
  });
  const input = screen.getByLabelText("statement file") as HTMLInputElement;
  await waitFor(() => expect(input).toBeInTheDocument());
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() => expect(screen.getByText(/CRED CARD BILL/)).toBeInTheDocument());
  // Committable from the start -- the row balances, it is merely
  // unclassified. Classifying it here is an option, not a toll.
  expect(screen.getByRole("button", { name: "Commit" })).not.toBeDisabled();

  const editable = document.querySelector(".ledger-review-code .cm-content") as HTMLElement;
  const view = EditorView.findFromDOM(editable);
  expect(view).not.toBeNull();
  const docText = view!.state.doc.toString();
  // Recategorize by replacing the Expenses:Uncategorized account with a real
  // one — the amount is untouched.
  const acctStart = docText.indexOf("Expenses:Uncategorized");
  view!.dispatch({
    changes: { from: acctStart, to: acctStart + "Expenses:Uncategorized".length, insert: "Expenses:Food" },
  });

  await waitFor(() => expect(lastPatchBody).not.toBeNull(), { timeout: 2000 });
  expect(lastPatchBody).toMatchObject({
    splits: [{ account: "Expenses:Food", amount: "8420.00" }],
    source_account: "Assets:Bank:HDFC",
    raw_narration: "CRED CARD BILL",
  });
});

test("LedgerImport walks the template-creation wizard for an unrecognized format", async () => {
  const responses: Record<string, unknown> = {
    "/api/accounts": { accounts: ["Assets:Bank:HDFC"] },
    "/api/import/detect": {
      matched: false,
      filename: "card.csv",
      fingerprint: "fp-new",
      raw_rows: [["Date", "Narration", "Withdrawal Amt", "Deposit Amt"], ["01/04/2026", "CARD PURCHASE", "500.00", ""]],
      suggested_header_row: 0,
      suggested_roles: { date: "Date", narration: "Narration", debit: "Withdrawal Amt", credit: "Deposit Amt" },
      suggested_date_format: "%d/%m/%Y",
      delimiter: ",",
    },
  };
  let savedPayload: Record<string, unknown> | null = null;
  vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
    const key = String(url);
    if (key in responses) {
      return new Response(JSON.stringify(responses[key]), { status: 200 });
    }
    if (key === "/api/templates" && init?.body) {
      savedPayload = JSON.parse(init.body as string);
      return new Response(
        JSON.stringify({ saved: true, batch_id: "b2", fingerprint: "fp-new", candidates: [] }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  render(<LedgerImport />);

  const file = new File(["Date,Narration\n01/04/2026,CARD PURCHASE"], "card.csv", {
    type: "text/csv",
  });
  const input = screen.getByLabelText("statement file") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });

  await screen.findByText("New statement format — create a template");
  fireEvent.click(screen.getByText("Save template & Continue"));

  await waitFor(() => expect(savedPayload).not.toBeNull());
  expect((savedPayload as Record<string, unknown>).filename).toBe("card.csv");
  expect((savedPayload as Record<string, unknown>).fingerprint).toBe("fp-new");
});

test("LedgerImport: Template/Review tabs — Parse with template fills the Review tab", async () => {
  // The wizard (new format) splits into a Template tab (config) and a Review
  // tab (the editor). 'Parse with this template' parses and jumps to Review,
  // where the editor renders the parsed entries. Toggling back shows the config.
  const detect = {
    matched: false,
    filename: "stmt.xls",
    fingerprint: "fp-live",
    raw_rows: [
      ["Date", "Narration", "Withdrawal Amt", "Deposit Amt"],
      ["01/04/26", "SWIGGY ORDER", "450.00", ""],
    ],
    suggested_header_row: 0,
    suggested_roles: { date: "Date", narration: "Narration", debit: "Withdrawal Amt", credit: "Deposit Amt" },
    suggested_date_format: "%d/%m/%Y",
    delimiter: ",",
  };
  const parsed = [
    { id: "p1", date: "2026-04-01", raw_narration: "SWIGGY ORDER", suggested_category: "Expenses:Food", amount: "450.00", direction: "debit", dedup_status: "new", needs_account: false, source_account: "Assets:Checking:HDFC" },
    { id: "p2", date: "2026-04-02", raw_narration: "AMZN PURCHASE", suggested_category: null, amount: "1200.00", direction: "debit", dedup_status: "new", needs_account: false, source_account: "Assets:Checking:HDFC" },
  ];
  vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
    const key = String(url);
    if (key === "/api/accounts")
      return new Response(JSON.stringify({ accounts: ["Assets:Checking:HDFC", "Expenses:Food"] }), { status: 200 });
    if (key === "/api/import/detect")
      return new Response(JSON.stringify(detect), { status: 200 });
    if (key === "/api/templates" && init?.body) {
      const body = JSON.parse(init.body as string) as { save: boolean };
      return new Response(
        JSON.stringify(
          body.save
            ? { saved: true, batch_id: "b9", fingerprint: "fp-live", candidates: parsed }
            : { saved: false, candidates: parsed },
        ),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  render(<LedgerImport />);
  const file = new File(["x"], "stmt.xls");
  const input = screen.getByLabelText("statement file") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });

  // Template tab is active by default; the editor isn't rendered yet.
  await screen.findByText("New statement format — create a template");
  expect(document.querySelector(".ledger-review-code")).toBeNull();

  // Parse with the template → jumps to the Review tab and fills the editor.
  fireEvent.click(screen.getByRole("button", { name: /Parse with this template/i }));
  await waitFor(
    () => {
      const editable = document.querySelector(".ledger-review-code .cm-content") as HTMLElement;
      const view = EditorView.findFromDOM(editable);
      expect(view).not.toBeNull();
      const text = view!.state.doc.toString();
      expect(text).toContain("SWIGGY ORDER");
      expect(text).toContain("AMZN PURCHASE");
    },
    { timeout: 2000 },
  );
  expect(screen.getByText(/Review queue · 2/)).toBeInTheDocument();
  // The dead-end standalone Preview button is gone.
  expect(screen.queryByRole("button", { name: "Preview" })).toBeNull();

  // Toggle back to the Template tab → config visible again, editor unmounted.
  fireEvent.click(screen.getByRole("button", { name: "Template" }));
  expect(screen.getByText("New statement format — create a template")).toBeInTheDocument();
  expect(document.querySelector(".ledger-review-code")).toBeNull();
});

test("LedgerTransactions annotate save shows conflict message on 409", async () => {
  const txn = {
    txn_id: "txn1",
    date: "2026-03-18",
    narration: "Groww redemption",
    year_file_hash: "abc123",
    postings: [
      { account: "Assets:Investment:MutualFund:PPFCF", account_display: "Assets:Investment:MutualFund:PPFCF", amount: "-18000.00", tags: [], meta: {} },
      { account: "Assets:Bank:HDFC", account_display: "Assets:Bank:HDFC", amount: "21500.00", tags: [], meta: {} },
    ],
  };
  vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
    const key = String(url);
    if (key === "/api/transactions" && (!init || init.method === "GET")) {
      return new Response(JSON.stringify({ transactions: [txn] }), { status: 200 });
    }
    if (key.includes("/annotate")) {
      return new Response(JSON.stringify({ detail: "conflict" }), { status: 409 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  render(
    <MemoryRouter>
      <LedgerTransactions />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/Groww redemption/)).toBeInTheDocument());

  fireEvent.click(screen.getByText("Assets:Investment:MutualFund:PPFCF"));
  const unitsInput = screen.getByLabelText("units");
  fireEvent.change(unitsInput, { target: { value: "100.0" } });
  fireEvent.click(screen.getByText("Save"));

  await waitFor(() =>
    expect(screen.getByText(/changed since you loaded/i)).toBeInTheDocument(),
  );
});

test("LedgerTransactions shows each posting's amount and direction", async () => {
  const txn = {
    txn_id: "t1",
    date: "2024-06-01",
    narration: "SALARY ACME PAYROLL",
    year_file_hash: "h",
    postings: [
      { account: "Income:Salary", account_display: "Income:Salary", amount: "-50000.00", tags: [], meta: {} },
      { account: "Assets:Checking:HDFC", account_display: "Assets:Checking:HDFC", amount: "50000.00", tags: [], meta: {} },
    ],
  };
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    if (String(url) === "/api/transactions")
      return new Response(JSON.stringify({ transactions: [txn] }), { status: 200 });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  render(
    <MemoryRouter>
      <LedgerTransactions />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/SALARY ACME PAYROLL/)).toBeInTheDocument());

  // Money into the account is a positive (green) inflow; out is a negative (red) outflow.
  const inflow = screen.getByText("+₹50,000");
  const outflow = screen.getByText("−₹50,000");
  expect(inflow).toBeInTheDocument();
  expect(outflow).toBeInTheDocument();
  expect(inflow).toHaveClass("v-green");
  expect(outflow).toHaveClass("v-red");
});

test("LedgerTransactions: clicking one posting does NOT open the popup for a different posting with the same account name", async () => {
  // Two transactions that both have a posting under the same account path.
  // Before the fix, expanded was keyed on account string only, so both popups
  // would open simultaneously. After the fix, only the clicked one opens.
  const txns = [
    {
      txn_id: "txn-a",
      date: "2026-04-01",
      narration: "Groww purchase A",
      year_file_hash: "hash1",
      postings: [
        { account: "Assets:Investment:MutualFund:Groww", account_display: "Assets:Investment:MutualFund:Groww", amount: "-5000.00", tags: [], meta: {} },
        { account: "Assets:Bank:HDFC", account_display: "Assets:Bank:HDFC", amount: "5000.00", tags: [], meta: {} },
      ],
    },
    {
      txn_id: "txn-b",
      date: "2026-04-02",
      narration: "Groww purchase B",
      year_file_hash: "hash2",
      postings: [
        { account: "Assets:Investment:MutualFund:Groww", account_display: "Assets:Investment:MutualFund:Groww", amount: "-3000.00", tags: [], meta: {} },
        { account: "Assets:Bank:HDFC", account_display: "Assets:Bank:HDFC", amount: "3000.00", tags: [], meta: {} },
      ],
    },
  ];
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ transactions: txns }), { status: 200 }),
  );

  render(
    <MemoryRouter>
      <LedgerTransactions />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/Groww purchase A/)).toBeInTheDocument());

  // Both postings render — two buttons with the same account label exist.
  const buttons = screen.getAllByRole("button", { name: "Assets:Investment:MutualFund:Groww" });
  expect(buttons).toHaveLength(2);

  // Click only the first one.
  fireEvent.click(buttons[0]);

  // Exactly one "units" input should be visible (only the clicked popup opened).
  expect(screen.getAllByLabelText("units")).toHaveLength(1);
});

test("LedgerTransactions: Cancel button closes the annotation popup without saving", async () => {
  const txn = {
    txn_id: "txn-cancel",
    date: "2026-04-05",
    narration: "Cancel test txn",
    year_file_hash: "hashX",
    postings: [
      { account: "Assets:Investment:MutualFund:PPFCF", account_display: "Assets:Investment:MutualFund:PPFCF", amount: "-2000.00", tags: [], meta: {} },
    ],
  };
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ transactions: [txn] }), { status: 200 }),
  );

  render(
    <MemoryRouter>
      <LedgerTransactions />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/Cancel test txn/)).toBeInTheDocument());

  // Open the popup.
  fireEvent.click(screen.getByRole("button", { name: "Assets:Investment:MutualFund:PPFCF" }));
  expect(screen.getByLabelText("units")).toBeInTheDocument();

  // Click Cancel — popup should disappear, no fetch for annotate.
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByLabelText("units")).not.toBeInTheDocument();
});

test("LedgerTransactions: Escape key closes the annotation popup", async () => {
  const txn = {
    txn_id: "txn-esc",
    date: "2026-04-06",
    narration: "Esc test txn",
    year_file_hash: "hashY",
    postings: [
      { account: "Assets:Investment:MutualFund:PPFCF", account_display: "Assets:Investment:MutualFund:PPFCF", amount: "-1000.00", tags: [], meta: {} },
    ],
  };
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ transactions: [txn] }), { status: 200 }),
  );

  render(
    <MemoryRouter>
      <LedgerTransactions />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/Esc test txn/)).toBeInTheDocument());

  // Open the popup.
  fireEvent.click(screen.getByRole("button", { name: "Assets:Investment:MutualFund:PPFCF" }));
  expect(screen.getByLabelText("units")).toBeInTheDocument();

  // Press Escape — popup should close.
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByLabelText("units")).not.toBeInTheDocument();
});

test("LedgerTransactions: annotation popup closes after a successful save", async () => {
  const txn = {
    txn_id: "txn-save",
    date: "2026-04-10",
    narration: "Save close test txn",
    year_file_hash: "hashZ",
    postings: [
      { account: "Assets:Investment:MutualFund:PPFCF", account_display: "Assets:Investment:MutualFund:PPFCF", amount: "-4000.00", tags: [], meta: {} },
    ],
  };
  vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
    const key = String(url);
    if (key === "/api/transactions" && (!init || init.method === "GET" || !init.method)) {
      return new Response(JSON.stringify({ transactions: [txn] }), { status: 200 });
    }
    if (key.includes("/annotate")) {
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  render(
    <MemoryRouter>
      <LedgerTransactions />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/Save close test txn/)).toBeInTheDocument());

  // Open the popup.
  fireEvent.click(screen.getByRole("button", { name: "Assets:Investment:MutualFund:PPFCF" }));
  expect(screen.getByLabelText("units")).toBeInTheDocument();

  // Fill in a value and save.
  fireEvent.change(screen.getByLabelText("units"), { target: { value: "200.0" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  // On success the popup must close — the units input disappears.
  await waitFor(() =>
    expect(screen.queryByLabelText("units")).not.toBeInTheDocument(),
  );
  // No conflict error banner should appear.
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("LedgerTransactions: a non-investment account (checking) is a static chip with no units editor", async () => {
  // Units/NAV only apply to investment holdings. Clicking a bank leg must not
  // open a units editor — it isn't a clickable button at all.
  const txn = {
    txn_id: "txn-bank",
    date: "2026-04-12",
    narration: "BESCOM ELECTRICITY BILL",
    year_file_hash: "hashB",
    postings: [
      { account: "Expenses:Utilities", account_display: "Expenses:Utilities", amount: "1800.00", tags: [], meta: {} },
      { account: "Assets:Checking:HDFC", account_display: "Assets:Checking:HDFC", amount: "-1800.00", tags: [], meta: {} },
    ],
  };
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ transactions: [txn] }), { status: 200 }),
  );

  render(
    <MemoryRouter>
      <LedgerTransactions />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByText(/BESCOM ELECTRICITY BILL/)).toBeInTheDocument(),
  );

  // The checking account renders, but not as a button.
  expect(screen.getByText("Assets:Checking:HDFC")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Assets:Checking:HDFC" }),
  ).not.toBeInTheDocument();

  // No units editor anywhere — non-investment legs are not annotatable here.
  expect(screen.queryByLabelText("units")).not.toBeInTheDocument();
});

test("LedgerImport shows already-recorded transfers with a commit-anyway override", async () => {
  const responses: Record<string, unknown> = {
    "/api/accounts": { accounts: ["Assets:Bank:HDFC"] },
    "/api/import/detect": {
      matched: true,
      batch_id: "b1",
      fingerprint: "fp1",
      candidates: [
        {
          id: "c1",
          date: "2026-04-02",
          raw_narration: "HDFC CARD PAYMENT",
          suggested_category: null,
          amount: "5000.00",
          direction: "debit",
          dedup_status: "transfer_already_recorded",
          matched_txn_id: "abc123",
          flags: ["already recorded by transaction abc123"],
          needs_account: false,
          source_account: "Assets:Bank:HDFC",
        },
      ],
    },
  };
  let lastPatchBody: unknown = null;
  vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
    const key = String(url);
    if (key in responses) {
      return new Response(JSON.stringify(responses[key]), { status: 200 });
    }
    if (key.startsWith("/api/review/") && init?.method === "POST") {
      lastPatchBody = JSON.parse(init.body as string);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  render(<LedgerImport />);

  const file = new File(["Date,Narration\n02/04/2026,HDFC CARD PAYMENT"], "hdfc.csv", {
    type: "text/csv",
  });
  const input = screen.getByLabelText("statement file") as HTMLInputElement;
  await waitFor(() => expect(input).toBeInTheDocument());
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() =>
    expect(screen.getByText(/already recorded by transaction abc123/i)).toBeInTheDocument(),
  );

  fireEvent.click(screen.getByText("Commit anyway"));

  await waitFor(() =>
    expect(lastPatchBody).toEqual({ dedup_status: "transfer_match_candidate" }),
  );
  await waitFor(() =>
    expect(screen.queryByText(/already recorded by transaction abc123/i)).not.toBeInTheDocument(),
  );
});

test("CashflowRecurring renders cleared item and status text", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        summary: {
          stream_count: 1,
          cleared: { count: 1, amount: "499" },
          past_due: { count: 0, amount: "0" },
          upcoming: { count: 0, amount: "0" },
        },
        items: [
          {
            key: "STREAMFLIX|Expenses:Subscriptions",
            label: "STREAMFLIX",
            account: "Expenses:Subscriptions",
            cadence: "monthly",
            status: "cleared",
            direction: "out",
            amount: "499",
            expected_amount: "499",
            expected_day: 3,
            expected_date: "2026-06-03",
            last_seen: "2026-05-03",
            actual_dates: ["2026-06-03"],
          },
        ],
      }),
      { status: 200 },
    ),
  );

  render(
    <PeriodProvider>
      <CashflowRecurring />
    </PeriodProvider>,
  );

  await waitFor(() => expect(screen.getByText("STREAMFLIX")).toBeInTheDocument());
  expect(screen.getAllByText("Cleared").length).toBeGreaterThanOrEqual(1);
});

test("CashflowRecurring renders zero-state when no streams detected", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        summary: {
          stream_count: 0,
          cleared: { count: 0, amount: "0" },
          past_due: { count: 0, amount: "0" },
          upcoming: { count: 0, amount: "0" },
        },
        items: [],
      }),
      { status: 200 },
    ),
  );

  render(
    <PeriodProvider>
      <CashflowRecurring />
    </PeriodProvider>,
  );

  await waitFor(() =>
    expect(
      screen.getByText(/No recurring patterns detected yet/i),
    ).toBeInTheDocument(),
  );
});

test("CashflowRecurring renders second zero-state when streams detected but none in selected month", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        summary: {
          stream_count: 2,
          cleared: { count: 0, amount: "0" },
          past_due: { count: 0, amount: "0" },
          upcoming: { count: 0, amount: "0" },
        },
        items: [],
      }),
      { status: 200 },
    ),
  );

  render(
    <PeriodProvider>
      <CashflowRecurring />
    </PeriodProvider>,
  );

  await waitFor(() =>
    expect(
      screen.getByText(/No recurring items expected in/i),
    ).toBeInTheDocument(),
  );
  // Should show the stream count stat
  expect(screen.getByText("2")).toBeInTheDocument();
});

test("LiabilitiesInterest renders overview + per-account breakdown from the existing series", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        rows: [
          { account: "Liabilities:CreditCard:HDFC", month: "2026-04", amount: "250" },
          { account: "Liabilities:CreditCard:HDFC", month: "2026-05", amount: "150" },
          { account: "Liabilities:Loan:Home", month: "2026-04", amount: "34000" },
        ],
      }),
      { status: 200 },
    ),
  );

  render(<LiabilitiesInterest />);

  // total = 250 + 150 + 34000 = 34400
  await waitFor(() => expect(screen.getByText("₹34,400")).toBeInTheDocument());
  // per-account breakdown labels (short form), ranked: Home (34000) before CreditCard:HDFC (400)
  expect(screen.getByText("Loan:Home")).toBeInTheDocument();
  expect(screen.getByText("CreditCard:HDFC")).toBeInTheDocument();
  // overview month labels present
  expect(screen.getByText("26-04")).toBeInTheDocument();
  expect(screen.getByText("26-05")).toBeInTheDocument();
});

test("LiabilitiesInterest shows a zero state when there are no charges", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ rows: [] }), { status: 200 }),
  );
  render(<LiabilitiesInterest />);
  await waitFor(() => expect(screen.getByText(/no interest charges/i)).toBeInTheDocument());
});

test("ExpenseBudget populated: shows card labels and 'out of … budgeted' subtitle", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        checking_balance: "210000",
        total_budgeted: "47000",
        total_spent: "36300",
        available_for_budgeting: "163000",
        available_for_spending: "10700",
        projected_month_end: "199300",
        targets: [
          { category: "Expenses:Food", budgeted: "1000", spent: "1300", pct: 130.0, over_budget: true },
          { category: "Expenses:Rent", budgeted: "35000", spent: "35000", pct: 100.0, over_budget: false },
        ],
      }),
      { status: 200 },
    ),
  );
  render(<ExpenseBudget />);
  await waitFor(() => expect(screen.getByText("Food")).toBeInTheDocument());
  expect(screen.getByText("Rent")).toBeInTheDocument();
  expect(screen.getByText(/out of.*budgeted/i)).toBeInTheDocument();
});

test("ExpenseBudget zero state: shows 'No budget configured' and no BudgetCard", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        checking_balance: "210000",
        total_budgeted: "0",
        total_spent: "0",
        available_for_budgeting: "210000",
        available_for_spending: "0",
        projected_month_end: "210000",
        targets: [],
      }),
      { status: 200 },
    ),
  );
  render(<ExpenseBudget />);
  await waitFor(() => expect(screen.getByText("No budget configured")).toBeInTheDocument());
  // no BudgetCard should appear (no category names like "Food" or "Rent")
  expect(screen.queryByText("Food")).not.toBeInTheDocument();
  expect(screen.queryByText("Rent")).not.toBeInTheDocument();
});

test("LiabilitiesRepayment shows schedule for a loan with terms and the terms form for one without", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        loans: [
          {
            account: "Liabilities:Loan:Car",
            label: null,
            card: null,
            terms_set: true,
            terms_source: "file",
            outstanding: "100000",
            original_principal: "100000",
            annual_rate_pct: "12",
            term_months: 12,
            paid_instalments: null,
            start_month: null,
            emi: "8885",
            total_interest: "6619",
            payoff_month: "2027-01",
            schedule: [
              { month: 1, emi: "8885", principal: "7885", interest: "1000", balance: "92115" },
              { month: 12, emi: "8885", principal: "8797", interest: "88", balance: "0" },
            ],
          },
          {
            account: "Liabilities:Loan:Home",
            label: null,
            card: null,
            terms_set: false,
            terms_source: null,
            outstanding: "500000",
            original_principal: "500000",
            annual_rate_pct: null,
            term_months: null,
            paid_instalments: null,
            start_month: null,
            emi: null,
            total_interest: null,
            payoff_month: null,
            schedule: [],
          },
        ],
        emi_pool: null,
      }),
      { status: 200 },
    ),
  );

  render(<LiabilitiesRepayment />);

  // first loan selected by default: payoff month + a schedule balance visible
  await waitFor(() => expect(screen.getByText("2027-01")).toBeInTheDocument());
  expect(screen.getByText("92115")).toBeInTheDocument(); // a schedule balance cell

  // switch to the loan without terms -> the in-app terms form, no table
  fireEvent.click(screen.getByText("Home"));
  await waitFor(() => expect(screen.getByText(/terms are not known/i)).toBeInTheDocument());
  expect(screen.getByLabelText("Annual rate %")).toBeInTheDocument();
  expect(screen.queryByText(/loan-terms\.yaml/)).toBeNull();
});

test("LiabilitiesRepayment shows a zero state when there are no loans", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ loans: [], emi_pool: null }), { status: 200 }),
  );
  render(<LiabilitiesRepayment />);
  await waitFor(() => expect(screen.getByText(/no loans to repay/i)).toBeInTheDocument());
});

test("LiabilitiesBalance renders tree rows with I owe/Owed to me/Interest/APR", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        balances: [
          {
            account: "Liabilities:Loan:HDFCHome",
            drawn: "5000000",
            repaid: "0",
            balance: "5000000",
            interest: "34000",
            apr: "8.5",
            apr_source: "file",
            direction: "i_owe",
            amount: "5000000",
          },
        ],
        in_transit: { amount: "0.00", attributed: "0.00", unattributed: "0.00", payments: [] },
        cards_outstanding: "0.00",
        cards_net_owed: "0.00",
      }),
      { status: 200 },
    ),
  );
  render(<LiabilitiesBalance />);
  await waitFor(() => expect(screen.getByText("HDFCHome")).toBeInTheDocument());
  expect(screen.getByText("I owe")).toBeInTheDocument();
  expect(screen.getByText("Owed to me")).toBeInTheDocument();
  expect(screen.getByText("Interest")).toBeInTheDocument();
  expect(screen.queryByText("Drawn")).not.toBeInTheDocument();
  expect(screen.queryByText("Repaid")).not.toBeInTheDocument();
  expect(screen.getByText("APR")).toBeInTheDocument();
  expect(screen.getByText("8.5")).toBeInTheDocument();
});

test("LiabilitiesBalance shows the no-liabilities zero state", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        balances: [],
        in_transit: { amount: "0.00", attributed: "0.00", unattributed: "0.00", payments: [] },
        cards_outstanding: "0.00",
        cards_net_owed: "0.00",
      }),
      { status: 200 },
    ),
  );
  render(<LiabilitiesBalance />);
  await waitFor(() =>
    expect(screen.getByText(/no liabilities/i)).toBeInTheDocument(),
  );
});

// FY convention: April-start year. currentFY() === 2026 when "now" is 2026-06-26.

// Pin "now" so currentFY() === 2026 (April-start convention). shouldAdvanceTime
// keeps async (waitFor polling, promise microtasks) progressing under fake time.
function withFY2026<T>(fn: () => T): T {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-06-26"));
  return fn();
}

// LedgerRaw is now a view/edit raw-ledger screen scoped by calendar year;
// its tests live in rawScreen.test.tsx (they need a client.PUT mock).
