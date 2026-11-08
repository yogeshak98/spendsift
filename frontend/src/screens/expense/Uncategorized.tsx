import { useEffect, useState, type JSX, type SyntheticEvent } from "react";
import { DataTable, type Column } from "../../components/DataTable";
import { cell } from "../../components/cell";
import { inrExact } from "@/shared/format/money";
import { useReport, triggerReload } from "@/shared/api/useReport";
import { api } from "@/shared/api/http";
import "./uncategorized.css";

type Row = {
  merchant: string;
  count: number;
  total: string;
  first: string;
  last: string;
  sample: string;
};

type Resp = {
  transactions: number;
  merchants: number;
  total: string;
  rows: Row[];
};

type ClassifyResp = {
  rules_added: number;
  postings_updated: number;
};

type ExportResp = {
  text: string;
  // number (as a string key) -> normalized merchant key. The server assigns
  // the numbering; this object is posted back verbatim with the pasted
  // answer rather than reconstructed, so the mapping can never drift.
  index: Record<string, string>;
  merchants: number;
};

type RejectLine = { line: string; reason: string };

type ImportResp = {
  rules_added: number;
  postings_updated: number;
  // Can legitimately be LESS than rules_added: a merchant whose backlog rows
  // carry no txn-id gets its rule saved but no posting rewritten -- success,
  // not an error, same as the per-row path's postings_updated: 0 case.
  merchants_applied: number;
  // Non-empty pasted lines that were neither applied nor rejected -- ordinary
  // chat prose around the answer block. Context, never a warning.
  ignored: number;
  // One entry per unusable answer line. The request still succeeded.
  rejected: RejectLine[];
};

// The one target the backend refuses (E1049): it is a seeded account, so it
// would otherwise be offered and typeable, and choosing it moves nothing while
// pinning the merchant to the backlog forever.
const BACKLOG_ACCOUNT = "Expenses:Uncategorized";

// A single <datalist> shared by every row: the whole screen offers the same
// categories, and one list means typing a brand-new one is the same gesture as
// picking an existing one.
const ACCOUNT_LIST_ID = "uncat-expense-accounts";

// Mirrors the server's own rule (E1048/E1049) so a typo is caught before a
// round trip -- the server stays the authority and its error is still
// rendered; this only saves the trip.
function targetProblem(account: string): string | null {
  if (!account.startsWith("Expenses:") || account === "Expenses:") {
    return "Pick or type an Expenses:… category — classifying only moves spend between expense categories.";
  }
  if (account === BACKLOG_ACCOUNT) {
    return "That is the backlog itself. Pick the category this merchant really belongs to.";
  }
  return null;
}

// Above this count, classifying rewrites enough journal history that a
// one-click accident is worth guarding against; below it, a confirm on every
// row would recreate the per-row toll this screen exists to remove.
const CONFIRM_THRESHOLD = 20;

// api.postJSON throws `new Error(\`${status} ${bodyText}\`)` -- for a coded
// error the body is the FULL error JSON stringified onto the message. A
// table cell showing that verbatim is "not swallowed" but not readable by
// the one person using this app. Same approach as
// settings/Accounts.tsx:doClose -- extract the JSON blob and prefer its
// human-written fields over the raw text, falling back to the raw string
// only when parsing fails entirely.
function describeError(e: unknown): string {
  const raw = String(e);
  try {
    const match = raw.match(/\{.*\}/s);
    if (!match) return raw;
    const parsed = JSON.parse(match[0]) as {
      description?: unknown;
      detail?: unknown;
      resolution?: unknown;
    };
    const base =
      typeof parsed.description === "string"
        ? parsed.description
        : typeof parsed.detail === "string"
          ? parsed.detail
          : null;
    if (!base) return raw;
    // resolution is the one part that tells the user what to actually do --
    // E1017 (INVALID_ACCOUNT_NAME) carries one, and it belongs alongside the
    // description rather than being dropped.
    return typeof parsed.resolution === "string" ? `${base} ${parsed.resolution}` : base;
  } catch {
    return raw;
  }
}

const COLUMNS: Column[] = [
  { key: "merchant", label: "Merchant" },
  { key: "sample", label: "Sample narration" },
  { key: "count", label: "Count", num: true },
  { key: "total", label: "Total", num: true },
  { key: "first", label: "First" },
  { key: "last", label: "Last" },
  { key: "classify", label: "Classify into", sortable: false },
];

