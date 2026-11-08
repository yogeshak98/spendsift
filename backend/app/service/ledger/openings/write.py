"""Approved opening writes: add, mark zero, correct.

Each write snapshots the journal first, validates after and restores on any
failure; nothing is written on any error. Every check runs before that
snapshot, against a fresh parse taken under the journal write lock, and the
lock is held from that parse through the write, so a concurrent write can
never make a check stale (two adds on one account: one writes, the other is
OPENING_EXISTS).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from beancount.core import data
from beancount.core.amount import Amount

from backend.app.core.errors.errors import (
    CodedError,
    ACCOUNT_CLOSED,
    ACCOUNT_NOT_OPEN,
    OPENING_AMOUNT_INVALID,
    OPENING_DATE_INVALID,
    OPENING_EXISTS,
    OPENING_NOT_APPLICABLE,
    OPENING_STALE,
    UNKNOWN_BATCH,
)
from backend.app.models.api.openings import (
    OpeningCorrectRequest,
    OpeningCreateRequest,
    OpeningDirection,
    OpeningKind,
    OpeningSource,
    OpeningSuggestion,
    OpeningWriteResponse,
    OpeningZeroRequest,
    OpeningZeroResponse,
)
from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.models.domain.accounts import EQUITY_OPENING, OPERATING_CURRENCY
from backend.app.models.storage.statement_record import StatementSummary
from backend.app.persistence.journal import Ledger
from backend.app.persistence.review_batches import ReviewStore
from backend.app.service.ledger.account_meta import AccountMetaService
from backend.app.service.ledger.openings.classify import (
    allowed_directions,
    ledger_amount,
    opening_kind,
)
from backend.app.service.ledger.openings.detect import (
    OpeningTxn,
    account_statuses,
    first_postings,
    is_opening_txn,
    opening_records,
)
from backend.app.service.ledger.openings.keys import (
    OPENING_BASIS_KEY,
    OPENING_CORRECTED_KEY,
    OPENING_NARRATION,
    OPENING_SOURCE_KEY,
    OPENING_START_KEY,
    OPENING_TXN_KEY,
)
from backend.app.service.ledger.openings.suggest import batch_suggestions
from backend.app.service.reports.health.ledger_view import (
    closed_accounts_from_entries,
    commodity_names_from_entries,
    display_account,
    open_meta_from_entries,
)

_TXN_ID_KEY = "txn-id"
_SOURCE_META = ("filename", "lineno")
_MONEY = Decimal("0.01")
# The basis of a figure the user typed (or edited away from the suggestion).
MANUAL_BASIS = "entered by the user"
BASIS_MAX = 200
# A magnitude as typed: digits with an optional fraction; no sign, no
# exponent, no separators. A signed ledger amount may lead with a minus.
_MAGNITUDE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_SIGNED = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_MAX_INTEGER_DIGITS = 13
_VALUE_ECHO_MAX = 40
_AFTER_TODAY = "it is after today"
_NOT_BEFORE_FIRST = "it is not before the account's first posting"
_NOT_BEFORE_BATCH = "it is not before the batch's earliest row for the account"
_NOT_BEFORE_OTHER = "it is not before another opening entry on the account,"
# beancount's default tolerance for a 2-decimal INR balance assertion.
_BALANCE_TOLERANCE = Decimal("0.005")


# ── checks ───────────────────────────────────────────────────────────────────
def _check_account(
    entries: list, account: str, direction: OpeningDirection | None
) -> OpeningKind:
    """ACCOUNT_NOT_OPEN (E1039), ACCOUNT_CLOSED (E1084), then
    OPENING_NOT_APPLICABLE (E1090) for the kind, then (with `direction`)
    for the direction. Returns the kind."""
    params = {"account": account}
    if account not in open_meta_from_entries(entries):
        raise ACCOUNT_NOT_OPEN.exc(params, data=params)
    if account in closed_accounts_from_entries(entries):
        raise ACCOUNT_CLOSED.exc(params, data=params)
    kind = opening_kind(account)
    if kind is None:
        raise _not_applicable(account, "it never takes an opening balance")
    if direction is not None and direction not in allowed_directions(account):
        raise _not_applicable(
            account, f"the direction {direction} is not allowed for it"
        )
    return kind


def _not_applicable(account: str, reason: str) -> CodedError:
    params = {"account": account, "reason": reason}
    return OPENING_NOT_APPLICABLE.exc(params, data=params)


def _invalid_amount(field: str, value: str, reason: str) -> CodedError:
    echo = value if len(value) <= _VALUE_ECHO_MAX else value[:_VALUE_ECHO_MAX] + "..."
    params = {"field": field, "value": echo, "reason": reason}
    return OPENING_AMOUNT_INVALID.exc(params, data=params)


def _parse_amount(field: str, value: str, *, signed: bool) -> Decimal:
    """`value` as a Decimal of at most 2 decimal places, or
    OPENING_AMOUNT_INVALID (E1093) naming `field`. A magnitude (`signed`
    False) must not be negative; zero is allowed (a statement can print a
    0.00 opening, e.g. a card paid in full)."""
    text = value.strip()
    if not (_SIGNED if signed else _MAGNITUDE).fullmatch(text):
        reason = (
            "must not be negative"
            if not signed and _SIGNED.fullmatch(text)
            else "is not a decimal number"
        )
        raise _invalid_amount(field, value, reason)
    whole, _, fraction = text.lstrip("-").partition(".")
    if len(fraction) > 2:
        raise _invalid_amount(field, value, "has more than 2 decimal places")
    if len(whole.lstrip("0")) > _MAX_INTEGER_DIGITS:
        raise _invalid_amount(field, value, "is too large")
    return Decimal(text).quantize(_MONEY)


def _check_date(
    account: str,
    when: date,
    *,
    today: date,
    first_posting: date | None,
    batch_first: date | None = None,
    other_opening: date | None = None,
) -> None:
    """OPENING_DATE_INVALID (E1089): after today (limit today; today itself
    is allowed); otherwise not strictly before first_posting or the batch's
    earliest row for the account, or another opening-shaped entry on the
    account; limit = the earliest such blocking date."""
    if when > today:
        raise _invalid_date(account, when, today, _AFTER_TODAY)
    limits: list[tuple[date | None, str]] = [
        (first_posting, _NOT_BEFORE_FIRST),
        (batch_first, _NOT_BEFORE_BATCH),
    ]
    if other_opening is not None:
        limits.append((other_opening, f"{_NOT_BEFORE_OTHER} dated {other_opening}"))
    blocking = [
        (limit, reason)
        for limit, reason in limits
        if limit is not None and when >= limit
    ]
    if blocking:
        limit, reason = min(blocking, key=lambda b: b[0])
        raise _invalid_date(account, when, limit, reason)


def _invalid_date(account: str, when: date, limit: date, reason: str) -> CodedError:
    params = {
        "account": account,
        "date": when.isoformat(),
        "limit": limit.isoformat(),
        "reason": reason,
    }
    return OPENING_DATE_INVALID.exc(params, data=params)


def _money(value: Decimal) -> str:
    return str(value.quantize(_MONEY))


def _exists(opening: OpeningTxn) -> CodedError:
    params = {
        "account": opening.account,
        "date": opening.date.isoformat(),
        "ledger_amount": _money(opening.ledger_amount),
    }
    return OPENING_EXISTS.exc(params, data=params)


def _stale(account: str) -> CodedError:
    params = {"account": account}
    return OPENING_STALE.exc(params, data=params)


# ── entries ──────────────────────────────────────────────────────────────────
def clean_basis(text: str) -> str:
    """`text` fit for the opening-basis metadata: quotes and backslashes
    dropped, line breaks and other control characters turned into spaces,
    runs of spaces collapsed, cut to BASIS_MAX characters."""
    kept = "".join(
        " " if not ch.isprintable() else ch for ch in text if ch not in "\"'\\"
    )
    return " ".join(kept.split())[:BASIS_MAX].rstrip()


def _new_txn_id() -> str:
    return uuid.uuid4().hex


def _opening_meta(
    txn_id: str,
    source: OpeningSource,
    basis: str,
    corrected: date | None,
    keep: dict | None = None,
    start: date | None = None,
) -> dict:
    """An opening's metadata: `keep` (an earlier version's keys, source
    position and opening-start aside) with txn-id, opening-source,
    opening-basis, opening-start when `start` is given and, for a
    correction, opening-corrected set. A correction is the user's own
    figure, so it never keeps the statement's opening-start."""
    meta = data.new_metadata("<opening>", 0)
    for key, value in (keep or {}).items():
        if key not in _SOURCE_META and key != OPENING_START_KEY:
            meta[key] = value
    meta[_TXN_ID_KEY] = txn_id
    meta[OPENING_SOURCE_KEY] = source
    meta[OPENING_BASIS_KEY] = clean_basis(basis)
    if start is not None:
        meta[OPENING_START_KEY] = start.isoformat()
    if corrected is not None:
        meta[OPENING_CORRECTED_KEY] = corrected.isoformat()
    return meta


