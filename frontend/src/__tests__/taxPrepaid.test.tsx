import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { TaxPrepaidContent } from "@/screens/tax/TaxPrepaid";

const mockTaxContext = vi.fn();
vi.mock("@/screens/tax/TaxLayout", () => ({
  TaxLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/screens/tax/taxContext", () => ({
  useTaxContext: () => mockTaxContext(),
}));

describe("TaxPrepaid", () => {
  it("shows prepaid + direct tax stats", () => {
    mockTaxContext.mockReturnValue({
      fy: 2026,
      setFy: vi.fn(),
      data: {
        period: "FY 2025-26",
        items: [
          { kind: "prepaid_tax", amount: "25000", source_refs: ["t1"] },
          { kind: "tax_paid", amount: "5000", source_refs: ["t2"] },
        ],
        deductions: [],
        summary: {
          gross_ordinary_income: "0",
          capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "0" },
          prepaid_tax: "25000",
          tax_paid_direct: "5000",
          estimate: null,
          rules_configured: false,
          uncovered: [],
        },
      },
    });
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.getAllByText(/25,000/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/5,000/).length).toBeGreaterThan(0);
  });

  const base = {
    period: "FY 2026-27",
    items: [{ kind: "prepaid_tax", amount: "60000", source_refs: ["t1"] }],
    deductions: [],
    summary: {
      gross_ordinary_income: "1200000",
      capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "0" },
      prepaid_tax: "150000",
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
        tds: {
          source: "declared",
          value: "150000",
          ignored_amount: "60000",
          ignored_postings: 1,
        },
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

  it("states that TDS was declared and names the single ledger transaction it ignored", () => {
    mockTaxContext.mockReturnValue(withSummary({}));
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.getByText(/TDS .*1,50,000, declared/i)).toBeInTheDocument();
    expect(
      screen.getByText(/1 ledger transaction of .*60,000 ignored for this estimate/i),
    ).toBeInTheDocument();
  });

  it("attributes TDS to the ledger, with no ignored clause, when nothing was declared", () => {
    mockTaxContext.mockReturnValue(
      withSummary({
        prepaid_tax: "60000",
        provenance: {
          ...base.summary.provenance,
          tds: {
            source: "ledger",
            value: "60000",
            ignored_amount: "0",
            ignored_postings: 0,
          },
        },
      }),
    );
    const { container } = render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    const prov = container.querySelector(".figure-prov");
    expect(prov?.textContent).toBe("TDS \u20b960,000, from the ledger.");
    // TDS is never "gross" in either branch, but the ledger branch must not
    // borrow the salary branch's "net of TDS" tail either.
    expect(prov?.textContent).not.toMatch(/gross|net of TDS/i);
    expect(screen.queryByText(/ignored/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/declared/i)).not.toBeInTheDocument();
  });

  it("flags a declared TDS at or above the declared gross salary", () => {
    mockTaxContext.mockReturnValue(
      withSummary({
        declared_tds_at_or_above_gross: true,
        provenance: {
          gross_salary: {
            source: "declared",
            value: "150000",
            ignored_amount: "900000",
            ignored_postings: 12,
          },
          tds: {
            source: "declared",
            value: "1200000",
            ignored_amount: "60000",
            ignored_postings: 1,
          },
        },
      }),
    );
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(
      screen.getByText(/Check the two declared figures .*is not below gross salary/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/used exactly as declared/i)).toBeInTheDocument();
  });

  it("does not flag the pair when declared TDS is below declared gross", () => {
    mockTaxContext.mockReturnValue(withSummary({ declared_tds_at_or_above_gross: false }));
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.queryByText(/is not below gross salary/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/used exactly as declared/i)).not.toBeInTheDocument();
  });

  // The discoverability hole this closes: a mistyped `tds` key leaves this
  // screen showing "TDS \u20b9X, from the ledger" -- true -- while nothing says a
  // declaration was dropped, and `salary_is_net` does not fire for TDS.
  it("names a dropped income key on the prepaid screen", () => {
    mockTaxContext.mockReturnValue(
      withSummary({
        prepaid_tax: "60000",
        unrecognised_income_keys: ["tds_total"],
        provenance: {
          ...base.summary.provenance,
          tds: {
            source: "ledger",
            value: "60000",
            ignored_amount: "0",
            ignored_postings: 0,
          },
        },
      }),
    );
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.getByText(/does not\s+recognise/i)).toBeInTheDocument();
    expect(screen.getByText("tds_total")).toBeInTheDocument();
  });

  it("says nothing about dropped keys on the prepaid screen when every key was recognised", () => {
    // `unrecognised_income_keys` is the ONLY field off the base fixture here.
    mockTaxContext.mockReturnValue(withSummary({ unrecognised_income_keys: [] }));
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.queryByText(/does not\s+recognise/i)).not.toBeInTheDocument();
    expect(screen.queryByText("tds_total")).not.toBeInTheDocument();
  });

  it("shows the declared TDS and the surviving ledger advance tax as separate parts", () => {
    mockTaxContext.mockReturnValue(
      withSummary({
        prepaid_tax: "165000",
        provenance: {
          ...base.summary.provenance,
          tds: { ...base.summary.provenance.tds, advance_tax: "15000", advance_tax_postings: 1 },
        },
      }),
    );
    const { container } = render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    const text = container.querySelector(".figure-prov")?.textContent;
    expect(text).toMatch(/TDS .*1,50,000, declared/);
    expect(text).toMatch(/1 ledger transaction of .*60,000 ignored for this estimate/);
    expect(text).toMatch(/plus advance tax .*15,000 from the ledger, counted in full/);
  });

  it("names TDS and advance tax as both from the ledger when nothing was declared", () => {
    mockTaxContext.mockReturnValue(
      withSummary({
        prepaid_tax: "75000",
        provenance: {
          ...base.summary.provenance,
          tds: {
            source: "ledger",
            value: "60000",
            ignored_amount: "0",
            ignored_postings: 0,
            advance_tax: "15000",
            advance_tax_postings: 1,
          },
        },
      }),
    );
    const { container } = render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(container.querySelector(".figure-prov")?.textContent).toBe(
      "TDS \u20b960,000 and advance tax \u20b915,000, both from the ledger.",
    );
  });
});

