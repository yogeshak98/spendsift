import { useEffect, useState, type ChangeEvent, type JSX } from "react";
import { api } from "@/shared/api/http";
import { parseCodedError } from "@/shared/api/codedError";
import { triggerReload } from "@/shared/api/useReport";

// GET /api/accounts/card-properties and PUT /api/accounts/card-properties/batch
// (API_CONTRACT.md "Card properties").
type CardProps = {
  account: string;
  statement_day: number | null;
  due_day: number | null;
  last4: string | null;
  expiry: string | null; // "YYYY-MM"
  suggested: {
    statement_day: number | null;
    due_day: number | null;
    last4: string | null;
    statement_count: number;
    last4_source: "pdf_template" | "account_name" | null;
  };
};

type Draft = { statement_day: string; due_day: string; last4: string; expiry: string };
type Field = keyof Draft;

/** A save error the server pinned on one card (and maybe one of its fields). */
type CardError = { account: string; field: Field | null; message: string };

const FIELDS: readonly Field[] = ["statement_day", "due_day", "last4", "expiry"];

const LAST4_SOURCE: Record<"pdf_template" | "account_name", string> = {
  pdf_template: "from PDF template",
  account_name: "from account name",
};

function leaf(account: string): string {
  return account.split(":").pop() ?? account;
}

function text(value: number | string | null): string {
  return value === null ? "" : String(value);
}

/** "2029-08" -> "08/29", the way it is printed on the card. */
function expiryText(expiry: string | null): string {
  const m = expiry?.match(/^\d{2}(\d{2})-(\d{2})$/);
  return m ? `${m[2]}/${m[1]}` : text(expiry);
}

function toDraft(card: CardProps): Draft {
  return {
    statement_day: text(card.statement_day),
    due_day: text(card.due_day),
    last4: text(card.last4),
    expiry: expiryText(card.expiry),
  };
}

/** An empty input is sent as null (removes the property); a day is sent as
 * a number so the server's range check names the field. */
function dayValue(raw: string): number | string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : trimmed;
}

/** `MM/YY` (or `MM/YYYY`) -> `"YYYY-MM"`; anything else is sent as typed so
 * the server's E1082 names the field. An untouched input sends the stored
 * value back as it is. */
function expiryValue(raw: string, card: CardProps): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (trimmed === expiryText(card.expiry)) return card.expiry;
  const m = trimmed.match(/^(\d{1,2})\s*\/\s*(\d{2}|\d{4})$/);
  if (!m) return trimmed;
  const year = m[2].length === 2 ? `20${m[2]}` : m[2];
  return `${year}-${m[1].padStart(2, "0")}`;
}

/** What saving this draft would send for the card. */
function payload(card: CardProps, draft: Draft) {
  return {
    account: card.account,
    statement_day: dayValue(draft.statement_day),
    due_day: dayValue(draft.due_day),
    last4: draft.last4.trim() || null,
    expiry: expiryValue(draft.expiry, card),
  };
}

/** The draft would change what is stored: compared as the values it would
 * send (`8/29` is the stored `2029-08`; ` 1234 ` is `1234`), not as text. */
function isDirty(card: CardProps, draft: Draft | undefined): boolean {
  if (!draft) return false;
  const next = payload(card, draft);
  return (
    next.statement_day !== card.statement_day ||
    next.due_day !== card.due_day ||
    next.last4 !== card.last4 ||
    next.expiry !== card.expiry
  );
}

function statements(count: number): string {
  return `from ${count} statement${count === 1 ? "" : "s"}`;
}

/** The hints for the values a card does not store but a suggestion knows. */
function hints(card: CardProps): Partial<Record<Field, string>> {
  const s = card.suggested;
  const out: Partial<Record<Field, string>> = {};
  if (card.statement_day === null && s.statement_day !== null) {
    out.statement_day = `Suggested: ${s.statement_day} (${statements(s.statement_count)})`;
  }
  if (card.due_day === null && s.due_day !== null) {
    out.due_day = `Suggested: ${s.due_day} (${statements(s.statement_count)})`;
  }
  if (card.last4 === null && s.last4 !== null && s.last4_source !== null) {
    out.last4 = `Suggested: ${s.last4} (${LAST4_SOURCE[s.last4_source]})`;
  }
  return out;
}

