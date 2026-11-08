import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { test, expect, vi, beforeEach, afterEach } from "vitest";
import { LedgerImport } from "@/screens/ledger/Import";
import commitResponse from "@/__tests__/fixtures/importReview/commit-response.json";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

const MATCHED = {
  matched: true,
  batch_id: "b1",
  fingerprint: "0123456789abcdef",
  candidates: [
    {
      id: "c1", date: "2026-04-01", raw_narration: "FICTIONAL CAFE", suggested_category: "Expenses:Food",
      amount: "1250.50", direction: "debit", dedup_status: "new", needs_account: false,
      source_account: "Liabilities:Card:Fictional",
    },
  ],
  skipped_rows: [],
  statement: null,
};

test("the commit message reports the rules learned and the accounts opened (I2)", async () => {
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    if (key === "/api/accounts") return new Response(JSON.stringify({ accounts: [] }));
    if (key === "/api/import/detect") return new Response(JSON.stringify(MATCHED));
    if (key === "/api/commit/b1") return new Response(JSON.stringify(commitResponse));
    if (key.endsWith("/learning")) return new Response(JSON.stringify({ will_learn: [], not_learned: [], would_open: [] }));
    return new Response("{}");
  });
  const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
  render(<LedgerImport />);
  fireEvent.change(screen.getByLabelText("statement file"), { target: { files: [new File(["x"], "s.csv")] } });
  fireEvent.click(await screen.findByRole("button", { name: "Commit" }));
  await waitFor(() => expect(alertSpy).toHaveBeenCalled());
  const msg = alertSpy.mock.calls[0][0] as string;
  expect(msg).toContain("Committed 11 transactions");
  expect(msg).toContain("Learned 2 rules.");
  expect(msg).toContain("Opened: Expenses:Food:Cafe, Expenses:Household:Appliances, Expenses:Subscriptions.");
});

function stubCommit(commitRes: () => Response) {
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    if (key === "/api/accounts") return new Response(JSON.stringify({ accounts: [] }));
    if (key === "/api/import/detect") return new Response(JSON.stringify(MATCHED));
    if (key === "/api/commit/b1") return commitRes();
    if (key.endsWith("/learning")) return new Response(JSON.stringify({ will_learn: [], not_learned: [], would_open: [] }));
    return new Response("{}");
  });
}

async function openAndCommit() {
  render(<LedgerImport />);
  fireEvent.change(screen.getByLabelText("statement file"), { target: { files: [new File(["x"], "s.csv")] } });
  fireEvent.click(await screen.findByRole("button", { name: "Commit" }));
}

test("E1073 says the rows were committed and not to retry (M3)", async () => {
  stubCommit(
    () =>
      new Response(
        JSON.stringify({
          code: "E1073",
          description: "the rows were committed; the learned rules were not saved: disk full",
          resolution: "Do not commit this batch again: its rows are in the journal.",
        }),
        { status: 500 },
      ),
  );
  const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
  await openAndCommit();
  const note = await screen.findByText(/WERE committed/);
  expect(note.textContent).toContain("do not retry");
  expect(note.textContent).toContain("learned rules were not saved: disk full");
  expect(note.textContent).toContain("Do not commit this batch again");
  expect(note.className).toContain("callout");
  expect(alertSpy).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Commit" })).not.toBeInTheDocument();
});

test("any other commit error shows its description and resolution and keeps the batch (M3)", async () => {
  stubCommit(
    () =>
      new Response(
        JSON.stringify({ code: "E1081", description: "rows no longer add up", resolution: "Start over." }),
        { status: 422 },
      ),
  );
  vi.spyOn(window, "alert").mockImplementation(() => {});
  await openAndCommit();
  const note = await screen.findByText(/rows no longer add up/);
  expect(note.textContent).toContain("Start over.");
  expect(note.textContent).not.toContain("WERE committed");
  expect(screen.getByRole("button", { name: "Commit" })).toBeInTheDocument();
});
