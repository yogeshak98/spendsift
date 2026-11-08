import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { TaxDeductionsContent } from "@/screens/tax/TaxDeductions";

const mockTaxContext = vi.fn();
vi.mock("@/screens/tax/TaxLayout", () => ({
  TaxLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/screens/tax/taxContext", () => ({
  useTaxContext: () => mockTaxContext(),
}));

describe("TaxDeductions", () => {
  it("shows deduction rows", () => {
    mockTaxContext.mockReturnValue({
      fy: 2026,
      setFy: vi.fn(),
      data: {
        period: "FY 2025-26",
        items: [],
        deductions: [
          { section: "80c", declared: "150000", applied_old: "150000", applied_new: "0" },
          { section: "80d", declared: "25000", applied_old: "25000", applied_new: "0" },
        ],
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
    render(<MemoryRouter><TaxDeductionsContent /></MemoryRouter>);
    expect(screen.getAllByText(/80C/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/80D/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/user-declared/i)).toBeInTheDocument();
  });
});
