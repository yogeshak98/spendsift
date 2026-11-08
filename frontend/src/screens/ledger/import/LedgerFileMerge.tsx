import { useEffect, useRef, useState } from "react";
import { api } from "@/shared/api/http";
import { LedgerReviewEditor, type LedgerReviewEditorHandle } from "./LedgerReviewEditor";
import { toEditorState, type MergeTxn } from "./mergeEditorAdapter";

type MergeAccount = {
  raw: string;
  suggested: string;
  status: "known" | "new" | "needs_mapping";
};

type MergeCommodity = {
  raw: string;
  suggested: string;
  display_name: string | null;
  status: "new" | "needs_mapping";
};

type PreviewResponse = {
  batch_id: string;
  transactions: MergeTxn[];
  accounts: MergeAccount[];
  commodities: MergeCommodity[];
  unparsed: { line_no: number; text: string; reason: string }[];
  ignored_lines: number;
  ignored?: { line_no: number; text: string; reason: string }[];
  imbalances: { line_no: number; narration: string; residual: string }[];
  summary: {
    transactions: number;
    duplicates: number;
    new_accounts: number;
    new_commodities: number;
    unparsed: number;
    imbalances: number;
  };
};

type CommitResult = {
  committed: number;
  skipped_duplicates: number;
  opened_accounts: string[];
  declared_commodities: string[];
};

