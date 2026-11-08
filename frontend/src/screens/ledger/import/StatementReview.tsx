import { useEffect, useRef, useState } from "react";
import { Callout } from "../../../components/Callout";
import { Panel } from "../../../components/Panel";
import { api } from "@/shared/api/http";
import { LedgerReviewEditor, type CandidateMeta, type LedgerReviewEditorHandle } from "./LedgerReviewEditor";
import { computeRemaining, serializeEntry, type ParsedEntry } from "./ledgerEntries";
import { WillLearn, type ReviewLearning } from "./WillLearn";

export type CategorySource = "rule" | "dictionary" | "user" | "taught_batch" | "fee_follows" | "none";

// Copied verbatim from Import.tsx (local type; no shared module exists yet — do NOT refactor it out).
export type Candidate = {
  id: string;
  date: string;
  raw_narration: string;
  suggested_category: string | null;
  amount: string;
  direction: string;
  dedup_status: string;
  matched_txn_id?: string | null;
  flags?: string[];
  needs_account: boolean;
  unresolved_marker_key?: string | null;
  source_account: string;
  // Investment-ingestion fields. The backend has always sent these (see
  // CandidateTxn in backend/app/models/domain/candidate.py); they were simply absent from
  // this type, so the investment review discarded them and rendered every
  // stock and mutual-fund row as a bare rupee amount.
  commodity?: string | null;
  units?: string | null;
  unit_cost?: string | null;
  unresolved_scheme?: string | null;
  // Soft review flags on the investment path (set before the batch is stored,
  // so review can show them before the write). See CandidateTxn. Rendered as
  // a batch-level callout below -- a Groww export can reach THIS screen via
  // the statement dropzone, and declaring the fields without rendering them
  // is the same thing as not having them.
  unexplained_charge?: boolean;
  unexplained_charge_amount?: string | null;
  // Categories and tags at review (contract 1.1). Optional: the investment and
  // preview paths build a Candidate without them.
  tags?: string[];
  category_source?: CategorySource;
  follows?: string | null;
  learn?: boolean;
};

export type StatementReviewProps = {
  batch: string | null;                  // null during template-preview; set after detect-match or save
  templateFingerprint: string | null;
  rows: Candidate[];                     // committed/detected candidates (drive callouts + resolve UI); [] during preview
  previewCandidates: Candidate[];        // live-parsed rows shown before a template is saved
  accounts: string[];
  showTemplateLink: boolean;             // true when a template wizard is in progress (renders the "Template" back-link)
  onBackToMap: () => void;               // navigate the shell back to the Map step
  onCommit: () => Promise<void>;         // POST /api/commit/{batch}; caller resets state
  commitBusy?: boolean;                  // a Commit is waiting or in flight: the button is disabled
  onRowsChange: (updated: Candidate[]) => void; // caller updates rows after marker/transfer resolution
};

/** GET /learning, or null when it can't be read or isn't the expected shape.
 * A null is shown as a notice, never as an absent list: a commit learns rules
 * the user must not be unable to see. */
async function fetchLearning(batch: string): Promise<ReviewLearning | null> {
  try {
    const l = await api.getJSON<ReviewLearning>(`/api/review/${batch}/learning`);
    return Array.isArray(l?.will_learn) ? l : null;
  } catch {
    return null;
  }
}

function followsRowOf(r: Candidate, rows: Candidate[]): number | undefined {
  if (!r.follows) return undefined;
  const at = rows.findIndex((x) => x.id === r.follows);
  return at >= 0 ? at + 1 : undefined;
}

const UNCATEGORIZED = "Expenses:Uncategorized";

// The flag the backend's Deduper puts on a row left NEW only because identical
// rows in the same import already matched every journal entry it could be.
// See dedup.py `_possible_dup_flag`.
const POSSIBLE_DUPLICATE_PREFIX = "possible duplicate:";

// eslint-disable-next-line react-refresh/only-export-components
export function possibleDuplicateFlag(r: { flags?: string[] }): string | undefined {
  return r.flags?.find((f) => f.startsWith(POSSIBLE_DUPLICATE_PREFIX));
}

