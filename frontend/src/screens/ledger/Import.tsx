import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Callout } from "../../components/Callout";
import { Panel } from "../../components/Panel";
import { api } from "@/shared/api/http";
import { LedgerFileMerge } from "./import/LedgerFileMerge";
import { ImportShell, type ImportShellHandle, type ImportSource, type StepId } from "./import/ImportShell";
import { PdfUnlock } from "./import/PdfUnlock";
import { StatementSummary, type Statement } from "./import/StatementSummary";
import { parseCodedError } from "@/shared/api/codedError";
import { StatementReview, possibleDuplicateFlag, type Candidate } from "./import/StatementReview";
import type { CommitResult as CommitResponse } from "./import/WillLearn";
import { OpeningStep, type OpeningResolution } from "./import/OpeningStep";
import {
  CHECKLIST_PATH,
  OPENINGS_URL,
  mfOpeningFlag,
  type OpeningPromptResponse,
  type OpeningsStatusResponse,
} from "../../openings";
import { InvestmentReview, type WalletLeg, type CommitResult } from "./import/InvestmentReview";

type DetectResponse =
  | {
      matched: true;
      batch_id: string;
      fingerprint: string;
      candidates: Candidate[];
      statement?: Statement | null;
      warnings?: string[];
    }
  | { matched: false; kind: "pdf_locked"; filename: string }
  | { matched: false; kind: "pdf_unknown"; filename: string; message: string }
  | {
      matched: false;
      filename: string;
      fingerprint: string;
      raw_rows: string[][];
      suggested_header_row: number;
      suggested_roles: {
        date: string | null;
        narration: string | null;
        debit: string | null;
        credit: string | null;
        amount: string | null;
        sign: string | null;
      };
      suggested_date_format: string | null;
      delimiter: string;
    };

type WizardDetect = Exclude<DetectResponse, { matched: true } | { kind: string }>;

type AmountMode = "two-column" | "single";

