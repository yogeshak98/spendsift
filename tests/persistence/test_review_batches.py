from datetime import date
from decimal import Decimal

import pytest

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.models.storage.opening_evidence import OpeningEvidence, PrintedOpening
from backend.app.persistence.review_batches import ReviewStore


def cand(narr="SWIGGY"):
    return CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("412.00"),
        raw_narration=narr,
        normalized_narration=narr,
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
    )


def test_add_and_get_batch():
    store = ReviewStore()
    batch_id = store.add_batch([cand(), cand("BLINKIT")])
    assert len(store.get_batch(batch_id)) == 2


def test_update_candidate_category():
    store = ReviewStore()
    batch_id = store.add_batch([cand()])
    cid = store.get_batch(batch_id)[0].id
    store.update(batch_id, cid, {"suggested_category": "Expenses:Food"})
    assert store.get_batch(batch_id)[0].suggested_category == "Expenses:Food"


def test_drop_candidate():
    store = ReviewStore()
    batch_id = store.add_batch([cand(), cand("BLINKIT")])
    cid = store.get_batch(batch_id)[0].id
    store.drop(batch_id, cid)
    assert len(store.get_batch(batch_id)) == 1


def test_missing_batch_raises():
    with pytest.raises(KeyError):
        ReviewStore().get_batch("nope")


def _evidence(account: str | None = "Assets:Bank:HDFC") -> OpeningEvidence:
    return OpeningEvidence(
        account=account,
        printed=PrintedOpening(
            label_text="Opening Balance",
            raw_text="10000.00",
            value=Decimal("10000.00"),
            suffix=None,
            row=2,
            column=4,
        ),
        first_row=None,
        printed_error=None,
    )


def test_opening_evidence_set_get_clear():
    store = ReviewStore()
    batch_id = store.add_batch([cand()])
    other = store.add_batch([cand()])
    assert store.opening_evidence(batch_id) is None
    store.set_opening_evidence(batch_id, _evidence())
    store.set_opening_evidence(other, _evidence())
    got = store.opening_evidence(batch_id)
    assert got is not None and got.printed is not None
    assert got.printed.value == Decimal("10000.00")
    store.clear(batch_id)
    assert store.opening_evidence(batch_id) is None
    assert store.opening_evidence(other) is not None


def test_rename_account_rewrites_and_undoes_opening_evidence():
    store = ReviewStore()
    batch_id = store.add_batch([cand()])
    store.set_opening_evidence(batch_id, _evidence())
    changes = store.rename_account("Assets:Bank:HDFC", "Assets:Bank:HDFC2")
    assert store.opening_evidence(batch_id).account == "Assets:Bank:HDFC2"
    assert batch_id in changes.batch_ids
    store.undo_rename(changes)
    assert store.opening_evidence(batch_id).account == "Assets:Bank:HDFC"
