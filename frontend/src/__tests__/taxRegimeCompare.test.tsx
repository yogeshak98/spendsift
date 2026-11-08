import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { TaxRegimeCompareContent } from "@/screens/tax/TaxRegimeCompare";

const mockTaxContext = vi.fn();
vi.mock("@/screens/tax/TaxLayout", () => ({
  TaxLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/screens/tax/taxContext", () => ({
  useTaxContext: () => mockTaxContext(),
}));

const REGIME = {
  exempt_allowances: "0",
  total_deductions: "150000",
  taxable_income: "350000",
  slab_tax: "12500",
  rebate: "12500",
  surcharge: "0",
  cess: "0",
  capital_gains_tax: "0",
  total: "0",
};

describe("TaxRegimeCompare", () => {
  it("renders not-configured callout when estimate absent", () => {
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
    render(<MemoryRouter><TaxRegimeCompareContent /></MemoryRouter>);
    expect(screen.getByText(/not available/i)).toBeInTheDocument();
  });

  it("renders regime columns when estimate present", () => {
    mockTaxContext.mockReturnValue({
      fy: 2026,
      setFy: vi.fn(),
      data: {
        period: "FY 2025-26",
        items: [],
        deductions: [],
        summary: {
          gross_ordinary_income: "500000",
          capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "0" },
          prepaid_tax: "0",
          tax_paid_direct: "0",
          estimate: {
            new_regime: { ...REGIME, total_deductions: "0", taxable_income: "500000", total: "31200" },
            old_regime: REGIME,
            lower_tax_regime: "old",
            balance_due: "0",
          },
          rules_configured: true,
          uncovered: [],
          // The pill is a claim about which regime wins, so it is only
          // printable from a comparison both figures were computed for.
          comparison_applicable: true,
          comparison_complete: true,
        },
      },
    });
    render(<MemoryRouter><TaxRegimeCompareContent /></MemoryRouter>);
    expect(screen.getByText(/Old regime/i)).toBeInTheDocument();
    expect(screen.getByText(/New regime/i)).toBeInTheDocument();
    expect(screen.getAllByText(/recommended/i).length).toBeGreaterThan(0);
  });
});

/** The pill says "recommended". The engine applies HRA s.10(13A) + LTA s.10(5)
 * under the old regime only, so with no declared exemption the old-regime
 * column is computed without reliefs it would really get: its tax is an upper
 * bound and no regime can be named the winner. */
