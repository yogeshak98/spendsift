// Hand-written mirror of backend/app/models/api/openings.py (screens do not
// consume schema.d.ts). Amounts are decimal strings; dates are YYYY-MM-DD.
export type OpeningKind = "bank" | "card" | "loan" | "person" | "cash" | "other_asset" | "other_liability";
export type OpeningDirection = "have" | "owe" | "owed_to_me";
export type OpeningStatus = "opening" | "zero" | "missing" | "empty";
export type OpeningSource = "statement" | "derived" | "manual" | "legacy";
export type SuggestionSource = "statement" | "derived";

export type OpeningRecord = {
  date: string;
  amount: string;
  direction: OpeningDirection;
  ledger_amount: string;
  narration: string;
  txn_id: string | null;
  shared: boolean;
  source: OpeningSource;
  balance_check: boolean;
};

export type OpeningStatusRow = {
  account: string;
  account_display: string;
  kind: OpeningKind;
  status: OpeningStatus;
  opening: OpeningRecord | null;
  first_posting: string | null;
  suggested_date: string;
  april: boolean;
  warn: boolean;
  directions: OpeningDirection[];
};

export type OpeningsStatusResponse = {
  accounts: OpeningStatusRow[];
  missing_count: number;
  warning: string | null;
};

export type OpeningSuggestion = {
  source: SuggestionSource;
  amount: string;
  direction: OpeningDirection;
  ledger_amount: string;
  arithmetic: string;
  label: string | null;
};

export type OpeningPromptItem = {
  account: string;
  account_display: string;
  kind: OpeningKind;
  statement_start: string;
  suggested_date: string;
  april: boolean;
  april_advice: string | null;
  suggestion: OpeningSuggestion | null;
  alternatives: OpeningSuggestion[];
  mismatch: boolean;
  note: string | null;
  directions: OpeningDirection[];
};

export type OpeningPromptResponse = {
  batch_id: string;
  prompts: OpeningPromptItem[];
  missing: string[];
};

export type OpeningWriteResponse = {
  account: string;
  account_display: string;
  txn_id: string;
  date: string;
  ledger_amount: string;
  source: OpeningSource;
  balance_check: boolean;
  snapshot: string;
};

export const DIRECTION_LABEL: Record<OpeningDirection, string> = {
  have: "I have",
  owe: "I owe",
  owed_to_me: "Owed to me",
};

export const OPENINGS_URL = "/api/openings";
export const CHECKLIST_PATH = "/settings/openings";

// Verbatim copy of the backend constant (openings/keys.py). Matched by exact
// equality: a reworded backend flag must fail loudly here, not half-match.
export const MF_OPENING_FLAG =
  "this scheme has opening lots; check this order is not already counted in them";

/** The MF opening-lots flag on a candidate, or null when it carries none. */
export function mfOpeningFlag(c: { flags?: string[] }): string | null {
  return c.flags?.find((f) => f === MF_OPENING_FLAG) ?? null;
}

export type OpeningZeroResponse = {
  account: string;
  account_display: string;
  status: OpeningStatus;
};

/** Why a warning row warns: no opening at all, or an opening that does not
 *  cover earlier activity (dated after it, or taken from a later statement). */
export type OpeningWarning = "missing" | "late";

/** A row with an opening that still warns: dated after earlier activity. */
export function isLateOpening(row: OpeningStatusRow): boolean {
  return row.status === "opening" && row.warn;
}

/** Warning rows by account: "late" for an opening dated after earlier
 *  activity, "missing" for any other (activity but no opening balance). */
export function missingAccounts(data: OpeningsStatusResponse | null): Map<string, OpeningWarning> {
  return new Map(
    (data?.accounts ?? [])
      .filter((r) => r.warn)
      .map((r): [string, OpeningWarning] => [r.account, isLateOpening(r) ? "late" : "missing"]),
  );
}
