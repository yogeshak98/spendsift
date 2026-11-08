"""Models for the opening-balance routes: the per-account statuses, the
first-import prompt, and the writes (add, correct, mark zero, MF lots).

Amounts are decimal strings with 2 decimal places. `amount` is always a
magnitude and `direction` says which way it points; `ledger_amount` is the
signed figure as booked on the account. Dates are `YYYY-MM-DD`.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OpeningKind = Literal[
    "bank", "card", "loan", "person", "cash", "other_asset", "other_liability"
]
OpeningDirection = Literal["have", "owe", "owed_to_me"]
OpeningStatus = Literal["opening", "zero", "missing", "empty"]
OpeningSource = Literal["statement", "derived", "manual", "legacy"]
SuggestionSource = Literal["statement", "derived"]


# ── reads ────────────────────────────────────────────────────────────────────
class OpeningRecord(BaseModel):
    """An account's current opening: its earliest opening transaction.
    `txn_id` is None for a legacy entry written without one; `shared` is true
    when the same transaction also opens other accounts; `balance_check` is
    true when a paired `balance` directive exists."""

    date: dt.date
    amount: str
    direction: OpeningDirection
    ledger_amount: str
    narration: str
    txn_id: str | None
    shared: bool
    source: OpeningSource
    balance_check: bool


class OpeningStatusRow(BaseModel):
    """One balance-sheet account's opening status. `warn` is true for
    "missing", and for an "opening" that does not cover the account's
    activity: one dated after its first posting (late), or one taken from a
    statement with a posting from its date up to (not on) the statement's
    start (a gap). `directions` lists the allowed directions, the default first.

    `suggested_date`: for a late "opening", the date rule applied to
    `first_posting`; for any other "opening", the opening's own date; for
    "missing",
    the date rule applied to `first_posting`; for "empty" and "zero", 31 Mar
    before the current financial year. `april`: for "missing", whether the
    date rule's April case applied; for "empty" and "zero", true (the 31 Mar
    default applies)."""

    account: str
    account_display: str
    kind: OpeningKind
    status: OpeningStatus
    opening: OpeningRecord | None
    first_posting: dt.date | None
    suggested_date: dt.date
    april: bool
    warn: bool
    directions: list[OpeningDirection]


class OpeningsStatusResponse(BaseModel):
    """Every account considered for an opening, in kind order then by account.
    `warning` is the fixed warning sentence when `missing_count` > 0, else
    null."""

    accounts: list[OpeningStatusRow]
    missing_count: int
    warning: str | None


class OpeningSuggestion(BaseModel):
    """A suggested opening and the arithmetic behind it. `label` is the
    matched label cell (CSV, at most 60 characters), "previous balance"
    (PDF), or null (derived from the first row)."""

    source: SuggestionSource
    amount: str
    direction: OpeningDirection
    ledger_amount: str
    arithmetic: str
    label: str | None


class OpeningPromptItem(BaseModel):
    """The first-import question for one account with no records.
    `alternatives` holds the derived suggestion when a printed one also
    exists; `mismatch` is true when their ledger amounts differ; `note`
    explains a missing suggestion (e.g. the opening cell could not be
    read)."""

    account: str
    account_display: str
    kind: OpeningKind
    statement_start: dt.date
    suggested_date: dt.date
    april: bool
    april_advice: str | None
    suggestion: OpeningSuggestion | None
    alternatives: list[OpeningSuggestion]
    mismatch: bool
    note: str | None
    directions: list[OpeningDirection]


class OpeningPromptResponse(BaseModel):
    """`prompts` covers the batch's accounts whose status is "empty", plus a
    "missing" account whose own statement is in this batch and whose
    suggested date is before its first posting. `missing` lists every batch
    account whose status row warns: "missing" (records but no opening), a
    late opening (dated after earlier records) or a gapped opening (records
    from its date up to its opening-start). Shown as one informational
    line; a listed account may also have a prompt."""

    batch_id: str
    prompts: list[OpeningPromptItem]
    missing: list[str]


# ── writes ───────────────────────────────────────────────────────────────────
class OpeningCreateRequest(BaseModel):
    """Add an opening. `amount` is a magnitude, validated by the service
    (E1093). `batch_id` ties the write to an import being reviewed, so the
    date must also be before that batch's earliest row for the account."""

    model_config = ConfigDict(extra="forbid")

    account: str
    amount: str
    direction: OpeningDirection
    date: dt.date
    batch_id: str | None = None
    balance_check: bool = False


class OpeningExpected(BaseModel):
    """The opening a correction was made against (stale check, E1091)."""

    model_config = ConfigDict(extra="forbid")

    date: dt.date
    ledger_amount: str


class OpeningCorrectRequest(BaseModel):
    """Rewrite an account's opening in place."""

    model_config = ConfigDict(extra="forbid")

    account: str
    amount: str
    direction: OpeningDirection
    date: dt.date
    balance_check: bool
    expected: OpeningExpected


class OpeningWriteResponse(BaseModel):
    """The opening as written. `snapshot` is the snapshot directory name
    under `<storage>/snapshots` taken before the write."""

    account: str
    account_display: str
    txn_id: str
    date: dt.date
    ledger_amount: str
    source: OpeningSource
    balance_check: bool
    snapshot: str


class OpeningZeroRequest(BaseModel):
    """Mark an account as started at zero; `zero: false` undoes the mark."""

    model_config = ConfigDict(extra="forbid")

    account: str
    zero: bool


class OpeningZeroResponse(BaseModel):
    account: str
    account_display: str
    status: OpeningStatus


class MfOpeningLot(BaseModel):
    """One mutual-fund opening lot. `symbol` is "MF" + the AMFI code;
    `units` > 0 with at most 4 decimal places; `cost` is the total INR,
    > 0 with at most 2 decimal places; `date` is the unit allotment date,
    on or before today."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    units: str
    cost: str
    date: dt.date


class MfOpeningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lots: list[MfOpeningLot] = Field(min_length=1, max_length=50)


class MfOpeningWritten(BaseModel):
    """One lot as written to both ledgers: `unit_cost` is cost / units to 8
    decimal places and `cost_booked` is units x unit_cost."""

    symbol: str
    name: str
    account: str
    account_display: str
    units: str
    unit_cost: str
    cost_booked: str
    date: dt.date
    txn_id: str


class MfOpeningResponse(BaseModel):
    written: list[MfOpeningWritten]
    snapshot: str
