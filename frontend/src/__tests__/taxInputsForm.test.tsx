import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { TaxInputsForm } from "@/screens/tax/TaxInputsForm";
import { triggerReload } from "@/shared/api/useReport";
import { inputsPayload } from "@/__tests__/taxInputsPayload";

/** The declared-inputs form: the one writable surface in the tax workspace.
 *
 * Everything here turns on the three-valued PATCH body, because that is what
 * keeps the form from destroying declarations it was not asked about:
 *  - a key ABSENT from the body leaves the stored value alone,
 *  - `null` deletes it,
 *  - a string sets it,
 * and a block absent from the body leaves the whole block. So the assertions
 * are mostly about what the request does NOT contain.
 */

vi.mock("@/shared/api/useReport", async (original) => ({
  ...(await original<typeof import("@/shared/api/useReport")>()),
  triggerReload: vi.fn(),
}));

type Call = { url: string; method: string; body: Record<string, unknown> | null };

let calls: Call[];
/** Queue of responses for the writes, in order. A GET is always the payload. */
let getPayload: ReturnType<typeof inputsPayload>;
let writeResponses: { status: number; body: unknown }[];

function stubFetch(): void {
  vi.spyOn(global, "fetch").mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({
      url,
      method,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
    });
    if (method === "GET") {
      return Promise.resolve(new Response(JSON.stringify(getPayload), { status: 200 }));
    }
    const next = writeResponses.shift() ?? { status: 200, body: getPayload };
    return Promise.resolve(
      new Response(JSON.stringify(next.body), { status: next.status }),
    );
  });
}

beforeEach(() => {
  calls = [];
  writeResponses = [];
  getPayload = inputsPayload();
  stubFetch();
  vi.mocked(triggerReload).mockClear();
});

afterEach(() => vi.restoreAllMocks());

const write = (): Call | undefined => calls.find((c) => c.method !== "GET");

async function mount(): Promise<void> {
  render(<MemoryRouter><TaxInputsForm fy={2026} /></MemoryRouter>);
  await screen.findByText(/declared tax inputs — FY/i);
}

/** The form starts collapsed once something is declared; open it. */
async function open(): Promise<void> {
  const toggle = screen.queryByRole("button", { name: /^(edit|declare|repair)$/i });
  if (toggle) fireEvent.click(toggle);
  await screen.findByLabelText(/gross salary/i);
}

const type = (label: RegExp, value: string): void =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

const save = (): void =>
  fireEvent.click(screen.getByRole("button", { name: /save declared inputs/i }));

