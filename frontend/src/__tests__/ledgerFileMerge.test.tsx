import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { EditorView } from "@codemirror/view";
import { LedgerFileMerge } from "@/screens/ledger/import/LedgerFileMerge";

const PREVIEW = {
  batch_id: "b1",
  transactions: [
    {
      id: "t1", date: "2024-01-05", narration: "Buy units", duplicate: false, unbookable_sell: false,
      postings: [
        { account: "Assets:Investment:MutualFund:Groww:ACMEELSS", amount: "742.61", commodity: "ACMEELSS", cost: "83.48", cost_currency: "INR" },
        { account: "Assets:Checking:HDFC", amount: "-61993.69", commodity: "INR" },
      ],
    },
  ],
  accounts: [
    { raw: "Assets:Checking:HDFC", suggested: "Assets:Checking:HDFC", status: "known" },
    { raw: "Random:Thing", suggested: "Random:Thing", status: "needs_mapping" },
  ],
  commodities: [{ raw: "AcmeELSS", suggested: "ACMEELSS", display_name: "Acme ELSS", status: "new" }],
  unparsed: [],
  ignored_lines: 0,
  imbalances: [],
  summary: { transactions: 1, duplicates: 0, new_accounts: 0, new_commodities: 1, unparsed: 0, imbalances: 0 },
};

afterEach(() => vi.restoreAllMocks());

function mockFetch(payload: unknown) {
  vi.spyOn(global, "fetch").mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 }));
}

// The Transactions tab now renders a CodeMirror editor; its seeded ledger text
// lives in the .cm-content element rather than in <input> display values.
function editorText(): string {
  return document.querySelector(".cm-content")?.textContent ?? "";
}
async function findEditorNarration(text: string) {
  await waitFor(() => expect(editorText()).toContain(text));
}