def _units(number: Decimal) -> Amount:
    """`number` INR; a zero is written unsigned (never -0.00)."""
    return Amount(abs(number) if number == 0 else number, OPERATING_CURRENCY)


def _new_opening(
    account: str,
    when: date,
    signed: Decimal,
    txn_id: str,
    source: OpeningSource,
    basis: str,
    corrected: date | None = None,
    start: date | None = None,
) -> data.Transaction:
    postings = [
        data.Posting(account, _units(signed), None, None, None, None),
        data.Posting(EQUITY_OPENING, _units(-signed), None, None, None, None),
    ]
    return data.Transaction(
        _opening_meta(txn_id, source, basis, corrected, start=start),
        when,
        "*",
        None,
        OPENING_NARRATION,
        data.EMPTY_SET,
        data.EMPTY_SET,
        postings,
    )


def _paired_balance(
    account: str, opening_date: date, signed: Decimal, txn_id: str
) -> data.Balance:
    """The optional assertion: beancount checks a balance at the start of
    its day, so on the day after the opening it sees the opening alone."""
    meta = data.new_metadata("<opening>", 0)
    meta[OPENING_TXN_KEY] = txn_id
    return data.Balance(
        meta, opening_date + timedelta(days=1), account, _units(signed), None, None
    )


def _repost(
    entry: data.Transaction, account: str, new: Decimal | None
) -> list[data.Posting]:
    """`entry`'s postings with the account's posting(s) replaced by one at
    `new` (in the first one's place) or removed (`new` None), and the first
    opening-equity posting moved by the opposite of the change, so the
    entry still balances and every other posting is kept as it is."""
    old = sum(
        (
            p.units.number
            for p in entry.postings
            if p.account == account and p.units is not None and p.units.number
        ),
        Decimal(0),
    )
    delta = (new if new is not None else Decimal(0)) - old
    out: list[data.Posting] = []
    placed = equity_moved = False
    for posting in entry.postings:
        if posting.account == account:
            if new is not None and not placed:
                out.append(posting._replace(units=_units(new)))
                placed = True
            continue
        if posting.account == EQUITY_OPENING and not equity_moved:
            number = posting.units.number if posting.units is not None else None
            if not isinstance(number, Decimal):
                raise ValueError("the opening's equity posting has no amount")
            out.append(posting._replace(units=_units(number - delta)))
            equity_moved = True
            continue
        out.append(posting)
    if not equity_moved:
        raise ValueError("the opening has no opening-equity posting")
    return out


