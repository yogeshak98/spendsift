import { render, screen } from "@testing-library/react";
import { RawTransactionList } from "@/screens/ledger/raw/RawTransactionList";

test("RawTransactionList renders a transaction's date, narration, accounts and amounts read-only", () => {
  const txn = {
    txn_id: "t1",
    date: "2026-04-01",
    narration: "Rent",
    year_file_hash: "h",
    postings: [
      { account: "Expenses:Rent", amount: "15000", tags: [], meta: {} },
      { account: "Assets:Checking", amount: "-15000", tags: [], meta: {} },
    ],
  };
  render(<RawTransactionList transactions={[txn]} />);

  // Header combines date + narration in one span.
  expect(screen.getByText(/2026-04-01 · Rent/)).toBeInTheDocument();
  expect(screen.getByText("Expenses:Rent")).toBeInTheDocument();
  expect(screen.getByText("Assets:Checking")).toBeInTheDocument();
  expect(screen.getByText("15000")).toBeInTheDocument();
  expect(screen.getByText("-15000")).toBeInTheDocument();

  // READ-ONLY: no interactive controls.
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});

test("RawTransactionList renders a quiet empty state when there are no transactions", () => {
  render(<RawTransactionList transactions={[]} />);
  expect(screen.getByText("No transactions for this year.")).toBeInTheDocument();
});