describe("LedgerFileMerge", () => {
  it("renders each section tab after preview and gates commit on needs_mapping", async () => {
    mockFetch(PREVIEW);
    render(<LedgerFileMerge />);
    const file = new File(["x"], "acme.ledger", { type: "text/plain" });
    const input = screen.getByLabelText(/ledger file/i) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await findEditorNarration("Buy units");                                // transactions tab (default)
    fireEvent.click(screen.getByRole("tab", { name: /commodities/i }));    // switch to commodities tab
    expect(screen.getByText("Acme ELSS")).toBeInTheDocument();             // commodity display name
    expect(screen.getByText("ACMEELSS")).toBeInTheDocument();              // commodity symbol
    // commit disabled while a needs_mapping account is unresolved
    const commit = screen.getByRole("button", { name: /post/i }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true);
  });

  it("enables commit once every needs_mapping is resolved and posts both maps", async () => {
    mockFetch(PREVIEW);
    render(<LedgerFileMerge />);
    const file = new File(["x"], "acme.ledger", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(/ledger file/i), { target: { files: [file] } });
    await findEditorNarration("Buy units");

    fireEvent.click(screen.getByRole("tab", { name: /accounts/i }));
    const mapInput = screen.getByLabelText(/map Random:Thing/i);
    fireEvent.change(mapInput, { target: { value: "Expenses:Misc" } });

    const commit = screen.getByRole("button", { name: /post/i }) as HTMLButtonElement;
    await waitFor(() => expect(commit.disabled).toBe(false));

    const spy = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ committed: 1, skipped_duplicates: 0, opened_accounts: [], declared_commodities: ["ACMEELSS"] }), { status: 200 }),
    );
    fireEvent.click(commit);
    await waitFor(() => {
      const call = spy.mock.calls.find(([url]) => String(url).endsWith("/api/import/ledger/commit"));
      expect(call).toBeTruthy();
      const sent = JSON.parse(String((call![1] as RequestInit).body));
      expect(sent.account_map["Random:Thing"]).toBe("Expenses:Misc");
      expect(sent.commodity_map["AcmeELSS"]).toBe("ACMEELSS");
    });
  });

  it("gates commit on needs_mapping commodity if manually cleared", async () => {
    const previewWithMixedCommodities = {
      batch_id: "b2",
      transactions: [
        {
          id: "t1", date: "2024-01-05", narration: "Buy units", duplicate: false, unbookable_sell: false,
          postings: [
            { account: "Assets:Investment:MutualFund:Groww:ACMEELSS", amount: "742.61", commodity: "ACMEELSS", cost: "83.48", cost_currency: "INR" },
            { account: "Assets:Checking:HDFC", amount: "-61993.69", commodity: "INR" },
            { account: "Assets:Investment:MutualFund:Groww:MYSTERYBLAH", amount: "100", commodity: "MYSTERYBLAH" },
          ],
        },
      ],
      accounts: [
        { raw: "Assets:Checking:HDFC", suggested: "Assets:Checking:HDFC", status: "known" },
        { raw: "Assets:Investment:MutualFund:Groww:ACMEELSS", suggested: "Assets:Investment:MutualFund:Groww:ACMEELSS", status: "known" },
        { raw: "Assets:Investment:MutualFund:Groww:MYSTERYBLAH", suggested: "Assets:Investment:MutualFund:Groww:MYSTERYBLAH", status: "known" },
      ],
      commodities: [
        { raw: "AcmeELSS", suggested: "ACMEELSS", display_name: "Acme ELSS", status: "new" },
        { raw: "MysteryBlah", suggested: "MYSTERYBLAH", display_name: null, status: "needs_mapping" },
      ],
      unparsed: [],
      ignored_lines: 0,
      imbalances: [],
      summary: { transactions: 1, duplicates: 0, new_accounts: 0, new_commodities: 2, unparsed: 0, imbalances: 0 },
    };

    mockFetch(previewWithMixedCommodities);
    render(<LedgerFileMerge />);
    const file = new File(["x"], "acme.ledger", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(/ledger file/i), { target: { files: [file] } });
    await findEditorNarration("Buy units");

    // Commit is initially enabled (all needs_mapping auto-seeded with suggested)
    const commit = screen.getByRole("button", { name: /post/i }) as HTMLButtonElement;
    expect(commit.disabled).toBe(false);

    // Clear the needs_mapping commodity symbol to simulate unsetting
    fireEvent.click(screen.getByRole("tab", { name: /commodities/i }));
    const symbolInput = screen.getByLabelText(/symbol MysteryBlah/i) as HTMLInputElement;
    fireEvent.change(symbolInput, { target: { value: "" } });

    // Commit becomes disabled
    await waitFor(() => expect(commit.disabled).toBe(true));

    // Re-enter a valid symbol
    fireEvent.change(symbolInput, { target: { value: "UNKNOWNCOMMODITY" } });

    // Commit becomes enabled again
    await waitFor(() => expect(commit.disabled).toBe(false));
  });

  it("counts a split MF trade (shared id, two targets) as one transaction and labels the investment half", async () => {
    const SPLIT_PREVIEW = {
      batch_id: "bSplit",
      transactions: [
        {
          id: "t1", target: "main", date: "2024-01-05", narration: "Buy units", duplicate: false, unbookable_sell: false,
          postings: [
            { account: "Assets:Checking:HDFC", amount: "-61993.69", commodity: "INR", cost: null, cost_currency: null },
            { account: "Equity:Opening-Balances", amount: null, commodity: null, cost: null, cost_currency: null },
          ],
        },
        {
          id: "t1", target: "investment", date: "2024-01-05", narration: "Buy units", duplicate: false, unbookable_sell: false,
          postings: [
            { account: "Assets:Investment:MutualFund:Groww:ACMEELSS", amount: "742.61", commodity: "ACMEELSS", cost: "83.48", cost_currency: "INR" },
            { account: "Equity:Opening-Balances", amount: null, commodity: null, cost: null, cost_currency: null },
          ],
        },
      ],
      accounts: [
        { raw: "Assets:Checking:HDFC", suggested: "Assets:Checking:HDFC", status: "known" },
        { raw: "Assets:Investment:MutualFund:Groww:ACMEELSS", suggested: "Assets:Investment:MutualFund:Groww:ACMEELSS", status: "known" },
      ],
      commodities: [{ raw: "AcmeELSS", suggested: "ACMEELSS", display_name: "Acme ELSS", status: "new" }],
      unparsed: [],
      ignored_lines: 0,
      imbalances: [],
      summary: { transactions: 1, duplicates: 0, new_accounts: 0, new_commodities: 1, unparsed: 0, imbalances: 0 },
    };

    mockFetch(SPLIT_PREVIEW);
    render(<LedgerFileMerge />);
    fireEvent.change(screen.getByLabelText(/ledger file/i), { target: { files: [new File(["x"], "split.ledger")] } });
    await findEditorNarration("Buy units");

    // Two rows share one id — the badge should count 1 logical transaction.
    const txnsTab = screen.getByRole("tab", { name: /transactions/i });
    expect(txnsTab).toHaveTextContent("1");

    // The investment-target half is visually annotated so it doesn't look
    // like an accidental duplicate of the main half.
    await findEditorNarration("Buy units [→ investments.beancount]");
  });

  it("blocks commit and lists the line number when a txn does not balance", async () => {
    mockFetch({
      ...PREVIEW,
      accounts: [{ raw: "Assets:Checking:HDFC", suggested: "Assets:Checking:HDFC", status: "known" }],
      imbalances: [{ line_no: 7, narration: "Short txn", residual: "10.00 INR" }],
      summary: { ...PREVIEW.summary, imbalances: 1 },
    });
    render(<LedgerFileMerge />);
    fireEvent.change(screen.getByLabelText(/ledger file/i), { target: { files: [new File(["x"], "a.ledger")] } });
    await findEditorNarration("Buy units");
    fireEvent.click(screen.getByRole("tab", { name: /issues/i }));
    expect(screen.getByText(/line 7/i)).toBeInTheDocument();
    const commit = screen.getByRole("button", { name: /post/i }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true);
  });
});

