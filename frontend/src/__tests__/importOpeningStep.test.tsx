import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { test, expect, vi, beforeEach, afterEach } from "vitest";
import { LedgerImport } from "@/screens/ledger/Import";
import commitResponse from "./fixtures/importReview/commit-response.json";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

const MATCHED = {
  matched: true,
  batch_id: "b1",
  fingerprint: "0123456789abcdef",
  candidates: [
    {
      id: "c1", date: "2026-05-01", raw_narration: "FICTIONAL CAFE", suggested_category: "Expenses:Food",
      amount: "1250.50", direction: "debit", dedup_status: "new", needs_account: false,
      source_account: "Assets:Checking:Acme",
    },
  ],
  skipped_rows: [],
  statement: null,
};

// API_CONTRACT.md "Opening balances": GET /api/review/{batch_id}/opening (verbatim)
const PROMPT = {
  batch_id: "9f0c2b7e4d1a4c3b8e6f5a2d1c0b9a87",
  prompts: [
    {
      account: "Assets:Checking:Acme",
      account_display: "Assets:Checking:Acme",
      kind: "bank",
      statement_start: "2026-05-01",
      suggested_date: "2026-04-30",
      april: false,
      april_advice:
        "This statement starts on 01 May 2026. For a full FY 2026-27, import the statement that starts in April 2026 first; otherwise the opening is dated 30 Apr 2026.",
      suggestion: {
        source: "statement",
        amount: "8450.75",
        direction: "have",
        ledger_amount: "8450.75",
        arithmetic: "printed opening balance 8,450.75",
        label: "Opening Balance",
      },
      alternatives: [
        {
          source: "derived",
          amount: "8450.75",
          direction: "have",
          ledger_amount: "8450.75",
          arithmetic: "first row balance 9,450.75 − credit 1,000.00 = 8,450.75",
          label: null,
        },
      ],
      mismatch: false,
      note: null,
      directions: ["have", "owe"],
    },
  ],
  missing: [],
};

const NO_PROMPT = { batch_id: "b1", prompts: [], missing: [] };

// POST /api/openings response
const WRITTEN = {
  account: "Assets:Checking:Acme",
  account_display: "Assets:Checking:Acme",
  txn_id: "a1b2c3d4e5f60718",
  date: "2026-04-30",
  ledger_amount: "8450.75",
  source: "statement",
  balance_check: false,
  snapshot: "20261101-101500-opening",
};

// POST /api/openings/zero response (verbatim)
const ZERO = { account: "Liabilities:People:Ravi", account_display: "Liabilities:People:Ravi", status: "zero" };

type Call = { url: string; method: string; body: unknown };

function stub(opening: () => Response | Promise<Response>, commitRes?: () => Response): Call[] {
  const calls: Call[] = [];
  vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
    const key = String(url);
    calls.push({ url: key, method: init?.method ?? "GET", body: typeof init?.body === "string" ? JSON.parse(init.body) : null });
    if (key === "/api/accounts") return new Response(JSON.stringify({ accounts: [] }));
    if (key === "/api/import/detect") return new Response(JSON.stringify(MATCHED));
    if (key === "/api/review/b1/opening") return opening();
    if (key === "/api/openings") return new Response(JSON.stringify(WRITTEN));
    if (key === "/api/openings/zero") return new Response(JSON.stringify(ZERO));
    if (key === "/api/commit/b1") return commitRes ? commitRes() : new Response(JSON.stringify(commitResponse));
    if (key.endsWith("/learning")) return new Response(JSON.stringify({ will_learn: [], not_learned: [], would_open: [] }));
    return new Response("{}");
  });
  vi.spyOn(window, "alert").mockImplementation(() => {});
  return calls;
}

const json = (b: unknown) => () => new Response(JSON.stringify(b));
const commitCalls = (calls: Call[]) => calls.filter((c) => c.url.startsWith("/api/commit/"));

async function detectAndClickCommit() {
  render(
    <MemoryRouter>
      <LedgerImport />
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText("statement file"), { target: { files: [new File(["x"], "s.csv")] } });
  // Wait for the prompt fetch to settle: the Opening step appears in the rail.
  await screen.findByRole("button", { name: "Commit" });
}

async function openOpeningStep() {
  await detectAndClickCommit();
  await screen.findByText("Opening");
  fireEvent.click(screen.getByRole("button", { name: "Commit" }));
  return screen.findByText("Assets:Checking:Acme: first import");
}

test("matched detect with empty account routes commit through opening step", async () => {
  const calls = stub(json(PROMPT));
  await openOpeningStep();
  expect(commitCalls(calls)).toHaveLength(0);
  expect(screen.getByRole("button", { name: "Continue to commit" })).toBeDisabled();
});

