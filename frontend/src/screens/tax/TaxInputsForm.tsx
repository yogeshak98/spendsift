import { useCallback, useEffect, useState, type JSX } from "react";
import { api } from "@/shared/api/http";
import { triggerReload } from "@/shared/api/useReport";
import { parseCodedError } from "@/shared/api/codedError";
import { UnknownInputKeysNotice } from "./DeclaredNotices";
import { incomeFieldCopy } from "./incomeFieldCopy";
import { sectionLabel } from "./utils";
import type { TaxInputField, TaxInputsResponse } from "./types";
import "./tax.css";

/** The declared-inputs form: the Form 16 figures and Chapter VI-A amounts the
 * user declares, read and written through `/api/tax/inputs`.
 *
 * It drives itself off its OWN endpoint, never the tax workspace. Every tax
 * screen early-returns on a null workspace, and the workspace route is a coded
 * 422 for exactly the files this form exists to repair (a declared value that
 * is not a number, an inputs file that will not parse) -- so a form reading the
 * workspace, or rendered inside that guard, would disappear in the states it is
 * needed in. `GET /api/tax/inputs` answers 200 in all of them.
 */

/** `api.ts` has no PATCH helper and is outside this unit's reach, so the one
 * PATCH in the app is spelled here -- with api.ts's own error convention
 * (`Error("<status> <body>")`), because `parseCodedError` reads that shape. */
async function patchJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

type Block = "income" | "deductions";

/** A touched field, keyed by block and key. Only touched fields are in here:
 * that is how the form knows which keys may appear in the PATCH body at all. */
type Drafts = Record<string, string>;

const fieldId = (block: Block, key: string): string => `${block}:${key}`;

/** The server's current document, as a 409 attaches it. */
type ConflictDoc = {
  revision: string;
  document: {
    regime?: unknown;
    income?: Record<string, unknown>;
    deductions?: Record<string, unknown>;
  } | null;
};

/** A 409 carries `data.revision` and `data.document` so the form can show what
 * changed on disk WITHOUT discarding what the user typed. `parseCodedError`
 * deliberately exposes only the human strings, so the payload is read here. */
function parseConflict(raw: unknown): ConflictDoc | null {
  // `api.ts` throws `Error("<status> <body>")` and `String(error)` prefixes
  // "Error: " -- so the status is matched, not assumed to lead the string.
  const text = String(raw).replace(/^Error:\s*/, "");
  if (!text.startsWith("409")) return null;
  const match = text.match(/\{.*\}/s);
  if (!match) return null;
  try {
    const parsed = JSON.parse(match[0]) as { data?: unknown };
    const data = parsed.data;
    if (typeof data !== "object" || data === null) return null;
    const rec = data as { revision?: unknown; document?: unknown };
    if (typeof rec.revision !== "string") return null;
    return {
      revision: rec.revision,
      document:
        typeof rec.document === "object" && rec.document !== null
          ? (rec.document as ConflictDoc["document"])
          : null,
    };
  } catch {
    return null;
  }
}

function conflictValue(conflict: ConflictDoc, block: Block, key: string): string | null {
  const section = conflict.document?.[block];
  if (typeof section !== "object" || section === null) return null;
  const raw = (section as Record<string, unknown>)[key];
  return raw === undefined || raw === null ? null : String(raw);
}

/** What the backend reported for one key, or undefined when the key is absent
 * from the file -- which is NOT the same answer as a declared "0". */
function stored(fields: TaxInputField[], key: string): TaxInputField | undefined {
  return fields.find((f) => f.key === key);
}

/** The text an untouched input shows: the saved figure, or the file's own text
 * when the backend could not read it as an amount, or empty for a key that is
 * not declared at all. */
function storedText(field: TaxInputField | undefined): string {
  if (!field) return "";
  return field.value ?? field.raw ?? "";
}

function humanError(raw: unknown): string {
  const coded = parseCodedError(raw);
  if (!coded) return String(raw);
  return coded.resolution ? `${coded.description} ${coded.resolution}` : coded.description;
}