// One row per normalized merchant with no category -- these postings already
// landed in Expenses:Uncategorized and are committed to the journal, so net
// worth and cash flow are unaffected; only spend-by-category is wrong until a
// merchant is answered here. One decision per merchant rewrites every past
// posting under it AND saves a learned rule for future imports, which is why
// one datalist-backed text box + one button is the whole interaction -- the
// point of this screen is clearing 250 rows in a handful of clicks, not one at
// a time. The box accepts an existing category OR a brand-new name (the
// backend opens it during the rewrite), so no row forces a detour to
// Settings → Accounts.
export function ExpenseUncategorized(): JSX.Element {
  const { data, error } = useReport<Resp>("/api/reports/uncategorized");
  const [accounts, setAccounts] = useState<string[]>([]);
  const [selected, setSelected] = useState<Record<string, string>>({});
  // Keyed by merchant, not a single value -- classifying two rows in quick
  // succession must leave each row's own disabled/"Classifying…" state
  // independent of whichever request started (or finished) most recently.
  const [inFlight, setInFlight] = useState<Record<string, boolean>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  // Per-row outcome of the last classify -- the response body is the only
  // place that says whether anything actually moved, and a row that did not
  // move stays on screen and WILL be clicked again unless it says so.
  const [rowNotes, setRowNotes] = useState<Record<string, string>>({});
  // An unreadable account list disables every button on this screen; saying so
  // is the difference between a broken screen and an explained one.
  const [accountsError, setAccountsError] = useState<string | null>(null);

  // The bulk-classify panel's own state. The export is a full journal parse
  // (~241 merchants), so it must not be paid for just because the screen
  // mounted -- only once the panel is actually opened. "idle" guards against
  // firing a second fetch on a fast close/reopen before the first resolves.
  const [exportStatus, setExportStatus] = useState<"idle" | "loading" | "ready" | "error">(
    "idle",
  );
  const [exportData, setExportData] = useState<ExportResp | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  // Whether the panel is currently open -- a successful per-row classify
  // needs to know whether the numbered list the user is LOOKING AT right now
  // has gone stale (open -> refetch it) or simply needs invalidating for next
  // time (closed -> back to idle).
  const [panelOpen, setPanelOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState<ImportResp | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  // "idle": never attempted or deliberately reset; "copied"/"failed" are
  // transient read-outs of the LAST copy click, not of the export itself.
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  async function loadAccounts() {
    try {
      const res = await api.getJSON<{ accounts: string[] }>("/api/accounts");
      setAccounts(
        res.accounts
          .filter((a) => a.startsWith("Expenses:") && a !== BACKLOG_ACCOUNT)
          .sort(),
      );
      setAccountsError(null);
    } catch (e) {
      setAccounts([]);
      setAccountsError(describeError(e));
    }
  }

  useEffect(() => {
    void loadAccounts();
  }, []);

  // Creating a category must be a visible act, not a side effect of a typo:
  // the old <select> made "Expenses:Fod" impossible, a free-text box does
  // not. Only meaningful once the list has actually loaded -- with no list,
  // every name would look new.
  function isNewCategory(account: string): boolean {
    return accounts.length > 0 && !accounts.includes(account);
  }

  // Everything on this screen that a journal write invalidates, in ONE place:
  // both the per-row classify and the bulk apply write the same two things
  // (postings and newly opened categories), so two call sites doing it
  // separately is how they drift -- the bulk apply shipped doing neither,
  // which left a stale numbered list and its pre-apply counts sitting above a
  // refreshed table, and a category created by a paste missing from the
  // <datalist> for every later per-row classify in the session.
  function invalidateAfterWrite() {
    // A newly typed or pasted category is opened by the backend during the
    // rewrite -- refetch so it is offered to the remaining rows.
    void loadAccounts();
    // The bulk panel's numbered list is a snapshot of the SAME backlog the
    // write just rewrote -- it is now stale (classified merchants should drop
    // out and the numbering may shift). If the user is looking at it right
    // now, refetch so what they see matches the journal; if it is closed,
    // just invalidate it so the next open fetches fresh instead of serving
    // the old snapshot.
    if (panelOpen) {
      void loadExport();
    } else {
      setExportStatus("idle");
      setExportData(null);
    }
  }

  async function classify(merchant: string, count: number) {
    const account = (selected[merchant] ?? "").trim();
    if (!account) return;
    const problem = targetProblem(account);
    if (problem) {
      setRowErrors((prev) => ({ ...prev, [merchant]: problem }));
      return;
    }
    if (count > CONFIRM_THRESHOLD) {
      const ok = window.confirm(
        `Classify ${count} ${merchant} transactions as ${account}?` +
          (isNewCategory(account)
            ? ` ${account} does not exist yet and will be created.`
            : "") +
          " This rewrites them in the journal.",
      );
      if (!ok) return;
    }
    // Functional form only -- two classifies started close together must
    // each set/clear their OWN key without clobbering the other's, which a
    // read-then-write of the current object (or a single scalar "busy
    // merchant") cannot guarantee.
    setInFlight((prev) => ({ ...prev, [merchant]: true }));
    setRowErrors((prev) => {
      if (!(merchant in prev)) return prev;
      const next = { ...prev };
      delete next[merchant];
      return next;
    });
    // The previous note -- typically the zero-match sentence, on exactly the
    // row that stays on screen -- must not sit there looking like this
    // attempt's result while this attempt is still in flight.
    setRowNotes((prev) => {
      if (!(merchant in prev)) return prev;
      const next = { ...prev };
      delete next[merchant];
      return next;
    });
    try {
      const res = await api.postJSON<ClassifyResp>(
        "/api/reports/uncategorized/classify",
        { merchant, account },
      );
      // `postings_updated: 0` is a success the user must NOT read as one:
      // rewriting targets `txn-id`, and a row authored by hand (or through
      // ledger/raw) has none, so it is counted in the backlog but cannot be
      // rewritten. Without this it reloads unchanged and gets re-clicked
      // forever.
      setRowNotes((prev) => ({
        ...prev,
        [merchant]:
          res.postings_updated === 0
            ? `Rule saved — future imports of ${merchant} will land in ${account}. No existing transaction was rewritten (these rows carry no import id), so the row stays. Clicking again will not change that.`
            : `Reclassified ${res.postings_updated} transaction${
                res.postings_updated === 1 ? "" : "s"
              } as ${account}.`,
      }));
      invalidateAfterWrite();
      // A write that rewrites journal history -- every mounted report (this
      // one included) must re-read, since reclassifying changes
      // spend-by-category everywhere, not just this screen.
      triggerReload();
    } catch (e) {
      // The row MUST stay and the failure MUST be visible on it -- never
      // silently swallowed, never left looking classified.
      setRowErrors((prev) => ({ ...prev, [merchant]: describeError(e) }));
      setRowNotes((prev) => {
        if (!(merchant in prev)) return prev;
        const next = { ...prev };
        delete next[merchant];
        return next;
      });
    } finally {
      setInFlight((prev) => {
        const next = { ...prev };
        delete next[merchant];
        return next;
      });
    }
  }

  async function loadExport() {
    setExportStatus("loading");
    try {
      const res = await api.getJSON<ExportResp>("/api/reports/uncategorized/export");
      setExportData(res);
      setExportStatus("ready");
    } catch (e) {
      setExportError(describeError(e));
      setExportStatus("error");
    }
  }

  function onPanelToggle(e: SyntheticEvent<HTMLDetailsElement>) {
    const open = e.currentTarget.open;
    setPanelOpen(open);
    if (open) {
      if (exportStatus === "idle") {
        void loadExport();
      }
    } else {
      // Closing invalidates the snapshot -- the backlog may have moved
      // (classified elsewhere, or by this very panel) while it sat open, so
      // the NEXT open must always refetch rather than serve stale numbering.
      setExportStatus("idle");
      setExportData(null);
      // ... and the last apply's read-out goes with it. It describes the
      // numbered list being discarded here: left standing, the reopened panel
      // shows a FRESH list beneath the PREVIOUS apply's summary, with reject
      // lines citing numbers that are no longer on screen.
      setApplyResult(null);
      setApplyError(null);
      setPasteText("");
    }
  }

  async function copyExportText() {
    if (!exportData) return;
    setCopyState("idle");
    try {
      // navigator.clipboard can be absent (older/non-secure contexts, some
      // test environments) -- treated the same as a rejected write below:
      // the textarea's own text stays selectable by hand either way.
      if (!navigator.clipboard) throw new Error("clipboard API unavailable");
      await navigator.clipboard.writeText(exportData.text);
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 1500);
    } catch {
      setCopyState("failed");
    }
  }

  // "Nothing was applied" must read as a result, not a failure -- the paste
  // was received and parsed fine; it just had no usable answers in it.
  function summarizeApply(res: ImportResp): string {
    if (res.merchants_applied === 0) {
      return res.rules_added > 0
        ? `Nothing was applied — no existing transaction was rewritten, but ${res.rules_added} rule${
            res.rules_added === 1 ? "" : "s"
          } saved for future imports.`
        : "Nothing was applied.";
    }
    return (
      `Classified ${res.merchants_applied} merchant${res.merchants_applied === 1 ? "" : "s"} — ` +
      `${res.postings_updated} transaction${res.postings_updated === 1 ? "" : "s"} rewritten, ` +
      `${res.rules_added} rule${res.rules_added === 1 ? "" : "s"} saved.`
    );
  }

  async function applyBulk() {
    if (!exportData || !pasteText.trim() || applying) return;
    setApplying(true);
    setApplyError(null);
    setApplyResult(null);
    try {
      const res = await api.postJSON<ImportResp>("/api/reports/uncategorized/import", {
        text: pasteText,
        index: exportData.index,
      });
      setApplyResult(res);
      // Anything at all changed -- postings OR rules -- invalidates this
      // screen's own two snapshots. `rules_added > 0` matters on its own: a
      // paste can save rules without moving a posting, and the saved RULE is
      // not idempotent the way the postings are. Leaving the pre-apply list
      // on screen invites a second round over the already-answered merchants,
      // where the model may answer one differently and overwrite its rule
      // while the rewritten history keeps the first account.
      if (res.postings_updated > 0 || res.rules_added > 0) {
        invalidateAfterWrite();
      }
      // Every other mounted report must re-read -- but only if something in
      // the journal actually moved; an all-ignored/all-rejected paste changes
      // nothing and must not spuriously re-fetch everything on screen.
      if (res.postings_updated > 0) {
        triggerReload();
        // Only clear the paste when it actually moved something -- a
        // zero-applied or all-rejected result leaves the paste in the box so
        // the user can fix and resubmit it rather than retyping from scratch.
        setPasteText("");
      }
    } catch (e) {
      setApplyError(describeError(e));
    } finally {
      setApplying(false);
    }
  }

  if (data === null && error === null) {
    return <div className="uncat-page">Loading…</div>;
  }

  if (error) {
    return (
      <div className="uncat-page">
        <div className="callout bad">
          Could not load the uncategorized backlog — {error}. The journal is unchanged.
        </div>
      </div>
    );
  }

  const resp = data as Resp;

  return (
    <div className="uncat-page">
      {resp.transactions === 0 ? (
        <div className="callout info">
          Nothing uncategorized — every transaction already has a category.
        </div>
      ) : (
        <>
          <div className="callout info">
            These rows are already committed to the journal and counted in net worth and cash
            flow — only spend-by-category is affected until each merchant below is classified.
          </div>
          {accountsError && (
            <div className="callout bad">
              The list of existing categories could not be loaded — {accountsError} You can
              still type a full category name (e.g. Expenses:Food) to classify a row.
            </div>
          )}
          {/* Collapsed by default -- the screen's primary job is the per-row
              path above, and the export is a full journal parse that must
              not be paid for unless this panel is actually opened. */}
          <details className="uncat-bulk" onToggle={onPanelToggle}>
            <summary>Classify every merchant at once</summary>
            <div className="uncat-bulk-body">
              <p className="uncat-bulk-help">
                Copy the numbered list below into an LLM, then paste its numbered answers
                back here and apply — one pass classifies every merchant it answered.
              </p>
              {exportStatus === "loading" && (
                <div className="uncat-bulk-loading">Loading…</div>
              )}
              {exportStatus === "error" && (
                <div className="callout bad">
                  Could not build the export — {exportError}
                </div>
              )}
              {exportStatus === "ready" && exportData && (
                <>
                  <div className="uncat-bulk-count">
                    {exportData.merchants} merchant{exportData.merchants === 1 ? "" : "s"}{" "}
                    to classify.
                  </div>
                  <div className="uncat-bulk-caption">
                    This list was captured when the panel was opened.
                  </div>
                  <textarea
                    className="uncat-bulk-textarea"
                    aria-label="Merchant export text"
                    readOnly
                    value={exportData.text}
                  />
                  <button type="button" className="btn sm" onClick={() => void copyExportText()}>
                    {copyState === "copied" ? "Copied" : "Copy"}
                  </button>
                  {copyState === "failed" && (
                    <div className="uncat-row-note">
                      Could not copy automatically — select the text above and copy it by
                      hand instead.
                    </div>
                  )}
                  <textarea
                    className="uncat-bulk-textarea"
                    aria-label="Pasted answers"
                    placeholder="Paste the model's numbered answers here…"
                    value={pasteText}
                    onChange={(e) => setPasteText(e.target.value)}
                  />
                  <button
                    type="button"
                    className="btn sm"
                    disabled={!pasteText.trim() || applying}
                    onClick={() => void applyBulk()}
                  >
                    {applying ? "Applying…" : "Apply"}
                  </button>
                </>
              )}
              {/* Sibling of the loading/error/ready branches above, not nested
                  inside the ready one -- invalidateAfterWrite() (called right
                  after a successful apply) drives exportStatus through
                  "loading" and possibly "error" on a transient refetch
                  failure, and either would unmount a read-out nested in the
                  ready fragment. The apply already landed in the journal by
                  the time this renders, so the summary and any rejected
                  lines must survive regardless of what the refetch does. */}
              {applyError && <div className="uncat-row-error">{applyError}</div>}
              {applyResult && (
                <div className="uncat-bulk-result">
                  <div className="uncat-row-note">{summarizeApply(applyResult)}</div>
                  {applyResult.ignored > 0 && (
                    <div className="uncat-bulk-ignored">
                      {applyResult.ignored} line
                      {applyResult.ignored === 1 ? "" : "s"} ignored.
                    </div>
                  )}
                  {applyResult.rejected.length > 0 && (
                    <ul className="uncat-bulk-rejects">
                      {applyResult.rejected.map((r, i) => (
                        <li key={i}>
                          <code>{r.line}</code> — {r.reason}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          </details>
          {/* Typing a category that does not exist yet is the point: the
              backend opens it during the rewrite, so the backlog never has to
              be abandoned for a trip to Settings → Accounts. */}
          <datalist id={ACCOUNT_LIST_ID}>
            {accounts.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
          <div className="uncat-summary">
            {resp.merchants} merchant{resp.merchants === 1 ? "" : "s"} ·{" "}
            {resp.transactions} transaction{resp.transactions === 1 ? "" : "s"} ·{" "}
            {inrExact(resp.total)} total
          </div>
          <DataTable
            columns={COLUMNS}
            rows={resp.rows.map((row) => ({
              merchant: row.merchant,
              sample: row.sample,
              count: row.count,
              total: cell(Number(row.total), inrExact(row.total)),
              first: row.first,
              last: row.last,
              classify: cell(
                null,
                <div className="uncat-classify">
                  <input
                    type="text"
                    list={ACCOUNT_LIST_ID}
                    aria-label={`Account for ${row.merchant}`}
                    placeholder="Expenses:…"
                    autoComplete="off"
                    spellCheck={false}
                    value={selected[row.merchant] ?? ""}
                    onChange={(e) =>
                      setSelected((prev) => ({ ...prev, [row.merchant]: e.target.value }))
                    }
                  />
                  <button
                    type="button"
                    className="btn sm"
                    disabled={
                      !(selected[row.merchant] ?? "").trim() || !!inFlight[row.merchant]
                    }
                    onClick={() => classify(row.merchant, row.count)}
                  >
                    {inFlight[row.merchant] ? "Classifying…" : "Classify"}
                  </button>
                  {rowErrors[row.merchant] && (
                    <div className="uncat-row-error">{rowErrors[row.merchant]}</div>
                  )}
                  {rowNotes[row.merchant] && (
                    <div className="uncat-row-note">{rowNotes[row.merchant]}</div>
                  )}
                  {(() => {
                    const typed = (selected[row.merchant] ?? "").trim();
                    if (!typed || targetProblem(typed) || !isNewCategory(typed)) {
                      return null;
                    }
                    return (
                      <div className="uncat-row-new">
                        New category — {typed} will be created.
                      </div>
                    );
                  })()}
                </div>,
              ),
            }))}
          />
        </>
      )}
    </div>
  );
}