export function LedgerImport() {
  const shellRef = useRef<ImportShellHandle>(null);

  const [batch, setBatch] = useState<string | null>(null);
  // True once a plain-text ledger is loaded and under review, so the hero
  // statement dropzone collapses and the review takes the space.
  const [ledgerActive, setLedgerActive] = useState(false);
  const [templateFingerprint, setTemplateFingerprint] = useState<string | null>(null);
  const [rows, setRows] = useState<Candidate[]>([]);
  const [accounts, setAccounts] = useState<string[]>([]);

  const [wizard, setWizard] = useState<WizardDetect | null>(null);
  // PDF statements: the locked file is held only until unlock succeeds or the
  // user cancels. The password itself lives inside PdfUnlock and nowhere else.
  const [lockedPdf, setLockedPdf] = useState<File | null>(null);
  const [pdfMessage, setPdfMessage] = useState<string | null>(null);
  const [detectError, setDetectError] = useState<string | null>(null);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [statement, setStatement] = useState<Statement | null>(null);
  const [statementWarnings, setStatementWarnings] = useState<string[]>([]);
  const [headerRowIndex, setHeaderRowIndex] = useState(0);
  const [dateColumn, setDateColumn] = useState("");
  const [dateFormat, setDateFormat] = useState("");
  // True when the detect response could not guess a date format at all, so
  // the field below starts empty and needs the user's input rather than a
  // format already known not to parse this file's dates.
  const [dateFormatUnknown, setDateFormatUnknown] = useState(false);
  const [narrationColumn, setNarrationColumn] = useState("");
  const [amountMode, setAmountMode] = useState<AmountMode>("two-column");
  const [debitColumn, setDebitColumn] = useState("");
  const [creditColumn, setCreditColumn] = useState("");
  const [amountColumn, setAmountColumn] = useState("");
  const [signColumn, setSignColumn] = useState("");
  const [creditIndicators, setCreditIndicators] = useState("Cr, CR, Credit");
  const [markerPattern, setMarkerPattern] = useState("");
  const [institution, setInstitution] = useState("");
  const [defaultAccount, setDefaultAccount] = useState("");
  // Rows parsed from the in-progress template config (via "Parse with this
  // template"). The editor renders these on the Review step; once a template is
  // saved, `batch`/`rows` take over.
  const [previewCandidates, setPreviewCandidates] = useState<Candidate[]>([]);
  const [parseError, setParseError] = useState<string | null>(null);

  // First-import opening-balance question for the staged batch. State is tagged
  // with the batch it was fetched for, so a new batch never sees a stale answer.
  // A failed fetch lets the commit go straight through.
  const [openingFetch, setOpeningFetch] = useState<{
    batch: string;
    prompt: OpeningPromptResponse | null;
    failed: boolean;
  } | null>(null);
  const [openingResolvedFor, setOpeningResolvedFor] = useState<{
    batch: string;
    map: Record<string, OpeningResolution>;
  } | null>(null);
  const openingNow = batch !== null && openingFetch?.batch === batch ? openingFetch : null;
  const openingPrompt = openingNow?.prompt ?? null;
  const openingCheckFailed = openingNow?.failed ?? false;
  const openingResolved = batch !== null && openingResolvedFor?.batch === batch ? openingResolvedFor.map : {};

  // Whenever a batch is staged (matched detect, PDF detect, saved template),
  // ask the server which of its accounts need an opening balance. Read-only.
  const commitGuard = useRef(false);
  const [commitBusy, setCommitBusy] = useState(false);
  // The in-flight fetch is kept so a fast Commit click can wait for it.
  const openingPending = useRef<{ batch: string; done: Promise<OpeningPromptResponse | null> } | null>(null);
  useEffect(() => {
    if (!batch) return;
    let live = true;
    const done = api
      .getJSON<OpeningPromptResponse>(`/api/review/${batch}/opening`)
      .then((res) => {
        const prompt = { ...res, prompts: res.prompts ?? [], missing: res.missing ?? [] };
        if (live) setOpeningFetch({ batch, prompt, failed: false });
        return prompt;
      })
      .catch(() => {
        if (live) setOpeningFetch({ batch, prompt: null, failed: true });
        return null;
      });
    openingPending.current = { batch, done };
    return () => {
      live = false;
    };
  }, [batch]);

  const openingPrompts = openingPrompt?.prompts ?? [];
  // Accounts the server lists in `missing` that the Opening step does not
  // already ask about (a prompted account is answered there).
  const missingNotPrompted = useMemo(() => {
    const prompted = new Set((openingPrompt?.prompts ?? []).map((p) => p.account));
    return (openingPrompt?.missing ?? []).filter((a) => !prompted.has(a));
  }, [openingPrompt]);
  // `missing` holds raw account paths: their display names come from the
  // checklist read, fetched only when there is something to name. A failed
  // fetch leaves the raw path.
  const [missingNames, setMissingNames] = useState<Record<string, string>>({});
  const missingKey = missingNotPrompted.join("\n");
  useEffect(() => {
    if (missingKey === "") return;
    let live = true;
    api
      .getJSON<OpeningsStatusResponse>(OPENINGS_URL)
      .then((res) => {
        if (!live || !Array.isArray(res?.accounts)) return;
        setMissingNames(Object.fromEntries(res.accounts.map((r) => [r.account, r.account_display])));
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [missingKey]);
  const missingDisplay = (account: string) => missingNames[account] ?? account;

  // Opening step's Continue: the same one-at-a-time guard as Review's Commit,
  // so a double click never sends a second commit for the same batch.
  async function continueFromOpening() {
    if (commitGuard.current) return;
    commitGuard.current = true;
    setCommitBusy(true);
    try {
      if (await commit()) shellRef.current?.setStep("review");
    } finally {
      commitGuard.current = false;
      setCommitBusy(false);
    }
  }

  function resetImportState() {
    setOpeningFetch(null);
    setOpeningResolvedFor(null);
    setBatch(null);
    setTemplateFingerprint(null);
    setRows([]);
    setWizard(null);
    setPreviewCandidates([]);
    setLockedPdf(null);
    setPdfMessage(null);
    setDetectError(null);
    setStatement(null);
    setStatementWarnings([]);
  }

  async function ensureAccounts() {
    if (accounts.length > 0) return accounts;
    const res = await api.getJSON<{ accounts: string[] }>("/api/accounts");
    setAccounts(res.accounts);
    return res.accounts;
  }

  async function onFile(file: File) {
    // Stale PDF/error state is cleared once the response arrives (not before
    // the request), so a successful CSV detect renders in one pass.
    try {
      await ensureAccounts();
      const form = new FormData();
      form.append("file", file);
      const res = await api.postForm<DetectResponse>("/api/import/detect", form);
      handleDetect(res, file);
    } catch (err) {
      setLockedPdf(null);
      setPdfMessage(null);
      const coded = parseCodedError(err);
      setDetectError(
        coded
          ? [coded.code ? `${coded.code}:` : "", coded.description, coded.resolution ?? ""]
              .filter(Boolean)
              .join(" ")
          : "Could not read this file.",
      );
    }
  }

  function handleDetect(res: DetectResponse, file: File) {
    setDetectError(null);
    setCommitError(null);
    if (res.matched) {
      setBatch(res.batch_id);
      setTemplateFingerprint(res.fingerprint);
      setRows(res.candidates);
      setStatement(res.statement ?? null);
      setStatementWarnings(res.warnings ?? []);
      setWizard(null);
      setLockedPdf(null);
      setPdfMessage(null);
      shellRef.current?.setStep("review");
      return;
    }
    if ("kind" in res) {
      if (res.kind === "pdf_locked") {
        setLockedPdf(file);
        setPdfMessage(null);
      } else {
        setLockedPdf(null);
        setPdfMessage(res.message);
      }
      return;
    }
    setLockedPdf(null);
    setPdfMessage(null);
    setStatement(null);
    setStatementWarnings([]);
    setWizard(res);
    setHeaderRowIndex(res.suggested_header_row);
    setDateColumn(res.suggested_roles.date ?? "");
    // The backend returns null when no format in its list parses this file's
    // own dates. An empty field the user must fill is a better starting point
    // than a format known not to work -- see _guess_date_format's docstring.
    setDateFormat(res.suggested_date_format ?? "");
    setDateFormatUnknown(res.suggested_date_format === null);
    setNarrationColumn(res.suggested_roles.narration ?? "");
    setDebitColumn(res.suggested_roles.debit ?? "");
    setCreditColumn(res.suggested_roles.credit ?? "");
    setAmountColumn(res.suggested_roles.amount ?? "");
    setSignColumn(res.suggested_roles.sign ?? "");
    // The backend now reports exactly one of the two shapes, and they are
    // mutually exclusive. Reading `sign` first matters: before this, the guess
    // was `debit || credit`, and a `Debit/Credit` column was returned for BOTH
    // of those, so a sign-column file opened in two-column mode with each money
    // leg pointed at a column of the word "Debit".
    setAmountMode(res.suggested_roles.sign ? "single" : "two-column");
    setPreviewCandidates([]);
    setParseError(null);
    shellRef.current?.setStep("map");
  }

  async function onInvestmentFile(file: File) {
    await ensureAccounts();
    const form = new FormData();
    form.append("file", file);
    const res = await api.postForm<DetectResponse>("/api/import/detect", form);
    if (res.matched) {
      setBatch(res.batch_id);
      setTemplateFingerprint(res.fingerprint);
      setRows(res.candidates);
      setWizard(null);
      shellRef.current?.setStep("inv-review");
    }
  }

  function headerCells(): string[] {
    if (!wizard) return [];
    return wizard.raw_rows[headerRowIndex] ?? [];
  }

  function markerMatches(): string[] {
    if (!wizard || !markerPattern) return [];
    try {
      const re = new RegExp(markerPattern);
      return wizard.raw_rows
        .map((r) => r.join(" "))
        .filter((line) => re.test(line));
    } catch {
      return [];
    }
  }

  function draftPayload(save: boolean) {
    if (!wizard) return null;
    if (amountMode === "single" && !signColumn.trim()) {
      setParseError(
        "Choose the column that marks a row as a credit — without it every row " +
          "would be imported as money going out.",
      );
      return null;
    }
    return {
      filename: wizard.filename,
      fingerprint: wizard.fingerprint,
      institution: institution || "Unknown",
      header_row_index: headerRowIndex,
      delimiter: wizard.delimiter,
      date_column: dateColumn,
      date_format: dateFormat,
      narration_column: narrationColumn,
      debit_column: amountMode === "two-column" ? debitColumn : null,
      credit_column: amountMode === "two-column" ? creditColumn : null,
      amount_column: amountMode === "single" ? amountColumn : null,
      sign_column: amountMode === "single" ? signColumn : null,
      credit_indicators: creditIndicators.split(",").map((s) => s.trim()).filter(Boolean),
      account_marker_pattern: markerPattern || null,
      default_account: defaultAccount || null,
      save,
    };
  }

  // Explicit parse: on the Map step the user clicks "Parse with this template"
  // to parse (save:false) and jump to the Review step to inspect the result.
  // They can toggle back, adjust the mapping, and parse again.
  async function parseWithTemplate() {
    const payload = draftPayload(false);
    if (!payload) return;
    setParseError(null);
    try {
      const res = await api.postJSON<{ saved: boolean; candidates: Candidate[] }>(
        "/api/templates",
        payload,
      );
      setPreviewCandidates(res.candidates);
      shellRef.current?.setStep("review");
    } catch {
      setParseError(
        "Couldn't parse with this template — check the column mapping and date format.",
      );
    }
  }

  async function saveTemplate() {
    const payload = draftPayload(true);
    if (!payload) return;
    const res = await api.postJSON<{
      saved: boolean;
      batch_id: string;
      fingerprint: string;
      candidates: Candidate[];
    }>("/api/templates", payload);
    setBatch(res.batch_id);
    setTemplateFingerprint(res.fingerprint);
    setRows(res.candidates);
    setWizard(null);
    setPreviewCandidates([]);
  }

  // Review's Commit: a first import asks for the opening balance before writing.
  async function commitFromReview() {
    // One Commit at a time: further clicks while waiting or committing do nothing.
    if (commitGuard.current) return;
    commitGuard.current = true;
    setCommitBusy(true);
    try {
      await routeCommit();
    } finally {
      commitGuard.current = false;
      setCommitBusy(false);
    }
  }

  async function routeCommit() {
    // A Commit click that beats the prompt fetch waits for it, so a first
    // import is never committed past the question. A failed fetch commits (null).
    const pending = openingPending.current;
    const prompt =
      batch !== null && pending?.batch === batch && openingNow === null ? await pending.done : openingPrompt;
    const unresolved = (prompt?.prompts ?? []).some((p) => openingResolved[p.account] === undefined);
    if (unresolved) {
      shellRef.current?.setStep("opening");
      return;
    }
    await commit();
  }

  async function commit(): Promise<boolean> {
    if (!batch) return false;
    setCommitError(null);
    let res: CommitResponse;
    try {
      res = await api.postJSON<CommitResponse>(`/api/commit/${batch}`, {});
    } catch (err) {
      const coded = parseCodedError(err);
      const detail = coded
        ? [coded.description, coded.resolution ?? ""].filter(Boolean).join(" ")
        : "Could not commit this batch.";
      if (coded?.code === "E1073") {
        // The journal write succeeded; only the learned rules were lost. The
        // batch is gone server-side, so leave it here too: a retry cannot work.
        setCommitError(
          `The rows WERE committed -- do not retry the commit. ${detail}`,
        );
        setBatch(null);
        setRows([]);
      } else {
        setCommitError(detail);
      }
      return false;
    }
    // Say what still needs attention, rather than having refused the import
    // over it. The rows are in the journal either way.
    const pending = res.uncategorized ?? 0;
    // The write path's own count, recomputed against the rows it actually
    // committed -- so a row whose numbers were edited after the review
    // warning is still reported here. The money is in the journal under
    // Expenses:Investment:Charges:Unexplained; this is what makes "never
    // blocks" audible rather than silent.
    const unexplained = res.unexplained ?? 0;
    const lines = [`Committed ${res.committed} transactions`];
    if (pending > 0) {
      lines.push(
        `${pending} are uncategorized — find them under Expense → Uncategorized.`,
      );
    }
    if (unexplained > 0) {
      lines.push(
        `${unexplained} had cash we could not fully explain — the remainder is in ` +
          `Expenses:Investment:Charges:Unexplained.`,
      );
    }
    const learned = res.learned ?? [];
    if (learned.length > 0) {
      lines.push(`Learned ${learned.length} rule${learned.length === 1 ? "" : "s"}.`);
    }
    const opened = res.opened ?? [];
    if (opened.length > 0) lines.push(`Opened: ${opened.join(", ")}.`);
    alert(lines.join(" "));
    setBatch(null);
    setRows([]);
    return true;
  }

  async function investmentCommit(): Promise<CommitResult> {
    if (!batch) return { ok: false };
    try {
      await api.postJSON<{ committed: number }>(`/api/commit/${batch}`, {});
      setBatch(null);
      setRows([]);
      return { ok: true };
    } catch (e) {
      const msg = (e as Error).message;
      // api.postJSON throws `Error("${status} ${bodyText}")` — strip the leading status code
      const spaceIdx = msg.indexOf(" ");
      const bodyText = spaceIdx >= 0 ? msg.slice(spaceIdx + 1) : msg;
      try {
        const parsed = JSON.parse(bodyText) as {
          code?: string;
          description?: string;
          resolution?: string;
        };
        return {
          ok: false,
          code: parsed.code,
          description: parsed.description,
          // Every coded error carries a resolution — what the user should
          // actually do about it. Dropping it left them with a diagnosis and
          // no next step.
          resolution: parsed.resolution,
        };
      } catch {
        return { ok: false };
      }
    }
  }

  // Mirror an accepted resolution into local state. The server has already
  // cleared `unresolved_scheme`; without this the row would keep rendering its
  // hold, and the user would have no way to tell a successful resolution from
  // one that silently failed.
  function onSchemeResolved(candidateId: string, commodity: string, schemeName: string) {
    setRows((current) =>
      current.map((c) =>
        c.id === candidateId
          ? { ...c, commodity, unresolved_scheme: null, raw_narration: schemeName }
          : c,
      ),
    );
  }

  function computeLegs(candidates: Candidate[]): WalletLeg[] {
    return candidates
      .map((c, originalIndex) => ({ c, originalIndex }))
      .sort((a, b) => {
        if (a.c.date < b.c.date) return -1;
        if (a.c.date > b.c.date) return 1;
        return a.originalIndex - b.originalIndex;
      })
      .map(({ c }) => ({
        kind: (c.direction === "debit" ? "buy" : "refund") as "buy" | "refund",
        candidateId: c.id,
        date: c.date,
        narration: c.raw_narration,
        amount: c.amount,
        // Pass the lot through instead of discarding it. These were hardcoded
        // to null, so a review of a stock or fund import showed only a rupee
        // total -- never the symbol, units or price it was about to book.
        commodity: c.commodity ?? null,
        units: c.units ?? null,
        unitCost: c.unit_cost ?? null,
        unresolvedScheme: c.unresolved_scheme ?? null,
        // Carried through for the same reason as unresolved_scheme: the review
        // row is the last place this can be said before the journal is
        // written, and this one no longer blocks the commit.
        unexplainedCharge: c.unexplained_charge ?? false,
        unexplainedChargeAmount: c.unexplained_charge_amount ?? null,
        possibleDuplicate: possibleDuplicateFlag(c) ?? null,
        openingLots: mfOpeningFlag(c) !== null,
      }));
  }

  function renderStep(source: ImportSource, step: StepId) {
    if (source === "statement") {
      if (step === "upload") {
        return (
          <div className="upload-step">
            {/* Primary path: a bank/card statement export. Whole card is a label
                so a click anywhere — or a file drop — opens the picker. Hidden
                once a plain-text ledger is under review, so it doesn't crowd the
                review out at the top of the screen. */}
            {!ledgerActive && (
            <label
              className="dropzone"
              onDragOver={(e) => {
                e.preventDefault();
                e.currentTarget.classList.add("dragging");
              }}
              onDragLeave={(e) => e.currentTarget.classList.remove("dragging")}
              onDrop={(e) => {
                e.preventDefault();
                e.currentTarget.classList.remove("dragging");
                if (e.dataTransfer.files[0]) onFile(e.dataTransfer.files[0]);
              }}
            >
              <input
                type="file"
                className="file-input"
                aria-label="statement file"
                accept=".csv,.txt,.xls,.xlsx,.pdf"
                onChange={(e) => e.target.files && onFile(e.target.files[0])}
              />
              <div className="big" aria-hidden="true">↓</div>
              <div className="dz-main">Drop a bank or card statement, or browse</div>
              <div className="dz-sub">CSV exports from HDFC · ICICI · IDFC · Groww, and PDF card statements — we detect the format</div>
              <span className="dz-btn">Choose a file</span>
            </label>
            )}

            {detectError && <Callout kind="bad">{detectError}</Callout>}
            {pdfMessage && <Callout kind="info">{pdfMessage}</Callout>}
            {lockedPdf && (
              <PdfUnlock<DetectResponse>
                file={lockedPdf}
                onResult={(res) => handleDetect(res, lockedPdf)}
                onCancel={() => setLockedPdf(null)}
              />
            )}

            {/* Special path: a plain-text ledger. Quiet by default; expands for
                anyone who keeps one and goes looking. Forced open while a journal
                is under review so the review stays visible. */}
            <details className="ledger-import" open={ledgerActive || undefined}>
              <summary>
                <span className="li-title">Already keep a plain-text ledger?</span>
                <span className="li-sub">Import a ledger-cli / hledger journal</span>
              </summary>
              <div className="li-body">
                <LedgerFileMerge onActiveChange={setLedgerActive} />
              </div>
            </details>
          </div>
        );
      }
      if (step === "map") {
        return wizard ? (
          <Panel title="New statement format — create a template" glow>
            <div className="tmpl-form">
              {/* Source preview — pick the header row, see the raw lines. */}
              <div className="field tmpl-headerrow">
                <label htmlFor="header-row">Header row (0-indexed)</label>
                <input
                  id="header-row"
                  type="number"
                  value={headerRowIndex}
                  onChange={(e) => setHeaderRowIndex(Number(e.target.value))}
                />
              </div>
              <div className="tmpl-rawrows" role="group" aria-label="source rows">
                {wizard.raw_rows.slice(0, 40).map((row, i) => (
                  <div key={i} className={i === headerRowIndex ? "row hdr" : "row"}>
                    <span className="ln">{i}</span>
                    {row.join(" | ")}
                  </div>
                ))}
              </div>

              {/* Core column mapping. */}
              <div className="tmpl-grid">
                <div className="field">
                  <label htmlFor="institution">Institution</label>
                  <input id="institution" value={institution} onChange={(e) => setInstitution(e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="date-column">Date column</label>
                  <select id="date-column" value={dateColumn} onChange={(e) => setDateColumn(e.target.value)}>
                    {headerCells().map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="date-format">Date format</label>
                  <input id="date-format" value={dateFormat} onChange={(e) => setDateFormat(e.target.value)} />
                  {dateFormatUnknown && (
                    <span className="field-hint">
                      Could not guess the date format from this file — enter it yourself, e.g. %d/%m/%Y.
                    </span>
                  )}
                </div>
                <div className="field">
                  <label htmlFor="narration-column">Narration column</label>
                  <select
                    id="narration-column"
                    value={narrationColumn}
                    onChange={(e) => setNarrationColumn(e.target.value)}
                  >
                    {headerCells().map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Amount shape. */}
              <div className="field tmpl-amountmode">
                <span className="tmpl-grouplabel">Amount columns</span>
                <div className="tmpl-radios">
                  <label>
                    <input
                      type="radio"
                      name="amount-mode"
                      checked={amountMode === "two-column"}
                      onChange={() => setAmountMode("two-column")}
                    />
                    separate debit / credit
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="amount-mode"
                      checked={amountMode === "single"}
                      onChange={() => setAmountMode("single")}
                    />
                    single amount + sign
                  </label>
                </div>
              </div>

              <div className="tmpl-grid">
                {amountMode === "two-column" ? (
                  <>
                    <div className="field">
                      <label htmlFor="debit-column">Debit column</label>
                      <select id="debit-column" value={debitColumn} onChange={(e) => setDebitColumn(e.target.value)}>
                        {headerCells().map((c) => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor="credit-column">Credit column</label>
                      <select id="credit-column" value={creditColumn} onChange={(e) => setCreditColumn(e.target.value)}>
                        {headerCells().map((c) => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="field">
                      <label htmlFor="amount-column">Amount column</label>
                      <select id="amount-column" value={amountColumn} onChange={(e) => setAmountColumn(e.target.value)}>
                        {headerCells().map((c) => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor="sign-column">Sign column</label>
                      <select id="sign-column" value={signColumn} onChange={(e) => setSignColumn(e.target.value)}>
                        <option value="">— choose —</option>
                        {headerCells().map((c) => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor="credit-indicators">Credit indicators</label>
                      <input
                        id="credit-indicators"
                        value={creditIndicators}
                        onChange={(e) => setCreditIndicators(e.target.value)}
                      />
                      <span className="field-hint">comma-separated, e.g. Cr, CR, Credit</span>
                    </div>
                  </>
                )}
              </div>

              {/* Account routing. */}
              <div className="tmpl-grid">
                <div className="field">
                  <label htmlFor="marker-pattern">Account marker</label>
                  <input
                    id="marker-pattern"
                    value={markerPattern}
                    onChange={(e) => setMarkerPattern(e.target.value)}
                  />
                  <span className="field-hint">
                    {markerPattern
                      ? `matches: ${markerMatches().length === 0 ? "none" : markerMatches().join(" / ")}`
                      : "optional regex with one capture group"}
                  </span>
                </div>
                <div className="field">
                  <label htmlFor="default-account">Default account</label>
                  {/* Free-text + datalist: open accounts are suggestions, but a
                      new institution's account (not yet in the journal) can be
                      typed directly — importing a brand-new bank shouldn't need
                      the account to pre-exist. */}
                  <input
                    id="default-account"
                    list="default-account-options"
                    value={defaultAccount}
                    placeholder="— none —"
                    onChange={(e) => setDefaultAccount(e.target.value)}
                  />
                  <datalist id="default-account-options">
                    {accounts.map((a) => (
                      <option key={a} value={a} />
                    ))}
                  </datalist>
                  <span className="field-hint">
                    used when no marker resolves one — type any account or pick one
                  </span>
                </div>
              </div>
            </div>

            {parseError && <Callout kind="bad">{parseError}</Callout>}

            <div className="import-template-actions">
              <button type="button" onClick={parseWithTemplate}>
                Parse with this template
              </button>
              <button type="button" className="primary" onClick={saveTemplate}>
                Save template &amp; Continue
              </button>
            </div>
          </Panel>
        ) : null;
      }
      if (step === "review") {
        return (
          <>
            {statement && <StatementSummary statement={statement} warnings={statementWarnings} />}
            {commitError && <Callout kind="bad">{commitError}</Callout>}
            {openingCheckFailed && (
              <Callout kind="warn">
                Could not check for an opening balance; you can add it later in Settings · Opening balances
              </Callout>
            )}
            {missingNotPrompted.length > 0 && (
              <Callout kind="info">
                {missingNotPrompted.length === 1
                  ? `${missingDisplay(missingNotPrompted[0])} has activity not covered by an opening balance. `
                  : `${missingNotPrompted.length} accounts have activity not covered by an opening balance. `}
                <Link to={CHECKLIST_PATH}>Review them in Settings · Opening balances</Link>
              </Callout>
            )}
            <StatementReview
            batch={batch}
            templateFingerprint={templateFingerprint}
            rows={rows}
            previewCandidates={previewCandidates}
            accounts={accounts}
            showTemplateLink={wizard !== null}
            onBackToMap={() => shellRef.current?.setStep("map")}
            onCommit={commitFromReview}
            commitBusy={commitBusy}
            onRowsChange={setRows}
            />
          </>
        );
      }
      if (step === "opening") {
        if (!batch || !openingPrompt || openingPrompts.length === 0) {
          // After a commit from this step `batch` is null: keep showing why it
          // failed (E1073: rows WERE committed), else say there is nothing to ask.
          return commitError ? (
            <Callout kind="bad">{commitError}</Callout>
          ) : (
            <div>Nothing to ask: no opening balance is needed for this import.</div>
          );
        }
        return (
          <>
            {commitError && <Callout kind="bad">{commitError}</Callout>}
            <OpeningStep
              batchId={batch}
              prompt={openingPrompt}
              resolutions={openingResolved}
              onResolve={(account, how) =>
                setOpeningResolvedFor((cur) => ({
                  batch,
                  map: { ...(cur?.batch === batch ? cur.map : {}), [account]: how },
                }))
              }
              onContinue={() => void continueFromOpening()}
              continueBusy={commitBusy}
            />
          </>
        );
      }
      if (step === "commit") {
        return (
          <>
            {statement && <StatementSummary statement={statement} warnings={statementWarnings} />}
            {commitError && <Callout kind="bad">{commitError}</Callout>}
            <StatementReview
            batch={batch}
            templateFingerprint={templateFingerprint}
            rows={rows}
            previewCandidates={previewCandidates}
            accounts={accounts}
            showTemplateLink={wizard !== null}
            onBackToMap={() => shellRef.current?.setStep("map")}
            onCommit={commitFromReview}
            commitBusy={commitBusy}
            onRowsChange={setRows}
            />
          </>
        );
      }
    }

    if (source === "investment") {
      if (step === "inv-upload") {
        return (
          <div className="dropzone">
            <input
              type="file"
              aria-label="investment file"
              onChange={(e) => e.target.files && onInvestmentFile(e.target.files[0])}
            />
          </div>
        );
      }
      const invReviewEl = batch ? (
        <InvestmentReview
          batchId={batch}
          legs={computeLegs(rows)}
          onCommit={investmentCommit}
          onResolved={onSchemeResolved}
        />
      ) : (
        <div>Upload a Groww statement first.</div>
      );
      if (step === "inv-review") return invReviewEl;
      if (step === "inv-commit") return invReviewEl;
    }

    return null;
  }

  return <ImportShell
      ref={shellRef}
      renderStep={renderStep}
      onSourceChange={resetImportState}
      showOpening={openingPrompts.length > 0}
    />;
}
