from __future__ import annotations

import uuid
from datetime import date as date_type
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.models.domain.tags import normalize_tags


SPENDSIFT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "backend.local")


class Direction(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class DedupStatus(str, Enum):
    NEW = "new"
    EXACT_DUP = "exact_dup"
    TRANSFER_MATCH = "transfer_match_candidate"
    TRANSFER_ALREADY_RECORDED = "transfer_already_recorded"


class Split(BaseModel):
    account: str
    amount: Decimal
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, value: list[str]) -> list[str]:
        return normalize_tags(value)

    @field_validator("amount")
    @classmethod
    def _nonzero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("split amount must not be zero")
        return value


CategorySource = Literal[
    "rule", "dictionary", "user", "taught_batch", "fee_follows", "none"
]


class CandidateTxn(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: str = ""
    date: date_type
    amount: Decimal
    raw_narration: str
    normalized_narration: str
    source_account: str
    direction: Direction
    suggested_category: str | None = None
    suggested_other_leg: str | None = None
    splits: list[Split] = Field(default_factory=list)
    # Normalised transaction-level tags, written to the journal as `#tag`.
    tags: list[str] = Field(default_factory=list)
    # Where `suggested_category` came from; set only by the app, never by a client.
    category_source: CategorySource = "none"
    # Id of the charge row this fee row follows (its category is derived from it).
    follows: str | None = None
    # False is the user's "don't learn that one" veto.
    learn: bool = True
    confidence: float = 1.0
    dedup_status: DedupStatus = DedupStatus.NEW
    flags: list[str] = Field(default_factory=list)
    matched_txn_id: str | None = None
    needs_account: bool = False
    unresolved_marker_key: str | None = None
    # Investment-ingestion fields (default None so non-investment imports are
    # unaffected). When `commodity` is set, the entry writer emits a commodity-lot
    # posting instead of a plain INR posting.
    units: Decimal | None = None
    commodity: str | None = None
    unit_cost: Decimal | None = None
    unresolved_scheme: str | None = None
    # Soft review flag (parallels unresolved_scheme): a REDEEM whose units exceed
    # the lots known to the ledger + earlier candidates in the batch. Blocks commit
    # until the covering purchases are imported or the units corrected.
    uncovered_redemption: bool = False
    # Soft review flag (same family as uncovered_redemption): the trade's cash
    # differs from units*NAV by more than the display precision of either can
    # explain. The remainder IS booked, to
    # Expenses:Investment:Charges:Unexplained, so the entry balances and the
    # amount is totallable -- this flag is what stops it being silent.
    unexplained_charge: bool = False
    # The amount behind the flag above, so review can say HOW MUCH could not
    # be explained rather than only that something could not. It is read off
    # the charge legs (`ChargeResult.legs[charge_unexplained_account]`), so it
    # is the same figure the journal will carry. Set at PREVIEW time by
    # `ImportService.detect` -- the commit path has already written the leg by
    # the time it flags the row, so it has no reader for this and does not set
    # it. The flag, not this amount, is the signal; this is display detail.
    unexplained_charge_amount: Decimal | None = None
    # Soft review flag (parallels uncovered_redemption): a stock order whose
    # per-lot postings do not add up to the order's Value. Set by entry_writer.py
    # immediately before it refuses the commit, so the batch that stays in
    # review carries the reason on the offending row.
    unreconciled_lots: bool = False
    # Soft review flag (same family): why this lot row cannot be written to a
    # journal at all — an unroutable source_account, or a symbol beancount
    # will not accept as a commodity name. Set by entry_writer.py immediately before
    # it refuses the commit.
    lot_blocked_reason: str | None = None
    # Broker order identity. `order_id` is the dedup key for stock imports
    # (Groww's "Exchange Order Id"); `isin` becomes commodity metadata and is
    # the input the deferred ISIN-based price resolution needs.
    order_id: str | None = None
    isin: str | None = None
    # Soft review flag (same family as uncovered_redemption/unreconciled_lots):
    # this candidate's order_id already exists in the journal, attached to a
    # transaction whose content (symbol/direction/units/value/date) does NOT
    # match this row -- a restatement, not a re-import. Distinct from an exact
    # duplicate (same order_id, same content), which is Task 6's dedup and
    # never reaches review. Set by `groww_stocks.flag_restated_orders`.
    order_restated: bool = False
    # Foreign-currency original of a card row, e.g. "USD 12.00", read from a
    # PDF template's fx_currency/fx_amount groups. Display metadata only: it is
    # NOT part of the content-derived id below, and nothing writes it to the
    # journal yet.
    fx: str | None = None

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, value: list[str]) -> list[str]:
        return normalize_tags(value)

    @model_validator(mode="after")
    def _default_id(self) -> "CandidateTxn":
        if not self.id:
            # Quantize first: Decimal('2601.00') and Decimal('2601') are the
            # same amount but format to different strings, which would
            # otherwise give the same real transaction two different ids
            # depending on the source's decimal precision.
            normalized = self.amount.quantize(Decimal("0.01"))
            # Direction is part of the identity: a charge and its same-day
            # reversal share date, amount and narration, and must still be
            # two rows a review edit can target one at a time.
            key = (
                f"{self.date}|{normalized}|{self.normalized_narration}|"
                f"{self.source_account}|{self.direction.value}"
            )
            self.id = uuid.uuid5(SPENDSIFT_NAMESPACE, key).hex
        return self


