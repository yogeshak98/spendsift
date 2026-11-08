"""Models for the settings routes (accounts, templates)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.app.models.storage.template import Template as IngestTemplate


# ── Settings — Accounts ──────────────────────────────────────────────────────
class AccountsListResponse(BaseModel):
    accounts: list[str]


class AccountsCreateRequest(BaseModel):
    accounts: list[str]


class RejectedAccount(BaseModel):
    name: str
    reason: str


class AccountsCreateResponse(BaseModel):
    opened: list[str]
    skipped_existing: list[str]
    rejected: list[RejectedAccount]


class AccountRenameRequest(BaseModel):
    account: str
    new_name: str


class RenameReferencesUpdated(BaseModel):
    """References an account rename rewrote outside the journal, by store."""

    # templates/*.yaml: default_account and account_marker_map values.
    templates: int
    # templates/pdf/*.yaml: default_account.
    pdf_templates: int
    # rules/loan-terms.yaml keys.
    loan_terms: int
    # config/merge_booking.yaml *_account values.
    merge_booking: int
    # config/budget.yaml targets keys.
    budget: int
    # rules/account_aliases.yaml entries written (1 per rename).
    aliases: int
    # Imports waiting for review that had at least one field rewritten.
    review_batches: int


class AccountRenameResponse(BaseModel):
    renamed: str
    to: str
    postings_updated: int
    # Learned categorisation rules (rules/learned.yaml) now naming `to`.
    rules_updated: int
    references_updated: RenameReferencesUpdated


class AccountCloseRequest(BaseModel):
    account: str


class AccountCloseResponse(BaseModel):
    closed: str
    as_of: str


# ── Settings — Card properties ───────────────────────────────────────────────
class CardPropertiesSuggestion(BaseModel):
    """What imported PDF statements and the account name suggest. Shown as a
    hint with "Use suggestion"; never written to the journal by itself."""

    statement_day: int | None
    due_day: int | None
    last4: str | None
    statement_count: int
    last4_source: Literal["pdf_template", "account_name"] | None


class CardProperties(BaseModel):
    """One open, not-closed `Liabilities:CreditCard:*` account. The four
    values are what its `open` directive stores (`statement-day`, `due-day`,
    `last4`, `expiry` as `"YYYY-MM"`), or null."""

    account: str
    statement_day: int | None
    due_day: int | None
    last4: str | None
    expiry: str | None
    suggested: CardPropertiesSuggestion


class CardPropertiesListResponse(BaseModel):
    cards: list[CardProperties]


class CardPropertiesUpdateRequest(BaseModel):
    """Every key but `expiry` is required; null removes the property. `expiry`
    (`"YYYY-MM"`) may be left out, which keeps the stored one: callers written
    before it existed keep working. Ranges and formats are checked by the
    service so a bad value answers with the coded E1082 naming its field."""

    model_config = ConfigDict(extra="forbid")

    account: str
    statement_day: int | None
    due_day: int | None
    last4: str | None
    expiry: str | None = None


class CardPropertiesBatchItem(BaseModel):
    """One card in a batch save: its complete set, every key required (null
    removes the property). Checked by the service like the single PUT."""

    model_config = ConfigDict(extra="forbid")

    account: str
    statement_day: int | None
    due_day: int | None
    last4: str | None
    expiry: str | None


class CardPropertiesBatchRequest(BaseModel):
    """The cards to save in one journal write (send only the changed ones)."""

    model_config = ConfigDict(extra="forbid")

    cards: list[CardPropertiesBatchItem]


# ── Settings — Templates ─────────────────────────────────────────────────────
class TemplateListResponse(BaseModel):
    templates: list[IngestTemplate]