export function TaxInputsForm({ fy }: { fy: number }): JSX.Element {
  const [doc, setDoc] = useState<TaxInputsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Drafts>({});
  // null means "untouched", so the regime key is omitted from the body; "" means
  // the user cleared it, which deletes the key.
  const [regimeDraft, setRegimeDraft] = useState<string | null>(null);
  const [rawDraft, setRawDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<ConflictDoc | null>(null);
  const [saved, setSaved] = useState(false);
  const [open, setOpen] = useState(false);

  const fetchDoc = useCallback(() => {
    api.getJSON<TaxInputsResponse>(`/api/tax/inputs?fy=${fy}`).then(
      (d) => {
        setDoc(d);
        setDrafts({});
        setRegimeDraft(null);
        setRawDraft(null);
        setConflict(null);
        setSaveError(null);
        // Open when there is something to ask for or to repair: a file that
        // will not parse (this form is its only door), a value the backend
        // could not read as an amount (which also takes the whole workspace
        // down), or nothing declared yet -- the state every honesty notice
        // links here to fix. A complete, readable declaration stays collapsed,
        // because the overview is the nav default and does not need a form
        // across the top of it every visit.
        const invalid = [...d.income, ...d.deductions].some((f) => f.invalid);
        if (d.parse_error !== null || invalid || d.income.length === 0) setOpen(true);
      },
      (e) => setLoadError(humanError(e)),
    );
  }, [fy]);

  useEffect(fetchDoc, [fetchDoc]);

  function retry(): void {
    setDoc(null);
    setLoadError(null);
    fetchDoc();
  }

  function setDraft(block: Block, key: string, value: string): void {
    setSaved(false);
    setDrafts((prev) => ({ ...prev, [fieldId(block, key)]: value }));
  }

  /** The body for one block, or undefined when nothing in it was touched.
   *
   * Three-valued, and the whole point of tracking touched fields: an untouched
   * key is ABSENT from the body (the stored value stands), a cleared field is
   * `null` (delete it), and a typed field is its string. `""` is never sent.
   * An untouched block is omitted entirely, so a deductions-only save cannot
   * clear the declared salary. */
  function blockBody(
    block: Block,
    keys: string[],
  ): Record<string, string | null> | undefined {
    const out: Record<string, string | null> = {};
    for (const key of keys) {
      const draft = drafts[fieldId(block, key)];
      if (draft === undefined) continue;
      const trimmed = draft.trim();
      out[key] = trimmed === "" ? null : trimmed;
    }
    return Object.keys(out).length > 0 ? out : undefined;
  }

  function patchBody(revision: string): Record<string, unknown> {
    if (!doc) return { fy, revision };
    const body: Record<string, unknown> = { fy, revision };
    if (regimeDraft !== null) body.regime = regimeDraft === "" ? null : regimeDraft;
    const income = blockBody("income", doc.income_fields);
    if (income) body.income = income;
    const deductions = blockBody("deductions", doc.deduction_sections);
    if (deductions) body.deductions = deductions;
    return body;
  }

  const touched =
    regimeDraft !== null ||
    Object.keys(drafts).some((k) => {
      const [block, key] = k.split(":") as [Block, string];
      const current = drafts[k].trim();
      const was = storedText(stored(block === "income" ? (doc?.income ?? []) : (doc?.deductions ?? []), key));
      return current !== was;
    });

  async function save(revision: string): Promise<void> {
    if (!doc) return;
    setBusy(true);
    setSaveError(null);
    setConflict(null);
    setSaved(false);
    try {
      const next = await patchJSON<TaxInputsResponse>("/api/tax/inputs", patchBody(revision));
      setDoc(next);
      setDrafts({});
      setRegimeDraft(null);
      setSaved(true);
      // The house mechanism: every mounted report re-reads, so the screens
      // around this form pick up the new figures. The form itself keeps the
      // PATCH response -- it already carries the new revision, so a second save
      // is not a false conflict, and it does not refetch its own endpoint.
      triggerReload();
    } catch (e) {
      const clash = parseConflict(e);
      // Typed input is never discarded on a conflict: `drafts` is left exactly
      // as it is and the file's current values are shown beside it.
      if (clash) setConflict(clash);
      setSaveError(humanError(e));
    } finally {
      setBusy(false);
    }
  }

  async function replaceFile(): Promise<void> {
    if (!doc) return;
    setBusy(true);
    setSaveError(null);
    setConflict(null);
    setSaved(false);
    try {
      const next = await api.postJSON<TaxInputsResponse>("/api/tax/inputs/replace", {
        fy,
        revision: doc.revision,
        text: rawDraft ?? doc.raw_text ?? "",
      });
      setDoc(next);
      setRawDraft(null);
      setDrafts({});
      setRegimeDraft(null);
      setSaved(true);
      triggerReload();
    } catch (e) {
      const clash = parseConflict(e);
      if (clash) setConflict(clash);
      setSaveError(humanError(e));
    } finally {
      setBusy(false);
    }
  }

  if (loadError !== null) {
    return (
      <div className="callout bad">
        <span>
          Could not read your declared tax inputs. {loadError}{" "}
          <button className="btn sm" onClick={retry}>
            Retry
          </button>
        </span>
      </div>
    );
  }

  if (doc === null) return <div className="callout info">Reading your declared inputs…</div>;

  const unparseable = doc.parse_error !== null;
  const regimeValue = regimeDraft ?? doc.regime ?? "";

  return (
    <div className="panel declared-form" style={{ marginBottom: "1.2308rem" }}>
      <div className="panel-head">
        <span>Declared tax inputs — FY {fy}</span>
        <button
          className="btn sm"
          style={{ marginLeft: "auto" }}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Hide" : unparseable ? "Repair" : doc.exists ? "Edit" : "Declare"}
        </button>
      </div>

      {open && (
        <div className="panel-body">
          <div className="declared-path">
            {doc.exists ? "Saved in " : "Will be written to "}
            <span className="tax-key">{doc.path}</span>
            {" — a plain YAML file you can also edit by hand."}
          </div>

          {doc.unknown_keys.length > 0 && (
            <UnknownInputKeysNotice path={doc.path} keys={doc.unknown_keys} />
          )}

          {doc.rules_error !== null && (
            <div className="callout warn">
              <span>
                The tax rules for this FY could not be read ({doc.rules_error}).
                The standard Chapter VI-A sections are offered below so your
                figures can still be saved; the estimate stays unavailable until
                the rules file is fixed.
              </span>
            </div>
          )}

          {saved && (
            <div className="callout info" role="status">
              Saved to <span className="tax-key">{doc.path}</span>. The figures
              on screen have been re-read.
            </div>
          )}

          {saveError !== null && (
            <div className="callout bad">
              <span>
                Nothing was written. {saveError}
                {conflict !== null && (
                  <>
                    {" "}
                    <button
                      className="btn sm"
                      disabled={busy}
                      onClick={() => void save(conflict.revision)}
                    >
                      Save over the file as it is now
                    </button>
                  </>
                )}
              </span>
            </div>
          )}

          {unparseable ? (
            <div className="field">
              <div className="callout bad">
                <span>
                  This file cannot be read as declared tax inputs (
                  {doc.parse_error}), so the figures in it are not being used
                  anywhere and the fields cannot be shown. Correct the text
                  below and replace the whole file — the one save that works on
                  an unparseable file. Nothing else in the app is affected.
                </span>
              </div>
              <label htmlFor="tax-inputs-raw">The file, as it is on disk</label>
              <textarea
                id="tax-inputs-raw"
                aria-label="The file, as it is on disk"
                rows={10}
                value={rawDraft ?? doc.raw_text ?? ""}
                onChange={(e) => {
                  setSaved(false);
                  setRawDraft(e.target.value);
                }}
              />
              <button
                className="btn primary"
                disabled={busy}
                onClick={() => void replaceFile()}
              >
                Replace the whole file
              </button>
            </div>
          ) : (
            <>
              <div className="field">
                <label htmlFor="tax-inputs-regime">Regime you file under</label>
                <select
                  id="tax-inputs-regime"
                  value={regimeValue}
                  onChange={(e) => {
                    setSaved(false);
                    setRegimeDraft(e.target.value);
                  }}
                >
                  <option value="">Not stated</option>
                  <option value="new">New regime (s.115BAC)</option>
                  <option value="old">Old regime</option>
                </select>
                <span className="field-hint">
                  Balances and the tax due are quoted against this regime. Left
                  unstated, the cheaper regime is used instead.
                </span>
              </div>

              <div className="declared-group">Income — from your payslip or Form 16</div>
              {doc.income_fields.map((key) => (
                <InputField
                  key={key}
                  block="income"
                  fieldKey={key}
                  label={incomeFieldCopy(key).label}
                  hint={incomeFieldCopy(key).hint}
                  field={stored(doc.income, key)}
                  draft={drafts[fieldId("income", key)]}
                  conflict={conflict}
                  onChange={(v) => setDraft("income", key, v)}
                />
              ))}

              <div className="declared-group">Chapter VI-A deductions</div>
              {doc.deduction_sections.map((key) => (
                <InputField
                  key={key}
                  block="deductions"
                  fieldKey={key}
                  label={sectionLabel(key)}
                  hint=""
                  field={stored(doc.deductions, key)}
                  draft={drafts[fieldId("deductions", key)]}
                  conflict={conflict}
                  onChange={(v) => setDraft("deductions", key, v)}
                />
              ))}

              <div className="declared-actions">
                <button
                  className="btn primary"
                  disabled={busy || !touched}
                  onClick={() => void save(doc.revision)}
                >
                  {busy ? "Saving…" : "Save declared inputs"}
                </button>
                <span className="field-hint">
                  A field left as it is keeps its saved value; clearing a field
                  removes the declaration, which is not the same as declaring 0.
                </span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function InputField({
  block,
  fieldKey,
  label,
  hint,
  field,
  draft,
  conflict,
  onChange,
}: {
  block: Block;
  fieldKey: string;
  label: string;
  hint: string;
  field: TaxInputField | undefined;
  draft: string | undefined;
  conflict: ConflictDoc | null;
  onChange: (v: string) => void;
}): JSX.Element {
  const id = `tax-inputs-${block}-${fieldKey}`;
  const value = draft ?? storedText(field);
  const onDisk = conflict ? conflictValue(conflict, block, fieldKey) : null;
  const clashes = conflict !== null && onDisk !== (draft ?? storedText(field) ?? null);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="text"
        inputMode="decimal"
        aria-label={label}
        value={value}
        placeholder="not declared"
        onChange={(e) => onChange(e.target.value)}
      />
      {field?.invalid && (
        <span className="field-hint declared-invalid">
          The file has <span className="tax-key">{field.raw}</span> here, which
          is not an amount this form can save. Plain digits only, at most two
          decimal places — no digit grouping, no symbol, no sign.
        </span>
      )}
      {clashes && (
        <span className="field-hint declared-invalid">
          On disk now: {onDisk === null ? "not declared" : onDisk}. Yours above
          has been kept.
        </span>
      )}
      {hint !== "" && <span className="field-hint">{hint}</span>}
    </div>
  );
}