describe("TaxInputsForm", () => {
  it("renders the declared figures from its own endpoint and saves an edit", async () => {
    getPayload = inputsPayload({
      regime: "new",
      income: [
        { key: "gross_salary", value: "1200000", raw: null, invalid: false },
        { key: "tds", value: "150000", raw: null, invalid: false },
      ],
      deductions: [{ key: "80c", value: "150000", raw: null, invalid: false }],
    });
    await mount();
    await open();

    expect(screen.getByLabelText(/gross salary/i)).toHaveValue("1200000");
    expect(screen.getByLabelText(/tds on salary/i)).toHaveValue("150000");
    expect(screen.getByLabelText("80C")).toHaveValue("150000");
    // Absent from the file, so no figure -- not a zero.
    expect(screen.getByLabelText(/exempt hra \+ lta/i)).toHaveValue("");
    // The form reads /api/tax/inputs, never the workspace.
    expect(calls[0].url).toContain("/api/tax/inputs?fy=2026");
    expect(calls.some((c) => c.url.includes("reports/tax/workspace"))).toBe(false);

    type(/exempt hra \+ lta/i, "240000");
    save();

    await waitFor(() => expect(write()).toBeDefined());
    const req = write()!;
    expect(req.method).toBe("PATCH");
    expect(req.body).toEqual({
      fy: 2026,
      revision: "rev-1",
      income: { exempt_hra_lta: "240000" },
    });
    // The house refresh mechanism, so the figures around the form re-read.
    await waitFor(() => expect(vi.mocked(triggerReload)).toHaveBeenCalled());
  });

  // The defect this guards is the one that loses user data: sending every
  // field on every save turns "I edited 80C" into "delete my declared salary".
  it("omits the income block entirely on a deductions-only save", async () => {
    getPayload = inputsPayload({
      income: [{ key: "gross_salary", value: "1200000", raw: null, invalid: false }],
      deductions: [{ key: "80c", value: "100000", raw: null, invalid: false }],
    });
    await mount();
    await open();

    type(/^80C$/, "150000");
    save();

    await waitFor(() => expect(write()).toBeDefined());
    const body = write()!.body!;
    expect(body).toEqual({ fy: 2026, revision: "rev-1", deductions: { "80c": "150000" } });
    // Said separately from the equality above, because this is the assertion
    // that matters: an `income` key present AT ALL -- even as `{}` -- is the
    // bug, and `income: { gross_salary: null }` would delete the declaration.
    expect(Object.keys(body)).not.toContain("income");
  });

  it("sends null for a cleared field and omits every field left alone", async () => {
    getPayload = inputsPayload({
      income: [
        { key: "gross_salary", value: "1200000", raw: null, invalid: false },
        { key: "tds", value: "150000", raw: null, invalid: false },
        { key: "exempt_hra_lta", value: "240000", raw: null, invalid: false },
      ],
    });
    await mount();
    await open();

    type(/exempt hra \+ lta/i, "");
    save();

    await waitFor(() => expect(write()).toBeDefined());
    const body = write()!.body as { income: Record<string, unknown> };
    expect(body.income).toEqual({ exempt_hra_lta: null });
    // Never `""`: the backend refuses it, and it would read as a figure rather
    // than as "no longer declared".
    expect(body.income.exempt_hra_lta).not.toBe("");
    expect(Object.keys(body.income)).toEqual(["exempt_hra_lta"]);
  });

  it("declares an explicit zero as a figure, not as an absence", async () => {
    await mount();
    await open();
    type(/exempt hra \+ lta/i, "0");
    save();
    await waitFor(() => expect(write()).toBeDefined());
    expect((write()!.body as { income: Record<string, unknown> }).income).toEqual({
      exempt_hra_lta: "0",
    });
  });

  it("shows what changed on disk on a 409 without discarding what was typed", async () => {
    getPayload = inputsPayload({
      income: [{ key: "gross_salary", value: "1200000", raw: null, invalid: false }],
    });
    writeResponses = [
      {
        status: 409,
        body: {
          detail: "changed since you loaded it",
          code: "E1058",
          description: "config/tax/2026-inputs.yaml changed since you loaded it.",
          resolution: "The current contents are attached: merge what you typed in.",
          resolvedByRefresh: true,
          data: {
            revision: "rev-2",
            document: { income: { gross_salary: "999999" } },
            parse_error: null,
            raw_text: null,
          },
        },
      },
    ];
    await mount();
    await open();

    type(/gross salary/i, "1300000");
    save();

    expect(await screen.findByText(/changed since you loaded it/i)).toBeInTheDocument();
    // The typed value is still in the field -- the whole point of the 409
    // carrying the document instead of the form reloading itself.
    expect(screen.getByLabelText(/gross salary/i)).toHaveValue("1300000");
    expect(screen.getByText(/on disk now: 999999/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing was written/i)).toBeInTheDocument();

    // Retrying uses the revision the server attached, and still carries the
    // typed figure.
    writeResponses = [{ status: 200, body: inputsPayload({ revision: "rev-3" }) }];
    fireEvent.click(screen.getByRole("button", { name: /save over the file as it is now/i }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").length).toBe(2));
    expect(calls.filter((c) => c.method === "PATCH")[1].body).toEqual({
      fy: 2026,
      revision: "rev-2",
      income: { gross_salary: "1300000" },
    });
  });

  it("repairs a file that will not parse through /replace", async () => {
    getPayload = inputsPayload({
      parse_error: "mapping values are not allowed here (line 3)",
      raw_text: "income:\n  gross_salary: \"1200000\"\n   tds: oops\n",
      income: [],
      deductions: [],
      revision: "rev-broken",
    });
    await mount();

    // No disclosure click needed: an unparseable file opens the repair door.
    expect(screen.getByText(/cannot be read as declared tax inputs/i)).toBeInTheDocument();
    expect(screen.getByText(/mapping values are not allowed here/i)).toBeInTheDocument();
    const area = screen.getByLabelText(/the file, as it is on disk/i);
    expect(area).toHaveValue("income:\n  gross_salary: \"1200000\"\n   tds: oops\n");
    // The fields cannot be rendered from a document that does not exist.
    expect(screen.queryByLabelText(/exempt hra \+ lta/i)).not.toBeInTheDocument();

    fireEvent.change(area, { target: { value: 'income:\n  gross_salary: "1200000"\n' } });
    writeResponses = [
      {
        status: 200,
        body: inputsPayload({
          revision: "rev-fixed",
          income: [{ key: "gross_salary", value: "1200000", raw: null, invalid: false }],
        }),
      },
    ];
    fireEvent.click(screen.getByRole("button", { name: /replace the whole file/i }));

    await waitFor(() => expect(write()).toBeDefined());
    const req = write()!;
    expect(req.method).toBe("POST");
    expect(req.url).toContain("/api/tax/inputs/replace");
    expect(req.body).toEqual({
      fy: 2026,
      revision: "rev-broken",
      text: 'income:\n  gross_salary: "1200000"\n',
    });
    expect(await screen.findByLabelText(/gross salary/i)).toHaveValue("1200000");
  });

  it("shows the file's own text for a value it cannot save back", async () => {
    getPayload = inputsPayload({
      income: [{ key: "tds", value: null, raw: "1,50,000", invalid: true }],
    });
    await mount();
    await open();
    expect(screen.getByLabelText(/tds on salary/i)).toHaveValue("1,50,000");
    expect(screen.getByText(/not an amount this form can save/i)).toBeInTheDocument();
  });

  it("names keys nothing reads", async () => {
    getPayload = inputsPayload({ unknown_keys: ["gross_salery", "deducations"] });
    await mount();
    await open();
    expect(screen.getByText(/nothing reads/i)).toBeInTheDocument();
    expect(screen.getByText("gross_salery, deducations")).toBeInTheDocument();
    expect(screen.getByText(/has no effect/i)).toBeInTheDocument();
  });

  it("renders its fields from the endpoint's catalog, not a hardcoded list", async () => {
    getPayload = inputsPayload({
      income_fields: ["gross_salary", "tds", "exempt_hra_lta", "future_field"],
      deduction_sections: ["80c", "80ggc"],
    });
    await mount();
    await open();
    // A field the catalog advertises and this build has no copy for still gets
    // an input, labelled by its key.
    expect(screen.getByLabelText("future_field")).toBeInTheDocument();
    expect(screen.getByLabelText("80GGC")).toBeInTheDocument();
    // ...and a section the catalog does NOT advertise is not invented here.
    expect(screen.queryByLabelText("80D")).not.toBeInTheDocument();
  });

  // A new-regime filer who reads "old regime only" skips this field, and is
  // then the one user who can never get a complete comparison.
  it("explains the exemption field as the input the comparison needs", async () => {
    await mount();
    await open();
    const hint = screen.getByText(/HRA s\.10\(13A\) and LTA s\.10\(5\) together/i);
    expect(hint).toHaveTextContent(/used when comparing against the old regime/i);
    expect(hint).toHaveTextContent(/enter 0 if you claim neither/i);
    expect(hint.textContent ?? "").not.toMatch(/old regime only/i);
  });

  it("reports a failing read with a retry instead of an empty form", async () => {
    vi.restoreAllMocks();
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response("Internal Server Error", { status: 500 }),
    );
    render(<MemoryRouter><TaxInputsForm fy={2026} /></MemoryRouter>);
    expect(
      await screen.findByText(/could not read your declared tax inputs/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
