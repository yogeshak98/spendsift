import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, it, expect, vi } from "vitest";
import { TaxOverview } from "@/screens/tax/TaxOverview";
import { currentFY } from "@/shared/period/useAvailableYears";
import { inputsPayload } from "@/__tests__/taxInputsPayload";

/** The form has to survive the state it exists to repair.
 *
 * `GET /api/reports/tax/workspace` is a coded 422 when the inputs file does not
 * parse (E1059) or carries a value that is not a number (E1051), and
 * `TaxLayout` used to render that error INSTEAD of its children -- while every
 * tax screen also early-returns on a null workspace. A form inside that guard
 * disappears exactly when it is needed, so these tests mount the REAL layout
 * against a failing workspace and a 200 from the form's own endpoint.
 */

const FY = currentFY();
const INPUTS_PATH = `config/tax/${FY}-inputs.yaml`;

type Stub = { inputs: ReturnType<typeof inputsPayload>; code: string; status: number };

const calls: { url: string; method: string; body: unknown }[] = [];

function stub({ inputs, code, status }: Stub): void {
  calls.length = 0;
  vi.spyOn(global, "fetch").mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({
      url,
      method,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
    });
    if (url.includes("available-years")) {
      return Promise.resolve(
        new Response(JSON.stringify({ calendar_years: [FY], fiscal_years: [FY] }), {
          status: 200,
        }),
      );
    }
    if (url.includes("/api/tax/inputs/replace")) {
      return Promise.resolve(
        new Response(JSON.stringify(inputsPayload({ fy: FY, path: INPUTS_PATH })), {
          status: 200,
        }),
      );
    }
    // The form's own endpoint never fails on content: a broken file is DATA
    // here, which is what makes a 200 and a 422 coexist on one screen.
    if (url.includes("/api/tax/inputs")) {
      return Promise.resolve(new Response(JSON.stringify(inputs), { status: 200 }));
    }
    return Promise.resolve(
      new Response(
        JSON.stringify({
          detail: "cannot be read as declared tax inputs",
          code,
          description: `${INPUTS_PATH} cannot be read as declared tax inputs (mapping values are not allowed here).`,
          resolution: "Fix it in the tax inputs form using 'replace the whole file'.",
          resolvedByRefresh: false,
        }),
        { status },
      ),
    );
  });
}

afterEach(() => vi.restoreAllMocks());

const broken = inputsPayload({
  fy: FY,
  path: INPUTS_PATH,
  revision: "rev-broken",
  parse_error: "mapping values are not allowed here (line 3)",
  raw_text: 'income:\n  gross_salary: "1200000"\n   tds: oops\n',
});

describe("the declared-inputs form under a failing tax workspace", () => {
  it("renders the form beside the coded workspace error, and /replace repairs it", async () => {
    stub({ inputs: broken, code: "E1059", status: 422 });
    render(<MemoryRouter><TaxOverview /></MemoryRouter>);

    // The workspace error is stated...
    expect(await screen.findByText(/Could not load the tax workspace/i)).toBeInTheDocument();
    expect(screen.getByText("E1059")).toBeInTheDocument();
    // ...and the form is still on screen under it, with the file's own text.
    const form = screen.getByText(/declared tax inputs — FY/i).closest(".declared-form")!;
    // Said inside the FORM, not only in the workspace error above it: the form
    // is what the user acts in, so it states the problem it is repairing.
    expect(
      within(form as HTMLElement).getByText(/mapping values are not allowed here/i),
    ).toBeInTheDocument();
    const area = screen.getByLabelText(/the file, as it is on disk/i);
    expect(area).toHaveValue('income:\n  gross_salary: "1200000"\n   tds: oops\n');
    // No forever-"Loading…" under the error: there is nothing still arriving.
    expect(screen.queryByText(/^Loading…$/)).not.toBeInTheDocument();

    fireEvent.change(area, { target: { value: 'income:\n  gross_salary: "1200000"\n' } });
    fireEvent.click(screen.getByRole("button", { name: /replace the whole file/i }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/tax/inputs/replace"))).toBe(true),
    );
    const req = calls.find((c) => c.url.includes("/api/tax/inputs/replace"))!;
    expect(req.method).toBe("POST");
    expect(req.body).toEqual({
      fy: FY,
      revision: "rev-broken",
      text: 'income:\n  gross_salary: "1200000"\n',
    });
  });

  it("keeps the form reachable when a declared value is not a number (E1051)", async () => {
    stub({
      inputs: inputsPayload({
        fy: FY,
        path: INPUTS_PATH,
        income: [{ key: "gross_salary", value: null, raw: "12,00,000", invalid: true }],
      }),
      code: "E1051",
      status: 422,
    });
    render(<MemoryRouter><TaxOverview /></MemoryRouter>);

    expect(await screen.findByText(/Could not load the tax workspace/i)).toBeInTheDocument();
    // The value the workspace choked on is editable, showing the file's text.
    expect(await screen.findByLabelText(/gross salary/i)).toHaveValue("12,00,000");
    expect(screen.getByText(/not an amount this form can save/i)).toBeInTheDocument();
  });

  // Asserted on TaxOverview -- the screen that HOSTS the form -- and on the
  // form's own heading. The previous version of this test mounted TaxIncome and
  // asserted its panel title was absent, which `TaxIncomeContent` satisfies in
  // both branches (it renders `<></>` whenever data is null and an error is
  // set), so it survived adding "E1054" to FORM_FIXABLE_CODES. The two halves
  // below are the discrimination: the SAME screen, the SAME assertion, one code
  // the form can fix and one it cannot.
  it("still replaces the screen, form and all, for an error the form cannot fix", async () => {
    // A broken RULES file is not a declared-value problem: nothing on this
    // screen fixes it, so the old replace behaviour stands rather than
    // offering a form that cannot help.
    stub({ inputs: inputsPayload({ fy: FY, path: INPUTS_PATH }), code: "E1054", status: 422 });
    render(<MemoryRouter><TaxOverview /></MemoryRouter>);

    expect(await screen.findByText(/Could not load the tax workspace/i)).toBeInTheDocument();
    expect(screen.getByText("E1054")).toBeInTheDocument();
    expect(screen.queryByText(/declared tax inputs — FY/i)).not.toBeInTheDocument();
  });

  it("keeps the form on the same screen for an error the form CAN fix (E1059)", async () => {
    stub({ inputs: broken, code: "E1059", status: 422 });
    render(<MemoryRouter><TaxOverview /></MemoryRouter>);

    expect(await screen.findByText(/Could not load the tax workspace/i)).toBeInTheDocument();
    expect(screen.getByText("E1059")).toBeInTheDocument();
    expect(screen.getByText(/declared tax inputs — FY/i)).toBeInTheDocument();
  });
});