// ── Shared CodeMirror editor in the merge Transactions tab ───────────────────

const CLEAN = {
  batch_id: "bClean",
  transactions: [
    {
      id: "t1", date: "2024-01-05", narration: "Coffee run", duplicate: false, unbookable_sell: false,
      postings: [
        { account: "Expenses:Food", amount: "250.00", commodity: "INR", cost: null, cost_currency: null },
        { account: "Assets:Checking:HDFC", amount: "-250.00", commodity: "INR", cost: null, cost_currency: null },
      ],
    },
  ],
  accounts: [
    { raw: "Expenses:Food", suggested: "Expenses:Food", status: "known" },
    { raw: "Assets:Checking:HDFC", suggested: "Assets:Checking:HDFC", status: "known" },
  ],
  commodities: [],
  unparsed: [], ignored_lines: 0, imbalances: [],
  summary: { transactions: 1, duplicates: 0, new_accounts: 0, new_commodities: 0, unparsed: 0, imbalances: 0 },
};

async function loadClean() {
  mockFetch(CLEAN);
  render(<LedgerFileMerge />);
  const input = screen.getByLabelText(/ledger file/i) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "c.ledger")] } });
  await findEditorNarration("Coffee run");
}

describe("LedgerFileMerge shared editor", () => {
  it("seeds the CodeMirror editor with the batch's journal text", async () => {
    await loadClean();
    const text = editorText();
    expect(text).toContain("2024/01/05 Coffee run");
    expect(text).toContain("Expenses:Food");
    expect(text).toContain("Assets:Checking:HDFC");
  });

  it("Find & replace button opens the CodeMirror search panel", async () => {
    await loadClean();
    fireEvent.click(screen.getByRole("button", { name: /find/i }));
    await waitFor(() =>
      expect(document.querySelector(".cm-search")).not.toBeNull(),
    );
  });

  it("Re-parse edits re-POSTs the edited doc to /preview and reseeds", async () => {
    await loadClean();

    // Edit the buffer directly via the live EditorView (jsdom can't type into CM).
    const content = document.querySelector(".cm-content") as HTMLElement;
    const view = EditorView.findFromDOM(content)!;
    const pos = view.state.doc.toString().indexOf("Coffee run");
    view.dispatch({ changes: { from: pos, to: pos + "Coffee run".length, insert: "Tea break" } });

    const reseeded = { ...CLEAN, batch_id: "bReparsed" };
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify(reseeded), { status: 200 }),
    );
    fireEvent.click(screen.getByRole("button", { name: /re-parse/i }));
    await waitFor(() => {
      const call = spy.mock.calls.find(([url]) =>
        String(url).endsWith("/api/import/ledger/preview"),
      );
      expect(call).toBeTruthy();
      // The edited doc is submitted as a File in a multipart form.
      const body = (call![1] as RequestInit).body as FormData;
      expect(body).toBeInstanceOf(FormData);
      const sent = body.get("file") as File;
      expect(sent).toBeInstanceOf(File);
    });
  });

  it("Post still commits through /api/import/ledger/commit", async () => {
    await loadClean();
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ committed: 1, skipped_duplicates: 0, opened_accounts: [], declared_commodities: [] }), { status: 200 }),
    );
    fireEvent.click(screen.getByRole("button", { name: /^post$/i }));
    await waitFor(() => {
      const call = spy.mock.calls.find(([url]) =>
        String(url).endsWith("/api/import/ledger/commit"),
      );
      expect(call).toBeTruthy();
      const sent = JSON.parse(String((call![1] as RequestInit).body));
      expect(sent.batch_id).toBe("bClean");
    });
  });
});

