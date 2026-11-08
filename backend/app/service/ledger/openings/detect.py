"""Find each account's opening transaction and its opening status."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import get_args

from beancount.core import data

from backend.app.models.api.openings import (
    OpeningRecord,
    OpeningSource,
    OpeningStatus,
    OpeningStatusRow,
)
from backend.app.models.domain.accounts import EQUITY_OPENING
from backend.app.service.ledger.openings.classify import (
    KIND_ORDER,
    allowed_directions,
    direction_of,
    opening_kind,
)
from backend.app.service.ledger.openings.dates import fy_start_year, opening_date_for
from backend.app.service.ledger.openings.keys import (
    OPENING_OPEN_KEY,
    OPENING_SOURCE_KEY,
    OPENING_START_KEY,
    OPENING_TXN_KEY,
    OPENING_ZERO,
)
from backend.app.service.reports.health.ledger_view import (
    closed_accounts_from_entries,
    commodity_names_from_entries,
    display_account,
    open_meta_from_entries,
)

_TXN_ID_KEY = "txn-id"
_MONEY = Decimal("0.01")
_SOURCES: frozenset[str] = frozenset(get_args(OpeningSource))
# The roots an opening transaction's non-equity postings may sit under.
_BALANCE_SHEET = ("Assets:", "Liabilities:")


@dataclass(frozen=True)
class OpeningTxn:
    """One account's opening: `ledger_amount` is the account's posting in
    the opening transaction; `shared` is true when the transaction also opens
    other accounts; `source` is the transaction's opening-source metadata,
    else "legacy"; `balance` is the paired `balance` directive, if any;
    `start` is the transaction's opening-start metadata (the date the
    statement behind the figure starts on), else None."""

    account: str
    date: date
    ledger_amount: Decimal
    narration: str
    txn_id: str | None
    shared: bool
    source: OpeningSource
    entry: data.Transaction
    balance: data.Balance | None
    start: date | None = None


def is_opening_txn(entry: data.Transaction) -> bool:
    """True when the transaction has at least one posting on the opening
    equity account and every other posting is on an Assets: or Liabilities:
    account. A purchase funded from equity with an Expenses: charge leg is
    therefore not an opening.

    The rule is structural and deliberately strict: the equity leg must be
    exactly the opening equity account (not a child of it, not another
    Equity: account), and a single leg on any Income:, Expenses: or other
    Equity: account disqualifies the whole entry, so it opens nothing. A
    false opening would hide a genuinely missing one; a false miss is only
    a warning. A transaction posting only to the equity account opens
    nothing either.
    """
    accounts = [p.account for p in entry.postings]
    if EQUITY_OPENING not in accounts:
        return False
    others = [a for a in accounts if a != EQUITY_OPENING]
    return bool(others) and all(a.startswith(_BALANCE_SHEET) for a in others)


def _order(entry: data.Transaction | data.Balance) -> tuple[date, int]:
    return entry.date, int((entry.meta or {}).get("lineno") or 0)


def _source(entry: data.Transaction) -> OpeningSource:
    value = (entry.meta or {}).get(OPENING_SOURCE_KEY)
    return value if isinstance(value, str) and value in _SOURCES else "legacy"


def _start(entry: data.Transaction) -> date | None:
    """The opening-start metadata as a date; None when absent or unreadable."""
    value = (entry.meta or {}).get(OPENING_START_KEY)
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _paired_balances(entries: list) -> dict[tuple[str, str], data.Balance]:
    """(account, opening-txn) -> the earliest `balance` directive carrying
    that opening-txn metadata, indexed in one pass over the entries."""
    out: dict[tuple[str, str], data.Balance] = {}
    for entry in entries:
        if not isinstance(entry, data.Balance):
            continue
        txn_id = (entry.meta or {}).get(OPENING_TXN_KEY)
        if not txn_id:
            continue
        key = (entry.account, str(txn_id))
        seen = out.get(key)
        if seen is None or _order(entry) < _order(seen):
            out[key] = entry
    return out


def opening_records(entries: list) -> dict[str, OpeningTxn]:
    """Account -> its opening: the earliest opening transaction posting to
    it, by date and then line number. Later opening-shaped entries on the
    same account do not replace it.

    `ledger_amount` sums the account's own postings in that transaction
    (never the equity leg's); `shared` is true when the transaction posts
    to more than one non-equity account; `txn_id` is the transaction's
    txn-id metadata, else None; `balance` is the earliest `balance`
    directive on the account whose opening-txn metadata names that txn-id.
    """
    openings: list[data.Transaction] = [
        e for e in entries if isinstance(e, data.Transaction) and is_opening_txn(e)
    ]
    openings.sort(key=_order)
    balances = _paired_balances(entries)
    out: dict[str, OpeningTxn] = {}
    for entry in openings:
        opened: dict[str, Decimal] = {}
        for posting in entry.postings:
            if posting.account == EQUITY_OPENING or posting.units is None:
                continue
            number = posting.units.number or Decimal(0)
            opened[posting.account] = opened.get(posting.account, Decimal(0)) + number
        meta = entry.meta or {}
        raw_id = meta.get(_TXN_ID_KEY)
        txn_id = str(raw_id) if raw_id else None
        for account, amount in opened.items():
            if account in out:
                continue
            out[account] = OpeningTxn(
                account=account,
                date=entry.date,
                ledger_amount=amount,
                narration=entry.narration or "",
                txn_id=txn_id,
                shared=len(opened) > 1,
                source=_source(entry),
                entry=entry,
                balance=balances.get((account, txn_id)) if txn_id else None,
                start=_start(entry),
            )
    return out


def first_postings(entries: list) -> dict[str, date]:
    """Account -> the earliest date of a posting on it in a transaction that
    is not an opening."""
    out: dict[str, date] = {}
    for entry in entries:
        if not isinstance(entry, data.Transaction) or is_opening_txn(entry):
            continue
        for posting in entry.postings:
            seen = out.get(posting.account)
            if seen is None or entry.date < seen:
                out[posting.account] = entry.date
    return out


def gap_accounts(entries: list, openings: dict[str, OpeningTxn]) -> set[str]:
    """Accounts whose opening carries opening-start and that have a posting,
    in a transaction that is not an opening, dated on or after the opening's
    date and strictly before opening-start: activity the figure (the balance
    as of opening-start) does not cover. A same-day posting counts: the
    figure is as of opening-start, not as of the opening's date. An April-rule opening
    is dated 31 Mar while its statement may start later in April, so rows
    from those days, imported afterwards, would otherwise be counted twice
    without any warning."""
    windows = {
        account: (o.date, o.start)
        for account, o in openings.items()
        if o.start is not None and o.start > o.date
    }
    out: set[str] = set()
    if not windows:
        return out
    for entry in entries:
        if not isinstance(entry, data.Transaction) or is_opening_txn(entry):
            continue
        for posting in entry.postings:
            window = windows.get(posting.account)
            if window is not None and window[0] <= entry.date < window[1]:
                out.add(posting.account)
    return out


def _record(opening: OpeningTxn) -> OpeningRecord:
    signed = opening.ledger_amount.quantize(_MONEY)
    return OpeningRecord(
        date=opening.date,
        amount=str(abs(signed)),
        direction=direction_of(opening.account, signed),
        ledger_amount=str(signed),
        narration=opening.narration,
        txn_id=opening.txn_id,
        shared=opening.shared,
        source=opening.source,
        balance_check=opening.balance is not None,
    )


def account_statuses(entries: list, *, today: date) -> list[OpeningStatusRow]:
    """One row per account opened in the main ledger, not closed, whose kind
    is not None, in kind order then by account.

    status: "opening" when it has an opening transaction; else "zero" when
    its `open` directive carries opening: "zero"; else "missing" when it has
    postings; else "empty".
    warn: true for "missing"; for a late "opening": one dated strictly
    after first_posting (for example a mid-year "Liability Reset" or a pad
    on an account with earlier activity); and for an "opening" with a gap
    (`gap_accounts`: a posting on or after the opening's date but before
    its opening-start). Activity a late or gapped opening does not cover
    has no opening of its own, so the balances around it are unknown; the row
    keeps status "opening" and its record, so it can still be corrected.
    An opening on the same date as first_posting is not late; an opening
    without opening-start (manual, legacy, corrected) never has a gap.
    suggested_date: for a late "opening", the date rule applied to
    first_posting (a date the correction accepts); for any other "opening",
    the opening's own date; for "missing", the date rule applied to
    first_posting; for "empty" and "zero", 31 Mar before the financial year
    containing `today`. april: for "missing" and a late "opening", the date
    rule's flag; for "empty" and "zero", True; for any other "opening",
    whether the opening is dated 31 Mar (for a card: whether the cycle
    starting the day after the opening contains 1 April, the card date
    rule's flag).
    """
    open_meta = open_meta_from_entries(entries)
    closed = closed_accounts_from_entries(entries)
    openings = opening_records(entries)
    firsts = first_postings(entries)
    gaps = gap_accounts(entries, openings)
    names = commodity_names_from_entries(entries)
    default_date = date(fy_start_year(today), 3, 31)

    rows: list[OpeningStatusRow] = []
    for account, meta in open_meta.items():
        kind = opening_kind(account)
        if kind is None or account in closed:
            continue
        opening = openings.get(account)
        first = firsts.get(account)
        status: OpeningStatus
        late = opening is not None and first is not None and opening.date > first
        if opening is not None and late and first is not None:
            # A correction must move the opening before the earlier activity.
            status = "opening"
            suggested, april = opening_date_for(first, kind)
        elif opening is not None:
            status = "opening"
            suggested = opening.date
            # Whether the opening is as of the FY start: for a card, whether
            # the cycle starting the day after it contains 1 April.
            april = (
                opening_date_for(opening.date + timedelta(days=1), kind)[1]
                if kind == "card"
                else (opening.date.month, opening.date.day) == (3, 31)
            )
        elif meta.get(OPENING_OPEN_KEY) == OPENING_ZERO:
            status, suggested, april = "zero", default_date, True
        elif first is not None:
            # With no opening, every posting on the account is outside one,
            # so "has postings" is exactly "has a first posting".
            status = "missing"
            suggested, april = opening_date_for(first, kind)
        else:
            status, suggested, april = "empty", default_date, True
        rows.append(
            OpeningStatusRow(
                account=account,
                account_display=display_account(account, names),
                kind=kind,
                status=status,
                opening=_record(opening) if opening is not None else None,
                first_posting=first,
                suggested_date=suggested,
                april=april,
                warn=status == "missing" or late or account in gaps,
                directions=allowed_directions(account),
            )
        )
    rows.sort(key=lambda r: (KIND_ORDER.index(r.kind), r.account))
    return rows
