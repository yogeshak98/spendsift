"""Adapter: ledger-cli `ParsedTxn`s with an MF (priced, non-INR) leg ->
synthetic `CandidateTxn`s, so `LedgerMergeService.convert_batch_split` can
reuse `backend.app.service.ingest.pipeline.entry_writer`'s already-decided `_subsystem_entry`/
`_vehicle_entry`/`_consume_from_lots`/`_classify_lot`/`_bucket_gains` split
machinery instead of re-implementing FIFO/gains booking a second time.

The partitioning rule: a ParsedTxn is routed here when it carries a priced,
non-INR leg; everything else stays on the ordinary merge path.
"""

from __future__ import annotations

from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import _current_lots
from backend.app.models.domain.ledger_cli import (
    ParsedLedger,
    ParsedPosting,
    ParsedTxn,
    UnparsedLine,
)
from backend.app.models.domain.accounts import OPERATING_CURRENCY
from backend.app.service.ingest.platform_accounts import (
    GROWW,
    platform_clearing_account,
)
from backend.app.persistence.journal import Ledger


MULTI_LEG_REASON = (
    "multiple commodity legs in one transaction — not supported by the "
    "investment-ledger split; split into separate transactions in the "
    "source file first"
)


def _is_mf_leg(p: ParsedPosting) -> bool:
    """A leg qualifies as MF exactly as `ledger_merge_service._posting_account`
    already tests it."""
    return (
        p.cost is not None
        and p.commodity is not None
        and p.commodity != OPERATING_CURRENCY
    )


def _map_extra_posting(p: ParsedPosting, account_map: dict[str, str]) -> data.Posting:
    """Map a non-MF, non-resolved-cash posting verbatim (elided -> amountless;
    plain INR -> priced posting), for pass-through as an `extra_postings` leg
    on the vehicle entry."""
    account = account_map[p.account]
    if p.amount is None:
        return data.Posting(account, None, None, None, None, None)
    return data.Posting(
        account,
        Amount(p.amount, p.commodity or OPERATING_CURRENCY),
        None,
        None,
        None,
        None,
    )


def mf_leg_to_candidate(
    txn: ParsedTxn,
    mf_leg: ParsedPosting,
    account_map: dict[str, str],
    commodity_map: dict[str, str],
) -> tuple[CandidateTxn, list[data.Posting]]:
    """Build a synthetic CandidateTxn from one MF-bearing ParsedPosting plus
    the transaction's other postings, for reuse with entry_writer.py's
    _subsystem_entry/_vehicle_entry.

    Returns (candidate, extra_vehicle_postings) -- extra_vehicle_postings is
    every OTHER posting in `txn` beyond the resolved cash leg(s) (elided
    legs, additional explicit legs), converted via the existing per-posting
    mapping and passed through verbatim for
    `_vehicle_entry(..., extra_postings=extra_vehicle_postings)`.
    """
    assert mf_leg.commodity is not None and mf_leg.amount is not None
    assert mf_leg.cost is not None
    symbol = commodity_map[mf_leg.commodity]
    units = abs(mf_leg.amount)
    unit_cost = mf_leg.cost
    direction = Direction.DEBIT if mf_leg.amount >= 0 else Direction.CREDIT

    other_postings = [p for p in txn.postings if p is not mf_leg]
    cash_postings = [p for p in other_postings if p.amount is not None]
    extra_parsed = [p for p in other_postings if p.amount is None]

    if cash_postings:
        amount = sum(
            (abs(p.amount) for p in cash_postings if p.amount is not None), Decimal(0)
        )
        source_account = account_map[cash_postings[0].account]
    else:
        # No other non-elided posting (the cash leg is itself elided, or
        # there genuinely is none besides the commodity leg): assume no
        # charge signal is extractable and let the pass-through elided leg
        # absorb whatever beancount computes at load time. This is a safe,
        # explicit default -- it produces gap == 0 in compute_charges, so no
        # spurious charge leg is invented.
        amount = units * unit_cost
        # A placeholder, never actually posted -- CandidateTxn.source_account
        # exists only because the pydantic model requires the field; neither
        # _subsystem_entry nor _vehicle_entry reads it. Reusing the configured
        # subsystem clearing account here is purely cosmetic (keeps the
        # placeholder recognizably investment-related).
        source_account = platform_clearing_account(GROWW)

    candidate = CandidateTxn(
        id=txn.id,
        date=txn.date,
        amount=amount,
        raw_narration=txn.narration,
        normalized_narration=txn.narration,
        source_account=source_account,
        direction=direction,
        units=units,
        commodity=symbol,
        unit_cost=unit_cost,
    )
    extra_postings = [_map_extra_posting(p, account_map) for p in extra_parsed]
    return candidate, extra_postings


def partition_mf_transactions(
    parsed: ParsedLedger, commodity_map: dict[str, str]
) -> tuple[list[ParsedTxn], list[tuple[ParsedTxn, ParsedPosting]], list[UnparsedLine]]:
    """Classify every `ParsedTxn` by its MF (priced, non-INR) leg count.

    Returns (zero_mf_leg_txns, single_mf_leg_pairs, multi_leg_unparsed)."""
    zero_leg: list[ParsedTxn] = []
    single_leg: list[tuple[ParsedTxn, ParsedPosting]] = []
    multi_leg_unparsed: list[UnparsedLine] = []
    for txn in parsed.transactions:
        mf_legs = [p for p in txn.postings if _is_mf_leg(p)]
        if not mf_legs:
            zero_leg.append(txn)
        elif len(mf_legs) == 1:
            single_leg.append((txn, mf_legs[0]))
        else:
            multi_leg_unparsed.append(
                UnparsedLine(txn.source_line, txn.narration, MULTI_LEG_REASON)
            )
    return zero_leg, single_leg, multi_leg_unparsed


def _current_investment_lots(investment_ledger: Ledger, symbol: str) -> list[list]:
    """Thin wrapper delegating to `entry_writer.py::_current_lots` -- already a free
    function taking (investment_ledger, symbol), no `self`, genuinely
    reusable with zero changes."""
    return _current_lots(investment_ledger, symbol)