describe("TaxRegimeCompare honesty about an incomplete comparison", () => {
  const estimate = {
    new_regime: { ...REGIME, exempt_allowances: "0", total_deductions: "0", total: "100000" },
    old_regime: { ...REGIME, exempt_allowances: "240000", total: "80000" },
    lower_tax_regime: "old",
    balance_due: "20000",
    balance_due_regime: "old",
  };

  const ctx = (summaryOver: Record<string, unknown>) => ({
    fy: 2026,
    setFy: vi.fn(),
    data: {
      period: "FY 2025-26",
      items: [],
      deductions: [],
      summary: {
        gross_ordinary_income: "1200000",
        capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "0" },
        prepaid_tax: "0",
        tax_paid_direct: "0",
        estimate,
        rules_configured: true,
        uncovered: [],
        salary_is_net: false,
        declared_tds_at_or_above_gross: false,
        unrecognised_income_keys: [],
        declared_regime: null,
        comparison_applicable: true,
        comparison_complete: true,
        provenance: {
          gross_salary: {
            source: "declared",
            value: "1200000",
            ignored_amount: "0",
            ignored_postings: 0,
          },
          tds: { source: "ledger", value: "0", ignored_amount: "0", ignored_postings: 0 },
        },
        ...summaryOver,
      },
    },
  });

  const declaredGross = {
    gross_salary: {
      source: "declared",
      value: "1200000",
      ignored_amount: "0",
      ignored_postings: 0,
    },
    tds: { source: "ledger", value: "0", ignored_amount: "0", ignored_postings: 0 },
  };
  const ledgerGross = {
    gross_salary: {
      source: "ledger",
      value: "900000",
      ignored_amount: "0",
      ignored_postings: 0,
    },
    tds: { source: "ledger", value: "0", ignored_amount: "0", ignored_postings: 0 },
  };

  // The pill is asserted by its EXACT text, not a /recommended/ regex: the
  // honesty copy for the incomplete states contains the word too ("neither is
  // recommended"), so a regex here would pass whether or not the pill rendered.
  it("recommends a regime when the comparison is complete", () => {
    mockTaxContext.mockReturnValue(ctx({ comparison_complete: true }));
    render(<MemoryRouter><TaxRegimeCompareContent /></MemoryRouter>);
    expect(screen.getByText("recommended")).toBeInTheDocument();
    expect(screen.queryByText(/upper bound/i)).not.toBeInTheDocument();
  });

  it("withholds the pill and names BOTH causes of a missing exemption, each with its own remedy", () => {
    // The response cannot say which cause this is -- the figure was never
    // declared, or it was declared and the FY's rules file does not authorise
    // exemptions -- so neither remedy may be the unconditional lead. A user
    // who has already declared the figure must not be told to declare it.
    mockTaxContext.mockReturnValue(
      ctx({ comparison_complete: false, provenance: declaredGross }),
    );
    render(<MemoryRouter><TaxRegimeCompareContent /></MemoryRouter>);
    expect(screen.queryByText("recommended")).not.toBeInTheDocument();
    expect(screen.getByText(/If you have not declared the figure yet/i)).toBeInTheDocument();
    // Still links to the form, which is where that figure is entered.
    expect(screen.getByRole("link", { name: /declared-inputs form/i })).toHaveAttribute(
      "href",
      "/tax/overview",
    );
    // No dead-end instruction: neither an imperative to declare what may
    // already be declared, nor a promise that entering 0 ends this state.
    expect(screen.queryByText(/Declare your exempt HRA \+ LTA/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/completes the comparison/i)).not.toBeInTheDocument();
    // The other cause's remedy is actionable: file, block, key AND value, and
    // the rules file is distinguished from the inputs file the form writes.
    expect(screen.getByText("allows_exemptions: true")).toBeInTheDocument();
    expect(screen.getByText("old_regime:")).toBeInTheDocument();
    expect(screen.getByText("config/tax/2026.yaml")).toBeInTheDocument();
    expect(screen.getByText("config/tax/2026-inputs.yaml")).toBeInTheDocument();
    expect(screen.getAllByText(/upper bound/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/withdrawn under the new one/i)).toBeInTheDocument();
  });

  it("shows neither a verdict nor a bound when the comparison does not apply", () => {
    mockTaxContext.mockReturnValue(
      ctx({ comparison_applicable: false, comparison_complete: false, provenance: ledgerGross }),
    );
    render(<MemoryRouter><TaxRegimeCompareContent /></MemoryRouter>);
    expect(screen.queryByText("recommended")).not.toBeInTheDocument();
    // A bound would be a claim about a comparison that is not about this user.
    expect(screen.queryByText(/upper bound/i)).not.toBeInTheDocument();
    expect(screen.getByText(/not what decides your tax here/i)).toBeInTheDocument();
  });

  it("shows neither a verdict nor a bound when nothing is declared at all", () => {
    // The salary base is then a net-of-TDS bank credit, so BOTH regimes are
    // understated -- an upper bound on the old one would contradict the
    // net-salary notice already on screen.
    mockTaxContext.mockReturnValue(
      ctx({ comparison_complete: false, provenance: ledgerGross }),
    );
    render(<MemoryRouter><TaxRegimeCompareContent /></MemoryRouter>);
    expect(screen.queryByText("recommended")).not.toBeInTheDocument();
    expect(screen.queryByText(/upper bound/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Both.*regimes are understated/i)).toBeInTheDocument();
  });

  it("shows the exempt-allowance line each regime actually applied", () => {
    mockTaxContext.mockReturnValue(ctx({}));
    render(<MemoryRouter><TaxRegimeCompareContent /></MemoryRouter>);
    expect(screen.getByText(/Exempt allowances \(HRA \+ LTA\)/i)).toBeInTheDocument();
    expect(screen.getByText(/2,40,000/)).toBeInTheDocument();
  });
});
