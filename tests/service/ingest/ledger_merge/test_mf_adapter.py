from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import Direction
from backend.app.models.domain.ledger_cli import ParsedLedger, ParsedPosting, ParsedTxn
from backend.app.service.ingest.ledger_merge.mf_adapter import (
    MULTI_LEG_REASON,
    mf_leg_to_candidate,
    partition_mf_transactions,
)

SOURCE_ACCOUNT = "Assets:Checking:HDFC"
ACCOUNT_MAP = {
    SOURCE_ACCOUNT: SOURCE_ACCOUNT,
    "Equity:Opening-Balances": "Equity:Opening-Balances",
}
COMMODITY_MAP = {"AcmeELSS": "ACMEELSS", "FundA": "FUNDA", "FundB": "FUNDB"}


def _mf_posting(
    amount: str, cost: str = "80.00", comment: str | None = None
) -> ParsedPosting:
    return ParsedPosting(
        account="Assets:Investment:MutualFund:Groww:AcmeELSS",
        amount=Decimal(amount),
        commodity="AcmeELSS",
        cost=Decimal(cost),
        cost_currency="INR",
        comment=comment,
    )


def _cash_posting(account: str, amount: str) -> ParsedPosting:
    return ParsedPosting(
        account=account,
        amount=Decimal(amount),
        commodity="INR",
        cost=None,
        cost_currency=None,
        comment=None,
    )


def _elided_posting(account: str) -> ParsedPosting:
    return ParsedPosting(
        account=account,
        amount=None,
        commodity=None,
        cost=None,
        cost_currency=None,
        comment=None,
    )


def test_single_mf_leg_buy_candidate_fields():
    """A single-MF-leg buy ParsedTxn -> a CandidateTxn matching its fields."""
    txn = ParsedTxn(
        date=date(2024, 1, 5),
        narration="Buy units",
        postings=[
            _mf_posting("742.61", "83.48"),
            _cash_posting(SOURCE_ACCOUNT, "-61993.69"),
        ],
        source_line=1,
    )
    candidate, extra = mf_leg_to_candidate(
        txn, txn.postings[0], ACCOUNT_MAP, COMMODITY_MAP
    )
    assert candidate.id == txn.id
    assert candidate.date == date(2024, 1, 5)
    assert candidate.commodity == "ACMEELSS"
    assert candidate.units == Decimal("742.61")
    assert candidate.unit_cost == Decimal("83.48")
    assert candidate.direction == Direction.DEBIT
    assert candidate.amount == Decimal("61993.69")
    assert extra == []


def test_single_mf_leg_sell_with_extra_elided_leg_preserved():
    """A single-MF-leg sell with an extra elided leg -> that leg appears
    verbatim on the vehicle entry via extra_postings, not dropped."""
    mf_leg = _mf_posting("-50.00", "90.00")
    cash_leg = _cash_posting(SOURCE_ACCOUNT, "4400.00")
    elided_leg = _elided_posting("Equity:Opening-Balances")
    txn = ParsedTxn(
        date=date(2024, 6, 1),
        narration="Sell some ACMEELSS",
        postings=[mf_leg, cash_leg, elided_leg],
        source_line=10,
    )
    candidate, extra = mf_leg_to_candidate(txn, mf_leg, ACCOUNT_MAP, COMMODITY_MAP)
    assert candidate.direction == Direction.CREDIT
    assert candidate.units == Decimal("50.00")
    assert candidate.unit_cost == Decimal("90.00")
    assert candidate.amount == Decimal("4400.00")
    assert len(extra) == 1
    assert extra[0].account == "Equity:Opening-Balances"
    assert extra[0].units is None  # elided -> amountless posting, preserved verbatim


def test_single_mf_leg_no_other_posting_falls_back_to_units_times_cost():
    """No other non-elided posting: amount = units * unit_cost, no charge signal
    invented (documented safe default)."""
    mf_leg = _mf_posting("10.00", "100.00")
    elided_leg = _elided_posting("Equity:Opening-Balances")
    txn = ParsedTxn(
        date=date(2024, 2, 1),
        narration="Buy, cash elided",
        postings=[mf_leg, elided_leg],
        source_line=5,
    )
    candidate, extra = mf_leg_to_candidate(txn, mf_leg, ACCOUNT_MAP, COMMODITY_MAP)
    assert candidate.amount == Decimal("1000.00")
    assert len(extra) == 1
    assert extra[0].account == "Equity:Opening-Balances"


def test_two_mf_legs_land_in_multi_leg_unparsed():
    """A ParsedTxn with two or more commodity legs is surfaced in
    multi_leg_unparsed with the documented reason string, never silently
    split or dropped."""
    leg_a = ParsedPosting(
        account="Assets:Investment:MutualFund:Groww:FundA",
        amount=Decimal("-10.00"),
        commodity="FundA",
        cost=Decimal("100.00"),
        cost_currency="INR",
        comment=None,
    )
    leg_b = ParsedPosting(
        account="Assets:Investment:MutualFund:Groww:FundB",
        amount=Decimal("10.00"),
        commodity="FundB",
        cost=Decimal("100.00"),
        cost_currency="INR",
        comment=None,
    )
    txn = ParsedTxn(
        date=date(2024, 2, 1),
        narration="Switch funds",
        postings=[leg_a, leg_b],
        source_line=20,
    )
    parsed = ParsedLedger(transactions=[txn], unparsed=[], ignored_lines=0)
    zero_leg, single_leg, multi_leg_unparsed = partition_mf_transactions(
        parsed, COMMODITY_MAP
    )
    assert zero_leg == []
    assert single_leg == []
    assert len(multi_leg_unparsed) == 1
    assert multi_leg_unparsed[0].reason == MULTI_LEG_REASON
    assert multi_leg_unparsed[0].line_no == 20


def test_zero_mf_leg_transaction_is_unaffected():
    """A ParsedTxn with no MF leg is classified zero-leg, untouched by the
    adapter."""
    txn = ParsedTxn(
        date=date(2024, 3, 1),
        narration="Coffee",
        postings=[
            _cash_posting("Expenses:Food", "250.00"),
            _cash_posting(SOURCE_ACCOUNT, "-250.00"),
        ],
        source_line=30,
    )
    parsed = ParsedLedger(transactions=[txn], unparsed=[], ignored_lines=0)
    zero_leg, single_leg, multi_leg_unparsed = partition_mf_transactions(
        parsed, COMMODITY_MAP
    )
    assert zero_leg == [txn]
    assert single_leg == []
    assert multi_leg_unparsed == []
