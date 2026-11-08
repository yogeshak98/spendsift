import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { TaxIncomeContent } from "@/screens/tax/TaxIncome";

const mockTaxContext = vi.fn();
vi.mock("@/screens/tax/TaxLayout", () => ({
  TaxLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/screens/tax/taxContext", () => ({
  useTaxContext: () => mockTaxContext(),
}));

describe("TaxIncome", () => {
  // The gratuity ceiling panel fetches its own all-time route; answering
  // "no gratuity" keeps it off these cases. Its own behaviour is asserted in
  // `taxGratuityCeiling.test.tsx`.
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          ceiling: null,
          recorded: "0",
          unused: null,
          journal_start: "",
          has_gratuity: false,
        }),
        { status: 200 },
      ),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows salary and interest rows, excludes CG rows", () => {
    mockTaxContext.mockReturnValue({
      fy: 2026,
      setFy: vi.fn(),
      data: {
        period: "FY 2025-26",
        items: [
          { kind: "salary", amount: "700000", source_refs: ["t1", "t2"], treatment: "taxed" },
          { kind: "interest", amount: "15000", source_refs: ["t3"], treatment: "taxed" },
          { kind: "capital_gain_ltcg", amount: "50000", source_refs: ["t4"], treatment: "taxed" },
        ],
        deductions: [],
        summary: {
          gross_ordinary_income: "715000",
          capital_gains: { ltcg: "50000", stcg: "0", crypto: "0", debt: "0" },
          prepaid_tax: "0",
          tax_paid_direct: "0",
          estimate: null,
          rules_configured: false,
          uncovered: [],
        },
      },
    });
    render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(screen.getByText("Salary")).toBeInTheDocument();
    expect(screen.getByText("Interest")).toBeInTheDocument();
    expect(screen.queryByText(/Capital gains.*LTCG/i)).not.toBeInTheDocument();
  });

  const base = {
    period: "FY 2026-27",
    items: [
      { kind: "salary", amount: "900000", source_refs: ["t1"], treatment: "taxed" },
      { kind: "interest", amount: "15000", source_refs: ["t9"], treatment: "taxed" },
    ],
    deductions: [],
    // gross_ordinary_income = declared gross salary + LEDGER interest, so it is
    // deliberately NOT equal to provenance.gross_salary.value (salary only).
    summary: {
      gross_ordinary_income: "1215000",
      capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "0" },
      prepaid_tax: "0",
      tax_paid_direct: "0",
      estimate: null,
      rules_configured: false,
      uncovered: [],
      provenance: {
        gross_salary: {
          source: "declared",
          value: "1200000",
          ignored_amount: "900000",
          ignored_postings: 12,
        },
        tds: { source: "ledger", value: "60000", ignored_amount: "0", ignored_postings: 0 },
      },
      salary_is_net: false,
      declared_tds_at_or_above_gross: false,
      unrecognised_income_keys: [],
    },
  };

  // deep-merge just the summary keys a case needs
  const withSummary = (over: Record<string, unknown>) => ({
    fy: 2026,
    setFy: vi.fn(),
    data: { ...base, summary: { ...base.summary, ...over } },
  });

  it("states that gross salary was declared and names the ledger amount it ignored", () => {
    mockTaxContext.mockReturnValue(withSummary({}));
    render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(
      screen.getByText(/Gross salary .*12,00,000.*declared/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/12 ledger transactions totalling .*9,00,000 ignored/i),
    ).toBeInTheDocument();
    // The headline is gross_ordinary_income (salary + ledger interest), NOT the
    // salary-only provenance value: rendering the latter would understate gross
    // income by the year's interest.
    expect(screen.getByText("₹12,15,000")).toBeInTheDocument();
    // The salary ROW still shows the ledger net, which is not in the total
    // above it. The note is what stops the visible numbers reading as broken.
    expect(
      screen.getByText(/Ledger salary, net of TDS .* declared gross instead/i),
    ).toBeInTheDocument();
  });

  it("does not annotate the salary row when the gross came from the ledger", () => {
    mockTaxContext.mockReturnValue(
      withSummary({
        gross_ordinary_income: "915000",
        provenance: {
          ...base.summary.provenance,
          gross_salary: {
            source: "ledger",
            value: "900000",
            ignored_amount: "0",
            ignored_postings: 0,
          },
        },
      }),
    );
    render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(screen.queryByText(/declared gross instead/i)).not.toBeInTheDocument();
  });

  // `source: "ledger"` means no gross was declared, which is exactly the
  // condition that makes the ledger salary NET of TDS -- and the condition
  // under which the sibling notice says so. Calling that figure "gross" next
  // to the number inverts this whole unit in its own copy, so this asserts
  // against the provenance line itself rather than the document: the Income
  // stat label legitimately contains the word "gross".
  it("never calls the ledger salary gross, and says it is net of TDS", () => {
    mockTaxContext.mockReturnValue(
      withSummary({
        gross_ordinary_income: "915000",
        provenance: {
          ...base.summary.provenance,
          gross_salary: {
            source: "ledger",
            value: "900000",
            ignored_amount: "0",
            ignored_postings: 0,
          },
        },
      }),
    );
    const { container } = render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    const prov = container.querySelector(".figure-prov");
    expect(prov).not.toBeNull();
    expect(prov?.textContent).toBe("Salary \u20b99,00,000, from the ledger \u2014 net of TDS.");
    expect(prov?.textContent).not.toMatch(/gross/i);
    expect(prov?.textContent).not.toMatch(/declared/i);
    expect(screen.queryByText(/ignored/i)).not.toBeInTheDocument();
  });

  it("warns that salary is net of TDS and says to declare gross", () => {
    mockTaxContext.mockReturnValue(withSummary({ salary_is_net: true }));
    render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(screen.getByText(/Declare your gross salary/i)).toBeInTheDocument();
    expect(screen.getByText(/payslip year-to-date gross/i)).toBeInTheDocument();
    expect(screen.getByText("config/tax/2026-inputs.yaml")).toBeInTheDocument();
  });

  it("does not warn about net salary when gross was declared", () => {
    mockTaxContext.mockReturnValue(withSummary({ salary_is_net: false }));
    render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(screen.queryByText(/Declare your gross salary/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/payslip year-to-date gross/i)).not.toBeInTheDocument();
  });

  // A dropped `gross_salary` key is read on THIS screen: without it the
  // net-salary notice tells a user who did declare gross to declare it again.
  it("names a dropped income key on the income screen", () => {
    mockTaxContext.mockReturnValue(
      withSummary({ unrecognised_income_keys: ["gross_salery"] }),
    );
    render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(screen.getByText(/does not\s+recognise/i)).toBeInTheDocument();
    expect(screen.getByText("gross_salery")).toBeInTheDocument();
  });

  it("says nothing about dropped keys on the income screen when every key was recognised", () => {
    // `unrecognised_income_keys` is the ONLY field off the base fixture here.
    mockTaxContext.mockReturnValue(withSummary({ unrecognised_income_keys: [] }));
    render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(screen.queryByText(/does not\s+recognise/i)).not.toBeInTheDocument();
    expect(screen.queryByText("gross_salery")).not.toBeInTheDocument();
  });

  // ---- the two-panel split, grouped on `treatment` ------------------------
  // Fictional figures throughout: no amount, account or name from the real
  // journal appears in a fixture.
  const grouped = {
    period: "FY 2026-27",
    items: [
      { kind: "salary", amount: "500000", source_refs: ["s1"], treatment: "taxed" },
      { kind: "interest", amount: "2000", source_refs: ["i1"], treatment: "taxed" },
      { kind: "exempt_gratuity", amount: "430000", source_refs: ["g1"], treatment: "exempt" },
      {
        kind: "exempt_leave_encashment",
        amount: "120000",
        source_refs: ["l1"],
        treatment: "exempt",
      },
      { kind: "refund", amount: "3300", source_refs: ["r1"], treatment: "not_income" },
      {
        kind: "transfers_from_people",
        amount: "7700",
        source_refs: ["p1", "p2"],
        treatment: "not_income",
      },
      { kind: "unclassified_income", amount: "50", source_refs: ["u1"], treatment: "unknown" },
      // Taxed, but it has its own screen.
      { kind: "capital_gain_ltcg", amount: "90000", source_refs: ["c1"], treatment: "taxed" },
      // A tax PAYMENT: `treatment: null`, not income at all.
      { kind: "prepaid_tax", amount: "40000", source_refs: ["x1"], treatment: null },
    ],
    deductions: [],
    summary: { ...base.summary },
  };
  const groupedCtx = () => ({ fy: 2026, setFy: vi.fn(), data: grouped });

  /** Which treatment-group heading a row's label sits under, by walking the
   * rendered tbody in order. Asserts GROUP MEMBERSHIP, which a text-presence
   * assertion cannot: "Not identified" is both a group heading and a row
   * label, so `getByText` alone cannot tell the two apart. */
  function groupOfLabel(container: HTMLElement, label: string): string | null {
    let current: string | null = null;
    for (const tr of Array.from(container.querySelectorAll("tbody tr"))) {
      const head = tr.querySelector("td.treat-group");
      if (head !== null) {
        current = head.textContent;
        continue;
      }
      const cell = tr.querySelector("td.label");
      if (cell !== null && cell.firstChild?.textContent?.trim() === label) return current;
    }
    return null;
  }

  // 1. The prefix exclusion. Asserts the LABEL is absent rather than counting
  // rows: a row count passes whether or not capital gains are filtered out,
  // because the excluded panel would simply hold one more row.
  it("keeps capital gains off the income screen entirely", () => {
    mockTaxContext.mockReturnValue(groupedCtx());
    render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(screen.queryByText("Capital gains (LTCG)")).not.toBeInTheDocument();
    expect(screen.queryByText("₹90,000")).not.toBeInTheDocument();
  });

  // 2. `treatment: null` is a tax payment, not income.
  it("keeps tax payments off the income screen", () => {
    mockTaxContext.mockReturnValue(groupedCtx());
    render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(screen.queryByText("Prepaid tax (TDS / advance)")).not.toBeInTheDocument();
    expect(screen.queryByText("₹40,000")).not.toBeInTheDocument();
  });

  // 3. The design's central honesty claim: `unknown` is not `not_income`.
  it("files an unidentified account under Not identified, never under Not income", () => {
    mockTaxContext.mockReturnValue(groupedCtx());
    const { container } = render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(groupOfLabel(container, "Not identified")).toBe("Not identified");
    expect(groupOfLabel(container, "Not identified")).not.toBe("Not income");
    // ...and it says we cannot say, not that it is untaxable.
    expect(
      screen.getByText(/No prefix rule names this account.*figure above is too low/),
    ).toBeInTheDocument();
  });

  it("puts the exempt and not-income rows in their own groups", () => {
    mockTaxContext.mockReturnValue(groupedCtx());
    const { container } = render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(groupOfLabel(container, "Gratuity")).toBe("Exempt on leaving a job");
    expect(groupOfLabel(container, "Leave encashment")).toBe("Exempt on leaving a job");
    expect(groupOfLabel(container, "Refunds")).toBe("Not income");
    expect(groupOfLabel(container, "Transfers from people")).toBe("Not income");
    // The taxed rows are in the other panel, under no treatment group.
    expect(groupOfLabel(container, "Salary")).toBeNull();
    expect(groupOfLabel(container, "Interest")).toBeNull();
    expect(screen.getByText("Reaches your tax figure")).toBeInTheDocument();
  });

  // 6. Each excluded row says why, in the ledger's own terms.
  it("carries each excluded row's explanation, including the unseparated leave split", () => {
    mockTaxContext.mockReturnValue(groupedCtx());
    render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    expect(
      screen.getByText(
        /s\.10\(10AA\)\. Partly taxable — the exempt and taxable parts are not separated in the ledger, only described in a note on the transaction\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("s.10(10)")).toBeInTheDocument();
    expect(screen.getByText("Reversals of money you paid, not earnings.")).toBeInTheDocument();
  });

  it("totals the excluded group in its own panel head and tile", () => {
    mockTaxContext.mockReturnValue(groupedCtx());
    const { container } = render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    // 430000 + 120000 + 3300 + 7700 + 50, and nothing from the taxed panel,
    // the capital gain or the tax payment.
    expect(container.querySelector(".panel-head .tot")?.textContent).toBe("\u20b95,61,050");
    const tile = Array.from(container.querySelectorAll(".stat")).find(
      (el) => el.querySelector(".l")?.textContent === "Outside your tax figure",
    );
    expect(tile?.querySelector(".n")?.textContent).toBe("\u20b95,61,050");
    expect(tile?.querySelector(".sub")?.textContent).toBe("5 rows, 6 transactions");
  });

  // The two tiles are not addends of each other: the left is the DECLARED
  // gross, the right is a ledger total. Any arithmetic between them would be
  // an invented relationship.
  it("asserts no arithmetic between the two tiles", () => {
    mockTaxContext.mockReturnValue(groupedCtx());
    const { container } = render(<MemoryRouter><TaxIncomeContent /></MemoryRouter>);
    const stats = container.querySelector(".stats")?.textContent ?? "";
    expect(stats).not.toMatch(/of which/i);
    expect(stats).not.toMatch(/%/);
    expect(stats).not.toMatch(/total of/i);
  });
});
