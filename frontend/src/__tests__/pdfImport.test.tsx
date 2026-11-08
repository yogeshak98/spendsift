import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LedgerImport } from "@/screens/ledger/Import";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

const LOCKED = { matched: false, kind: "pdf_locked", filename: "card.pdf" };
const UNKNOWN = {
  matched: false,
  kind: "pdf_unknown",
  filename: "card.pdf",
  message: "New card layout. Run /ingest-stmt on this file in Claude Code once.",
};
const MATCHED = {
  matched: true,
  batch_id: "b1",
  fingerprint: "0123456789abcdef",
  candidates: [
    {
      id: "c1",
      date: "2026-04-01",
      raw_narration: "FICTIONAL CAFE",
      suggested_category: null,
      amount: "1250.50",
      direction: "debit",
      dedup_status: "new",
      needs_account: false,
      source_account: "Liabilities:Card:Fictional",
    },
  ],
  skipped_rows: [],
  statement: {
    period_start: "2026-03-01",
    period_end: "2026-03-31",
    roles: {
      total_due: { label: "Total Amount Due", value: "123456.50" },
      minimum_due: { label: "Min Due", value: "5000.00" },
      due_date: { label: "Pay By", value: "2026-04-18" },
    },
  },
};
const WRONG_PW = {
  detail: "wrong password",
  code: "E1068",
  description: "That password did not open the PDF.",
  resolution: "Check the password and try again.",
  resolvedByRefresh: false,
};

type Handlers = { detect: [number, unknown]; unlock?: [number, unknown] };
function mock(h: Handlers) {
  return vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    if (key === "/api/accounts")
      return new Response(JSON.stringify({ accounts: ["Liabilities:Card:Fictional"] }), {
        status: 200,
      });
    if (key === "/api/import/detect")
      return new Response(JSON.stringify(h.detect[1]), { status: h.detect[0] });
    if (key === "/api/import/unlock" && h.unlock)
      return new Response(JSON.stringify(h.unlock[1]), { status: h.unlock[0] });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
}

async function upload() {
  const input = screen.getByLabelText("statement file") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["%PDF"], "card.pdf")] } });
}

async function submitPassword(pw: string) {
  const field = (await screen.findByLabelText("PDF password")) as HTMLInputElement;
  fireEvent.change(field, { target: { value: pw } });
  fireEvent.click(screen.getByRole("button", { name: "Unlock" }));
  return field;
}

test("shows password field for a locked pdf and posts password in the form body", async () => {
  const spy = mock({ detect: [200, LOCKED], unlock: [200, LOCKED] });
  render(<LedgerImport />);
  await upload();
  const field = await submitPassword("hunter2-fictional");
  await waitFor(() =>
    expect(spy.mock.calls.some(([u]) => String(u) === "/api/import/unlock")).toBe(true),
  );

  const call = spy.mock.calls.find(([u]) => String(u) === "/api/import/unlock")!;
  expect(String(call[0])).not.toContain("hunter2");
  const body = (call[1] as RequestInit).body as FormData;
  expect(body.get("password")).toBe("hunter2-fictional");
  expect(body.get("file")).toBeInstanceOf(File);
  expect(JSON.stringify(spy.mock.calls.map((c) => String(c[0])))).not.toContain("hunter2");
  // still mounted (the server said "locked" again) and the field is empty
  await waitFor(() => expect(field.value).toBe(""));
});

test("unknown layout after unlock shows the message", async () => {
  mock({ detect: [200, LOCKED], unlock: [200, UNKNOWN] });
  render(<LedgerImport />);
  await upload();
  await submitPassword("pw-fictional");
  await screen.findByText(UNKNOWN.message);
});

test("password input has autocomplete new-password", async () => {
  mock({ detect: [200, LOCKED] });
  render(<LedgerImport />);
  await upload();
  const field = (await screen.findByLabelText("PDF password")) as HTMLInputElement;
  expect(field.type).toBe("password");
  expect(field.getAttribute("autocomplete")).toBe("new-password");
});