def disambiguate_ids(candidates: list[CandidateTxn]) -> None:
    """Give every row of one statement its own id, in place.

    The content-derived id is kept by the first row that has it. A later row
    with the same id (a line the statement genuinely prints twice) gets an id
    derived from that id and its ordinal among the repeats, so the ids depend
    only on the statement's own row order: staging the same file again gives
    the same ids, and running this twice changes nothing.

    Ids are review handles and the committed `txn-id`. Re-import dedup keys
    a row on content (`dedup.posting_key`, signed), never on the row's own
    id; it reads the JOURNAL's `txn-id`s only to tell recorded units apart
    (`dedup.journal_unit_identity` pairs the two halves of an MF order across
    the main and investment journals). Unique ids keep twins two units there
    too; twins committed by older code under one shared id are told apart by
    occurrence.
    """
    seen: set[str] = {c.id for c in candidates}
    counts: dict[str, int] = {}
    for c in candidates:
        n = counts.get(c.id, 0)
        counts[c.id] = n + 1
        if n == 0:
            continue
        new = uuid.uuid5(SPENDSIFT_NAMESPACE, f"{c.id}|{n}").hex
        while new in seen:  # never collide with another row's id
            new = uuid.uuid5(SPENDSIFT_NAMESPACE, f"{new}|{n}").hex
        seen.add(new)
        c.id = new


# Rows that are never written: their id is never a txn-id.
_NOT_WRITTEN = (DedupStatus.EXACT_DUP, DedupStatus.TRANSFER_ALREADY_RECORDED)


def avoid_taken_ids(candidates: list[CandidateTxn], taken: set[str]) -> None:
    """Re-number, in place, every row that will be written whose id is
    already a `txn-id` in the journal (`taken`), so a commit never writes a
    second entry under an existing txn-id.

    Run after dedup: a row marked a duplicate is not written, so its id may
    (and usually does) equal the entry it duplicates. A written row can still
    hold a taken id: the journal kept only the second of two identical
    entries (the first was deleted), and re-staging both rows lets the first
    row claim that entry while the second gets the id `disambiguate_ids`
    gives a second twin -- the recorded one's. Such a row's suffix is bumped
    until its id is neither taken nor another row's, so the ids stay a
    function of the statement and the journal: staging again gives the same.
    """
    seen = {c.id for c in candidates}
    for c in candidates:
        if c.dedup_status in _NOT_WRITTEN or c.id not in taken:
            continue
        n = 1
        new = uuid.uuid5(SPENDSIFT_NAMESPACE, f"{c.id}|{n}").hex
        while new in taken or new in seen:
            n += 1
            new = uuid.uuid5(SPENDSIFT_NAMESPACE, f"{c.id}|{n}").hex
        seen.add(new)
        c.id = new
