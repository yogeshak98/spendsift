import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { TaxOverviewContent } from "@/screens/tax/TaxOverview";

const mockTaxContext = vi.fn();
vi.mock("@/screens/tax/TaxLayout", () => ({
  TaxLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/screens/tax/taxContext", () => ({
  useTaxContext: () => mockTaxContext(),
}));

// The overview hosts the declared-inputs form, which reads its OWN endpoint.
// These tests are about the screen's copy, not the form, so that read is left
// pending: the form sits in its reading state and cannot update state after a
// test has finished. The form has its own suite (`taxInputsForm.test.tsx`), and
// `taxWorkspaceRepair.test.tsx` covers the two rendered together.
beforeEach(() => {
  vi.spyOn(global, "fetch").mockImplementation(() => new Promise(() => {}));
});
afterEach(() => vi.restoreAllMocks());

function wrap(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe("TaxOverview", () => {
  it("shows loading when data is null", () => {
    mockTaxContext.mockReturnValue({ data: null, fy: 2026, setFy: vi.fn() });
    wrap(<TaxOverviewContent />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("does not sit on a loading state when the workspace failed to load", () => {
    // `TaxLayout` renders a form-fixable coded error ABOVE the children now, so
    // a screen that answered a failure with "Loading…" would give a second,
    // wrong account of the same situation.
    mockTaxContext.mockReturnValue({
      data: null,
      error: "422 E1059",
      fy: 2026,
      setFy: vi.fn(),
    });
    wrap(<TaxOverviewContent />);
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    // ...and the form, which is the repair tool, is still mounted (here on its
    // own pending read -- see the stub above).
    expect(screen.getByText(/Reading your declared inputs/i)).toBeInTheDocument();
  });

  it("shows not-configured callout when rules_configured false", () => {
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
          prepaid_tax: "25000",
          tax_paid_direct: "0",
          estimate: null,
          rules_configured: false,
          uncovered: [],
        },
      },
    });
    wrap(<TaxOverviewContent />);
    expect(screen.getByText(/tax rules not configured/i)).toBeInTheDocument();
    expect(screen.getByText(/5,00,000/)).toBeInTheDocument();
    expect(screen.getByText(/25,000/)).toBeInTheDocument();
  });

  it("shows regime estimate stats when rules configured", () => {
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
          prepaid_tax: "25000",
          tax_paid_direct: "0",
          estimate: {
            new_regime: {
              total_deductions: "0",
              taxable_income: "500000",
              slab_tax: "30000",
              rebate: "0",
              surcharge: "0",
              cess: "1200",
              capital_gains_tax: "0",
              total: "31200",
            },
            old_regime: {
              total_deductions: "150000",
              taxable_income: "350000",
              slab_tax: "12500",
              rebate: "12500",
              surcharge: "0",
              cess: "0",
              capital_gains_tax: "0",
              total: "0",
            },
            lower_tax_regime: "old",
            balance_due: "-25000",
          },
          rules_configured: true,
          uncovered: [],
        },
      },
    });
    wrap(<TaxOverviewContent />);
    expect(screen.getAllByText(/estimated refund/i).length).toBeGreaterThan(0);
  });

  const base = {
    period: "FY 2026-27",
    items: [],
    deductions: [],
    summary: {
      gross_ordinary_income: "900000",
      capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "0" },
      prepaid_tax: "60000",
      tax_paid_direct: "0",
      estimate: null,
      rules_configured: true,
      uncovered: [],
      provenance: {
        gross_salary: {
          source: "ledger",
          value: "900000",
          ignored_amount: "0",
          ignored_postings: 0,
        },
        tds: { source: "ledger", value: "60000", ignored_amount: "0", ignored_postings: 0 },
      },
      salary_is_net: false,
      declared_tds_at_or_above_gross: false,
      unrecognised_income_keys: [],
    },
  };

  const withSummary = (over: Record<string, unknown>) => ({
    fy: 2026,
    setFy: vi.fn(),
    data: { ...base, summary: { ...base.summary, ...over } },
  });

  it("names an unrecognised income key and the file it was typed in", () => {
    mockTaxContext.mockReturnValue(withSummary({ unrecognised_income_keys: ["gross_salery"] }));
    wrap(<TaxOverviewContent />);
    expect(screen.getByText(/does not\s+recognise/i)).toBeInTheDocument();
    expect(screen.getByText("gross_salery")).toBeInTheDocument();
    expect(screen.getByText("config/tax/2026-inputs.yaml")).toBeInTheDocument();
    // The ordinary typo case must NOT reach for the malformed-shape copy.
    expect(screen.queryByText(/not a set of key\/value lines/i)).not.toBeInTheDocument();
  });

  // The backend reports a non-mapping `income:` (a scalar, or the YAML list a
  // hand-edit produces) as the single entry "income". `income` IS a recognised
  // key -- only its SHAPE is wrong, and both correct keys may already sit
  // underneath it -- so the unrecognised-key copy would send the user to fix
  // the one thing that is right.
  it("describes a malformed income block by its shape, not as an unrecognised key", () => {
    mockTaxContext.mockReturnValue(withSummary({ unrecognised_income_keys: ["income"] }));
    wrap(<TaxOverviewContent />);
    expect(screen.getByText(/Rewrite the/i)).toBeInTheDocument();
    expect(screen.getByText(/not a set of key\/value lines/i)).toBeInTheDocument();
    expect(screen.getByText(/the whole block was ignored/i)).toBeInTheDocument();
    expect(screen.queryByText(/does not\s+recognise/i)).not.toBeInTheDocument();
  });

  it("says nothing about unrecognised keys when every key was recognised", () => {
    // `unrecognised_income_keys` is the ONLY field off the base fixture here.
    mockTaxContext.mockReturnValue(withSummary({ unrecognised_income_keys: [] }));
    wrap(<TaxOverviewContent />);
    expect(screen.queryByText(/does not\s+recognise/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/not a set of key\/value lines/i)).not.toBeInTheDocument();
    expect(screen.queryByText("gross_salery")).not.toBeInTheDocument();
  });

  it("warns on the overview that the estimate stands on net salary", () => {
    mockTaxContext.mockReturnValue(withSummary({ salary_is_net: true }));
    wrap(<TaxOverviewContent />);
    expect(screen.getByText(/Declare your gross salary/i)).toBeInTheDocument();
    expect(screen.getByText(/payslip year-to-date gross/i)).toBeInTheDocument();
  });

  it("does not warn on the overview when gross was declared", () => {
    // `salary_is_net` is the ONLY field off the base fixture here.
    mockTaxContext.mockReturnValue(withSummary({ salary_is_net: false }));
    wrap(<TaxOverviewContent />);
    expect(screen.queryByText(/Declare your gross salary/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/payslip year-to-date gross/i)).not.toBeInTheDocument();
  });

  // The overview is the nav default, so these two overridden figures are the
  // ones most likely to be the only ones read -- unattributed they are numbers
  // trusted on their own.
  it("attributes both overview figures to their source", () => {
    mockTaxContext.mockReturnValue(
      withSummary({
        gross_ordinary_income: "1215000",
        prepaid_tax: "150000",
        provenance: {
          gross_salary: {
            source: "declared",
            value: "1200000",
            ignored_amount: "900000",
            ignored_postings: 12,
          },
          tds: {
            source: "declared",
            value: "150000",
            ignored_amount: "60000",
            ignored_postings: 1,
          },
        },
      }),
    );
    wrap(<TaxOverviewContent />);
    expect(screen.getByText(/Gross salary .*12,00,000, declared/i)).toBeInTheDocument();
    expect(screen.getByText(/TDS .*1,50,000, declared/i)).toBeInTheDocument();
  });

  it("attributes the overview figures to the ledger, never calling the salary gross", () => {
    // base fixture: both figures ledger-sourced, nothing declared.
    mockTaxContext.mockReturnValue(withSummary({}));
    const { container } = wrap(<TaxOverviewContent />);
    const prov = [...container.querySelectorAll(".figure-prov")].map((n) => n.textContent);
    expect(prov).toEqual([
      "Salary \u20b99,00,000, from the ledger \u2014 net of TDS.",
      "TDS \u20b960,000, from the ledger.",
    ]);
    expect(prov.join(" ")).not.toMatch(/gross/i);
  });
});

