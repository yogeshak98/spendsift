"""Mutual-fund opening lots, written to both ledgers under one restore point.

The two ledgers share one `journal/` folder, so one `Ledger.snapshot()` of
it covers both: a failure anywhere after it restores that snapshot, so the
investments and main ledgers are never left out of step.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.position import Cost

from backend.app.core.errors.errors import OPENING_LOT_INVALID, CodedError
from backend.app.models.api.openings import (
    MfOpeningLot,
    MfOpeningRequest,
    MfOpeningResponse,
    MfOpeningWritten,
)
from backend.app.models.domain.accounts import (
    EQUITY_OPENING,
    HOLDING_PREFIX,
    INVESTMENT_MUTUAL_FUND,
    OPERATING_CURRENCY,
    Vehicle,
    holding_account,
)
from backend.app.persistence.journal import Ledger, hold_write_locks
from backend.app.service.ingest.parsers.scheme_master import SchemeMatch
from backend.app.service.ingest.platform_accounts import (
    GROWW,
    platform_clearing_account,
)
from backend.app.service.ledger.openings.keys import OPENING_SOURCE_KEY
from backend.app.service.reports.health.ledger_view import (
    commodity_names_from_entries,
    display_account,
)

_TXN_ID_KEY = "txn-id"
_COMMODITY_KEY = "commodity"
_SOURCE = "manual"
_NARRATION = "Opening lot: {name}"
_UNIT_COST_PLACES = Decimal("1e-8")
_UNITS_PLACES = 4
_COST_PLACES = 2
_MAX_INTEGER_DIGITS = 13
# A magnitude as typed: digits with an optional fraction; no sign, no
# exponent, no separators.
_MAGNITUDE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_SIGNED = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_MF_PREFIX = HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value]


@dataclass(frozen=True)
class _Lot:
    symbol: str
    name: str
    units: Decimal
    unit_cost: Decimal
    cost_booked: Decimal
    date: date


def _invalid(symbol: str, field: str, reason: str) -> CodedError:
    params = {"symbol": symbol, "field": field, "reason": reason}
    return OPENING_LOT_INVALID.exc(params, data=params)


def _parse(symbol: str, field: str, value: str, places: int) -> Decimal:
    """`value` as a Decimal > 0 with at most `places` decimal places, or
    OPENING_LOT_INVALID (E1092) naming `field`."""
    text = value.strip()
    if not _MAGNITUDE.fullmatch(text):
        reason = (
            "must be greater than 0"
            if _SIGNED.fullmatch(text)
            else "is not a decimal number"
        )
        raise _invalid(symbol, field, reason)
    whole, _, fraction = text.partition(".")
    if len(fraction) > places:
        raise _invalid(symbol, field, f"has more than {places} decimal places")
    if len(whole.lstrip("0")) > _MAX_INTEGER_DIGITS:
        raise _invalid(symbol, field, "is too large")
    number = Decimal(text)
    if number == 0:
        raise _invalid(symbol, field, "must be greater than 0")
    return number


def _posted_symbols(investment_entries: list) -> set[str]:
    """Every commodity with a posting in the investments ledger, and every
    scheme whose holding account has one."""
    out: set[str] = set()
    for entry in investment_entries:
        if not isinstance(entry, data.Transaction):
            continue
        for posting in entry.postings:
            if posting.units is not None and posting.units.currency:
                out.add(posting.units.currency)
            if posting.account.startswith(_MF_PREFIX):
                out.add(posting.account.removeprefix(_MF_PREFIX))
    return out


def _check(
    lot: MfOpeningLot,
    *,
    scheme: Callable[[str], SchemeMatch | None],
    posted: set[str],
    today: date,
) -> _Lot:
    """The lot as it will be written, or E1092: symbol (unknown scheme, then
    the scheme already has investments-ledger postings), units, cost, date.
    Every check after the lookup, and the write, use the scheme's own symbol
    ("MF" + AMFI code), whatever form `scheme` accepted (a bare code too)."""
    match = scheme(lot.symbol)
    if match is None:
        raise _invalid(lot.symbol, "symbol", "is not a scheme in the scheme master")
    symbol = match.symbol
    if symbol in posted:
        raise _invalid(
            symbol, "symbol", "already has postings in the investments ledger"
        )
    units = _parse(symbol, "units", lot.units, _UNITS_PLACES)
    cost = _parse(symbol, "cost", lot.cost, _COST_PLACES)
    if lot.date > today:
        raise _invalid(symbol, "date", "is after today")
    unit_cost = (cost / units).quantize(_UNIT_COST_PLACES, rounding=ROUND_HALF_UP)
    return _Lot(
        symbol=symbol,
        name=match.name,
        units=units,
        unit_cost=unit_cost,
        # Exact (not rounded to paise): it is the holding's cost basis, so
        # the main ledger's figure equals the investments ledger's
        # units x unit_cost and Doctor's mf_reconciliation_gap stays unchanged,
        # as the Groww import books its trade value.
        cost_booked=units * unit_cost,
        date=lot.date,
    )


def _new_txn_id() -> str:
    return uuid.uuid4().hex


def _meta(txn_id: str, **extra: str) -> dict:
    meta = data.new_metadata("<opening>", 0)
    meta[_TXN_ID_KEY] = txn_id
    meta[OPENING_SOURCE_KEY] = _SOURCE
    meta.update(extra)
    return meta


def _inr(number: Decimal) -> Amount:
    return Amount(number, OPERATING_CURRENCY)


def _txn(meta: dict, lot: _Lot, postings: list[data.Posting]) -> data.Transaction:
    return data.Transaction(
        meta,
        lot.date,
        "*",
        None,
        _NARRATION.format(name=lot.name),
        data.EMPTY_SET,
        data.EMPTY_SET,
        postings,
    )


def _investment_entry(lot: _Lot, txn_id: str) -> data.Transaction:
    return _txn(
        _meta(txn_id),
        lot,
        [
            data.Posting(
                holding_account(lot.symbol),
                Amount(lot.units, lot.symbol),
                Cost(lot.unit_cost, OPERATING_CURRENCY, lot.date, None),
                None,
                None,
                None,
            ),
            data.Posting(
                platform_clearing_account(GROWW),
                _inr(-lot.cost_booked),
                None,
                None,
                None,
                None,
            ),
        ],
    )


def _main_entry(lot: _Lot, txn_id: str) -> data.Transaction:
    return _txn(
        _meta(txn_id, **{_COMMODITY_KEY: lot.symbol}),
        lot,
        [
            data.Posting(
                INVESTMENT_MUTUAL_FUND, _inr(lot.cost_booked), None, None, None, None
            ),
            data.Posting(
                EQUITY_OPENING, _inr(-lot.cost_booked), None, None, None, None
            ),
        ],
    )


def add_lots(
    req: MfOpeningRequest,
    *,
    ledger: Ledger,
    investment_ledger: Ledger,
    scheme: Callable[[str], SchemeMatch | None],
    today: date,
) -> MfOpeningResponse:
    """Write each lot as a lot with a cost basis, dated its allotment date.

    investments ledger: the holding account, `units SYM {unit_cost INR,
    date}`, against the platform clearing account. main ledger: the mutual
    fund account, `cost_booked INR`, against the opening equity account.
    unit_cost = (cost / units) quantized to 8 places (ROUND_HALF_UP) and
    cost_booked = units x unit_cost (exact, not rounded) in both. One restore
    point covers both ledgers: a failure on either leaves both unchanged.

    The write opens what it posts to, in both ledgers: in the investments
    ledger the scheme's commodity (named with the scheme's name), its holding
    account (FIFO booking, no currency constraint) and the platform clearing
    account; in the main ledger the mutual fund account. Each response row's
    `account` is the holding account, and its `account_display` is resolved
    from the investments ledger's commodity names after the write.

    Several lots of one scheme in one request are allowed (one scheme bought
    on several dates); each lot is its own pair of transactions, with one
    txn-id shared by its two.

    OPENING_LOT_INVALID (E1092): unknown scheme (`scheme(symbol)` is None),
    the scheme already has investments-ledger postings, units or cost <= 0
    or too precise, or the date is after today. Every lot is checked, against
    fresh parses taken under both journals' write locks (held from the
    checks through the last write or the restore), before anything is
    written: a refusal writes nothing and takes no snapshot.
    """
    if ledger.journal_dir.resolve() != investment_ledger.journal_dir.resolve():
        # One snapshot can only restore both when they share journal/.
        raise ValueError("the two ledgers must share one journal folder")
    with hold_write_locks(ledger, investment_ledger):
        posted = _posted_symbols(investment_ledger.load(fresh=True).entries)
        lots = [
            _check(lot, scheme=scheme, posted=posted, today=today) for lot in req.lots
        ]
        ids = [_new_txn_id() for _ in lots]
        inv_entries = [_investment_entry(lot, i) for lot, i in zip(lots, ids)]
        main_entries = [_main_entry(lot, i) for lot, i in zip(lots, ids)]
        snap = ledger.snapshot()
        try:
            investment_ledger.ensure_commodity_accounts(
                [(lot.symbol, lot.name) for lot in lots]
            )
            investment_ledger.append(inv_entries)
            ledger.append(main_entries)
        except Exception:
            ledger.restore(snap)
            raise
        names = commodity_names_from_entries(investment_ledger.load(fresh=True).entries)
    return MfOpeningResponse(
        written=[
            MfOpeningWritten(
                symbol=lot.symbol,
                name=lot.name,
                account=holding_account(lot.symbol),
                account_display=display_account(holding_account(lot.symbol), names),
                units=str(lot.units),
                unit_cost=str(lot.unit_cost),
                cost_booked=str(lot.cost_booked),
                date=lot.date,
                txn_id=txn_id,
            )
            for lot, txn_id in zip(lots, ids)
        ],
        snapshot=snap.name,
    )


def schemes_with_opening_lots(investment_entries: list) -> frozenset[str]:
    """The commodity symbols that have an opening lot in the investments
    ledger (for the import review's MF_OPENING_FLAG): a transaction carrying
    opening-source metadata with a mutual-fund holding posting."""
    return frozenset(
        posting.units.currency
        for entry in investment_entries
        if isinstance(entry, data.Transaction) and OPENING_SOURCE_KEY in entry.meta
        for posting in entry.postings
        if posting.units is not None
        and posting.account == holding_account(posting.units.currency)
    )
