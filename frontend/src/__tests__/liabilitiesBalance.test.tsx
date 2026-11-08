import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { LiabilitiesBalance } from "@/screens/liabilities/Balance";

afterEach(() => vi.restoreAllMocks());

type Row = {
  account: string;
  drawn: string;
  repaid: string;
  balance: string;
  interest: string;
  apr: string | null;
  apr_source: "account" | "file" | "inferred" | null;
  direction: "i_owe" | "owed_to_me" | "settled";
  amount: string;
};

// Synthetic rows shaped exactly as the locked contract (API_CONTRACT.md,
// GET /api/reports/liabilities/balance).
function row(
  account: string,
  direction: Row["direction"],
  amount: string,
  extra: Partial<Row> = {},
): Row {
  const net = direction === "i_owe" ? -Number(amount) : Number(amount);
  return {
    account,
    drawn: direction === "i_owe" ? amount : "0",
    repaid: direction === "owed_to_me" ? amount : "0",
    balance: String(-net),
    interest: "0",
    apr: null,
    apr_source: null,
    direction,
    amount,
    ...extra,
  };
}

const NO_TRANSIT = { amount: "0.00", attributed: "0.00", unattributed: "0.00", payments: [] };

function mockBalances(balances: Row[]) {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({ balances, in_transit: NO_TRANSIT, cards_outstanding: "0.00", cards_net_owed: "0.00" }),
      { status: 200 },
    ),
  );
}

// Cell text of the table row whose label is `label`, keyed by column header.
function cellsOf(label: string): Record<string, string> {
  const table = screen.getByRole("table");
  const headers = within(table)
    .getAllByRole("columnheader")
    .map((h) => h.textContent ?? "");
  const tr = within(table).getByText(label).closest("tr")!;
  const tds = Array.from(tr.querySelectorAll("td"));
  return Object.fromEntries(headers.map((h, i) => [h, tds[i]?.textContent ?? ""]));
}

test("shows owed-to-me amount in its own column without a minus sign", async () => {
  mockBalances([
    row("Liabilities:People:PersonA", "owed_to_me", "10000", { balance: "-10000" }),
  ]);
  render(<LiabilitiesBalance />);
  await waitFor(() => expect(screen.getByText("PersonA")).toBeInTheDocument());
  const cells = cellsOf("PersonA");
  expect(cells["Owed to me"]).toBe("10,000");
  expect(cells["I owe"]).toBe("");
  expect(screen.queryByText("-10,000")).toBeNull();
  expect(screen.queryByText("Drawn")).toBeNull();
  expect(screen.queryByText("Repaid")).toBeNull();
});