/** The overview is the nav default, so it is the screen most likely to be the
 * only one read -- and the one whose prose and tiles must not state a regime
 * recommendation as a verdict when the figures behind it are known to be
 * biased. */
describe("TaxOverview honesty about an incomplete regime comparison", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockImplementation(() => new Promise(() => {}));
  });
  afterEach(() => vi.restoreAllMocks());

  const REGIME = {
    exempt_allowances: "0",
    total_deductions: "0",
    taxable_income: "1200000",
    slab_tax: "100000",
    rebate: "0",
    surcharge: "0",
    cess: "4000",
    capital_gains_tax: "0",
    total: "104000",
  };

  const declaredGross = {
    gross_salary: {
      source: "declared",
      value: "1200000",
      ignored_amount: "0",
      ignored_postings: 0,
    },
    tds: { source: "ledger", value: "50000", ignored_amount: "0", ignored_postings: 0 },
  };
  const ledgerGross = {
    gross_salary: {
      source: "ledger",
      value: "900000",
      ignored_amount: "0",
      ignored_postings: 0,
    },
    tds: { source: "ledger", value: "50000", ignored_amount: "0", ignored_postings: 0 },
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
        prepaid_tax: "50000",
        tax_paid_direct: "0",
        estimate: {
          new_regime: { ...REGIME, total: "104000" },
          old_regime: { ...REGIME, exempt_allowances: "240000", total: "80000" },
          lower_tax_regime: "old",
          balance_due: "30000",
          balance_due_regime: "old",
        },
        rules_configured: true,
        uncovered: [],
        salary_is_net: false,
        declared_tds_at_or_above_gross: false,
        unrecognised_income_keys: [],
        declared_regime: null,
        comparison_applicable: true,
        comparison_complete: true,
        provenance: declaredGross,
        ...summaryOver,
      },
    },
  });

  it("states the lower-tax regime when the comparison is complete", () => {
    mockTaxContext.mockReturnValue(ctx({}));
    wrap(<TaxOverviewContent />);
    expect(screen.getByText(/lower-tax regime/i)).toBeInTheDocument();
    expect(screen.getByText(/Estimated tax \(lower regime\)/i)).toBeInTheDocument();
    expect(screen.queryByText(/upper bound/i)).not.toBeInTheDocument();
  });

  it("says the old regime is understated, and why, when the exemption is undeclared", () => {
    mockTaxContext.mockReturnValue(
      ctx({ comparison_complete: false, provenance: declaredGross }),
    );
    wrap(<TaxOverviewContent />);
    // No verdict in the prose...
    expect(screen.queryByText(/lower-tax regime/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Estimated tax \(lower regime\)/i)).not.toBeInTheDocument();
    // ...and the reason stated, naming the two reliefs and the regime that
    // withdraws them.
    expect(screen.getByText(/computed as if you claimed\s+nothing/i)).toBeInTheDocument();
    expect(screen.getByText(/withdrawn under the new one/i)).toBeInTheDocument();
    // Both figures still shown -- useful, not blank.
    expect(screen.getByText(/Estimated tax \(old regime\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Estimated tax \(new regime\)/i)).toBeInTheDocument();
  });

  it("marks the old-regime tile an upper bound when the exemption is undeclared", () => {
    mockTaxContext.mockReturnValue(
      ctx({ comparison_complete: false, provenance: declaredGross }),
    );
    const { container } = wrap(<TaxOverviewContent />);
    const oldTile = [...container.querySelectorAll(".stat")].find((t) =>
      /Estimated tax \(old regime\)/i.test(t.textContent ?? ""),
    );
    expect(oldTile?.textContent).toMatch(/Upper bound on tax/i);
    const newTile = [...container.querySelectorAll(".stat")].find((t) =>
      /Estimated tax \(new regime\)/i.test(t.textContent ?? ""),
    );
    // The new regime does not apply HRA/LTA at all, so its figure is not a
    // bound -- marking it one would be a different lie.
    expect(newTile?.textContent).not.toMatch(/upper bound/i);
  });

  it("presents no bound when nothing is declared, agreeing with the net-salary notice", () => {
    mockTaxContext.mockReturnValue(
      ctx({ comparison_complete: false, provenance: ledgerGross, salary_is_net: true }),
    );
    wrap(<TaxOverviewContent />);
    // The net-salary notice says the whole estimate is too LOW. An upper bound
    // on the old regime would say the opposite about the same figure.
    // Said twice on purpose: the net-salary notice and the comparison notice
    // both ask for it, and they must agree rather than contradict.
    expect(screen.getAllByText(/Declare your gross salary/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/upper bound/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/lower-tax regime/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Both.*regimes are understated/i)).toBeInTheDocument();
  });

  it("presents neither verdict nor bound when the comparison does not apply", () => {
    mockTaxContext.mockReturnValue(
      ctx({
        comparison_applicable: false,
        comparison_complete: false,
        provenance: ledgerGross,
      }),
    );
    wrap(<TaxOverviewContent />);
    expect(screen.queryByText(/lower-tax regime/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/upper bound/i)).not.toBeInTheDocument();
    expect(screen.getByText(/not what decides your tax here/i)).toBeInTheDocument();
  });

  it("names the regime a balance was computed against", () => {
    mockTaxContext.mockReturnValue(ctx({}));
    const { container } = wrap(<TaxOverviewContent />);
    const tile = [...container.querySelectorAll(".stat")].find((t) =>
      /Balance due/i.test(t.textContent ?? ""),
    );
    expect(tile?.textContent).toMatch(/Computed against the old regime/i);
  });

  it("says so when the regime the user files under is not the cheaper one", () => {
    mockTaxContext.mockReturnValue(ctx({ declared_regime: "new" }));
    wrap(<TaxOverviewContent />);
    expect(screen.getByText(/You file under the/i)).toBeInTheDocument();
    expect(screen.getByText(/not against the cheaper one/i)).toBeInTheDocument();
  });

  it("says nothing about a mismatch when the user files under the cheaper regime", () => {
    mockTaxContext.mockReturnValue(ctx({ declared_regime: "old" }));
    wrap(<TaxOverviewContent />);
    expect(screen.queryByText(/You file under the/i)).not.toBeInTheDocument();
  });
});
