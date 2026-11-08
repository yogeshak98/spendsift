"""Manual entry: turn a browser-submitted entry into a beancount transaction."""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount

from backend.app.models.api.entries import EntryIn
from backend.app.models.domain.accounts import OPERATING_CURRENCY


def build_transaction(entry: EntryIn, txn_id: str) -> data.Transaction:
    meta = data.new_metadata("<manual>", 0)
    meta["txn-id"] = txn_id
    year, month, day = entry.date.split("-")
    txn_date = date_type(int(year), int(month), int(day))
    tags: frozenset[str] = frozenset(t.lstrip("#") for t in entry.tags)
    postings = [
        data.Posting(
            p.account,
            Amount(Decimal(p.amount), OPERATING_CURRENCY),
            None,
            None,
            None,
            None,
        )
        for p in entry.postings
    ]
    return data.Transaction(
        meta, txn_date, "*", None, entry.narration, tags, data.EMPTY_SET, postings
    )
