import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { RangeSetBuilder } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  GutterMarker,
  ViewPlugin,
  type ViewUpdate,
  WidgetType,
  gutter,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { autocompletion } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { openSearchPanel, search, searchKeymap } from "@codemirror/search";
import {
  buildAcronymSet,
  canonicalizeAccount,
  computeRemaining,
  normalizeTag,
  parseBuffer,
  serializeEntries,
  type ParsedEntry,
} from "./ledgerEntries";
import { computeEntryBlocks } from "./ledgerBlocks";
import { buildAccountCompletionSource } from "./ledgerAutocomplete";
import { ledgerHighlightPlugin } from "./ledgerHighlight";

// CodeMirror's base theme draws the caret in plain black, which is invisible
// against this editor's dark background — without this, the editor has no
// visible insertion point at all, even though it's fully editable.
const ledgerEditorTheme = EditorView.theme({
  "&": { color: "var(--text)", backgroundColor: "transparent" },
  ".cm-content": { caretColor: "var(--amber)" },
  ".cm-cursor, .cm-cursor-primary": { borderLeftColor: "var(--amber)", borderLeftWidth: "2px" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": {
    backgroundColor: "rgba(251,191,36,0.25)",
  },
  // The account typeahead popup. CodeMirror injects a light default tooltip
  // theme at runtime, so these must live in the editor theme (a plain
  // stylesheet loses the specificity race) to match the dark app surface.
  ".cm-tooltip.cm-tooltip-autocomplete": {
    backgroundColor: "var(--surface2)",
    border: "1px solid var(--border)",
    borderRadius: "0.4615rem",
    boxShadow: "0 0.4615rem 1.5385rem rgba(0,0,0,0.35)",
    overflow: "hidden",
  },
  ".cm-tooltip-autocomplete > ul": {
    fontFamily: "inherit",
    maxHeight: "16em",
  },
  ".cm-tooltip-autocomplete > ul > li": {
    color: "var(--text)",
    padding: "3px 0.7692rem",
  },
  ".cm-tooltip-autocomplete > ul > li[aria-selected]": {
    backgroundColor: "rgba(251,191,36,0.18)",
    color: "var(--text)",
  },
  ".cm-completionLabel": { color: "var(--text)" },
  ".cm-completionMatchedText": {
    color: "var(--amber)",
    textDecoration: "none",
    fontWeight: "600",
  },
});

export type MatchSource = "learned" | "dictionary" | "unknown" | "transfer";

export type CandidateMeta = {
  id: string;
  amount: string;
  matchSource: MatchSource;
  matchDetail?: string;
  needsAccount: boolean;
  rawSource: string;
  /** 1-based number of the row this fee row follows (its charge). */
  followsRow?: number;
};

export type LedgerReviewEditorProps = {
  entries?: ParsedEntry[];
  candidates?: CandidateMeta[];
  knownAccounts?: string[];
  onChange?: (entries: ParsedEntry[]) => void;
  // Raw mode: when set, the editor seeds this exact text and runs WITHOUT the
  // review chrome (gutter / candidates / bulk-tag). Used by the Raw ledger editor.
  initialDoc?: string;
  // When true, the editor is non-editable (display only). Default: false.
  // Omitting this prop leaves existing editable behavior completely unchanged.
  readOnly?: boolean;
};

export type LedgerReviewEditorHandle = {
  openSearch: () => void;
  getDoc: () => string;
  /**
   * Replace the account on entry `index`'s single category line. Returns false
   * (and changes nothing) when the entry has no such line or has more than one
   * category posting, because a split has no single account to replace.
   */
  setCategory: (index: number, account: string) => boolean;
};

type RowStatus = "needs" | "transfer" | "ok";

// A row still parked in Expenses:Uncategorized needs a category even when it
// balances. We flag that from the account itself — NOT from an imbalance —
// so the real amount can sit on the posting (the user only changes the
// account to clear it, never re-types the amount).
const UNCATEGORIZED = "Expenses:Uncategorized";

function isUncategorized(entry: ParsedEntry | undefined): boolean {
  if (!entry) return false;
  // category postings = everything but the trailing source posting
  return entry.postings.slice(0, -1).some((p) => p.account === UNCATEGORIZED);
}