test("wrong password shows coded description and clears field", async () => {
  mock({ detect: [200, LOCKED], unlock: [422, WRONG_PW] });
  render(<LedgerImport />);
  await upload();
  const field = await submitPassword("nope-fictional");
  await screen.findByText(/That password did not open the PDF\./);
  expect(screen.getByText(/Check the password and try again\./)).toBeTruthy();
  await waitFor(() => expect(field.value).toBe(""));
});

test("unknown pdf layout shows the ingest-stmt message", async () => {
  mock({ detect: [200, UNKNOWN] });
  render(<LedgerImport />);
  await upload();
  const msg = await screen.findByText(UNKNOWN.message);
  expect(msg.className).toContain("info");
});

test("coded detect error is shown instead of an unhandled rejection", async () => {
  mock({
    detect: [
      422,
      { ...WRONG_PW, code: "E1069", description: "This PDF could not be read.", resolution: "Export it again." },
    ],
  });
  render(<LedgerImport />);
  await upload();
  await screen.findByText(/E1069: This PDF could not be read\. Export it again\./);
});

test("matched pdf shows statement summary above review", async () => {
  mock({ detect: [200, MATCHED] });
  render(<LedgerImport />);
  await upload();
  const summary = await screen.findByLabelText("statement summary");
  expect(screen.getByText("Total Amount Due")).toBeTruthy();
  expect(screen.getByText("₹1,23,456.50")).toBeTruthy();
  expect(screen.getByText("Min Due")).toBeTruthy();
  expect(screen.getByText("Pay By")).toBeTruthy();
  expect(screen.getByText("Totals reconciled with the statement summary")).toBeTruthy();
  const cafe = await screen.findByDisplayValue(/FICTIONAL CAFE/).catch(() => screen.findByText(/FICTIONAL CAFE/));
  expect(
    summary.compareDocumentPosition(cafe) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

test("matched pdf shows each warning as a callout beside the reconciled line", async () => {
  const warning =
    "this statement has a panel called EMI (lines 12, 13); EMI instalments are imported as ordinary rows";
  mock({ detect: [200, { ...MATCHED, warnings: [warning] }] });
  render(<LedgerImport />);
  await upload();
  const callout = await screen.findByText(warning);
  const summary = screen.getByLabelText("statement summary");
  expect(summary.contains(callout)).toBe(true);
  expect(screen.getByText("Totals reconciled with the statement summary")).toBeTruthy();
});

test("matched pdf with no warnings shows no warning callout", async () => {
  mock({ detect: [200, { ...MATCHED, warnings: [] }] });
  render(<LedgerImport />);
  await upload();
  await screen.findByLabelText("statement summary");
  expect(screen.queryByText(/instalments are imported/)).toBeNull();
});

test("matched unlock result goes to review with the summary", async () => {
  mock({ detect: [200, LOCKED], unlock: [200, MATCHED] });
  render(<LedgerImport />);
  await upload();
  await submitPassword("pw-fictional");
  await screen.findByLabelText("statement summary");
});

test("cash advances, other charges and split payments render as rupees", async () => {
  mock({
    detect: [
      200,
      {
        ...MATCHED,
        statement: {
          ...MATCHED.statement,
          roles: {
            cash_advances: { label: "Cash Advances", value: "2500.00" },
            other_charges: { label: "Other Debit & Charges", value: "199.50" },
            payments: { label: "Payments", value: "10000.00" },
            other_credits: { label: "Other Credits", value: "350.25" },
          },
        },
      },
    ],
  });
  render(<LedgerImport />);
  await upload();
  await screen.findByLabelText("statement summary");
  expect(screen.getByText("₹2,500.00")).toBeTruthy();
  expect(screen.getByText("₹199.50")).toBeTruthy();
  expect(screen.getByText("₹10,000.00")).toBeTruthy();
  expect(screen.getByText("₹350.25")).toBeTruthy();
});