export function LedgerFileMerge({
  onActiveChange,
}: {
  // Fires true once a journal is loaded and under review, false when cleared —
  // lets the Import screen collapse the statement dropzone while reviewing.
  onActiveChange?: (active: boolean) => void;
} = {}) {
  type Tab = "txns" | "accounts" | "commodities" | "issues";
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<Tab>("txns");
  const [accountOverrides, setAccountOverrides] = useState<Record<string, string>>({});
  const [commodityOverrides, setCommodityOverrides] = useState<Record<string, string>>({});
  const [knownAccounts, setKnownAccounts] = useState<string[]>([]);
  const [commitResult, setCommitResult] = useState<CommitResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const editorRef = useRef<LedgerReviewEditorHandle>(null);

  // Signal review-active state up so the Import screen can hide its dropzone.
  useEffect(() => {
    onActiveChange?.(preview !== null);
  }, [preview, onActiveChange]);

  function seedFromPreview(res: PreviewResponse) {
    setPreview(res);
    const acctInit: Record<string, string> = {};
    for (const a of res.accounts) {
      if (a.status !== "needs_mapping") acctInit[a.raw] = a.suggested;
    }
    setAccountOverrides(acctInit);
    const commInit: Record<string, string> = {};
    for (const c of res.commodities) commInit[c.raw] = c.suggested;
    setCommodityOverrides(commInit);
  }

  // The server holds a preview until it is posted or discarded, and while it
  // maps to an account that account cannot be renamed (E1095). So a preview
  // the user replaces or abandons is discarded on the server too.
  async function discardBatch(batchId: string): Promise<void> {
    try {
      await api.del(`/api/import/ledger/${encodeURIComponent(batchId)}`);
    } catch (err) {
      // 404: the server no longer has it (restart, already posted) - gone either way.
      if (err instanceof Error && err.message.startsWith("404")) return;
      throw err;
    }
  }

  function discardQuietly(batchId: string) {
    discardBatch(batchId).catch(() => {
      /* best effort: the replaced preview only blocks renames until restart */
    });
  }

  function clearReview() {
    setPreview(null);
    setAccountOverrides({});
    setCommodityOverrides({});
  }

  async function onDiscard() {
    if (!preview) return;
    if (
      !window.confirm(
        "Discard this ledger import? Nothing is posted, and your account and commodity mappings are lost.",
      )
    )
      return;
    setError(null);
    try {
      await discardBatch(preview.batch_id);
      clearReview();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Discard failed");
    }
  }

  async function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setCommitResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await api.postForm<PreviewResponse>("/api/import/ledger/preview", form);
      if (preview && preview.batch_id !== res.batch_id) discardQuietly(preview.batch_id);
      seedFromPreview(res);
      setTab("txns");
      // Account autocomplete suggestions; non-fatal if it fails.
      try {
        const acc = await api.getJSON<{ accounts: string[] }>("/api/accounts");
        if (Array.isArray(acc.accounts))
          setKnownAccounts(acc.accounts.filter((a) => typeof a === "string"));
      } catch {
        /* autocomplete is a nicety; ignore */
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Preview failed");
    }
  }

  // Re-parse the edited ledger text: read the editor buffer, wrap it as a File,
  // and re-POST /preview so the server re-derives accounts/commodities/issues
  // (and a fresh batch_id). This is the single edit path — there is no separate
  // structured staging endpoint.
  async function onReparse() {
    if (!preview) return;
    const doc = editorRef.current?.getDoc() ?? "";
    setError(null);
    setSaving(true);
    try {
      const form = new FormData();
      form.append("file", new File([doc], "edited.ledger", { type: "text/plain" }));
      const res = await api.postForm<PreviewResponse>("/api/import/ledger/preview", form);
      if (res.batch_id !== preview.batch_id) discardQuietly(preview.batch_id);
      seedFromPreview(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Re-parse failed");
    } finally {
      setSaving(false);
    }
  }

  async function onCommit() {
    if (!preview) return;
    setError(null);
    const account_map: Record<string, string> = {};
    for (const a of preview.accounts) account_map[a.raw] = accountOverrides[a.raw] ?? a.suggested;
    const commodity_map: Record<string, string> = {};
    for (const c of preview.commodities) commodity_map[c.raw] = commodityOverrides[c.raw] ?? c.suggested;
    try {
      const res = await api.postJSON<CommitResult>("/api/import/ledger/commit", {
        batch_id: preview.batch_id,
        account_map,
        commodity_map,
        include_duplicates: false,
      });
      setCommitResult(res);
      setPreview(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Commit failed");
    }
  }

  const needsMapping = preview
    ? preview.accounts.some((a) => a.status === "needs_mapping" && !accountOverrides[a.raw]) ||
      preview.commodities.some((c) => c.status === "needs_mapping" && !commodityOverrides[c.raw]) ||
      preview.imbalances.length > 0
    : true;

  return (
    <div>
      <div className="file-pick">
        <input
          id="ledger-file-input"
          type="file"
          className="file-input"
          aria-label="ledger file"
          accept=".ledger,.journal,.hledger,.bean,.txt"
          onChange={onFileChange}
        />
        <label htmlFor="ledger-file-input">Choose a journal file</label>
        <span className="fp-hint">.ledger · .journal · .hledger · .bean</span>
      </div>

      {error && (
        <div className="callout bad" style={{ marginBottom: "0.7692rem" }}>
          {error}
        </div>
      )}

      {commitResult && (
        <div className="callout good" style={{ marginBottom: "0.7692rem" }}>
          Committed {commitResult.committed} transaction{commitResult.committed !== 1 ? "s" : ""}.
          {commitResult.skipped_duplicates > 0 &&
            ` Skipped ${commitResult.skipped_duplicates} duplicate(s).`}
          {commitResult.opened_accounts.length > 0 &&
            ` Opened: ${commitResult.opened_accounts.join(", ")}.`}
          {commitResult.declared_commodities.length > 0 &&
            ` Declared: ${commitResult.declared_commodities.join(", ")}.`}
        </div>
      )}

      {preview && (() => {
        const issueCount =
          preview.summary.duplicates +
          preview.imbalances.length +
          preview.unparsed.length +
          (preview.ignored_lines > 0 ? 1 : 0);
        const { entries: editorEntries, candidates: editorCandidates } = toEditorState(
          preview.transactions,
        );
        // A single logical transaction (e.g. an MF trade) can surface as two
        // rows sharing one `id` — one per target journal. Count distinct ids
        // so the badge reflects real-world transactions, not raw rows.
        const distinctTxnCount = new Set(preview.transactions.map((t) => t.id)).size;
        return (
          <>
            {/* Sections are tabs so a 400-row file doesn't become an endless
                scroll; the active section lives in a fixed-height window. */}
            <div className="lf-tabs" role="tablist">
              <button type="button" role="tab" className={tab === "txns" ? "on" : ""} onClick={() => setTab("txns")}>
                Transactions <span className="ct">{distinctTxnCount}</span>
              </button>
              <button type="button" role="tab" className={tab === "accounts" ? "on" : ""} onClick={() => setTab("accounts")}>
                Accounts <span className="ct">{preview.accounts.length}</span>
              </button>
              {preview.commodities.length > 0 && (
                <button type="button" role="tab" className={tab === "commodities" ? "on" : ""} onClick={() => setTab("commodities")}>
                  Commodities <span className="ct">{preview.commodities.length}</span>
                </button>
              )}
              <button
                type="button"
                role="tab"
                className={`${tab === "issues" ? "on" : ""}${issueCount > 0 ? " has-issues" : ""}`}
                onClick={() => setTab("issues")}
              >
                Issues <span className="ct">{issueCount}</span>
              </button>
            </div>

            <div className="lf-scroll">
              {tab === "txns" && (
                <div className="lf-editor">
                  {preview.transactions.length === 0 && (
                    <div className="note">No transactions — the file parsed no entries.</div>
                  )}
                  {preview.transactions.length > 0 && (
                    <>
                      <div className="lf-editor-toolbar">
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => editorRef.current?.openSearch()}
                        >
                          Find &amp; replace
                        </button>
                      </div>
                      <LedgerReviewEditor
                        ref={editorRef}
                        // Remount on a fresh batch so the editor re-seeds with the
                        // re-parsed text (the editor only reads its seed on mount).
                        key={preview.batch_id}
                        entries={editorEntries}
                        candidates={editorCandidates}
                        knownAccounts={knownAccounts}
                      />
                    </>
                  )}
                </div>
              )}

              {tab === "accounts" && (
                <table className="data-table" style={{ width: "100%", fontSize: "1rem" }}>
                  <thead>
                    <tr>
                      <th>Raw account</th>
                      <th>Target account</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.accounts.map((acct) => (
                      <tr key={acct.raw}>
                        <td style={{ fontFamily: "monospace" }}>{acct.raw}</td>
                        <td>
                          <input
                            aria-label={`map ${acct.raw}`}
                            type="text"
                            value={accountOverrides[acct.raw] ?? ""}
                            placeholder={acct.status === "needs_mapping" ? "enter target account" : acct.suggested}
                            onChange={(e) =>
                              setAccountOverrides((prev) => ({ ...prev, [acct.raw]: e.target.value }))
                            }
                            style={{ width: "100%" }}
                          />
                        </td>
                        <td>
                          <span className={`chip ${acct.status === "needs_mapping" ? "bad" : acct.status === "new" ? "warn" : ""}`}>
                            {acct.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {tab === "commodities" && preview.commodities.length > 0 && (
                <table className="data-table" style={{ width: "100%", fontSize: "1rem" }}>
                  <thead>
                    <tr>
                      <th>Raw</th>
                      <th>Symbol</th>
                      <th>Display name</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.commodities.map((comm) => (
                      <tr key={comm.raw}>
                        <td style={{ fontFamily: "monospace" }}>{comm.raw}</td>
                        <td>
                          {comm.status === "needs_mapping" ? (
                            <input
                              aria-label={`symbol ${comm.raw}`}
                              type="text"
                              value={commodityOverrides[comm.raw] ?? ""}
                              placeholder="enter symbol"
                              onChange={(e) =>
                                setCommodityOverrides((prev) => ({ ...prev, [comm.raw]: e.target.value }))
                              }
                            />
                          ) : (
                            <span>{commodityOverrides[comm.raw] ?? comm.suggested}</span>
                          )}
                        </td>
                        <td>{comm.display_name ?? "—"}</td>
                        <td>
                          <span className={`chip ${comm.status === "needs_mapping" ? "bad" : "warn"}`}>
                            {comm.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {tab === "issues" && (
                <div style={{ padding: 2 }}>
                  {issueCount === 0 && <div className="note">No issues — everything parsed cleanly.</div>}

                  {preview.summary.duplicates > 0 && (
                    <div className="callout warn" style={{ marginBottom: "0.7692rem" }}>
                      {preview.summary.duplicates} duplicate transaction(s) found — they will be skipped on commit.
                    </div>
                  )}

                  {preview.imbalances.length > 0 && (
                    <div className="callout bad" style={{ marginBottom: "0.7692rem" }}>
                      <strong>{preview.imbalances.length} transaction(s) don't balance.</strong>
                      <ul>
                        {preview.imbalances.map((im) => (
                          <li key={im.line_no}>line {im.line_no}: {im.narration} — off by {im.residual}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {preview.ignored_lines > 0 && (
                    <div style={{ marginBottom: "0.7692rem" }}>
                      <div style={{ fontSize: "1rem", color: "var(--text3)", marginBottom: "0.4615rem" }}>
                        {preview.ignored_lines} line(s) ignored — comments and unsupported
                        directives. They don't create transactions, so they're skipped (not errors).
                      </div>
                      {preview.ignored && preview.ignored.length > 0 && (
                        <table className="data-table" style={{ width: "100%", fontSize: "1rem" }}>
                          <thead>
                            <tr>
                              <th>Line</th>
                              <th>Text</th>
                              <th>Reason</th>
                            </tr>
                          </thead>
                          <tbody>
                            {preview.ignored.map((g, i) => (
                              <tr key={i}>
                                <td>{g.line_no}</td>
                                <td style={{ fontFamily: "monospace" }}>{g.text}</td>
                                <td>{g.reason}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  )}

                  {preview.unparsed.length > 0 && (
                    <table className="data-table" style={{ width: "100%", fontSize: "1rem" }}>
                      <thead>
                        <tr>
                          <th>Line</th>
                          <th>Text</th>
                          <th>Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.unparsed.map((u, i) => (
                          <tr key={i}>
                            <td>{u.line_no}</td>
                            <td style={{ fontFamily: "monospace" }}>{u.text}</td>
                            <td>{u.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>

            <div className="lf-actions">
              <button type="button" disabled={saving} onClick={onReparse}>
                {saving ? "Re-parsing…" : "Re-parse edits"}
              </button>
              <button type="button" disabled={saving} onClick={onDiscard}>
                Discard
              </button>
              <button
                type="button"
                className="primary"
                disabled={needsMapping}
                onClick={onCommit}
              >
                Post
              </button>
            </div>
          </>
        );
      })()}
    </div>
  );
}
