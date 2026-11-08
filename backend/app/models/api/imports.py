"""Models for the import routes: statement import, ledger-cli merge, scheme
search and review learning."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SchemeSearchHit(BaseModel):
    """One AMFI scheme a review row could be identified as."""

    symbol: str  # the beancount commodity, "MF" + code
    code: str  # the raw AMFI numeric code, as printed on a statement
    name: str
    category: str


class SchemeSearchResponse(BaseModel):
    schemes: list[SchemeSearchHit]


# ---------------------------------------------------------------------------
# Ledger-CLI / hledger import merge (Task 7)
# ---------------------------------------------------------------------------
class LedgerMergePosting(BaseModel):
    account: str
    amount: str | None = None
    commodity: str | None = None
    cost: str | None = None
    cost_currency: str | None = None


class LedgerMergeTxn(BaseModel):
    id: str
    date: str
    narration: str
    postings: list[LedgerMergePosting]
    duplicate: bool
    unbookable_sell: bool


class LedgerMergeAccount(BaseModel):
    raw: str
    suggested: str
    status: str


class LedgerMergeCommodity(BaseModel):
    raw: str
    suggested: str
    display_name: str | None = None
    status: str


class LedgerMergeUnparsed(BaseModel):
    line_no: int
    text: str
    reason: str


class LedgerMergeImbalance(BaseModel):
    line_no: int
    narration: str
    residual: str


class LedgerMergeSummary(BaseModel):
    transactions: int
    duplicates: int
    new_accounts: int
    new_commodities: int
    unparsed: int
    imbalances: int


class LedgerMergePreviewResponse(BaseModel):
    batch_id: str
    transactions: list[LedgerMergeTxn]
    accounts: list[LedgerMergeAccount]
    commodities: list[LedgerMergeCommodity]
    unparsed: list[LedgerMergeUnparsed]
    ignored_lines: int
    ignored: list[LedgerMergeUnparsed] = []
    imbalances: list[LedgerMergeImbalance] = []
    summary: LedgerMergeSummary


class LedgerMergeCommitRequest(BaseModel):
    batch_id: str
    account_map: dict[str, str]
    commodity_map: dict[str, str]
    include_duplicates: bool = False


class LedgerMergeCommitResponse(BaseModel):
    committed: int
    skipped_duplicates: int
    opened_accounts: list[str]
    declared_commodities: list[str]


class LedgerMergeDiscardResponse(BaseModel):
    discarded: str


class WillLearnItem(BaseModel):
    """One rule `learned.yaml` would gain at commit."""

    pattern: str
    category: str
    candidate_ids: list[str]
    replaces: str | None
    # Set when the pattern's rows were set to several categories: each with
    # its row count, in batch order, and a sentence naming the rule's choice
    # (the category of the pattern's last row in the batch).
    conflicting: dict[str, int] = Field(default_factory=dict)
    warning: str | None = None


class NotLearnedItem(BaseModel):
    """A user-set row whose pattern commit will not write to `learned.yaml`."""

    candidate_id: str
    pattern: str
    reason: str


class ReviewLearningResponse(BaseModel):
    """Response of `GET /api/review/{batch_id}/learning`."""

    will_learn: list[WillLearnItem]
    not_learned: list[NotLearnedItem]
    would_open: list[str]


class MarkerResolution(BaseModel):
    batch_id: str
    marker_key: str | None
    account: str


class SchemeResolution(BaseModel):
    commodity: str
    scheme_name: str
