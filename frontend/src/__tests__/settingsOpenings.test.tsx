import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsOpenings } from "@/screens/settings/Openings";

afterEach(() => vi.restoreAllMocks());

const ACME = {
  account: "Assets:Checking:Acme",
  account_display: "Assets:Checking:Acme",
  kind: "bank",
  status: "opening",
  opening: {
    date: "2026-03-31",
    amount: "12500.00",
    direction: "have",
    ledger_amount: "12500.00",
    narration: "Opening balance",
    txn_id: "a1b2c3d4e5f60718",
    shared: false,
    source: "statement",
    balance_check: false,
  },
  first_posting: "2026-04-01",
  suggested_date: "2026-03-31",
  april: true,
  warn: false,
  directions: ["have", "owe"],
};
const RAVI = {
  account: "Liabilities:People:Ravi",
  account_display: "Liabilities:People:Ravi",
  kind: "person",
  status: "missing",
  opening: null,
  first_posting: "2026-06-14",
  suggested_date: "2026-06-13",
  april: false,
  warn: true,
  directions: ["owed_to_me", "owe"],
};
const ASHA = { ...RAVI, account: "Liabilities:People:Asha", account_display: "Liabilities:People:Asha", status: "empty", warn: false, first_posting: null };
const LOAN = {
  ...RAVI,
  account: "Liabilities:Loan:Home",
  account_display: "Liabilities:Loan:Home",
  kind: "loan",
  status: "zero",
  warn: false,
  directions: ["owe"],
};
// A liability opening: the magnitude and the signed ledger amount differ.
const CARD = {
  ...ACME,
  account: "Liabilities:CreditCard:Visa",
  account_display: "Liabilities:CreditCard:Visa",
  kind: "card",
  opening: { ...ACME.opening, date: "2026-03-30", amount: "8450.75", direction: "owe", ledger_amount: "-8450.75" },
  suggested_date: "2026-03-30",
  directions: ["owe", "owed_to_me"],
};
// A late opening: a mid-year reset dated after the account's first activity.
const LATE = {
  ...RAVI,
  account: "Liabilities:People:Bala",
  account_display: "Liabilities:People:Bala",
  status: "opening",
  opening: {
    ...ACME.opening,
    date: "2025-12-01",
    amount: "685.00",
    direction: "owed_to_me",
    ledger_amount: "685.00",
    narration: "Liability Reset",
    shared: true,
    source: "legacy",
  },
  first_posting: "2025-05-10",
  suggested_date: "2025-05-09",
  warn: true,
};
const SHARED = {
  ...ACME,
  account: "Assets:Checking:Beta",
  account_display: "Assets:Checking:Beta",
  opening: { ...ACME.opening, shared: true, narration: "Opening balances for FY start", source: "manual" },
};

function status(accounts: unknown[]) {
  return {
    accounts,
    missing_count: accounts.filter((a) => (a as { warn: boolean }).warn).length,
    warning: "Some accounts have activity that no opening balance covers, so their balances and your net worth may be inaccurate.",
  };
}

type Call = { url: string; method: string; body: unknown };
function stub(accounts: unknown[], writeBody: unknown = {}) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method === "GET") return new Response(JSON.stringify(status(accounts)), { status: 200 });
      calls.push({ url, method, body: JSON.parse(String(init?.body)) });
      return new Response(JSON.stringify(writeBody), { status: 200 });
    }),
  );
  return calls;
}

const WRITTEN = {
  account: "Liabilities:People:Ravi",
  account_display: "Liabilities:People:Ravi",
  txn_id: "c3d4e5f607182930",
  date: "2026-06-13",
  ledger_amount: "3000.00",
  source: "manual",
  balance_check: false,
  snapshot: "20261101T093100_234567",
};

function renderIt() {
  return render(
    <MemoryRouter initialEntries={["/settings/openings"]}>
      <SettingsOpenings />
    </MemoryRouter>,
  );
}
const row = (name: string) => screen.getByText(name).closest("li") as HTMLElement;
const ticked = () => fireEvent.click(screen.getByLabelText("I've checked this amount and date"));