# ── writes ───────────────────────────────────────────────────────────────────
def _source_and_basis(
    signed: Decimal, offered: Sequence[OpeningSuggestion | None]
) -> tuple[OpeningSource, str]:
    """The first offered suggestion whose ledger amount is the one written
    gives the source and its arithmetic; anything else was typed."""
    for suggestion in offered:
        if suggestion is not None and Decimal(suggestion.ledger_amount) == signed:
            return suggestion.source, suggestion.arithmetic
    return "manual", MANUAL_BASIS


def add(
    req: OpeningCreateRequest,
    *,
    ledger: Ledger,
    review: ReviewStore,
    meta: AccountMetaService,
    today: date,
) -> OpeningWriteResponse:
    """Append the opening transaction (against the opening equity account,
    narration OPENING_NARRATION, with a txn-id, opening-source and
    opening-basis metadata) and, with `balance_check`, a `balance` directive
    dated the day after carrying opening-txn, in one `Ledger.append`: one
    snapshot. A started-at-zero mark on the account is left in place: an
    opening takes precedence over it in the statuses, and
    POST /api/openings/zero {zero: false} clears it (`meta` is not written).

    Source: with `batch_id`, when the request's ledger amount equals the
    prompt suggestion's (or an alternative's) `ledger_amount`, the source is
    that suggestion's `source` and the basis is its `arithmetic`. Otherwise
    the source is `manual` (basis "entered by the user"). The basis is
    `clean_basis`'d. A "statement" or "derived" opening also carries
    opening-start: the date the batch's rows for the account start on (the
    card statement's period start, or the batch's earliest row for the
    account when that is earlier; else that earliest row), so detect can
    warn when earlier rows imported later fall between the opening's date
    and that start.

    Checks, in order: ACCOUNT_NOT_OPEN (E1039), ACCOUNT_CLOSED (E1084),
    OPENING_NOT_APPLICABLE (E1090) for the kind, then for the direction,
    OPENING_AMOUNT_INVALID (E1093), OPENING_EXISTS (E1088), UNKNOWN_BATCH
    (E1006, with batch_id), OPENING_DATE_INVALID (E1089: after today, not
    before first_posting, or not before the batch's earliest row for the
    account).

    E1089 `limit`: today when the date is after today (today itself is
    allowed); otherwise the earliest blocking date, first_posting or the
    batch's earliest row for the account (the date must be before it).
    `reason` is "it is after today", "it is not before the account's first
    posting" or "it is not before the batch's earliest row for the account".

    Every check reads a fresh parse under the journal write lock, held
    through the append.
    """
    del meta  # The zero mark stays (see above): nothing to write there.
    account = req.account
    with ledger.write_lock():
        entries = ledger.load(fresh=True).entries
        kind = _check_account(entries, account, req.direction)
        magnitude = _parse_amount("amount", req.amount, signed=False)
        signed = ledger_amount(account, magnitude, req.direction)
        existing = opening_records(entries).get(account)
        if existing is not None:
            raise _exists(existing)
        batch_first: date | None = None
        statement_start: date | None = None
        offered: list[OpeningSuggestion | None] = []
        if req.batch_id is not None:
            try:
                rows = review.get_batch(req.batch_id)
            except KeyError:
                raise UNKNOWN_BATCH.exc() from None
            dates = [
                c.date
                for c in rows
                if not c.needs_account and c.source_account == account
            ]
            batch_first = min(dates) if dates else None
            statement = review.statement(req.batch_id)
            statement_start = _statement_start(account, statement, batch_first)
            suggestion, alternatives, _ = batch_suggestions(
                account,
                kind,
                statement=statement,
                evidence=review.opening_evidence(req.batch_id),
            )
            offered = [suggestion, *alternatives]
        _check_date(
            account,
            req.date,
            today=today,
            first_posting=first_postings(entries).get(account),
            batch_first=batch_first,
        )
        source, basis = _source_and_basis(signed, offered)
        start = statement_start if source in ("statement", "derived") else None
        txn_id = _new_txn_id()
        written: list[data.Directive] = [
            _new_opening(account, req.date, signed, txn_id, source, basis, start=start)
        ]
        if req.balance_check:
            written.append(_paired_balance(account, req.date, signed, txn_id))
        snapshot = ledger.append(written)
        names = commodity_names_from_entries(entries)
    if snapshot is None:  # Unreachable: `written` is never empty.
        raise RuntimeError("the opening append took no snapshot")
    return OpeningWriteResponse(
        account=account,
        account_display=display_account(account, names),
        txn_id=txn_id,
        date=req.date,
        ledger_amount=_money(signed),
        source=source,
        balance_check=req.balance_check,
        snapshot=snapshot.name,
    )