test("recording then continue commits", async () => {
  const calls = stub(json(PROMPT));
  await openOpeningStep();
  fireEvent.click(screen.getByLabelText("I've checked this amount and date"));
  fireEvent.click(screen.getByRole("button", { name: "Record opening" }));
  const cont = await screen.findByRole("button", { name: "Continue to commit" });
  await waitFor(() => expect(cont).toBeEnabled());
  fireEvent.click(cont);
  await waitFor(() => expect(commitCalls(calls)).toHaveLength(1));
  const posts = calls.filter((c) => c.method === "POST").map((c) => c.url);
  expect(posts.indexOf("/api/openings")).toBeGreaterThan(-1);
  expect(posts.indexOf("/api/openings")).toBeLessThan(posts.indexOf("/api/commit/b1"));
  const write = calls.find((c) => c.url === "/api/openings")!;
  expect(write.body).toMatchObject({
    account: "Assets:Checking:Acme", amount: "8450.75", direction: "have", date: "2026-04-30", batch_id: "b1",
  });
});

test("skip then continue commits without writing an opening", async () => {
  const calls = stub(json(PROMPT));
  await openOpeningStep();
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  fireEvent.click(await screen.findByRole("button", { name: "Continue to commit" }));
  await waitFor(() => expect(commitCalls(calls)).toHaveLength(1));
  expect(calls.some((c) => c.url === "/api/openings" || c.url === "/api/openings/zero")).toBe(false);
});

test("started at zero writes nothing until confirmed, then posts once and commits", async () => {
  const calls = stub(json(PROMPT));
  await openOpeningStep();
  fireEvent.click(screen.getByRole("button", { name: "Started at zero" }));
  const mark = screen.getByRole("button", { name: "Mark started at zero" });
  expect(mark).toBeDisabled();
  fireEvent.click(mark);
  await new Promise((r) => setTimeout(r, 20));
  expect(calls.some((c) => c.url === "/api/openings/zero")).toBe(false);
  expect(screen.getByRole("button", { name: "Continue to commit" })).toBeDisabled();

  fireEvent.click(screen.getByLabelText("I've checked: this account started at zero"));
  act(() => {
    fireEvent.click(mark);
    fireEvent.click(mark);
  });
  expect(await screen.findByText("Marked as started at zero.")).toBeInTheDocument();
  expect(calls.filter((c) => c.url === "/api/openings/zero")).toHaveLength(1);
  const zero = calls.find((c) => c.url === "/api/openings/zero")!;
  expect(zero.body).toEqual({ account: "Assets:Checking:Acme", zero: true });
  fireEvent.click(screen.getByRole("button", { name: "Continue to commit" }));
  await waitFor(() => expect(commitCalls(calls)).toHaveLength(1));
});

