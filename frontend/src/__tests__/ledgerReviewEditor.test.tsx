import { createRef } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { test, expect, vi } from "vitest";
import { EditorView } from "@codemirror/view";
import {
  LedgerReviewEditor,
  type CandidateMeta as CandidateMetaT,
  type LedgerReviewEditorHandle,
} from "@/screens/ledger/import/LedgerReviewEditor";
import type { ParsedEntry } from "@/screens/ledger/import/ledgerEntries";

test("renders the serialized text of the given entries", () => {
  const entries: ParsedEntry[] = [
    {
      date: "2026/06/12",
      narration: "SWIGGY ORDER #4521",
      ref: null,
      postings: [
        { account: "Expenses:Food", amount: "420.00" },
        { account: "Assets:Bank:HDFC", amount: "-420.00" },
      ],
      tags: [],
    },
  ];
  render(<LedgerReviewEditor entries={entries} onChange={vi.fn()} />);
  expect(screen.getByText(/SWIGGY ORDER #4521/)).toBeInTheDocument();
});

test("shows a reason annotation for an unbalanced entry, not a badge stack", () => {
  const entries: ParsedEntry[] = [
    {
      date: "2026/06/12",
      narration: "SWIGGY ORDER",
      ref: null,
      postings: [
        { account: "Expenses:Food", amount: "300.00" },
        { account: "Assets:Bank:HDFC", amount: "-420.00" },
      ],
      tags: [],
    },
  ];
  const candidates = [
    { id: "c1", amount: "420.00", matchSource: "dictionary" as const, needsAccount: false, rawSource: "" },
  ];
  render(<LedgerReviewEditor entries={entries} candidates={candidates} onChange={vi.fn()} />);
  expect(screen.getByText(/needs a category — 120\.00 open/)).toBeInTheDocument();
});

test("shows no reason annotation for a balanced entry", () => {
  const entries: ParsedEntry[] = [
    {
      date: "2026/06/12",
      narration: "SWIGGY ORDER",
      ref: null,
      postings: [
        { account: "Expenses:Food", amount: "200.00" },
        { account: "Assets:Bank:HDFC", amount: "-200.00" },
      ],
      tags: [],
    },
  ];
  const candidates = [
    { id: "c1", amount: "200.00", matchSource: "dictionary" as const, needsAccount: false, rawSource: "" },
  ];
  render(<LedgerReviewEditor entries={entries} candidates={candidates} onChange={vi.fn()} />);
  expect(screen.queryByText(/needs a category/)).not.toBeInTheDocument();
});

test("flags a balanced Uncategorized row as needing a category, without an 'open' amount", () => {
  const entries: ParsedEntry[] = [
    {
      date: "2026/06/12",
      narration: "NEFT 45000",
      ref: null,
      postings: [
        { account: "Expenses:Uncategorized", amount: "45000.00" },
        { account: "Assets:Bank:HDFC", amount: "-45000.00" },
      ],
      tags: [],
    },
  ];
  const candidates = [
    { id: "c1", amount: "45000.00", matchSource: "unknown" as const, needsAccount: false, rawSource: "" },
  ];
  render(<LedgerReviewEditor entries={entries} candidates={candidates} onChange={vi.fn()} />);
  // Flagged via the account, balanced via the amount — so "needs a category"
  // shows but the "X open" imbalance hint does not.
  expect(screen.getByText(/needs a category/)).toBeInTheDocument();
  expect(screen.queryByText(/open/)).not.toBeInTheDocument();
});

test("clears the needs-a-category flag once the Uncategorized account is changed", () => {
  const entries: ParsedEntry[] = [
    {
      date: "2026/06/12",
      narration: "NEFT 45000",
      ref: null,
      postings: [
        { account: "Expenses:Rent", amount: "45000.00" },
        { account: "Assets:Bank:HDFC", amount: "-45000.00" },
      ],
      tags: [],
    },
  ];
  const candidates = [
    { id: "c1", amount: "45000.00", matchSource: "unknown" as const, needsAccount: false, rawSource: "" },
  ];
  render(<LedgerReviewEditor entries={entries} candidates={candidates} onChange={vi.fn()} />);
  expect(screen.queryByText(/needs a category/)).not.toBeInTheDocument();
});

test("canonicalizes account casing when a ':' is typed", () => {
  const entries: ParsedEntry[] = [
    {
      date: "2026/06/12",
      narration: "SWIGGY ORDER",
      ref: null,
      postings: [{ account: "expenses", amount: "420.00" }],
      tags: [],
    },
  ];
  const candidates = [
    { id: "c1", amount: "420.00", matchSource: "unknown" as const, needsAccount: false, rawSource: "" },
  ];
  const onChange = vi.fn();
  const { container } = render(
    <LedgerReviewEditor
      entries={entries}
      candidates={candidates}
      knownAccounts={["Expenses:Food"]}
      onChange={onChange}
    />,
  );
  const editable = container.querySelector(".cm-content") as HTMLElement;
  editable.focus();
  // jsdom does not implement document.execCommand, so a real keystroke
  // cannot be simulated reliably. Drive the captured EditorView directly
  // with a transaction that types ':' at the end of the account text,
  // and assert on the onChange callback's argument instead of DOM text.
  const view = EditorView.findFromDOM(editable);
  expect(view).not.toBeNull();
  const docText = view!.state.doc.toString();
  const pos = docText.indexOf("expenses") + "expenses".length;
  view!.dispatch({ changes: { from: pos, to: pos, insert: ":" }, selection: { anchor: pos + 1 } });
  const lastCall = onChange.mock.calls.at(-1)?.[0] as ParsedEntry[] | undefined;
  // The trailing ":" stays in the buffer — the user is mid-way through typing
  // the next account segment, only the segment just committed gets re-cased.
  expect(lastCall?.[0]?.postings?.[0]?.account).toBe("Expenses:");
});

test("bulk-applies a tag to all selected entries via the checkbox gutter", () => {
  const entries: ParsedEntry[] = [
    {
      date: "2026/06/12",
      narration: "A",
      ref: null,
      postings: [
        { account: "Expenses:Unknown", amount: "100.00" },
        { account: "Assets:Bank:HDFC", amount: "-100.00" },
      ],
      tags: [],
    },
    {
      date: "2026/06/12",
      narration: "B",
      ref: null,
      postings: [
        { account: "Expenses:Unknown", amount: "200.00" },
        { account: "Assets:Bank:HDFC", amount: "-200.00" },
      ],
      tags: [],
    },
  ];
  const candidates = [
    { id: "c1", amount: "100.00", matchSource: "unknown" as const, needsAccount: false, rawSource: "" },
    { id: "c2", amount: "200.00", matchSource: "unknown" as const, needsAccount: false, rawSource: "" },
  ];
  const onChange = vi.fn();
  const { container } = render(
    <LedgerReviewEditor entries={entries} candidates={candidates} knownAccounts={[]} onChange={onChange} />,
  );
  // CodeMirror marks its whole .cm-gutters wrapper aria-hidden, so these
  // checkboxes aren't reachable via getAllByRole — a real, separate
  // accessibility gap in the gutter, not something this test should mask.
  const checkboxes = container.querySelectorAll('input[type="checkbox"]');
  fireEvent.click(checkboxes[0]);
  fireEvent.click(checkboxes[1]);
  fireEvent.change(screen.getByLabelText("Tag to apply"), {
    target: { value: "#fy2025-26" },
  });
  fireEvent.click(screen.getByText("Apply to 2"));
  const lastCall = onChange.mock.calls.at(-1)?.[0] as ParsedEntry[] | undefined;
  expect(lastCall?.[0]?.tags).toEqual(["#fy2025-26"]);
  expect(lastCall?.[1]?.tags).toEqual(["#fy2025-26"]);
});

test("raw mode seeds initialDoc verbatim and reads it back via getDoc()", () => {
  const ref = createRef<LedgerReviewEditorHandle>();
  const doc = '2024-03-01 * "Coffee"\n    Expenses:Food   100 INR\n    Assets:Cash\n';
  const { container } = render(
    <LedgerReviewEditor ref={ref} initialDoc={doc} knownAccounts={[]} />,
  );
  expect(container.querySelector(".cm-content")?.textContent).toContain("Coffee");
  expect(ref.current?.getDoc()).toBe(doc);
});

test("raw mode renders no review bulk-tag chrome", () => {
  const ref = createRef<LedgerReviewEditorHandle>();
  const { container } = render(
    <LedgerReviewEditor ref={ref} initialDoc={"; empty\n"} />,
  );
  expect(container.querySelector(".ledger-bulk-toolbar")).toBeNull();
});

test("readOnly prop: editor content is still visible", () => {
  const doc = '2026/06/30 * "Coffee"\n    Expenses:Food   20 INR\n    Assets:Bank:HDFC   -20 INR\n';
  const { container } = render(
    <LedgerReviewEditor initialDoc={doc} knownAccounts={[]} readOnly />,
  );
  // Content is rendered and visible even in read-only mode
  expect(container.querySelector(".cm-content")?.textContent).toContain("Coffee");
});

test("readOnly omitted: existing editable behavior is unchanged (regression guard)", () => {
  const entries: ParsedEntry[] = [
    {
      date: "2026/06/30",
      narration: "Lunch",
      ref: null,
      postings: [
        { account: "Expenses:Food", amount: "100.00" },
        { account: "Assets:Bank:HDFC", amount: "-100.00" },
      ],
      tags: [],
    },
  ];
  const onChange = vi.fn();
  const { container } = render(
    <LedgerReviewEditor entries={entries} onChange={onChange} />,
  );
  // cm-content should NOT have contenteditable="false" when readOnly is not set
  const content = container.querySelector(".cm-content");
  expect(content?.getAttribute("contenteditable")).not.toBe("false");
});

function twoEntries(): { entries: ParsedEntry[]; candidates: CandidateMetaT[] } {
  const entries: ParsedEntry[] = [
    {
      date: "2026/06/12", narration: "CHARGE", ref: null, tags: [],
      postings: [
        { account: "Expenses:Food", amount: "100.00" },
        { account: "Assets:Bank:HDFC", amount: "-100.00" },
      ],
    },
    {
      date: "2026/06/12", narration: "FEE ON CHARGE", ref: null, tags: [],
      postings: [
        { account: "Expenses:Food", amount: "18.00" },
        { account: "Assets:Bank:HDFC", amount: "-18.00" },
      ],
    },
  ];
  const candidates: CandidateMetaT[] = [
    { id: "c1", amount: "100.00", matchSource: "dictionary", needsAccount: false, rawSource: "" },
    { id: "c2", amount: "18.00", matchSource: "dictionary", needsAccount: false, rawSource: "", followsRow: 1 },
  ];
  return { entries, candidates };
}

test("setCategory replaces the single category account and reports a split entry as false", () => {
  const ref = createRef<LedgerReviewEditorHandle>();
  const { entries, candidates } = twoEntries();
  entries[1].postings = [
    { account: "Expenses:Food", amount: "10.00" },
    { account: "Expenses:Travel", amount: "8.00" },
    { account: "Assets:Bank:HDFC", amount: "-18.00" },
  ];
  render(<LedgerReviewEditor ref={ref} entries={entries} candidates={candidates} knownAccounts={[]} onChange={vi.fn()} />);
  expect(ref.current!.setCategory(0, "Expenses:Subscriptions")).toBe(true);
  const doc = ref.current!.getDoc();
  expect(doc).toContain("Expenses:Subscriptions  100.00 INR");
  expect(doc.split("\n\n")[0]).not.toContain("Expenses:Food");
  // the split entry is untouched
  expect(ref.current!.setCategory(1, "Expenses:Subscriptions")).toBe(false);
  expect(doc.split("\n\n")[1]).toContain("Expenses:Travel");
  expect(ref.current!.getDoc()).toBe(doc);
});

test("bulk tag treats the input as one normalised tag", () => {
  const { entries, candidates } = twoEntries();
  const onChange = vi.fn();
  const { container } = render(
    <LedgerReviewEditor entries={entries} candidates={candidates} knownAccounts={[]} onChange={onChange} />,
  );
  fireEvent.click(container.querySelectorAll('input[type="checkbox"]')[0]);
  fireEvent.change(screen.getByLabelText("Tag to apply"), { target: { value: "Madikeri Oct-2026" } });
  fireEvent.click(screen.getByText("Apply to 1"));
  const last = onChange.mock.calls.at(-1)?.[0] as ParsedEntry[];
  expect(last[0].tags).toEqual(["#madikeri-oct-2026"]);
});

test("an invalid bulk tag applies nothing and says why", () => {
  const { entries, candidates } = twoEntries();
  const onChange = vi.fn();
  const { container } = render(
    <LedgerReviewEditor entries={entries} candidates={candidates} knownAccounts={[]} onChange={onChange} />,
  );
  fireEvent.click(container.querySelectorAll('input[type="checkbox"]')[0]);
  fireEvent.change(screen.getByLabelText("Tag to apply"), { target: { value: "café" } });
  fireEvent.click(screen.getByText("Apply to 1"));
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByText("not a valid tag: use letters, digits, - _ / .")).toBeInTheDocument();
});

test("a fee row shows which row it follows", () => {
  const { entries, candidates } = twoEntries();
  render(<LedgerReviewEditor entries={entries} candidates={candidates} knownAccounts={[]} onChange={vi.fn()} />);
  expect(screen.getByText(/follows row 1/)).toBeInTheDocument();
});

test("an uncategorised fee row says what it needs and which row it follows", () => {
  const { entries, candidates } = twoEntries();
  entries[1].postings[0].account = "Expenses:Uncategorized";
  render(<LedgerReviewEditor entries={entries} candidates={candidates} knownAccounts={[]} onChange={vi.fn()} />);
  expect(screen.getByText(/needs a category — follows row 1/)).toBeInTheDocument();
});