function statusFor(c: CandidateMeta, entry: ParsedEntry | undefined): RowStatus {
  if (c.matchSource === "transfer") return "transfer";
  if (c.needsAccount) return "needs";
  if (!entry) return "ok";
  const remaining = computeRemaining(c.amount, entry.postings.slice(0, -1));
  if (remaining !== 0) return "needs";
  if (isUncategorized(entry)) return "needs";
  return "ok";
}

function followsNote(c: CandidateMeta): string {
  return c.followsRow ? ` — follows row ${c.followsRow}` : "";
}

function reasonFor(c: CandidateMeta, entry: ParsedEntry | undefined, status: RowStatus): string {
  return reasonBase(c, entry, status) + followsNote(c);
}

function reasonBase(c: CandidateMeta, entry: ParsedEntry | undefined, status: RowStatus): string {
  if (status === "transfer") {
    return c.matchDetail ? `looks like a transfer — ${c.matchDetail}` : "looks like a transfer";
  }
  if (!entry) return "needs a category";
  const remaining = Math.abs(computeRemaining(c.amount, entry.postings.slice(0, -1)));
  // A genuine imbalance (mid-edit, or a split that doesn't sum) keeps the
  // "X open" hint; a balanced-but-uncategorized row just needs an account.
  if (remaining !== 0) return `needs a category — ${remaining.toFixed(2)} open`;
  return "needs a category";
}

// ── gutter: one checkbox (bulk-tag selection) + a colored status strip per
// transaction, both driven natively by CodeMirror so the gutter can never
// drift out of pixel alignment with the text the way a separate DOM column did.
class RowMarker extends GutterMarker {
  status: RowStatus;
  checkbox: { id: string; checked: boolean } | null;
  onToggle: (id: string) => void;
  constructor(
    status: RowStatus,
    checkbox: { id: string; checked: boolean } | null,
    onToggle: (id: string) => void,
  ) {
    super();
    this.status = status;
    this.checkbox = checkbox;
    this.onToggle = onToggle;
  }
  eq(other: RowMarker) {
    return (
      this.status === other.status &&
      JSON.stringify(this.checkbox) === JSON.stringify(other.checkbox)
    );
  }
  toDOM() {
    const wrap = document.createElement("div");
    wrap.className = `ledger-row-marker status-${this.status}`;
    if (this.checkbox) {
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "ledger-row-check";
      cb.checked = this.checkbox.checked;
      cb.setAttribute("aria-label", "select for bulk tagging");
      cb.addEventListener("mousedown", (e) => e.preventDefault());
      cb.addEventListener("change", () => this.onToggle(this.checkbox!.id));
      wrap.appendChild(cb);
    }
    return wrap;
  }
}

function ledgerRowGutter(opts: {
  getCandidates: () => CandidateMeta[];
  getSelected: () => Set<string>;
  onToggle: (id: string) => void;
}) {
  return gutter({
    class: "ledger-row-gutter",
    markers(view) {
      const builder = new RangeSetBuilder<GutterMarker>();
      const blocks = computeEntryBlocks(view.state.doc);
      const candidates = opts.getCandidates();
      const selected = opts.getSelected();
      const entries = parseBuffer(view.state.doc.toString());
      blocks.forEach((block, i) => {
        const c = candidates[i];
        if (!c) return;
        const status = statusFor(c, entries[i]);
        for (const lineNo of block.lines) {
          const line = view.state.doc.line(lineNo);
          const marker = new RowMarker(
            status,
            lineNo === block.headerLine ? { id: c.id, checked: selected.has(c.id) } : null,
            opts.onToggle,
          );
          builder.add(line.from, line.from, marker);
        }
      });
      return builder.finish();
    },
  });
}

// ── reason annotation: a dim, non-editable end-of-line note replacing the
// old stacked text badges. It's a decoration, not real document text, so
// copying/exporting the buffer never picks it up.
class ReasonWidget extends WidgetType {
  text: string;
  status: RowStatus;
  constructor(text: string, status: RowStatus) {
    super();
    this.text = text;
    this.status = status;
  }
  eq(other: ReasonWidget) {
    return this.text === other.text && this.status === other.status;
  }
  toDOM() {
    const span = document.createElement("span");
    span.className = `ledger-reason status-${this.status}`;
    span.textContent = `  ; ${this.text}`;
    return span;
  }
  ignoreEvent() {
    return true;
  }
}