/** The card (and field) a coded save error names in its `data`, if any. */
function cardError(e: unknown): CardError | null {
  const coded = parseCodedError(e);
  const match = String(e).match(/\{.*\}/s);
  if (!coded || !match) return null;
  let data: unknown;
  try {
    data = (JSON.parse(match[0]) as { data?: unknown }).data;
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null) return null;
  const { account, field } = data as { account?: unknown; field?: unknown };
  if (typeof account !== "string") return null;
  return { account, field: FIELDS.find((f) => f === field) ?? null, message: coded.description };
}

const INPUT_STYLE = {
  background: "var(--surface2)",
  border: "1px solid var(--border)",
  borderRadius: "0.3846rem",
  padding: "0.3077rem 0.6154rem",
  fontFamily: "var(--mono)",
  fontSize: "0.9231rem",
  color: "var(--text)",
  width: "4.9231rem",
} as const;

const INVALID_STYLE = { ...INPUT_STYLE, border: "1px solid var(--red)" } as const;

const ERROR_STYLE = { color: "var(--red)", fontSize: "0.8462rem", marginTop: "0.3077rem" } as const;

const FIELDSET_STYLE = { border: 0, padding: 0, margin: 0, minWidth: 0 } as const;

const LINK_STYLE = {
  background: "none",
  border: "none",
  padding: 0,
  color: "var(--blue)",
  textDecoration: "underline",
  cursor: "pointer",
  fontSize: "0.9231rem",
} as const;