def _statement_start(
    account: str, statement: StatementSummary | None, batch_first: date | None
) -> date | None:
    """The date the batch's evidence for `account` starts on: the card
    statement's period start when the statement is this account's (or the
    batch's earliest row for it, when a row is dated before the period),
    else the batch's earliest row for the account; None with neither."""
    starts = [batch_first] if batch_first is not None else []
    if statement is not None and statement.account == account:
        starts.append(statement.period_start)
    return min(starts, default=None)


def mark_zero(
    req: OpeningZeroRequest, *, ledger: Ledger, meta: AccountMetaService
) -> OpeningZeroResponse:
    """Set (zero: true) or remove (zero: false) opening: "zero" on the
    account's `open` directive through `AccountMetaService.set_opening_zero`
    (`Ledger.set_open_meta`: one snapshot, none when nothing changes), and
    return the account's resulting status.

    Checks, in order, under the journal write lock: ACCOUNT_NOT_OPEN
    (E1039), ACCOUNT_CLOSED (E1084), OPENING_NOT_APPLICABLE (E1090), then,
    for zero: true, OPENING_EXISTS (E1088) when the account has an opening.
    Removing the mark from an account with an opening is allowed: it clears
    a mark the opening superseded."""
    account = req.account
    with ledger.write_lock():
        entries = ledger.load(fresh=True).entries
        _check_account(entries, account, None)
        existing = opening_records(entries).get(account)
        if req.zero and existing is not None:
            raise _exists(existing)
        try:
            meta.set_opening_zero(account, req.zero)
        except LookupError:  # Unreachable under the lock: checked above.
            params = {"account": account}
            raise ACCOUNT_NOT_OPEN.exc(params, data=params) from None
        after = ledger.load(fresh=True).entries
    # The status never depends on `today` (only suggested_date does, and it
    # is not returned), so any date serves.
    row = next(
        r for r in account_statuses(after, today=date.max) if r.account == account
    )
    return OpeningZeroResponse(
        account=account, account_display=row.account_display, status=row.status
    )