function reasonPlugin(getCandidates: () => CandidateMeta[]) {
  function build(view: EditorView): DecorationSet {
    const builder = new RangeSetBuilder<Decoration>();
    const blocks = computeEntryBlocks(view.state.doc);
    const candidates = getCandidates();
    const entries = parseBuffer(view.state.doc.toString());
    blocks.forEach((block, i) => {
      const c = candidates[i];
      if (!c || block.categoryLine === null) return;
      const status = statusFor(c, entries[i]);
      // An otherwise-ok fee row still says which charge it follows.
      if (status === "ok" && !c.followsRow) return;
      const text =
        status === "ok" ? `follows row ${c.followsRow}` : reasonFor(c, entries[i], status);
      const line = view.state.doc.line(block.categoryLine);
      builder.add(line.to, line.to, Decoration.widget({ widget: new ReasonWidget(text, status), side: 1 }));
    });
    return builder.finish();
  }
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;
      constructor(view: EditorView) {
        this.decorations = build(view);
      }
      update(update: ViewUpdate) {
        // Rebuild on every update, not just doc changes: the candidates this
        // depends on can change from a server round-trip (see the
        // candidate-driven empty dispatch in LedgerReviewEditor) with no
        // text edit at all.
        this.decorations = build(update.view);
      }
    },
    { decorations: (v) => v.decorations },
  );
}

function canonicalizeOnCommit(view: EditorView, knownAccounts: string[]) {
  const acronyms = buildAcronymSet(knownAccounts);
  const line = view.state.doc.lineAt(view.state.selection.main.head);
  const match = /^(\s+)(\S[\S]*)$/.exec(line.text.slice(0, view.state.selection.main.head - line.from));
  if (!match) return;
  const [, lead, typedAccount] = match;
  const canonical = canonicalizeAccount(typedAccount, acronyms);
  if (canonical === typedAccount) return;
  const from = line.from + lead.length;
  const to = from + typedAccount.length;
  view.dispatch({ changes: { from, to, insert: canonical } });
}

function lastInsertedCommitChar(transactions: readonly import("@codemirror/state").Transaction[]): string | null {
  let lastChar: string | null = null;
  for (const tr of transactions) {
    if (!tr.docChanged) continue;
    tr.changes.iterChanges((_fromA, _toA, _fromB, _toB, inserted) => {
      const text = inserted.toString();
      if (text.length === 0) return;
      lastChar = text[text.length - 1];
    });
  }
  return lastChar;
}

