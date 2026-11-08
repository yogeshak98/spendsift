import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, it, expect, vi } from "vitest";
import { TaxIncome } from "@/screens/tax/TaxIncome";
import { TaxOverview } from "@/screens/tax/TaxOverview";
import { currentFY } from "@/shared/period/useAvailableYears";
import { inputsPayload } from "@/__tests__/taxInputsPayload";

/** The declared-income overlay travels response -> useReport -> TaxLayout
 * context -> screen. These tests deliberately use the REAL TaxLayout and a
 * stubbed HTTP response, so a notice that reads the wrong field name (or a
 * field the layout fails to pass on) fails here even though the component's
 * own branch is untouched. Every other tax screen test mocks `useTaxContext`
 * and so cannot see that hop. */

const FY = currentFY();

function workspacePayload(summaryOver: Record<string, unknown>) {
  return {
    period: `FY${FY}-${String(FY + 1).slice(-2)}`,
    items: [
      { kind: "salary", amount: "900000", source_refs: ["t1"] },
      { kind: "interest", amount: "15000", source_refs: ["t9"] },
    ],
    deductions: [],
    summary: {
      gross_ordinary_income: "1215000",
      capital_gains: { ltcg: "0", stcg: "0", crypto: "0", debt: "0" },
      prepaid_tax: "150000",
      tax_paid_direct: "0",
      estimate: null,
      rules_configured: true,
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
      ...summaryOver,
    },
  };
}

/** The overview hosts the declared-inputs form, which reads its OWN endpoint,
 * so every stub here has to answer `/api/tax/inputs` as well as the workspace.
 * That separation is the point of this unit: the two reads fail independently,
 * and the form survives a workspace that does not load. */
function stubApi(summaryOver: Record<string, unknown>) {
  vi.spyOn(global, "fetch").mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.includes("available-years")
      ? { calendar_years: [FY], fiscal_years: [FY] }
      : url.includes("/api/tax/inputs")
        ? inputsPayload({ fy: FY, path: `config/tax/${FY}-inputs.yaml` })
        : workspacePayload(summaryOver);
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
}

afterEach(() => vi.restoreAllMocks());

describe("declared-income overlay, end to end through the layout", () => {
  it("carries provenance from the response to the figure on the income screen", async () => {
    stubApi({});
    render(<MemoryRouter><TaxIncome /></MemoryRouter>);
    expect(await screen.findByText(/Gross salary .*12,00,000, declared/i)).toBeInTheDocument();
    expect(
      screen.getByText(/12 ledger transactions totalling .*9,00,000 ignored/i),
    ).toBeInTheDocument();
  });

  it("carries the net-salary warning and the unrecognised key from the response to the overview", async () => {
    stubApi({ salary_is_net: true, unrecognised_income_keys: ["gross_salery"] });
    render(<MemoryRouter><TaxOverview /></MemoryRouter>);
    expect(await screen.findByText(/Declare your gross salary/i)).toBeInTheDocument();
    expect(screen.getByText("gross_salery")).toBeInTheDocument();
    expect(screen.getAllByText(`config/tax/${FY}-inputs.yaml`).length).toBeGreaterThan(0);
  });

  it("shows no overlay notices when the response declares nothing unusual", async () => {
    stubApi({
      // Every flag the negatives below name is set EXPLICITLY false/empty, so
      // each one is the only thing that could produce its notice -- not left to
      // a default that happens to be falsy.
      salary_is_net: false,
      declared_tds_at_or_above_gross: false,
      unrecognised_income_keys: [],
      provenance: {
        gross_salary: {
          source: "ledger",
          value: "900000",
          ignored_amount: "0",
          ignored_postings: 0,
        },
        tds: { source: "ledger", value: "60000", ignored_amount: "0", ignored_postings: 0 },
      },
    });
    render(<MemoryRouter><TaxOverview /></MemoryRouter>);
    // wait for the fetched render before asserting absence
    expect(await screen.findByText(/Gross ordinary income/i)).toBeInTheDocument();
    expect(screen.queryByText(/Declare your gross salary/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/does not\s+recognise/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/is not below gross salary/i)).not.toBeInTheDocument();
  });

  // E1051: a declared value that is not a readable number (Indian digit
  // grouping is the realistic hand-edit) is a coded 422 for the WHOLE
  // workspace. `TaxLayout` used to destructure only `data`, so every tax
  // screen sat on "Loading…" for ever and the precise diagnosis the backend
  // had already written never reached the user.
  function stubCodedFailure() {
    vi.spyOn(global, "fetch").mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("available-years")) {
        return Promise.resolve(
          new Response(JSON.stringify({ calendar_years: [FY], fiscal_years: [FY] }), {
            status: 200,
          }),
        );
      }
      // The form's own endpoint is a 200 even when the workspace is a coded
      // 422: that is what makes it the repair tool for this exact state.
      if (url.includes("/api/tax/inputs")) {
        return Promise.resolve(
          new Response(
            JSON.stringify(inputsPayload({ fy: FY, path: `config/tax/${FY}-inputs.yaml` })),
            { status: 200 },
          ),
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            detail: "not a readable number",
            code: "E1051",
            description: `\`gross_salary\` under \`income\` in config/tax/${FY}-inputs.yaml is not a readable number (found \`12,00,000\`).`,
            resolution:
              "Write the value as plain digits in quotes with no digit grouping.",
            resolvedByRefresh: false,
          }),
          { status: 422 },
        ),
      );
    });
  }

  it("renders the coded failure instead of loading for ever", async () => {
    stubCodedFailure();
    render(<MemoryRouter><TaxIncome /></MemoryRouter>);
    expect(await screen.findByText(/Could not load the tax workspace/i)).toBeInTheDocument();
    expect(screen.getByText(/is not a readable number/i)).toBeInTheDocument();
    expect(screen.getByText(/no digit grouping/i)).toBeInTheDocument();
    expect(screen.getByText("E1051")).toBeInTheDocument();
    expect(screen.queryByText(/Loading/i)).not.toBeInTheDocument();
  });

  it("falls back to a read-failure message when the body is not a coded envelope", async () => {
    vi.spyOn(global, "fetch").mockImplementation((input: RequestInfo | URL) =>
      String(input).includes("available-years")
        ? Promise.resolve(
            new Response(JSON.stringify({ calendar_years: [FY], fiscal_years: [FY] }), {
              status: 200,
            }),
          )
        : String(input).includes("/api/tax/inputs")
          ? Promise.resolve(
              new Response(
                JSON.stringify(inputsPayload({ fy: FY, path: `config/tax/${FY}-inputs.yaml` })),
                { status: 200 },
              ),
            )
          : Promise.resolve(new Response("Internal Server Error", { status: 500 })),
    );
    render(<MemoryRouter><TaxIncome /></MemoryRouter>);
    expect(await screen.findByText(/Could not load the tax workspace/i)).toBeInTheDocument();
    expect(screen.getByText(/read failure, not a data loss/i)).toBeInTheDocument();
    expect(screen.queryByText("E1051")).not.toBeInTheDocument();
  });
});