def correct(
    req: OpeningCorrectRequest, *, ledger: Ledger, today: date
) -> OpeningWriteResponse:
    """Rewrite the account's opening (`Ledger.replace_entries`, one
    snapshot): its posting, date and paired `balance` directive. A
    correction on a shared opening always splits this account out into its
    own opening, whatever the date; the other accounts' postings, and the
    shared entry's source and basis, are untouched.

    - Single-account opening: the same transaction (narration, payee, tags,
      links, other metadata) with the new date and amount, its txn-id kept
      (a new one if it had none), opening-source "manual", opening-basis
      "entered by the user" and opening-corrected today. Its paired balance
      is rewritten (day after the new date, new amount), removed, or added
      according to `balance_check`.
    - Shared opening (same date or not): the account's posting removed from
      the shared transaction (the first opening-equity posting moved back by
      the removed amount, its metadata untouched), its paired balance
      removed, and a new single-account opening (new txn-id, source
      "manual", opening-corrected today, with its own paired balance when
      `balance_check`) appended.

    Source: always `manual`, with opening-corrected set to today; any
    opening-start is dropped (the corrected figure is the user's own).

    Checks, in order: ACCOUNT_NOT_OPEN (E1039), ACCOUNT_CLOSED (E1084),
    OPENING_NOT_APPLICABLE (E1090), OPENING_AMOUNT_INVALID (E1093, `amount`
    then `expected.ledger_amount`), OPENING_STALE (E1091: no opening, then
    `expected` differs from the current opening), OPENING_DATE_INVALID
    (E1089: after today, limit = today; or, for a date other than the
    opening's current one, not before first_posting, limit =
    first_posting; or not before another opening-shaped entry on the
    account, limit = that entry's date, so the corrected entry stays the
    account's opening), ENTRY_NOT_LOCATABLE (E1047, from replace_entries,
    before its snapshot). Every check reads a fresh parse
    (`Ledger.load(fresh=True)`) under the journal write lock, held through
    the rewrite.

    A write that fails validation is restored (one restore snapshot stays).
    When it failed because a `balance` assertion on the account no longer
    holds, that is OPENING_DATE_INVALID (E1089) naming the assertion's date
    (limit) and asking for it to be fixed or removed first; any other
    validation failure is re-raised as is.
    """
    account = req.account
    with ledger.write_lock():
        entries = ledger.load(fresh=True).entries
        _check_account(entries, account, req.direction)
        magnitude = _parse_amount("amount", req.amount, signed=False)
        expected = _parse_amount(
            "expected.ledger_amount", req.expected.ledger_amount, signed=True
        )
        signed = ledger_amount(account, magnitude, req.direction)
        opening = opening_records(entries).get(account)
        if opening is None:
            raise _stale(account)
        current = opening.ledger_amount.quantize(_MONEY)
        if opening.date != req.expected.date or current != expected:
            raise _stale(account)
        # An unchanged date is the user's existing judgement: a legacy
        # opening dated on (or after) its first posting can still have its
        # amount corrected without being moved.
        _check_date(
            account,
            req.date,
            today=today,
            first_posting=None
            if req.date == opening.date
            else first_postings(entries).get(account),
            other_opening=_other_opening_date(entries, opening),
        )
        replacements, appended, txn_id = _correction(opening, req, signed, today)
        try:
            snapshot = ledger.replace_entries(replacements, append=appended)
        except LedgerValidationError as exc:
            failed = _failed_assertion(exc, entries, opening, req.date, signed)
            if failed is None:
                raise
            raise _invalid_date(
                account,
                req.date,
                failed,
                f"a balance assertion on {account} dated {failed.isoformat()} "
                "no longer holds; fix or remove it first",
            ) from exc
        names = commodity_names_from_entries(entries)
    return OpeningWriteResponse(
        account=account,
        account_display=display_account(account, names),
        txn_id=txn_id,
        date=req.date,
        ledger_amount=_money(signed),
        source="manual",
        balance_check=req.balance_check,
        snapshot=snapshot.name,
    )