export const LedgerReviewEditor = forwardRef<LedgerReviewEditorHandle, LedgerReviewEditorProps>(
  function LedgerReviewEditor(props, ref) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(props.onChange);
  onChangeRef.current = props.onChange;
  const candidatesRef = useRef(props.candidates || []);
  candidatesRef.current = props.candidates || [];
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const [tagValue, setTagValue] = useState("");
  const [tagError, setTagError] = useState<string | null>(null);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
    // The gutter re-render is triggered by the `selected` effect below, not
    // here: dispatching synchronously inside this handler would still read
    // the *old* selectedRef.current — React hasn't committed the state
    // update yet, so the checkbox would always render one click behind.
  };

  function applyTagToSelection() {
    const view = viewRef.current;
    if (!view || !tagValue.trim() || selectedRef.current.size === 0) return;
    // The input is ONE tag ("Madikeri Oct-2026" -> #madikeri-oct-2026), run
    // through the same rule the backend applies on save.
    const normalised = normalizeTag(tagValue);
    if (normalised === null) {
      setTagError("not a valid tag: use letters, digits, - _ / .");
      return;
    }
    setTagError(null);
    const tag = `#${normalised}`;
    const blocks = computeEntryBlocks(view.state.doc);
    const candidates = candidatesRef.current;
    const changes: { from: number; to: number; insert: string }[] = [];
    blocks.forEach((block, i) => {
      const c = candidates[i];
      if (!c || !selectedRef.current.has(c.id)) return;
      const headerLine = view.state.doc.line(block.headerLine);
      changes.push({ from: headerLine.to, to: headerLine.to, insert: `\n    ${tag}` });
    });
    if (changes.length === 0) return;
    view.dispatch({ changes });
    setTagValue("");
    setSelected(new Set());
  }

  useEffect(() => {
    if (!hostRef.current) return;
    const knownAccounts = props.knownAccounts || [];
    const raw = props.initialDoc !== undefined;
    const common = [
      history(),
      // top: true anchors the find/replace panel above the buffer instead
      // of CodeMirror's default of appending it below — it's hidden until
      // Cmd+F/the "Find & replace" button opens it.
      search({ top: true }),
      // autocompletion() installs its own completion keymap (Enter/Tab to
      // accept, Esc to dismiss) at the correct internal precedence by default,
      // so it must not be added to the keymap below — doing so would let
      // defaultKeymap's Enter (newline) preempt accepting a completion.
      autocompletion({ override: [buildAccountCompletionSource(knownAccounts)] }),
      keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap]),
      lineNumbers(),
      ledgerEditorTheme,
      ledgerHighlightPlugin,
      // readOnly: additive guard — makes the pane genuinely non-editable when
      // the prop is set. Both facets are needed: editable controls pointer/key
      // events while readOnly blocks programmatic dispatches that change the doc.
      ...(props.readOnly ? [EditorView.editable.of(false), EditorState.readOnly.of(true)] : []),
    ];
    // Raw mode omits the review chrome (gutter, reason annotations) and the
    // entries-parsing updateListener: a raw buffer is read back as text, not
    // re-derived into ParsedEntry[].
    const reviewOnly = raw
      ? []
      : [
          ledgerRowGutter({
            getCandidates: () => candidatesRef.current,
            getSelected: () => selectedRef.current,
            onToggle: toggle,
          }),
          reasonPlugin(() => candidatesRef.current),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) {
              const commitChar = lastInsertedCommitChar(update.transactions);
              if (commitChar === ":" || commitChar === "\n") {
                canonicalizeOnCommit(update.view, knownAccounts);
              }
              onChangeRef.current?.(parseBuffer(update.view.state.doc.toString()));
            }
          }),
        ];
    const state = EditorState.create({
      doc: raw ? props.initialDoc : serializeEntries(props.entries ?? []),
      extensions: [...common, ...reviewOnly],
    });
    const view = new EditorView({ state, parent: hostRef.current });
    viewRef.current = view;
    return () => view.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The gutter accent and reason annotations are derived from `candidates`,
  // not from the document text — they only repaint on a real doc change
  // (see reasonPlugin/ledgerRowGutter's `update`/`markers`), so a
  // server-driven candidate change with no text edit (e.g. clearing a
  // transfer flag after "Commit anyway") would otherwise leave stale
  // markers on screen until the next keystroke. An empty dispatch forces
  // both to recompute without touching the document.
  useEffect(() => {
    viewRef.current?.dispatch({});
  }, [props.candidates]);

  // selectedRef.current is updated above (in the render body) before this
  // effect runs, so by the time the gutter rebuilds, it reads the
  // just-committed selection rather than the previous one.
  useEffect(() => {
    viewRef.current?.dispatch({});
  }, [selected]);

  useImperativeHandle(ref, () => ({
    openSearch: () => {
      if (viewRef.current) openSearchPanel(viewRef.current);
    },
    getDoc: () => viewRef.current?.state.doc.toString() ?? "",
    setCategory: (index, account) => {
      const view = viewRef.current;
      if (!view) return false;
      const entry = parseBuffer(view.state.doc.toString())[index];
      if (!entry || entry.postings.length - 1 !== 1) return false;
      const block = computeEntryBlocks(view.state.doc)[index];
      if (!block || block.categoryLine === null) return false;
      const line = view.state.doc.line(block.categoryLine);
      const m = /^(\s+)(.*?)(?=\s{2,}|\t|$)/.exec(line.text);
      if (!m) return false;
      const from = line.from + m[1].length;
      view.dispatch({ changes: { from, to: from + m[2].length, insert: account } });
      return true;
    },
  }));

  return (
    <div className="ledger-review-wrap">
      {selected.size > 0 && (
        <div className="ledger-bulk-toolbar">
          <span className="sel-count">{selected.size} selected</span>
          <span>Apply tag</span>
          <input
            aria-label="Tag to apply"
            value={tagValue}
            onChange={(e) => setTagValue(e.target.value)}
            placeholder="#fy2025-26"
            onKeyDown={(e) => e.key === "Enter" && applyTagToSelection()}
          />
          <button type="button" className="primary" onClick={applyTagToSelection}>
            Apply to {selected.size}
          </button>
          {tagError && <span className="note" role="alert">{tagError}</span>}
        </div>
      )}
      <div className="ledger-review-rows">
        <div className="ledger-review-code" ref={hostRef} />
      </div>
    </div>
  );
  },
);
