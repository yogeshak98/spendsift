from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.models.api.entries import EntryIn, PostingIn
from backend.app.service.ledger.entries import build_transaction


def test_build_transaction_maps_the_entry_onto_a_beancount_transaction() -> None:
    entry = EntryIn(
        date="2024-03-01",
        narration="Coffee",
        postings=[
            PostingIn(account="Expenses:Food", amount="100"),
            PostingIn(account="Assets:Cash", amount="-100"),
        ],
        tags=["#trip", "work"],
    )

    txn = build_transaction(entry, "txn-1")

    assert txn.meta["txn-id"] == "txn-1"
    assert txn.date == date(2024, 3, 1)
    assert txn.flag == "*"
    assert txn.narration == "Coffee"
    assert txn.tags == frozenset({"trip", "work"})
    assert [(p.account, p.units.number, p.units.currency) for p in txn.postings] == [
        ("Expenses:Food", Decimal("100"), "INR"),
        ("Assets:Cash", Decimal("-100"), "INR"),
    ]
