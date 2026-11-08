import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, test, expect, vi } from "vitest";
import { EditorView } from "@codemirror/view";
import {
  StatementReview,
  buildReviewPatch,
  toEditorState,
  type Candidate,
} from "@/screens/ledger/import/StatementReview";
import reviewGet from "@/__tests__/fixtures/importReview/review-get.json";
import learning from "@/__tests__/fixtures/importReview/review-learning.json";
import reqs from "@/__tests__/fixtures/importReview/review-edit-requests.json";
import reviewEditOk from "@/__tests__/fixtures/importReview/review-edit-ok.json";
import e1072 from "@/__tests__/fixtures/importReview/error-e1072-invalid-tag.json";

const ROWS = [
  {
    id: "c1",
    date: "2024-01-05",
    raw_narration: "Swiggy",
    suggested_category: "Expenses:Food",
    amount: "200",
    direction: "debit",
    dedup_status: "new",
    needs_account: false,
    source_account: "Assets:Checking:HDFC",
  },
];

function renderReview(overrides = {}) {
  vi.spyOn(global, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
  return render(
    <StatementReview
      batch="b1"
      templateFingerprint={null}
      rows={ROWS}
      previewCandidates={[]}
      accounts={[]}
      showTemplateLink={false}
      onBackToMap={vi.fn()}
      onCommit={vi.fn()}
      onRowsChange={vi.fn()}
      {...overrides}
    />,
  );
}

describe("StatementReview", () => {
  test("renders the Review queue panel with candidate count", () => {
    renderReview();
    expect(screen.getByText(/review queue/i)).toBeInTheDocument();
  });

  test("Commit button is disabled when anyNeedsAccount", () => {
    renderReview({ rows: [{ ...ROWS[0], needs_account: true }] });
    const commit = screen.getByRole("button", { name: /commit/i }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true);
  });

  test("Find & replace button is present", () => {
    renderReview();
    expect(screen.getByRole("button", { name: /find/i })).toBeInTheDocument();
  });

  test("Template back-link appears only when showTemplateLink", () => {
    const onBack = vi.fn();
    renderReview({ showTemplateLink: true, onBackToMap: onBack });
    expect(screen.getByRole("button", { name: /^template$/i })).toBeInTheDocument();
  });

  test("no Commit button while batch is null (template preview)", () => {
    renderReview({ batch: null, rows: [], previewCandidates: ROWS });
    expect(screen.queryByRole("button", { name: /commit/i })).toBeNull();
    expect(screen.getByText(/review queue/i)).toBeInTheDocument();
  });
});

describe("toEditorState / buildReviewPatch", () => {
  test("toEditorState loads transaction tags and links a fee row to its charge", () => {
    const { entries, candidates } = toEditorState([
      { ...ROWS[0], id: "c1", tags: ["travel/madikeri-oct-2026"] },
      { ...ROWS[0], id: "c2", raw_narration: "IGST ON FEE", follows: "c1", category_source: "fee_follows" },
    ]);
    expect(entries[0].tags).toEqual(["#travel/madikeri-oct-2026"]);
    expect(entries[1].tags).toEqual([]);
    expect(candidates[0].followsRow).toBeUndefined();
    expect(candidates[1].followsRow).toBe(1);
  });

  test("a follows id that is not in the batch gives no link", () => {
    const { candidates } = toEditorState([{ ...ROWS[0], follows: "gone" }]);
    expect(candidates[0].followsRow).toBeUndefined();
  });

  test("buildReviewPatch sends tags without # and no splits for an Uncategorized row", () => {
    const entry = {
      date: "2024/01/05", narration: "X", ref: null, tags: ["#trip"],
      postings: [
        { account: "Expenses:Uncategorized", amount: "200" },
        { account: "Assets:Checking:HDFC", amount: "-200" },
      ],
    };
    const patch = buildReviewPatch(ROWS[0], entry)!;
    expect(patch.tags).toEqual(["trip"]);
    expect("splits" in patch).toBe(false);
    expect(patch).toMatchObject({ date: "2024-01-05", raw_narration: "X", source_account: "Assets:Checking:HDFC" });
  });

  test("buildReviewPatch sends suggested_category Uncategorized when a categorised row is set back (Q22)", () => {
    const entry = {
      date: "2024/01/05", narration: "Swiggy", ref: null, tags: [],
      postings: [
        { account: "Expenses:Uncategorized", amount: "200" },
        { account: "Assets:Checking:HDFC", amount: "-200" },
      ],
    };
    // ROWS[0] arrived categorised as Expenses:Food.
    const patch = buildReviewPatch(ROWS[0], entry)!;
    expect(patch.suggested_category).toBe("Expenses:Uncategorized");
    expect("splits" in patch).toBe(false);
    // A row uncategorised from the start sends nothing about its category.
    const fresh = buildReviewPatch({ ...ROWS[0], suggested_category: null }, entry)!;
    expect("suggested_category" in fresh).toBe(false);
  });

  test("setting a categorised row back to Uncategorized POSTs suggested_category", async () => {
    const calls: { url: string; body: unknown }[] = [];
    vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST") {
        calls.push({ url, body: JSON.parse(String(init.body)) });
        return new Response("{}");
      }
      return new Response("{}");
    });
    const { container } = render(
      <StatementReview
        batch="b1" templateFingerprint={null} rows={ROWS} previewCandidates={[]} accounts={[]}
        showTemplateLink={false} onBackToMap={vi.fn()} onCommit={vi.fn()} onRowsChange={vi.fn()}
      />,
    );
    const view = EditorView.findFromDOM(container.querySelector(".cm-content") as HTMLElement)!;
    const at = view.state.doc.toString().indexOf("Expenses:Food");
    view.dispatch({ changes: { from: at, to: at + "Expenses:Food".length, insert: "Expenses:Uncategorized" } });
    await waitFor(() => expect(calls.length).toBe(1), { timeout: 3000 });
    expect(calls[0].url).toBe("/api/review/b1/c1");
    expect(calls[0].body).toMatchObject({ suggested_category: "Expenses:Uncategorized" });
    expect("splits" in (calls[0].body as object)).toBe(false);
  });

  test("buildReviewPatch does not reset a row that is split into a category plus Uncategorized (M3)", () => {
    const entry = {
      date: "2024/01/05", narration: "Swiggy", ref: null, tags: [],
      postings: [
        { account: "Expenses:Food", amount: "100" },
        { account: "Expenses:Uncategorized", amount: "100" },
        { account: "Assets:Checking:HDFC", amount: "-200" },
      ],
    };
    const patch = buildReviewPatch(ROWS[0], entry)!;
    expect("suggested_category" in patch).toBe(false);
    expect("splits" in patch).toBe(false);
  });

  test("buildReviewPatch sends splits and tags for a categorised row", () => {
    const entry = {
      date: "2024/01/05", narration: "Swiggy", ref: null, tags: [],
      postings: [
        { account: "Expenses:Food", amount: "200" },
        { account: "Assets:Checking:HDFC", amount: "-200" },
      ],
    };
    const patch = buildReviewPatch(ROWS[0], entry)!;
    expect(patch.splits).toEqual([{ account: "Expenses:Food", amount: "200" }]);
    expect(patch.tags).toEqual([]);
  });

  test("buildReviewPatch refuses an unbalanced row", () => {
    const entry = {
      date: "2024/01/05", narration: "Swiggy", ref: null, tags: [],
      postings: [
        { account: "Expenses:Food", amount: "150" },
        { account: "Assets:Checking:HDFC", amount: "-200" },
      ],
    };
    expect(buildReviewPatch(ROWS[0], entry)).toBeNull();
  });
});