test("cancelling started at zero brings Skip back and writes nothing", async () => {
  const calls = stub(json(PROMPT));
  await openOpeningStep();
  fireEvent.click(screen.getByRole("button", { name: "Started at zero" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Skip for now" })).toBeInTheDocument();
  expect(calls.some((c) => c.url === "/api/openings/zero")).toBe(false);
});

test("two Continue to commit clicks commit once", async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const calls = stub(json(PROMPT));
  await openOpeningStep();
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  const cont = await screen.findByRole("button", { name: "Continue to commit" });
  await waitFor(() => expect(cont).toBeEnabled());
  const fetchMock = vi.mocked(global.fetch);
  const inner = fetchMock.getMockImplementation()!;
  // The commit is held until both clicks have landed.
  fetchMock.mockImplementation(async (url, init) => {
    if (String(url) === "/api/commit/b1") await gate;
    return inner(url, init);
  });
  // Both clicks land before React re-renders, so only the handler's guard
  // can absorb the second one.
  act(() => {
    fireEvent.click(cont);
    fireEvent.click(cont);
  });
  expect(cont).toBeDisabled();
  release();
  await waitFor(() => expect(commitCalls(calls)).toHaveLength(1));
  await new Promise((r) => setTimeout(r, 30));
  expect(commitCalls(calls)).toHaveLength(1);
});

test("no prompts commits directly", async () => {
  const calls = stub(json(NO_PROMPT));
  await detectAndClickCommit();
  expect(screen.queryByText("Opening")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Commit" }));
  await waitFor(() => expect(commitCalls(calls)).toHaveLength(1));
});

test("prompt fetch failure still commits", async () => {
  const calls = stub(() => new Response("boom", { status: 500 }));
  await detectAndClickCommit();
  await screen.findByText(/Could not check for an opening balance; you can add it later in Settings · Opening balances/);
  fireEvent.click(screen.getByRole("button", { name: "Commit" }));
  await waitFor(() => expect(commitCalls(calls)).toHaveLength(1));
});

test("shows arithmetic and april advice", async () => {
  stub(json(PROMPT));
  await openOpeningStep();
  expect(screen.getByText("printed opening balance 8,450.75")).toBeInTheDocument();
  expect(screen.getByText(/import the statement that starts in April 2026 first/)).toBeInTheDocument();
  expect(screen.getByLabelText("Amount")).toHaveValue("8450.75");
  expect(screen.getByLabelText("Date")).toHaveValue("2026-04-30");
});

test("without a suggestion the step says the statement prints no opening", async () => {
  stub(json({ ...PROMPT, prompts: [{ ...PROMPT.prompts[0], suggestion: null, alternatives: [] }] }));
  await openOpeningStep();
  expect(
    screen.getByText("This statement does not print an opening balance. Enter it from your previous statement, or skip."),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Amount")).toHaveValue("");
});

test("accounts with activity but no opening get one info line linking to the checklist", async () => {
  stub(json({ ...NO_PROMPT, missing: ["Assets:Checking:Acme"] }));
  await detectAndClickCommit();
  const link = await screen.findByRole("link", { name: /Opening balances/ });
  expect(link).toHaveAttribute("href", "/settings/openings");
});

test("the info line names accounts by display name and skips prompted ones", async () => {
  stub(json({ ...PROMPT, missing: ["Assets:Checking:Acme", "Assets:Investment:Gold:G1"] }));
  const fetchMock = vi.mocked(global.fetch);
  const inner = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (url, init) => {
    if (String(url) === "/api/openings" && (init?.method ?? "GET") === "GET") {
      return new Response(
        JSON.stringify({
          accounts: [
            { account: "Assets:Checking:Acme", account_display: "Assets:Checking:Acme" },
            { account: "Assets:Investment:Gold:G1", account_display: "Assets:Investment:Gold:Fictive Gold Fund" },
          ],
          missing_count: 2,
          warning: "w",
        }),
      );
    }
    return inner(url, init);
  });
  await detectAndClickCommit();
  // Acme is asked about in the Opening step, so only the other account is named.
  expect(
    await screen.findByText(/Assets:Investment:Gold:Fictive Gold Fund has activity not covered by an opening balance/),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Assets:Checking:Acme has activity/)).toBeNull();
});

test("no info line when every missing account is prompted", async () => {
  stub(json({ ...PROMPT, missing: ["Assets:Checking:Acme"] }));
  await detectAndClickCommit();
  await screen.findByText("Opening");
  expect(screen.queryByText(/not covered by an opening balance/)).toBeNull();
});

test("E1073 from the opening step still says the rows were committed", async () => {
  const calls = stub(
    json(PROMPT),
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
  await openOpeningStep();
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  fireEvent.click(await screen.findByRole("button", { name: "Continue to commit" }));
  const note = await screen.findByText(/WERE committed/);
  expect(note.textContent).toContain("do not retry");
  expect(note.textContent).toContain("Do not commit this batch again");
  expect(commitCalls(calls)).toHaveLength(1);
  expect(screen.queryByText(/Nothing to ask/)).not.toBeInTheDocument();
});

test("a Commit click before the prompt arrives waits and still shows the opening step", async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const calls = stub(async () => {
    await gate;
    return new Response(JSON.stringify(PROMPT));
  });
  await detectAndClickCommit();
  fireEvent.click(screen.getByRole("button", { name: "Commit" }));
  await new Promise((r) => setTimeout(r, 20));
  expect(commitCalls(calls)).toHaveLength(0);
  release();
  await screen.findByText("Assets:Checking:Acme: first import");
  expect(commitCalls(calls)).toHaveLength(0);
});

test("two Commit clicks while the prompt is held commit once", async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const calls = stub(async () => {
    await gate;
    return new Response(JSON.stringify(NO_PROMPT));
  });
  await detectAndClickCommit();
  const btn = screen.getByRole("button", { name: "Commit" });
  // Both clicks land before React re-renders, so the disabled attribute cannot
  // absorb the second one: only the handler's own guard can.
  act(() => {
    fireEvent.click(btn);
    fireEvent.click(btn);
  });
  // Separate check: the button shows its busy state.
  expect(btn).toBeDisabled();
  release();
  await waitFor(() => expect(commitCalls(calls)).toHaveLength(1));
  await new Promise((r) => setTimeout(r, 30));
  expect(commitCalls(calls)).toHaveLength(1);
});