test("hides settled accounts until Show settled is clicked", async () => {
  mockBalances([
    row("Liabilities:People:Asha", "owed_to_me", "300"),
    row("Liabilities:People:Zed", "settled", "0"),
  ]);
  render(<LiabilitiesBalance />);
  await waitFor(() => expect(screen.getByText("Asha")).toBeInTheDocument());
  expect(screen.queryByText("Zed")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show settled (1)" }));
  expect(screen.getByText("Zed")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Hide settled" }));
  expect(screen.queryByText("Zed")).toBeNull();
});

test("hides the settled toggle when nothing is settled", async () => {
  mockBalances([row("Liabilities:CreditCard:Acme", "i_owe", "200")]);
  render(<LiabilitiesBalance />);
  await waitFor(() => expect(screen.getByText("Acme")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: /settled/i })).toBeNull();
});

test("parent row sums each direction separately", async () => {
  mockBalances([
    row("Liabilities:People:A", "i_owe", "500"),
    row("Liabilities:People:B", "owed_to_me", "10000"),
  ]);
  render(<LiabilitiesBalance />);
  await waitFor(() => expect(screen.getByText("People")).toBeInTheDocument());
  const cells = cellsOf("People");
  expect(cells["I owe"]).toBe("500");
  expect(cells["Owed to me"]).toBe("10,000");
});

test("a parent whose leaves are all settled is not rendered", async () => {
  mockBalances([
    row("Liabilities:CreditCard:Acme", "i_owe", "200"),
    row("Liabilities:Cash:Wallet", "settled", "0"),
  ]);
  render(<LiabilitiesBalance />);
  await waitFor(() => expect(screen.getByText("Acme")).toBeInTheDocument());
  expect(screen.queryByText("Cash")).toBeNull();
  expect(screen.queryByText("Wallet")).toBeNull();
});

test("drops APR column when no account has terms", async () => {
  mockBalances([row("Liabilities:CreditCard:Acme", "i_owe", "200")]);
  render(<LiabilitiesBalance />);
  await waitFor(() => expect(screen.getByText("Acme")).toBeInTheDocument());
  expect(screen.queryByRole("columnheader", { name: "APR" })).toBeNull();
});

test("shows APR column when any account has terms", async () => {
  mockBalances([
    row("Liabilities:CreditCard:Acme", "i_owe", "200"),
    row("Liabilities:Loan:Home", "i_owe", "900000", {
      apr: "8.5",
      apr_source: "file",
    }),
  ]);
  render(<LiabilitiesBalance />);
  await waitFor(() => expect(screen.getByText("Home")).toBeInTheDocument());
  expect(screen.getByRole("columnheader", { name: "APR" })).toBeInTheDocument();
  expect(cellsOf("Home")["APR"]).toBe("8.5");
});

test("shows everything-settled state when all rows are settled", async () => {
  mockBalances([row("Liabilities:People:Zed", "settled", "0")]);
  render(<LiabilitiesBalance />);
  await waitFor(() =>
    expect(screen.getByText("Everything is settled.")).toBeInTheDocument(),
  );
  expect(screen.queryByRole("table")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show settled (1)" }));
  expect(screen.getByText("Zed")).toBeInTheDocument();
});

test("shows the card payments in transit and net owed next to the ledger figure", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        balances: [row("Liabilities:CreditCard:Acme1234", "i_owe", "31872")],
        in_transit: {
          amount: "31860.00",
          attributed: "31860.00",
          unattributed: "0.00",
          payments: [{ txn_id: "t-pay-1", date: "2026-10-20", amount: "31860.00", account: "Liabilities:CreditCard:Acme1234" }],
        },
        cards_outstanding: "31872.00",
        cards_net_owed: "12.00",
      }),
      { status: 200 },
    ),
  );
  render(<LiabilitiesBalance />);
  const line = await screen.findByTestId("cards-in-transit");
  expect(line).toHaveTextContent("Cards outstanding ₹31,872");
  expect(line).toHaveTextContent("Paid, awaiting card statement: ₹31,860");
  expect(line).toHaveTextContent("Net owed ₹12");
  // The tree keeps the ledger figure.
  expect(cellsOf("Acme1234")["I owe"]).toBe("31,872");
});

test("a negative cards net owed reads as a credit, as on the Credit Cards screen", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        balances: [row("Liabilities:CreditCard:Acme1234", "i_owe", "150")],
        in_transit: {
          amount: "2650.00",
          attributed: "0.00",
          unattributed: "2650.00",
          payments: [{ txn_id: "t-pay-2", date: "2026-10-20", amount: "2650.00", account: null }],
        },
        cards_outstanding: "150.00",
        cards_net_owed: "-2500.00",
      }),
      { status: 200 },
    ),
  );
  render(<LiabilitiesBalance />);
  const line = await screen.findByTestId("cards-in-transit");
  expect(line).toHaveTextContent("Net ₹2,500 in credit");
  expect(line).not.toHaveTextContent("Net owed");
});

test("no in-transit line when nothing is in transit", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        balances: [row("Liabilities:CreditCard:Acme1234", "i_owe", "800")],
        in_transit: { amount: "0.00", attributed: "0.00", unattributed: "0.00", payments: [] },
        cards_outstanding: "800.00",
        cards_net_owed: "800.00",
      }),
      { status: 200 },
    ),
  );
  render(<LiabilitiesBalance />);
  await screen.findByRole("table");
  expect(screen.queryByTestId("cards-in-transit")).toBeNull();
});