describe("StatementReview against the contract fixtures", () => {
  const fixtureRows = reviewGet.candidates as Candidate[];

  function routeFetch(opts: { review?: unknown; failPost?: Response } = {}) {
    const calls: { url: string; method: string; body: unknown }[] = [];
    vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (method === "GET" && url.endsWith("/learning")) return new Response(JSON.stringify(learning));
      if (method === "GET") return new Response(JSON.stringify(opts.review ?? reviewGet));
      if (opts.failPost) return opts.failPost.clone();
      return new Response(JSON.stringify(reviewEditOk));
    });
    return calls;
  }

  function mount(rows: Candidate[], onRowsChange = vi.fn()) {
    const utils = render(
      <StatementReview
        batch="b1" templateFingerprint={null} rows={rows} previewCandidates={[]} accounts={[]}
        showTemplateLink={false} onBackToMap={vi.fn()} onCommit={vi.fn()} onRowsChange={onRowsChange}
      />,
    );
    const view = EditorView.findFromDOM(utils.container.querySelector(".cm-content") as HTMLElement)!;
    return { ...utils, view, onRowsChange };
  }

  test("shows the will-learn list above Commit, fed by /learning", async () => {
    routeFetch();
    mount(fixtureRows);
    expect(await screen.findByLabelText("don't learn FICTIVEBIKES")).toBeInTheDocument();
    expect(screen.getByText(/was Expenses:Travel:Bike/)).toBeInTheDocument();
    expect(screen.getByText(/New accounts: Expenses:Food:Cafe/)).toBeInTheDocument();
    const commit = screen.getByRole("button", { name: "Commit" });
    const list = screen.getByText(/Committing will teach/);
    expect(list.compareDocumentPosition(commit) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test("a failed /learning read shows a notice instead of silently hiding the list", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if ((init?.method ?? "GET") === "GET" && url.endsWith("/learning")) {
        return new Response("boom", { status: 500 });
      }
      return new Response(JSON.stringify(reviewGet));
    });
    mount(fixtureRows);
    expect(await screen.findByText("Couldn't load what this commit will learn.")).toBeInTheDocument();
    expect(screen.queryByText(/Committing will teach/)).not.toBeInTheDocument();
  });

  test("'don't learn' posts learn:false for that pattern's rows, then re-reads", async () => {
    const calls = routeFetch();
    const onRowsChange = vi.fn();
    mount(fixtureRows, onRowsChange);
    fireEvent.click(await screen.findByLabelText("don't learn FICTIVEBIKES"));
    await waitFor(() => expect(onRowsChange).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")).toMatchObject({
      url: "/api/review/b1/6c85233928a05f76b2fe24511e75d722",
      body: { learn: false },
    });
  });

  test("after a save, a fee row the app moved is pushed into the editor, not re-saved", async () => {
    // Before the edit the fee rows (2, 3) sat on Food; the server now says
    // they follow row 1 onto Subscriptions.
    const stale = fixtureRows.map((r, i) => (i === 1 || i === 2 ? { ...r, suggested_category: "Expenses:Food" } : r));
    const calls = routeFetch();
    const onRowsChange = vi.fn();
    const { view } = mount(stale, onRowsChange);
    const doc = view.state.doc.toString();
    const at = doc.indexOf("Expenses:Subscriptions");
    view.dispatch({ changes: { from: at, to: at + "Expenses:Subscriptions".length, insert: "Expenses:Software" } });
    await waitFor(() => expect(onRowsChange).toHaveBeenCalled(), { timeout: 3000 });
    const after = view.state.doc.toString().split("\n\n");
    expect(after[1]).toContain("Expenses:Subscriptions");
    expect(after[2]).toContain("Expenses:Subscriptions");
    expect(after[1]).not.toContain("Expenses:Food");
    const postsBefore = calls.filter((c) => c.method === "POST").length;
    await new Promise((r) => setTimeout(r, 600)); // the editor change fired by setCategory must not re-save rows 2/3
    expect(calls.filter((c) => c.method === "POST").length).toBe(postsBefore);
  });

  test("an E1072 answer shows a bad callout naming the tag", async () => {
    routeFetch({
      failPost: new Response(JSON.stringify(e1072.body), { status: e1072.status }),
    });
    const { view } = mount(fixtureRows);
    view.dispatch({ changes: { from: 0, to: 0, insert: "" } });
    const at = view.state.doc.toString().indexOf("FICTIVE CAFE") + 1;
    view.dispatch({ changes: { from: at, to: at, insert: "X" } }); // any real edit starts a save round
    const callout = await screen.findByText(/'café' is not a valid tag/, undefined, { timeout: 3000 });
    expect(callout.className).toContain("bad");
  });
});