export function CardProperties(): JSX.Element | null {
  const [cards, setCards] = useState<CardProps[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<CardError | null>(null);
  const [saving, setSaving] = useState(false);

  function show(list: CardProps[]) {
    setCards(list);
    setDrafts(Object.fromEntries(list.map((c) => [c.account, toDraft(c)])));
  }

  useEffect(() => {
    let alive = true;
    api.getJSON<{ cards?: CardProps[] }>("/api/accounts/card-properties").then(
      (res) => alive && show(res.cards ?? []),
      (e) => alive && setBanner(parseCodedError(e)?.description ?? String(e)),
    );
    return () => {
      alive = false;
    };
  }, []);

  const changed = cards.filter((c) => isDirty(c, drafts[c.account]));

  function edit(account: string, field: Field, value: string) {
    setSaved(null);
    setDrafts((d) => ({ ...d, [account]: { ...d[account], [field]: value } }));
    setError((err) =>
      err && err.account === account && (err.field === field || err.field === null) ? null : err,
    );
  }

  function applySuggestion(card: CardProps) {
    setSaved(null);
    const s = card.suggested;
    setDrafts((d) => {
      const cur = d[card.account];
      return {
        ...d,
        [card.account]: {
          ...cur,
          statement_day:
            card.statement_day === null && s.statement_day !== null
              ? String(s.statement_day)
              : cur.statement_day,
          due_day: card.due_day === null && s.due_day !== null ? String(s.due_day) : cur.due_day,
          last4: card.last4 === null && s.last4 !== null ? s.last4 : cur.last4,
        },
      };
    });
  }

  function discard() {
    show(cards);
    setError(null);
    setBanner(null);
    setSaved(null);
  }

  async function save() {
    if (!changed.length || saving) return;
    setBanner(null);
    setError(null);
    setSaved(null);
    setSaving(true);
    const body = { cards: changed.map((card) => payload(card, drafts[card.account])) };
    try {
      const res = await api.putJSON<{ cards?: CardProps[] }>(
        "/api/accounts/card-properties/batch",
        body,
      );
      show(res.cards ?? []);
      const n = body.cards.length;
      setSaved(`Saved ${n} card${n === 1 ? "" : "s"}`);
      triggerReload();
    } catch (e) {
      const pinned = cardError(e);
      if (pinned && cards.some((c) => c.account === pinned.account)) setError(pinned);
      else setBanner(parseCodedError(e)?.description ?? String(e));
    } finally {
      setSaving(false);
    }
  }

  if (!cards.length && !banner) return null;

  const count = changed.length;

  return (
    <div className="panel" style={{ marginBottom: "1.2308rem" }}>
      <div className="panel-head">Credit card properties</div>
      <div className="panel-body">
        {banner && (
          <div className="callout warn" style={{ marginBottom: "0.7692rem" }}>
            {banner}
          </div>
        )}
        {/* noValidate: the server checks every value and names the bad one
            (E1082); a native tooltip would block the save without that. */}
        <form
          aria-label="Credit card properties"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {/* Locked while a save is in flight: the response replaces every
              draft, so anything typed meanwhile would be lost. */}
          <fieldset disabled={saving} style={FIELDSET_STYLE}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Card</th>
                    <th>Statement day</th>
                    <th>Due day</th>
                    <th>Last 4 digits</th>
                    <th>Expiry (MM/YY)</th>
                    <th className="num">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {cards.map((card) => {
                    const name = leaf(card.account);
                    const draft = drafts[card.account] ?? toDraft(card);
                    const hint = hints(card);
                    const err = error?.account === card.account ? error : null;
                    const errorId = (field: Field) => `card-error-${card.account}-${field}`;
                    const invalid = (field: Field) => err?.field === field;
                    const cell = (field: Field, input: JSX.Element) => (
                      <td>
                        {input}
                        {hint[field] && <div className="field-hint">{hint[field]}</div>}
                        {invalid(field) && (
                          <div id={errorId(field)} role="alert" style={ERROR_STYLE}>
                            {err?.message}
                          </div>
                        )}
                      </td>
                    );
                    const inputProps = (field: Field) => ({
                      value: draft[field],
                      onChange: (e: ChangeEvent<HTMLInputElement>) =>
                        edit(card.account, field, e.target.value),
                      style: invalid(field) ? INVALID_STYLE : INPUT_STYLE,
                      "aria-invalid": invalid(field) || undefined,
                      "aria-describedby": invalid(field) ? errorId(field) : undefined,
                    });
                    return (
                      <tr key={card.account}>
                        <td className="label" title={card.account}>
                          {name}
                          {err && err.field === null && (
                            <div role="alert" style={ERROR_STYLE}>
                              {err.message}
                            </div>
                          )}
                        </td>
                        {cell(
                          "statement_day",
                          <input
                            type="number"
                            min={1}
                            max={31}
                            aria-label={`${name} statement day`}
                            {...inputProps("statement_day")}
                          />,
                        )}
                        {cell(
                          "due_day",
                          <input
                            type="number"
                            min={1}
                            max={31}
                            aria-label={`${name} due day`}
                            {...inputProps("due_day")}
                          />,
                        )}
                        {cell(
                          "last4",
                          <input
                            type="text"
                            inputMode="numeric"
                            maxLength={4}
                            aria-label={`${name} last 4`}
                            {...inputProps("last4")}
                          />,
                        )}
                        {cell(
                          "expiry",
                          <input
                            type="text"
                            inputMode="numeric"
                            placeholder="MM/YY"
                            maxLength={7}
                            aria-label={`${name} expiry`}
                            {...inputProps("expiry")}
                          />,
                        )}
                        <td className="num">
                          {Object.keys(hint).length > 0 && (
                            <button
                              type="button"
                              className="btn sm"
                              aria-label={`Use suggestion for ${name}`}
                              onClick={() => applySuggestion(card)}
                            >
                              Use suggestion
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "0.9231rem", marginTop: "0.7692rem" }}>
              <button type="submit" className="btn sm" disabled={count === 0}>
                {count > 0 ? `Save changes (${count})` : "Save changes"}
              </button>
              {count > 0 && (
                <button type="button" style={LINK_STYLE} onClick={discard}>
                  Discard
                </button>
              )}
              {saved && (
                <span role="status" style={{ fontSize: "0.9231rem", color: "var(--green)" }}>
                  {saved}
                </span>
              )}
            </div>
          </fieldset>
        </form>
      </div>
    </div>
  );
}
