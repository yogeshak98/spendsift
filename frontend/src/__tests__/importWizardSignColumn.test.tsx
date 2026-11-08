import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LedgerImport } from "@/screens/ledger/Import";

beforeEach(() => localStorage.clear());

// Fictional Axis-style sign-column statement: a single Amount column paired
// with a Debit/Credit indicator column, rather than separate debit/credit
// columns.
const SIGN_COLUMN_DETECT = {
  matched: false,
  filename: "axis-card.csv",
  fingerprint: "fp-sign",
  raw_rows: [
    ["Date", "Narration", "Amount (INR)", "Debit/Credit"],
    ["01/04/2026", "CARD PURCHASE", "500.00", "Debit"],
  ],
  suggested_header_row: 0,
  suggested_roles: {
    date: "Date",
    narration: "Narration",
    debit: null,
    credit: null,
    amount: "Amount (INR)",
    sign: "Debit/Credit",
  },
  suggested_date_format: "%d/%m/%Y",
  delimiter: ",",
};

// Fictional two-column statement, unchanged shape: separate debit/credit
// columns, no amount/sign.
const TWO_COLUMN_DETECT = {
  matched: false,
  filename: "idfc-bank.csv",
  fingerprint: "fp-two",
  raw_rows: [
    ["Date", "Narration", "Withdrawal Amt", "Deposit Amt"],
    ["01/04/2026", "CARD PURCHASE", "500.00", ""],
  ],
  suggested_header_row: 0,
  suggested_roles: {
    date: "Date",
    narration: "Narration",
    debit: "Withdrawal Amt",
    credit: "Deposit Amt",
    amount: null,
    sign: null,
  },
  suggested_date_format: "%d/%m/%Y",
  delimiter: ",",
};

function mockDetect(detect: Record<string, unknown>) {
  return vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    if (key === "/api/accounts")
      return new Response(JSON.stringify({ accounts: ["Assets:Bank:Fictional"] }), {
        status: 200,
      });
    if (key === "/api/import/detect")
      return new Response(JSON.stringify(detect), { status: 200 });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
}

async function uploadFile(filename: string) {
  const file = new File(["x"], filename);
  const input = screen.getByLabelText("statement file") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
  await screen.findByText("New statement format — create a template");
}

test("a detect response carrying amount+sign opens the wizard in single mode with both selectors pre-filled", async () => {
  mockDetect(SIGN_COLUMN_DETECT);
  render(<LedgerImport />);
  await uploadFile("axis-card.csv");

  const singleRadio = screen.getByLabelText("single amount + sign") as HTMLInputElement;
  expect(singleRadio.checked).toBe(true);

  expect((screen.getByLabelText("Amount column") as HTMLSelectElement).value).toBe(
    "Amount (INR)",
  );
  expect((screen.getByLabelText("Sign column") as HTMLSelectElement).value).toBe(
    "Debit/Credit",
  );
});

test("a detect response carrying debit+credit still opens the wizard in two-column mode", async () => {
  mockDetect(TWO_COLUMN_DETECT);
  render(<LedgerImport />);
  await uploadFile("idfc-bank.csv");

  const twoColumnRadio = screen.getByLabelText(
    "separate debit / credit",
  ) as HTMLInputElement;
  expect(twoColumnRadio.checked).toBe(true);

  expect((screen.getByLabelText("Debit column") as HTMLSelectElement).value).toBe(
    "Withdrawal Amt",
  );
  expect((screen.getByLabelText("Credit column") as HTMLSelectElement).value).toBe(
    "Deposit Amt",
  );
});

test("single mode with no sign column selected cannot be submitted", async () => {
  const fetchSpy = mockDetect(TWO_COLUMN_DETECT);
  render(<LedgerImport />);
  await uploadFile("idfc-bank.csv");

  // Switch into single-amount mode without ever touching the Sign column
  // selector — it stays at its initial empty value.
  fireEvent.click(screen.getByLabelText("single amount + sign"));

  fireEvent.click(screen.getByText("Save template & Continue"));

  await waitFor(() =>
    expect(
      screen.getByText(
        "Choose the column that marks a row as a credit — without it every row " +
          "would be imported as money going out.",
      ),
    ).toBeTruthy(),
  );

  // The refusal happened client-side — no POST to /api/templates was made.
  const templatePosts = fetchSpy.mock.calls.filter(([url]) => String(url) === "/api/templates");
  expect(templatePosts).toHaveLength(0);
});

// Fictional statement whose dates are in a format none of the backend's
// known parsers can read, so suggested_date_format comes back null.
const UNKNOWN_DATE_FORMAT_DETECT = {
  ...TWO_COLUMN_DETECT,
  filename: "unknown-date-format.csv",
  fingerprint: "fp-unknown-date",
  suggested_date_format: null,
};

test("a detect response with no date-format guess leaves the field empty and shows a hint, not the string null", async () => {
  mockDetect(UNKNOWN_DATE_FORMAT_DETECT);
  render(<LedgerImport />);
  await uploadFile("unknown-date-format.csv");

  expect((screen.getByLabelText("Date format") as HTMLInputElement).value).toBe("");
  expect(screen.getByText(/could not guess the date format/i)).toBeTruthy();
  expect(screen.queryByText("null")).toBeFalsy();
});

test("a two-column detect response renders the sign-column select with nothing pre-selected", async () => {
  mockDetect(TWO_COLUMN_DETECT);
  render(<LedgerImport />);
  await uploadFile("idfc-bank.csv");

  // Switch into single-amount mode to reveal the Sign column selector.
  fireEvent.click(screen.getByLabelText("single amount + sign"));

  expect((screen.getByLabelText("Sign column") as HTMLSelectElement).value).toBe("");
});