describe("SettingsOpenings", () => {
  it("groups rows by kind and shows status pills", async () => {
    stub([ACME, RAVI, ASHA, LOAN]);
    renderIt();
    expect(await screen.findByText("Bank accounts")).toBeInTheDocument();
    expect(screen.getByText("People")).toBeInTheDocument();
    expect(screen.getByText("Loans")).toBeInTheDocument();
    expect(within(row("Assets:Checking:Acme")).getByText("Opening set")).toBeInTheDocument();
    expect(within(row("Liabilities:People:Ravi")).getByText("Missing (has activity)")).toBeInTheDocument();
    expect(within(row("Liabilities:People:Asha")).getByText("Not set up")).toBeInTheDocument();
    expect(within(row("Liabilities:Loan:Home")).getByText("Started at zero")).toBeInTheDocument();
    expect(within(row("Assets:Checking:Acme")).getByText(/2026-03-31: 12500.00 I have \(from statement\)/)).toBeInTheDocument();
    expect(screen.getByText(/Openings are optional/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Create a person or loan account/ })).toHaveAttribute("href", "/settings/accounts");
    // People sit after Bank accounts, per the kind order.
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(["Bank accounts", "Loans", "People", "Mutual funds"]);
  });

  it("add opening posts create", async () => {
    const calls = stub([RAVI], WRITTEN);
    renderIt();
    await screen.findByText("Liabilities:People:Ravi");
    fireEvent.click(within(row("Liabilities:People:Ravi")).getByText("Add opening"));
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "3000.00" } });
    expect(screen.getByLabelText("Date")).toHaveValue("2026-06-13");
    expect(screen.getByRole("button", { name: "Record opening" })).toBeDisabled();
    ticked();
    fireEvent.click(screen.getByRole("button", { name: "Record opening" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      url: "/api/openings",
      method: "POST",
      body: { account: "Liabilities:People:Ravi", amount: "3000.00", direction: "owed_to_me", date: "2026-06-13", balance_check: false },
    });
  });

  it("correct posts PUT with expected from row", async () => {
    const calls = stub([CARD], { ...WRITTEN, account: CARD.account });
    renderIt();
    await screen.findByText("Liabilities:CreditCard:Visa");
    fireEvent.click(screen.getByText("Correct"));
    // Edit every field the user can change; expected must still be the stored opening.
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "8550.75" } });
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-03-29" } });
    ticked();
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].method).toBe("PUT");
    expect(calls[0].body).toEqual({
      account: "Liabilities:CreditCard:Visa",
      amount: "8550.75",
      direction: "owe",
      date: "2026-03-29",
      balance_check: false,
      expected: { date: "2026-03-30", ledger_amount: "-8450.75" },
    });
  });

  it("started at zero and undo write nothing until confirmed", async () => {
    const calls = stub([ASHA, LOAN], { account: "x", account_display: "x", status: "zero" });
    renderIt();
    await screen.findByText("Liabilities:People:Asha");
    fireEvent.click(within(row("Liabilities:People:Asha")).getByText("Started at zero"));
    const mark = within(row("Liabilities:People:Asha")).getByRole("button", { name: "Mark started at zero" });
    expect(mark).toBeDisabled();
    fireEvent.click(mark);
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toHaveLength(0);

    fireEvent.click(within(row("Liabilities:People:Asha")).getByLabelText("I've checked: this account started at zero"));
    fireEvent.click(mark);
    fireEvent.click(mark);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ url: "/api/openings/zero", method: "POST", body: { account: "Liabilities:People:Asha", zero: true } });

    fireEvent.click(within(row("Liabilities:Loan:Home")).getByText("Undo"));
    const remove = within(row("Liabilities:Loan:Home")).getByRole("button", { name: "Remove the mark" });
    fireEvent.click(remove);
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toHaveLength(1);
    fireEvent.click(within(row("Liabilities:Loan:Home")).getByLabelText("I've checked: remove the started-at-zero mark"));
    fireEvent.click(remove);
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].body).toEqual({ account: "Liabilities:Loan:Home", zero: false });
  });

  it("cancelling the zero confirmation writes nothing", async () => {
    const calls = stub([ASHA]);
    renderIt();
    await screen.findByText("Liabilities:People:Asha");
    fireEvent.click(within(row("Liabilities:People:Asha")).getByText("Started at zero"));
    fireEvent.click(screen.getByLabelText("I've checked: this account started at zero"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Mark started at zero" })).toBeNull();
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toHaveLength(0);
  });

  it("a late opening is labelled as such and Correct suggests a date before its activity", async () => {
    const calls = stub([LATE], { ...WRITTEN, account: LATE.account });
    renderIt();
    await screen.findByText("Liabilities:People:Bala");
    const late = row("Liabilities:People:Bala");
    const pill = within(late).getByText("Opening dated after earlier activity");
    expect(pill).toHaveAttribute("title", "Opening is dated after earlier activity");
    expect(within(late).queryByText("Opening set")).toBeNull();
    expect(within(late).getByText(/Activity from 2025-05-10 comes before this opening/)).toBeInTheDocument();
    expect(within(late).queryByText("Started at zero")).toBeNull();

    fireEvent.click(within(late).getByText("Correct"));
    expect(screen.getByLabelText("Date")).toHaveValue("2025-05-09");
    expect(screen.getByLabelText("Amount")).toHaveValue("685.00");
    // Still the user's call: nothing is sent without the tick.
    expect(screen.getByRole("button", { name: "Save correction" })).toBeDisabled();
    ticked();
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      url: "/api/openings",
      method: "PUT",
      body: {
        account: "Liabilities:People:Bala",
        amount: "685.00",
        direction: "owed_to_me",
        date: "2025-05-09",
        balance_check: false,
        expected: { date: "2025-12-01", ledger_amount: "685.00" },
      },
    });
  });

  it("shared opening shows narration note", async () => {
    stub([SHARED, ACME]);
    renderIt();
    await screen.findByText("Assets:Checking:Beta");
    expect(within(row("Assets:Checking:Beta")).getByText("Part of a shared entry: Opening balances for FY start")).toBeInTheDocument();
    expect(within(row("Assets:Checking:Acme")).queryByText(/Part of a shared entry/)).toBeNull();
  });

  it("switching rows does not carry the amount or confirmation over", async () => {
    stub([RAVI, ASHA]);
    renderIt();
    await screen.findByText("Liabilities:People:Ravi");
    fireEvent.click(within(row("Liabilities:People:Ravi")).getByText("Add opening"));
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "3000.00" } });
    ticked();
    fireEvent.click(within(row("Liabilities:People:Asha")).getByText("Add opening"));
    expect(screen.getByLabelText("Amount")).toHaveValue("");
    expect(screen.getByLabelText("I've checked this amount and date")).not.toBeChecked();
  });
});
