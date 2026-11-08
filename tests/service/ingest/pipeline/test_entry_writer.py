from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction, Split
from backend.app.service.ingest.pipeline.entry_writer import (
    candidate_to_entry,
    commit_candidates,
)


def cand(category="Expenses:Food", direction=Direction.DEBIT, other_leg=None):
    return CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("412.00"),
        raw_narration="UPI/DR/SWIGGY",
        normalized_narration="SWIGGY",
        source_account="Assets:Bank:HDFC",
        direction=direction,
        suggested_category=category,
        suggested_other_leg=other_leg,
    )


def test_debit_expense_entry_balances():
    entry = candidate_to_entry(cand())
    accounts = {p.account for p in entry.postings}
    assert accounts == {"Expenses:Food", "Assets:Bank:HDFC"}
    assert sum(p.units.number for p in entry.postings) == Decimal("0")


def test_transfer_uses_other_leg():
    entry = candidate_to_entry(cand(category=None, other_leg="Assets:Wallet:UPILite"))
    assert {p.account for p in entry.postings} == {
        "Assets:Wallet:UPILite",
        "Assets:Bank:HDFC",
    }


def test_commit_writes_through_ledger(ledger, investment_ledger):
    commit_candidates(ledger, investment_ledger, [cand()])
    assert "SWIGGY" in (ledger.journal_dir / "2026.beancount").read_text(
        encoding="utf-8"
    )
    assert ledger.validate() == []


def test_single_split_entry_carries_txn_id():
    entry = candidate_to_entry(cand())
    assert entry.meta["txn-id"] == cand().id


def test_multi_split_entry_balances_and_carries_txn_id():
    c = CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("21500.00"),
        raw_narration="Groww redemption",
        normalized_narration="GROWW REDEMPTION",
        source_account="Assets:Bank:HDFC",
        direction=Direction.CREDIT,
        splits=[
            Split(account="Assets:Investments:PPFCF", amount=Decimal("18000.00")),
            Split(account="Income:CapitalGains:LongTerm", amount=Decimal("2800.00")),
            Split(account="Income:CapitalGains:ShortTerm", amount=Decimal("700.00")),
        ],
    )
    entry = candidate_to_entry(c)
    accounts = {p.account for p in entry.postings}
    assert accounts == {
        "Assets:Investments:PPFCF",
        "Income:CapitalGains:LongTerm",
        "Income:CapitalGains:ShortTerm",
        "Assets:Bank:HDFC",
    }
    assert sum(p.units.number for p in entry.postings) == Decimal("0")
    assert entry.meta["txn-id"] == c.id


def test_split_tags_become_transaction_tags():
    c = CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("100.00"),
        raw_narration="CBDT",
        normalized_narration="CBDT",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
        tags=["Trip"],
        splits=[
            Split(
                account="Expenses:Tax",
                amount=Decimal("100.00"),
                tags=["tax-related", "advance-tax"],
            )
        ],
    )
    entry = candidate_to_entry(c)
    assert entry.tags == frozenset({"trip", "tax-related", "advance-tax"})
    assert all("tags" not in (p.meta or {}) for p in entry.postings)
