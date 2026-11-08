import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { LedgerTransactions } from "../Transactions";

// The Net Worth Overview's Property, Bank & cash and Other cards open the
// Ledger with `?group=<id>`; only transactions with a leg in that group show.

function txn(id: string, narration: string, accounts: string[]) {
  return {
    txn_id: id,
    date: "2026-10-01",
    narration,
    year_file_hash: "h",
    postings: accounts.map((account, i) => ({
      account,
      account_display: account,
      amount: i === 0 ? "-100.00" : "100.00",
      tags: [],
      meta: {},
    })),
  };
}

const TRANSACTIONS = [
  txn("1", "Monthly pay", ["Income:Salary", "Assets:Checking:HDFC"]),
  txn("2", "House down payment", ["Assets:Checking:HDFC", "Assets:House:Home"]),
  txn("3", "Groww wallet top-up", ["Assets:Wallet:Groww", "Liabilities:CreditCard:ICICI"]),
  txn("4", "Cash withdrawal", ["Assets:Cash", "Expenses:Food"]),
  txn("5", "Fund buy", ["Assets:Investment:MutualFund:Groww:MF1", "Expenses:Food"]),
];

afterEach(() => vi.restoreAllMocks());

function renderAt(path: string) {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ transactions: TRANSACTIONS }), { status: 200 }),
  );
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LedgerTransactions />
    </MemoryRouter>,
  );
}

test("?group=bank shows only transactions touching bank or cash accounts", async () => {
  renderAt("/ledger/transactions?group=bank");
  await waitFor(() => expect(screen.getByText(/Monthly pay/)).toBeInTheDocument());
  expect(screen.getByText(/House down payment/)).toBeInTheDocument();
  expect(screen.getByText(/Cash withdrawal/)).toBeInTheDocument();
  expect(screen.queryByText(/Groww wallet top-up/)).toBeNull();
  expect(screen.queryByText(/Fund buy/)).toBeNull();
  expect(screen.getByText("Transactions · 3")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Bank & cash");
});

test("?group=property and ?group=other follow the Overview's groups", async () => {
  const r = renderAt("/ledger/transactions?group=property");
  await waitFor(() => expect(screen.getByText(/House down payment/)).toBeInTheDocument());
  expect(screen.getByText("Transactions · 1")).toBeInTheDocument();
  r.unmount();
  vi.restoreAllMocks();
  renderAt("/ledger/transactions?group=other");
  await waitFor(() => expect(screen.getByText(/Groww wallet top-up/)).toBeInTheDocument());
  expect(screen.getByText("Transactions · 1")).toBeInTheDocument();
});

test("the active filter can be cleared", async () => {
  renderAt("/ledger/transactions?group=bank");
  await waitFor(() => expect(screen.getByText("Transactions · 3")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("link", { name: "Show all" }));
  await waitFor(() => expect(screen.getByText("Transactions · 5")).toBeInTheDocument());
  expect(screen.queryByRole("status")).toBeNull();
});

test("an unknown group shows everything, unfiltered", async () => {
  renderAt("/ledger/transactions?group=nope");
  await waitFor(() => expect(screen.getByText("Transactions · 5")).toBeInTheDocument());
  expect(screen.queryByRole("status")).toBeNull();
});