/** The screen that tells a person to pay a specific amount of money.
 *
 * Printing "pay ₹X by self-assessment challan" over a balance whose inputs are
 * known to be incomplete is the worst thing any tax screen here can do, so the
 * instruction is withheld while the figure itself stays on screen. */
describe("TaxPrepaid and the challan instruction", () => {
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
    tds: { source: "declared", value: "50000", ignored_amount: "0", ignored_postings: 0 },
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
      items: [{ kind: "prepaid_tax", amount: "50000", source_refs: ["t1"] }],
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

  const CHALLAN = /Pay via self-assessment challan/i;

  it("gives the challan instruction when the comparison is complete", () => {
    mockTaxContext.mockReturnValue(ctx({}));
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.getByText(CHALLAN)).toBeInTheDocument();
  });

  it("withholds the challan instruction when the HRA/LTA figure is undeclared", () => {
    mockTaxContext.mockReturnValue(
      ctx({ comparison_complete: false, provenance: declaredGross }),
    );
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.queryByText(CHALLAN)).not.toBeInTheDocument();
    expect(screen.getByText(/Not a challan amount yet/i)).toBeInTheDocument();
    // The figure itself is not hidden: it is still the best estimate there is.
    expect(screen.getAllByText(/30,000/).length).toBeGreaterThan(0);
    expect(screen.getByText(/missing your\s+exempt HRA \+ LTA/i)).toBeInTheDocument();
  });

  it("withholds the challan instruction when nothing is declared", () => {
    mockTaxContext.mockReturnValue(
      ctx({ comparison_complete: false, provenance: ledgerGross, salary_is_net: true }),
    );
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.queryByText(CHALLAN)).not.toBeInTheDocument();
    // Understated, not an upper bound: paying it would leave tax outstanding.
    expect(screen.getByText(/the balance is understated/i)).toBeInTheDocument();
    expect(screen.queryByText(/upper bound/i)).not.toBeInTheDocument();
  });

  it("keeps the challan instruction when the regime question does not apply", () => {
    // No salary at all, so no withdrawn-allowance term is missing from either
    // regime: the balance is not biased and withholding would be theatre.
    mockTaxContext.mockReturnValue(
      ctx({
        comparison_applicable: false,
        comparison_complete: false,
        provenance: ledgerGross,
      }),
    );
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.getByText(CHALLAN)).toBeInTheDocument();
    expect(screen.queryByText(/Not a challan amount yet/i)).not.toBeInTheDocument();
  });

  // s.115BAC grants no HRA/LTA exemption, so an undeclared exempt_hra_lta
  // cannot move a new-regime figure: the amount is exact, and withholding it
  // from the user who files under that regime costs them a correct number.
  it("keeps the challan instruction for a declared new-regime filer, with the regime caveat", () => {
    mockTaxContext.mockReturnValue(
      ctx({
        comparison_complete: false,
        provenance: declaredGross,
        declared_regime: "new",
        estimate: {
          new_regime: { ...REGIME, total: "104000" },
          old_regime: { ...REGIME, exempt_allowances: "240000", total: "80000" },
          lower_tax_regime: "old",
          balance_due: "30000",
          // The balance is the NEW regime's because that is the one declared,
          // even though the old one looks cheaper.
          balance_due_regime: "new",
        },
      }),
    );
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.getByText(CHALLAN)).toBeInTheDocument();
    expect(screen.queryByText(/Not a challan amount yet/i)).not.toBeInTheDocument();
    // The amount stands; which regime to be filing under does not.
    expect(screen.getByText(/That amount stands/i)).toBeInTheDocument();
    expect(screen.getByText(/What is not\s+settled is the regime itself/i)).toBeInTheDocument();
  });

  // The assertion that pins the predicate: `balance_due_regime === "new"` on its
  // own is NOT enough. With no declared regime the balance is the cheaper
  // regime's, and "cheaper" came out of the very comparison that is incomplete --
  // the old regime may win once the exemption is declared, making the figure on
  // screen the wrong regime's answer. Loosening the predicate to
  // `balance_due_regime` alone makes this test fail.
  it("still withholds it when the new regime was merely the cheaper one, not declared", () => {
    mockTaxContext.mockReturnValue(
      ctx({
        comparison_complete: false,
        provenance: declaredGross,
        declared_regime: null,
        estimate: {
          new_regime: { ...REGIME, total: "80000" },
          old_regime: { ...REGIME, exempt_allowances: "0", total: "104000" },
          lower_tax_regime: "new",
          balance_due: "30000",
          balance_due_regime: "new",
        },
      }),
    );
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.queryByText(CHALLAN)).not.toBeInTheDocument();
    expect(screen.getByText(/Not a challan amount yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/That amount stands/i)).not.toBeInTheDocument();
  });

  // Declaring the REGIME is not declaring the FIGURES: with no gross salary
  // declared the base is a net-of-TDS bank credit, so the balance is
  // understated under either regime and the instruction stays withheld.
  it("still withholds it for a declared new-regime filer who declared no figures", () => {
    mockTaxContext.mockReturnValue(
      ctx({
        comparison_complete: false,
        provenance: ledgerGross,
        salary_is_net: true,
        declared_regime: "new",
        estimate: {
          new_regime: { ...REGIME, total: "104000" },
          old_regime: { ...REGIME, total: "104000" },
          lower_tax_regime: "new",
          balance_due: "30000",
          balance_due_regime: "new",
        },
      }),
    );
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.queryByText(CHALLAN)).not.toBeInTheDocument();
    expect(screen.getByText(/the balance is understated/i)).toBeInTheDocument();
    expect(screen.queryByText(/That amount stands/i)).not.toBeInTheDocument();
  });

  // The fail-safe. `comparison_applicable` is typed required and the backend
  // declares it required, but a field dropped in transit arrives `undefined` --
  // and a falsy test read that as `not-applicable`, the ONE incomplete state
  // that grants the pay-this-amount instruction. So the highest-stakes surface
  // degraded towards printing the instruction. Only an explicit `false` may.
  it("withholds the challan instruction when the applicability flag is missing", () => {
    mockTaxContext.mockReturnValue(
      ctx({ comparison_applicable: undefined, comparison_complete: undefined }),
    );
    render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    expect(screen.queryByText(CHALLAN)).not.toBeInTheDocument();
    expect(screen.getByText(/Not a challan amount yet/i)).toBeInTheDocument();
    // The figure itself still stands: withholding the instruction is not
    // withholding the estimate.
    expect(screen.getAllByText(/30,000/).length).toBeGreaterThan(0);
  });

  it("names the regime the balance was computed against", () => {
    mockTaxContext.mockReturnValue(ctx({ declared_regime: "old" }));
    const { container } = render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    const tile = [...container.querySelectorAll(".stat")].find((t) =>
      /Estimated balance due/i.test(t.textContent ?? ""),
    );
    expect(tile?.textContent).toMatch(/Computed against the old regime/i);
  });
});