/**
 * The POST /api/review body for one edited row, or null while its category
 * postings do not balance. An Uncategorized row sends no `splits` (so it never
 * counts as a user-set category), but its tags still save.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function buildReviewPatch(row: Candidate, entry: ParsedEntry): Record<string, unknown> | null {
  const categoryPostings = entry.postings.slice(0, -1);
  if (computeRemaining(row.amount, categoryPostings) !== 0) return null;
  const sourcePosting = entry.postings.at(-1);
  const patch: Record<string, unknown> = {
    date: entry.date.replace(/\//g, "-"),
    raw_narration: entry.narration,
    tags: entry.tags.map((t) => t.replace(/^#/, "")),
  };
  if (!categoryPostings.some((p) => p.account === UNCATEGORIZED)) {
    patch.splits = categoryPostings.map((p) => ({ account: p.account, amount: p.amount }));
  } else if (
    categoryPostings.length === 1 &&
    row.suggested_category &&
    row.suggested_category !== UNCATEGORIZED
  ) {
    // Q22: a row that HAD a category and whose WHOLE category is now
    // Uncategorized must say so, or the server keeps the old category (and
    // would learn it). A split that merely contains an Uncategorized leg is
    // not a reset and sends nothing about the category.
    patch.suggested_category = UNCATEGORIZED;
  }
  if (sourcePosting) patch.source_account = sourcePosting.account;
  return patch;
}

// eslint-disable-next-line react-refresh/only-export-components
export function toEditorState(rows: Candidate[]): { entries: ParsedEntry[]; candidates: CandidateMeta[] } {
  const entries: ParsedEntry[] = rows.map((r) => ({
    date: r.date.replace(/-/g, "/"),
    narration: r.raw_narration,
    ref: r.matched_txn_id ? `matched ${r.matched_txn_id}` : null,
    postings: [
      {
        account: r.suggested_category ?? "Expenses:Uncategorized",
        // Always carry the real amount — even when uncategorized — so the row
        // balances and the user never has to re-type the number. "Needs a
        // category" is flagged from the Uncategorized account, not an imbalance.
        amount: r.amount,
      },
      { account: r.source_account, amount: `-${r.amount}` },
    ],
    tags: (r.tags ?? []).map((t) => `#${t}`),
  }));
  const candidates: CandidateMeta[] = rows.map((r) => ({
    id: r.id,
    amount: r.amount,
    matchSource:
      r.dedup_status === "transfer_match_candidate" || r.dedup_status === "transfer_already_recorded"
        ? "transfer"
        : r.suggested_category
          ? "dictionary"
          : "unknown",
    matchDetail: r.flags?.[0],
    needsAccount: r.needs_account,
    rawSource: r.raw_narration,
    // Twin rows share a content-derived id; a link resolves to the FIRST row
    // with that id (twins are unsupported for linking).
    followsRow: followsRowOf(r, rows),
  }));
  return { entries, candidates };
}

export function StatementReview(props: StatementReviewProps) {
  const [liveEntries, setLiveEntries] = useState<ParsedEntry[] | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedRef = useRef<string[]>([]);
  const editorRef = useRef<LedgerReviewEditorHandle>(null);
  const [learning, setLearning] = useState<ReviewLearning | null>(null);
  const [tagProblem, setTagProblem] = useState<string | null>(null);
  const [learnError, setLearnError] = useState<string | null>(null);
  const [learningFailed, setLearningFailed] = useState(false);

  async function commitAnyway(candidateId: string) {
    if (!props.batch) return;
    await api.postJSON(`/api/review/${props.batch}/${candidateId}`, {
      dedup_status: "transfer_match_candidate",
    });
    props.onRowsChange(
      props.rows.map((r) =>
        r.id === candidateId ? { ...r, dedup_status: "transfer_match_candidate", flags: [] } : r,
      ),
    );
  }

  async function resolveMarkerFor(candidate: Candidate, account: string, fingerprint: string) {
    if (!props.batch) return;
    await api.postJSON(`/api/templates/${fingerprint}/resolve-marker`, {
      batch_id: props.batch,
      marker_key: candidate.unresolved_marker_key ?? null,
      account,
    });
    props.onRowsChange(
      props.rows.map((r) =>
        r.unresolved_marker_key === candidate.unresolved_marker_key
          ? { ...r, needs_account: false, source_account: account }
          : r,
      ),
    );
  }

  function onEditorChange(entries: ParsedEntry[]) {
    // Mirror the live buffer immediately so the balance gutter and the
    // commit-disabled gate (anyUnbalanced below) read what's on screen,
    // not stale data from the last server round-trip.
    setLiveEntries(entries);
    // Debounce the actual network save: a statement can have dozens of rows,
    // and committing on every keystroke would hammer /api/review with mostly
    // mid-edit (necessarily unbalanced) states for every row, not just the
    // one being typed in.
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => saveEntries(entries), 400);
  }

  async function loadLearning(batch: string) {
    const l = await fetchLearning(batch);
    setLearning(l);
    setLearningFailed(l === null);
  }

  useEffect(() => {
    if (!props.batch) return; // the list renders only while a batch is set
    let live = true;
    fetchLearning(props.batch).then((l) => {
      if (!live) return;
      setLearning(l);
      setLearningFailed(l === null);
    });
    return () => {
      live = false;
    };
  }, [props.batch]);

  async function setLearn(candidateIds: string[], learn: boolean) {
    if (!props.batch) return;
    setLearnError(null);
    try {
      for (const id of candidateIds) {
        await api.postJSON(`/api/review/${props.batch}/${id}`, { learn });
      }
    } catch {
      // Part of the pattern may already be saved: show the server's real state.
      setLearnError("Could not save that choice. The list shows what is actually saved.");
      await loadLearning(props.batch);
      return;
    }
    await refreshFromServer();
  }

  /**
   * Re-read the batch after saves. A row whose category the app (not the user)
   * moved -- a fee row following its charge, or a spread from a taught
   * merchant -- is pushed into the editor so the buffer matches the server.
   */
  async function refreshFromServer() {
    if (!props.batch) return;
    const batch = props.batch;
    try {
      const fresh = await api.getJSON<{ candidates: Candidate[] }>(`/api/review/${batch}`);
      const next = fresh.candidates;
      // A different row count means the server's list no longer lines up with
      // the buffer: leave the rows alone (the list below still reloads).
      if (Array.isArray(next) && next.length === props.rows.length) {
        const nextEntries = toEditorState(next).entries;
        next.forEach((n, i) => {
          const old = props.rows[i];
          if (n.category_source !== "fee_follows" && n.category_source !== "taught_batch") return;
          if (n.suggested_category === old.suggested_category) return;
          if (editorRef.current?.setCategory(i, n.suggested_category ?? UNCATEGORIZED)) {
            lastSavedRef.current[i] = serializeEntry(nextEntries[i]);
          }
        });
        props.onRowsChange(next);
      }
    } catch {
      // A failed refresh leaves the buffer as the user typed it.
    }
    await loadLearning(batch);
  }

  async function saveEntries(entries: ParsedEntry[]) {
    if (!props.batch) return;
    // entries/rows must stay index-correlated (every entry is one candidate's
    // postings, in the same order) — if a structural edit (e.g. deleting the
    // blank line between two entries) changes the count, bail rather than
    // silently writing posting edits onto the wrong candidate.
    if (entries.length !== props.rows.length) return;
    const serialized = entries.map(serializeEntry);
    let anySaved = false;
    let tagProblem: string | null = null;
    for (let i = 0; i < entries.length; i++) {
      const row = props.rows[i];
      const entry = entries[i];
      if (!row || !entry) continue;
      // Only the row whose text actually changed needs a network round-trip —
      // re-sending every other untouched row on each keystroke just spams
      // /api/review (and 422s for any row still unbalanced).
      if (serialized[i] === lastSavedRef.current[i]) continue;
      // A row that doesn't locally balance is necessarily mid-edit (e.g. still
      // being typed into) — the backend would reject it with 422. Skip the
      // round-trip and DON'T record lastSavedRef, so the row saves on the next
      // change once it balances. An Uncategorized row balances (the amount sits
      // on that posting) and saves its tags, but no splits.
      const patch = buildReviewPatch(row, entry);
      if (!patch) continue;
      lastSavedRef.current[i] = serialized[i];
      try {
        await api.postJSON(`/api/review/${props.batch}/${row.id}`, patch);
        anySaved = true;
      } catch (e) {
        // Mostly an entry mid-edit and not yet balanced (422). Don't let one
        // row's failure stop the rest of the batch from saving. A bad tag is
        // different: say so, and let the row save again once it is fixed.
        const msg = (e as Error).message ?? "";
        if (msg.startsWith("422") && msg.includes('"E1072"')) {
          lastSavedRef.current[i] = "";
          try {
            tagProblem = (JSON.parse(msg.slice(msg.indexOf(" ") + 1)) as { description?: string }).description ?? null;
          } catch {
            tagProblem = null;
          }
          tagProblem ??= "One of the tags is not valid: use letters, digits, - _ / .";
        }
      }
    }
    setTagProblem(tagProblem);
    if (anySaved) await refreshFromServer();
  }

  // Before a template is saved the editor previews the live-parsed rows; after
  // save it shows the authoritative committed-batch rows (the editable surface).
  const sourceRows = props.batch ? props.rows : props.previewCandidates;
  const { entries: editorEntries, candidates: editorCandidates } = toEditorState(sourceRows);
  // Seed with what was loaded, so the first keystroke posts only the row it
  // changed (not every row of the statement, uncategorised ones included).
  if (props.batch && lastSavedRef.current.length === 0 && editorEntries.length > 0) {
    lastSavedRef.current = editorEntries.map(serializeEntry);
  }
  // CodeMirror seeds its doc once per mount, so remount it whenever the preview
  // content changes (the user isn't editing yet during preview — nothing to
  // lose). Once `batch` is set the key is stable, so editing/typeahead/saves
  // behave exactly as before.
  const previewSig = props.previewCandidates
    .map((c) => `${c.id}:${c.amount}:${c.suggested_category ?? ""}`)
    .join("|");
  const editorKey = props.batch ?? `preview:${previewSig}`;
  const currentEntries =
    liveEntries && liveEntries.length === props.rows.length ? liveEntries : editorEntries;
  const anyUnbalanced = props.rows.some((r, i) => {
    const entry = currentEntries[i];
    if (!entry) return false;
    return computeRemaining(r.amount, entry.postings.slice(0, -1)) !== 0;
  });
  // Counted, NOT a gate.
  //
  // This used to disable Commit. The backend has never refused an
  // uncategorized row -- import_service.commit() gates on needs_account and
  // unresolved_scheme only -- and a row with no category commits cleanly into
  // Expenses:Uncategorized with the journal still validating. But 96% of a
  // real bank statement arrives uncategorized, so the button meant ~250
  // hand-classifications before a single statement could land, and the
  // journal went six weeks stale.
  //
  // Net worth, cash flow and liabilities are correct the moment the rows are
  // in; only spend-by-category is affected, and it now degrades visibly via
  // the count below rather than by refusing the import.
  const uncategorizedCount = props.rows.filter((_r, i) => {
    const entry = currentEntries[i];
    if (!entry) return false;
    return entry.postings.slice(0, -1).some((p) => p.account === "Expenses:Uncategorized");
  }).length;
  const anyNeedsAccount = props.rows.some((r) => r.needs_account);
  // WHY this screen needs the warning at all: `/api/import/detect` is
  // header-driven and tab-agnostic, so a Groww MF export dropped on the
  // STATEMENT dropzone -- which advertises "Groww" in its own sub-text --
  // takes the same groww branch, gets the same flagged candidates, and lands
  // here rather than on InvestmentReview. The commit no longer refuses an
  // over-tolerance charge gap; it books the remainder to a named account. So
  // without this callout that is the one route by which money reaches
  // Expenses:Investment:Charges:Unexplained with no warning ever shown.
  // Batch-level, like the two callouts above, because this screen renders its
  // rows as journal text in a code editor and has no per-row slot.
  const unexplainedRows = sourceRows.filter((r) => r.unexplained_charge);
  // Rows identical to one already in the journal, beyond the number the
  // journal records. They commit as new (a second same-day charge is usually
  // real), so the doubt has to be said here, before the write.
  const possibleDuplicateRows = sourceRows.filter((r) => possibleDuplicateFlag(r));

  return (
    <>
      {props.showTemplateLink && (
        <div style={{ marginBottom: "0.9231rem" }}>
          <button type="button" className="link-button" onClick={props.onBackToMap}>
            Template
          </button>
        </div>
      )}
      {props.rows.some((r) => r.dedup_status === "transfer_match_candidate") && (
        <Callout kind="warn">
          Transfer match proposed — confirm before commit (UPI-Lite top-up / card payment).
        </Callout>
      )}
      {anyNeedsAccount && (
        <Callout kind="bad">Resolve the account for flagged rows before committing.</Callout>
      )}
      {unexplainedRows.length > 0 && (
        <Callout kind="warn">
          {unexplainedRows.length === 1 ? "One row's" : `${unexplainedRows.length} rows'`} cash
          doesn't match units × NAV, and no charge we know explains it. It will still be
          posted — to <b>Expenses:Investment:Charges:Unexplained</b>, so the money stays
          visible and nothing is dropped. Worth checking{" "}
          {unexplainedRows.length === 1 ? "it" : "them"} against your broker statement before
          you commit.
          <ul>
            {unexplainedRows.map((r) => (
              <li key={r.id}>
                {r.date} · {r.raw_narration}
                {r.unexplained_charge_amount ? ` — ₹${r.unexplained_charge_amount}` : ""}
              </li>
            ))}
          </ul>
        </Callout>
      )}
      {possibleDuplicateRows.length > 0 && (
        <Callout kind="warn">
          {possibleDuplicateRows.length === 1 ? "One row matches" : `${possibleDuplicateRows.length} rows match`}{" "}
          a transaction already in the journal, but this file has more identical rows than the
          journal records. {possibleDuplicateRows.length === 1 ? "It" : "They"} will be
          imported as new. Check against your statement: a second identical charge on the same
          day is usually real, but if the statement printed one charge twice, committing would
          book it twice.
          <ul>
            {possibleDuplicateRows.map((r, i) => (
              <li key={`${r.id}-${i}`}>
                {r.date} · {r.raw_narration} · ₹{r.amount} — {possibleDuplicateFlag(r)}
              </li>
            ))}
          </ul>
        </Callout>
      )}
      {props.rows.map((r) =>
        r.needs_account || r.dedup_status === "transfer_already_recorded" ? (
          <div className="data-row" key={r.id}>
            {r.needs_account && (
              <div className="callout-inline">
                <span>account needed{r.unresolved_marker_key ? ` (marker: ${r.unresolved_marker_key})` : ""}</span>
                <select
                  defaultValue=""
                  onChange={(e) => {
                    if (e.target.value && props.templateFingerprint) {
                      resolveMarkerFor(r, e.target.value, props.templateFingerprint);
                    }
                  }}
                >
                  <option value="" disabled>
                    choose account
                  </option>
                  {props.accounts.map((a) => (
                    <option key={a} value={a}>{a}</option>
                  ))}
                </select>
              </div>
            )}
            {r.dedup_status === "transfer_already_recorded" && (
              <div className="callout-inline">
                <button type="button" onClick={() => commitAnyway(r.id)}>
                  Commit anyway
                </button>
              </div>
            )}
          </div>
        ) : null,
      )}
      {tagProblem && <Callout kind="bad">{tagProblem}</Callout>}
      <Panel
        title={`Review queue · ${sourceRows.length}`}
        right={
          <button type="button" className="link-button" onClick={() => editorRef.current?.openSearch()}>
            Find &amp; replace
          </button>
        }
      >
        <LedgerReviewEditor
          ref={editorRef}
          key={editorKey}
          entries={editorEntries}
          candidates={editorCandidates}
          knownAccounts={props.accounts}
          onChange={onEditorChange}
        />
      </Panel>
      {props.batch && learnError && <Callout kind="bad">{learnError}</Callout>}
      {props.batch && learningFailed && (
        <Callout kind="warn">Couldn't load what this commit will learn.</Callout>
      )}
      {props.batch && learning && (
        <WillLearn learning={learning} rows={props.rows} onSetLearn={setLearn} />
      )}
      {props.batch && (
        <div className="commit-bar">
          <button
            className="primary"
            disabled={anyUnbalanced || anyNeedsAccount || props.commitBusy}
            aria-busy={props.commitBusy || undefined}
            onClick={props.onCommit}
          >
            Commit
          </button>
          {uncategorizedCount > 0 && (
            <span className="note">
              {uncategorizedCount} row{uncategorizedCount > 1 ? "s" : ""} will land
              uncategorized. They commit normally — net worth and cash flow are unaffected —
              and stay listed under <b>Expense → Uncategorized</b> to classify whenever.
            </span>
          )}
        </div>
      )}
    </>
  );
}