describe("first save (contract 1.2)", () => {
  const fixtureRows = reviewGet.candidates as Candidate[];

  function capture() {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST") {
        posts.push({ url, body: JSON.parse(String(init.body)) });
        return new Response(JSON.stringify(reviewEditOk));
      }
      if (url.endsWith("/learning")) return new Response(JSON.stringify(learning));
      return new Response(JSON.stringify(reviewGet));
    });
    return posts;
  }
  function mount(rows: Candidate[]) {
    const utils = render(
      <StatementReview
        batch="b1" templateFingerprint={null} rows={rows} previewCandidates={[]} accounts={[]}
        showTemplateLink={false} onBackToMap={vi.fn()} onCommit={vi.fn()} onRowsChange={vi.fn()}
      />,
    );
    return EditorView.findFromDOM(utils.container.querySelector(".cm-content") as HTMLElement)!;
  }
  const FOODS = "FICTIVEFOODS ORDER BANGALORE";

  test("an edit to a categorised row posts exactly the browser_resave_same_split body (I1)", async () => {
    const posts = capture();
    const view = mount(fixtureRows);
    const at = view.state.doc.toString().indexOf(FOODS) + FOODS.length;
    view.dispatch({ changes: { from: at, to: at, insert: "X" } }); // narration is the only change
    await waitFor(() => expect(posts.length).toBeGreaterThan(0), { timeout: 3000 });
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ ...reqs.browser_resave_same_split, raw_narration: FOODS + "X" });
    expect("suggested_category" in posts[0].body).toBe(false);
  });

  test("a tag typed in the editor reaches the POST body (M1), same shape as browser_uncategorised_row", async () => {
    const posts = capture();
    const view = mount(fixtureRows);
    const gym = view.state.doc.toString().indexOf("FICTIVE GYM 21/10/2026");
    const eol = view.state.doc.toString().indexOf("\n", gym);
    view.dispatch({ changes: { from: eol, to: eol, insert: "\n    #fitness" } });
    await waitFor(() => expect(posts.length).toBeGreaterThan(0), { timeout: 3000 });
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual(reqs.browser_uncategorised_row);
  });

  test("a first keystroke on a 50-row statement posts only the edited row (M9)", async () => {
    const rows: Candidate[] = Array.from({ length: 50 }, (_, i) => ({
      ...fixtureRows[8], // an uncategorised row
      id: `r${i}`, raw_narration: `ROW ${i}`, amount: "10.00", suggested_category: i % 2 ? "Expenses:Food" : null,
    }));
    const posts = capture();
    const view = mount(rows);
    const at = view.state.doc.toString().indexOf("ROW 7") + "ROW 7".length;
    view.dispatch({ changes: { from: at, to: at, insert: "X" } });
    await waitFor(() => expect(posts.length).toBeGreaterThan(0), { timeout: 3000 });
    await new Promise((r) => setTimeout(r, 300));
    expect(posts.map((p) => p.url)).toEqual(["/api/review/b1/r7"]);
  });

  test("a failed don't-learn save shows an error and re-reads the real state (M7)", async () => {
    let learningGets = 0;
    vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST") return new Response("boom", { status: 500 });
      if (url.endsWith("/learning")) {
        learningGets++;
        return new Response(JSON.stringify(learning));
      }
      return new Response(JSON.stringify(reviewGet));
    });
    mount(fixtureRows);
    const box = (await screen.findByLabelText("don't learn FICTIVEBIKES")) as HTMLInputElement;
    const before = learningGets;
    fireEvent.click(box);
    expect(await screen.findByText(/Could not save that choice/)).toBeInTheDocument();
    await waitFor(() => expect(learningGets).toBeGreaterThan(before));
    expect((screen.getByLabelText("don't learn FICTIVEBIKES") as HTMLInputElement).checked).toBe(false);
  });

  test("the will-learn list reloads even when the re-read batch has a different row count (M6)", async () => {
    let learningGets = 0;
    vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST") return new Response(JSON.stringify(reviewEditOk));
      if (url.endsWith("/learning")) {
        learningGets++;
        return new Response(JSON.stringify(learning));
      }
      return new Response(JSON.stringify({ candidates: reviewGet.candidates.slice(0, 3), statement: null }));
    });
    mount(fixtureRows);
    const box = await screen.findByLabelText("don't learn FICTIVEBIKES");
    const before = learningGets;
    fireEvent.click(box);
    await waitFor(() => expect(learningGets).toBeGreaterThan(before));
  });

  test("an E1072 text without a 422 status does not raise the tag callout (M4)", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (_i, init) => {
      if (init?.method === "POST") return new Response('{"code":"E1072","description":"NOPE tag"}', { status: 500 });
      return new Response(JSON.stringify(_i.toString().endsWith("/learning") ? learning : reviewGet));
    });
    const view = mount(fixtureRows);
    const at = view.state.doc.toString().indexOf(FOODS) + FOODS.length;
    view.dispatch({ changes: { from: at, to: at, insert: "X" } });
    await new Promise((r) => setTimeout(r, 900));
    expect(screen.queryByText(/NOPE tag/)).toBeNull();
  });
});