/** The discoverability notice for `tax-fy`.
 *
 * A direct-tax payment made in one financial year FOR another was credited to
 * the year it was paid in, and on this screen -- the one that tells a person to
 * pay -- that inverted the sign of the balance. A posting may now name the year
 * it was paid for, and nobody discovers posting metadata on their own, so the
 * payments still attributed by date alone are named here.
 *
 * Fictional dates and amounts throughout. */
describe("TaxPrepaid and the tax-fy notice", () => {
  // Both payments are counted in this FY. Only the untagged one is offered the
  // tag. Their dates AND amounts differ, or no assertion below could tell
  // which of the two the notice listed.
  const UNTAGGED = { date: "2024-07-19", amount: "4100", txn_id: "challan1" };
  const TAGGED = { date: "2024-09-02", amount: "7700", txn_id: "challan2" };

  const ctx = (untagged: typeof UNTAGGED[]) => ({
    fy: 2024,
    setFy: vi.fn(),
    data: {
      period: "FY2024-25",
      items: [
        {
          kind: "tax_paid",
          // Both payments, so the only distinguishing information the notice
          // could get wrong is the per-payment figure it prints.
          amount: "11800",
          source_refs: [UNTAGGED.txn_id, TAGGED.txn_id],
        },
      ],
      deductions: [],
      summary: {
        gross_ordinary_income: "0",
        capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "0" },
        prepaid_tax: "0",
        tax_paid_direct: "11800",
        // Null, so the balance/refund callout is not rendered and the notice
        // is the only `.callout info` on the screen.
        estimate: null,
        rules_configured: false,
        uncovered: [],
        salary_is_net: false,
        declared_tds_at_or_above_gross: false,
        unrecognised_income_keys: [],
        untagged_tax_payments: untagged,
      },
    },
  });

  it("lists the untagged payment and says nothing about the tagged one", () => {
    mockTaxContext.mockReturnValue(ctx([UNTAGGED]));
    const { container } = render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    const notice = container.querySelector(".callout.info");
    const text = notice?.textContent ?? "";
    expect(text).toContain(UNTAGGED.date);
    expect(text).toMatch(/4,100/);
    // The tagged payment has already said which year it was paid for: neither
    // its date nor its amount belongs in a notice offering the tag.
    expect(text).not.toContain(TAGGED.date);
    expect(text).not.toMatch(/7,700/);
    // Nor the bucket total, which would print the wrong figure against the one
    // payment listed.
    expect(text).not.toMatch(/11,800/);
    // Informational: nothing here is known to be wrong.
    expect(notice?.className).toContain("info");
    expect(notice?.className).not.toContain("warn");
    // The exact syntax, and the AY bridge a challan forces on the reader.
    expect(text).toContain('tax-fy: "2025"');
    expect(text).toContain("AY 2026-27");
    expect(text).toContain("FY2024-25");
  });

  it("drops the notice when every counted payment is tagged", () => {
    mockTaxContext.mockReturnValue(ctx([]));
    const { container } = render(<MemoryRouter><TaxPrepaidContent /></MemoryRouter>);
    // A SPECIFIC rendered string, not the callout as a whole: this screen has
    // other `callout info` notices, and asserting the absence of the container
    // would pass while the copy was rendered inside a different one.
    expect(container.textContent).not.toContain('tax-fy: "2025"');
    expect(container.textContent).not.toContain(UNTAGGED.date);
    // Absent because there is nothing to say, not because the screen broke.
    expect(screen.getByText("Prepaid Tax Items")).toBeInTheDocument();
    expect(screen.getAllByText(/11,800/).length).toBeGreaterThan(0);
  });
});