def _correction(
    opening: OpeningTxn, req: OpeningCorrectRequest, signed: Decimal, today: date
) -> tuple[
    list[tuple[data.Directive, list[data.Directive]]], list[data.Directive], str
]:
    """(replacements, appended, the account's opening txn-id after it) for
    one `replace_entries` call."""
    old = opening.entry
    replacements: list[tuple[data.Directive, list[data.Directive]]] = []
    appended: list[data.Directive] = []
    if opening.shared:
        remaining = old._replace(postings=_repost(old, req.account, None))
        replacements.append((old, [remaining]))
        if opening.balance is not None:
            replacements.append((opening.balance, []))
        txn_id = _new_txn_id()
        appended.append(
            _new_opening(
                req.account, req.date, signed, txn_id, "manual", MANUAL_BASIS, today
            )
        )
        if req.balance_check:
            appended.append(_paired_balance(req.account, req.date, signed, txn_id))
        return replacements, appended, txn_id

    # Only a single-account opening is rewritten in place.
    assert not opening.shared
    txn_id = opening.txn_id or _new_txn_id()
    rewritten = old._replace(
        meta=_opening_meta(txn_id, "manual", MANUAL_BASIS, today, keep=old.meta),
        date=req.date,
        postings=_repost(old, req.account, signed),
    )
    replacements.append((old, [rewritten]))
    balance: list[data.Directive] = (
        [_paired_balance(req.account, req.date, signed, txn_id)]
        if req.balance_check
        else []
    )
    if opening.balance is not None:
        replacements.append((opening.balance, balance))
    else:
        appended.extend(balance)
    return replacements, appended, txn_id


def _other_opening_date(entries: list, opening: OpeningTxn) -> date | None:
    """The earliest opening-shaped entry on the account other than its
    opening: a correction dated on or after it would hand the account's
    opening to that entry (an equal date is settled by line number, across
    files)."""
    dates = [
        e.date
        for e in entries
        if isinstance(e, data.Transaction)
        and e is not opening.entry
        and is_opening_txn(e)
        and any(p.account == opening.account for p in e.postings)
    ]
    return min(dates, default=None)


def _failed_assertion(
    exc: LedgerValidationError,
    entries: list,
    opening: OpeningTxn,
    when: date,
    signed: Decimal,
) -> date | None:
    """The date of the earliest `balance` assertion on the account (other
    than the opening's own paired one, which the correction rewrites) that
    the corrected opening breaks, when validation failed on a balance of
    this account; else None.

    Replays the account's postings from the pre-write `entries`, with the
    opening's contribution moved to `signed` on `when`."""
    marker = f"Balance failed for '{opening.account}'"
    if not any(marker in message for message in exc.messages):
        return None
    # Every posting on the account but the opening's own, then the corrected one.
    postings = [
        (e.date, p.units.number)
        for e in entries
        if isinstance(e, data.Transaction) and e is not opening.entry
        for p in e.postings
        if p.account == opening.account and p.units is not None
    ]
    postings.append((when, signed))
    assertions = sorted(
        (
            e
            for e in entries
            if isinstance(e, data.Balance)
            and e.account == opening.account
            and e is not opening.balance
        ),
        key=lambda b: b.date,
    )
    for assertion in assertions:
        total = sum(
            (number for day, number in postings if day < assertion.date), Decimal(0)
        )
        if abs(total - assertion.amount.number) > _BALANCE_TOLERANCE:
            return assertion.date
    return None