// ── Ignored lines surfaced in the Issues tab + active-state signal ───────────

describe("LedgerFileMerge ignored lines + active signal", () => {
  it("lists ignored lines with line number, text and reason on the Issues tab", async () => {
    mockFetch({
      ...CLEAN,
      batch_id: "bIgnored",
      ignored_lines: 2,
      ignored: [
        { line_no: 1, text: "; a header comment", reason: "comment line" },
        { line_no: 2, text: "account Assets:Checking:HDFC", reason: "unsupported directive (account)" },
      ],
    });
    render(<LedgerFileMerge />);
    fireEvent.change(screen.getByLabelText(/ledger file/i), { target: { files: [new File(["x"], "i.ledger")] } });
    await findEditorNarration("Coffee run");

    fireEvent.click(screen.getByRole("tab", { name: /issues/i }));
    expect(screen.getByText("; a header comment")).toBeInTheDocument();
    expect(screen.getByText("unsupported directive (account)")).toBeInTheDocument();
    expect(screen.getByText("account Assets:Checking:HDFC")).toBeInTheDocument();
  });

  it("fires onActiveChange(true) once a journal preview loads", async () => {
    mockFetch(CLEAN);
    const onActiveChange = vi.fn();
    render(<LedgerFileMerge onActiveChange={onActiveChange} />);
    // false on initial mount (no preview yet)
    expect(onActiveChange).toHaveBeenCalledWith(false);

    fireEvent.change(screen.getByLabelText(/ledger file/i), { target: { files: [new File(["x"], "a.ledger")] } });
    await findEditorNarration("Coffee run");
    await waitFor(() => expect(onActiveChange).toHaveBeenCalledWith(true));
  });
});

describe("LedgerFileMerge discard", () => {
  beforeEach(() => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("asks before discarding, and Cancel keeps the review", async () => {
    await loadPreview();
    (window.confirm as unknown as ReturnType<typeof vi.fn>).mockReturnValue(false);
    const spy = vi.spyOn(global, "fetch");
    fireEvent.click(screen.getByRole("button", { name: /discard/i }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/discard this ledger import/i));
    expect(spy.mock.calls.some(([url]) => String(url).endsWith("/api/import/ledger/b1"))).toBe(false);
    expect(screen.getByRole("button", { name: /post/i })).toBeInTheDocument();
  });

  async function loadPreview() {
    mockFetch(PREVIEW);
    render(<LedgerFileMerge />);
    const file = new File(["x"], "acme.ledger", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(/ledger file/i), { target: { files: [file] } });
    await findEditorNarration("Buy units");
  }

  it("Discard deletes the server-held preview and clears the review", async () => {
    await loadPreview();
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ discarded: "b1" }), { status: 200 }),
    );
    fireEvent.click(screen.getByRole("button", { name: /discard/i }));
    await waitFor(() => {
      const call = spy.mock.calls.find(([url]) => String(url).endsWith("/api/import/ledger/b1"));
      expect(call).toBeTruthy();
      expect((call![1] as RequestInit).method).toBe("DELETE");
    });
    await waitFor(() => expect(screen.queryByRole("button", { name: /post/i })).toBeNull());
  });

  it("Discard of a preview the server already dropped still clears the review", async () => {
    await loadPreview();
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "E1006" }), { status: 404 }),
    );
    fireEvent.click(screen.getByRole("button", { name: /discard/i }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /post/i })).toBeNull());
  });

  it("Discard that fails for another reason keeps the review and shows the error", async () => {
    await loadPreview();
    vi.spyOn(global, "fetch").mockResolvedValue(new Response("boom", { status: 500 }));
    fireEvent.click(screen.getByRole("button", { name: /discard/i }));
    await waitFor(() => expect(screen.getByText(/500/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /post/i })).toBeInTheDocument();
  });

  it("Re-parse discards the batch it replaces", async () => {
    await loadPreview();
    const spy = vi.spyOn(global, "fetch").mockImplementation(async (url) =>
      String(url).endsWith("/api/import/ledger/preview")
        ? new Response(JSON.stringify({ ...PREVIEW, batch_id: "b9" }), { status: 200 })
        : new Response(JSON.stringify({ discarded: "b1" }), { status: 200 }),
    );
    fireEvent.click(screen.getByRole("button", { name: /re-parse/i }));
    await waitFor(() => {
      const call = spy.mock.calls.find(([url]) => String(url).endsWith("/api/import/ledger/b1"));
      expect(call).toBeTruthy();
      expect((call![1] as RequestInit).method).toBe("DELETE");
    });
  });
});
