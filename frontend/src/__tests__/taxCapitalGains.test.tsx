import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { TaxCapitalGainsContent } from "@/screens/tax/TaxCapitalGains";

const mockTaxContext = vi.fn();
vi.mock("@/screens/tax/TaxLayout", () => ({
  TaxLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/screens/tax/taxContext", () => ({
  useTaxContext: () => mockTaxContext(),
}));

describe("TaxCapitalGains", () => {
  it("renders CG stat cards and items", () => {
    mockTaxContext.mockReturnValue({
      fy: 2026,
      setFy: vi.fn(),
      data: {
        period: "FY 2025-26",
        items: [
          { kind: "capital_gain_ltcg", amount: "80000", source_refs: ["t1"] },
          { kind: "capital_gain_stcg", amount: "20000", source_refs: ["t2"] },
        ],
        deductions: [],
        summary: {
          gross_ordinary_income: "0",
          capital_gains: { ltcg: "80000", stcg: "20000", crypto: "0", debt: "0" },
          prepaid_tax: "0",
          tax_paid_direct: "0",
          estimate: null,
          rules_configured: false,
          uncovered: [],
        },
      },
    });
    render(<MemoryRouter><TaxCapitalGainsContent /></MemoryRouter>);
    expect(screen.getByText("LTCG")).toBeInTheDocument();
    expect(screen.getByText("STCG")).toBeInTheDocument();
    expect(screen.getAllByText(/80,000/).length).toBeGreaterThan(0);
  });

  it("discloses that equity/stock gains are not yet included, and that cost basis excludes charges", () => {
    mockTaxContext.mockReturnValue({
      fy: 2026,
      setFy: vi.fn(),
      data: {
        period: "FY 2025-26",
        items: [],
        deductions: [],
        summary: {
          gross_ordinary_income: "0",
          capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "0" },
          prepaid_tax: "0",
          tax_paid_direct: "0",
          estimate: null,
          rules_configured: false,
          uncovered: [],
        },
      },
    });
    render(<MemoryRouter><TaxCapitalGainsContent /></MemoryRouter>);
    expect(
      screen.getByText(/equity and stock sales are not yet included in this view/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/exclude[s]? brokerage/i)).toBeInTheDocument();
  });

  it("names the undecided-treatment bucket and says why it is outside the tax figure", () => {
    mockTaxContext.mockReturnValue({
      fy: 2026,
      setFy: vi.fn(),
      data: {
        period: "FY 2025-26",
        items: [
          { kind: "capital_gain_debt", amount: "5000", source_refs: ["t3"] },
          { kind: "capital_gain_unclassified", amount: "70000", source_refs: ["t4"] },
        ],
        deductions: [],
        summary: {
          gross_ordinary_income: "0",
          capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "5000", unclassified: "70000" },
          prepaid_tax: "0",
          tax_paid_direct: "0",
          estimate: null,
          rules_configured: false,
          uncovered: ["capital_gain_debt", "capital_gain_unclassified"],
        },
      },
    });
    render(<MemoryRouter><TaxCapitalGainsContent /></MemoryRouter>);
    // The label AND the reason: a row with no explanation is the defect.
    expect(screen.getByText("Capital gains (treatment undecided)")).toBeInTheDocument();
    expect(
      screen.getByText(/does not classify this fund as equity or debt.*not in the tax figure/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/the figure above is too low/i)).toBeInTheDocument();
    // The debt row keeps its own label and carries no such reason.
    expect(screen.getByText("Capital gains (debt)")).toBeInTheDocument();
    expect(screen.getAllByText(/does not classify this fund/i)).toHaveLength(1);
  });
});
