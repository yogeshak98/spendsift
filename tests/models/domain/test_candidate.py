import pytest
from datetime import date
from decimal import Decimal
from pydantic import ValidationError

from backend.app.models.domain.candidate import (
    CandidateTxn,
    Direction,
    DedupStatus,
    Split,
)


def test_candidate_defaults():
    c = CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("412.00"),
        raw_narration="UPI/DR/SWIGGY/412",
        normalized_narration="SWIGGY",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
    )
    assert c.dedup_status == DedupStatus.NEW
    assert c.suggested_category is None
    assert c.confidence == 1.0
    assert c.flags == []
    assert c.id  # auto-assigned, non-empty


def test_candidate_ids_are_deterministic_for_identical_source_fields():
    base = dict(
        date=date(2026, 4, 1),
        amount=Decimal("1"),
        raw_narration="a",
        normalized_narration="a",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
    )
    assert CandidateTxn(**base).id == CandidateTxn(**base).id


def test_candidate_ids_differ_for_different_source_fields():
    base = dict(
        date=date(2026, 4, 1),
        amount=Decimal("1"),
        raw_narration="a",
        normalized_narration="a",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
    )
    other = dict(base, normalized_narration="b")
    assert CandidateTxn(**base).id != CandidateTxn(**other).id


def test_candidate_ids_match_across_differing_amount_precision():
    """Regression: Decimal('2601.00') and Decimal('2601') are the same real
    amount but formatted differently in the id key, so the same transaction
    re-imported from a source with different decimal precision (e.g. a bank
    CSV vs. a merged ledger-cli journal) got two different ids and slipped
    past dedup -- this caused ~89 real double-booked transactions."""
    base = dict(
        date=date(2026, 1, 3),
        raw_narration="a",
        normalized_narration="a",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
    )
    a = CandidateTxn(amount=Decimal("2601.00"), **base)
    b = CandidateTxn(amount=Decimal("2601"), **base)
    assert a.id == b.id


def test_split_defaults_to_no_tags():
    s = Split(account="Expenses:Food", amount=Decimal("100.00"))
    assert s.tags == []


def test_split_rejects_zero_amount():
    with pytest.raises(ValidationError):
        Split(account="Expenses:Food", amount=Decimal("0"))


def test_candidate_defaults_to_no_splits():
    c = CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("412.00"),
        raw_narration="UPI/DR/SWIGGY/412",
        normalized_narration="SWIGGY",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
    )
    assert c.splits == []


def test_matched_txn_id_defaults_to_none_and_is_settable():
    c = CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("412.00"),
        raw_narration="UPI/DR/SWIGGY/412",
        normalized_narration="SWIGGY",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
    )
    assert c.matched_txn_id is None
    c.dedup_status = DedupStatus.TRANSFER_ALREADY_RECORDED
    c.matched_txn_id = "abc123"
    assert c.dedup_status == DedupStatus.TRANSFER_ALREADY_RECORDED
    assert c.matched_txn_id == "abc123"


def test_needs_account_defaults_false_and_marker_key_defaults_none():
    c = CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("100.00"),
        raw_narration="X",
        normalized_narration="X",
        source_account="",
        direction=Direction.DEBIT,
    )
    assert c.needs_account is False
    assert c.unresolved_marker_key is None


def test_needs_account_can_be_set_true_with_marker_key():
    c = CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("100.00"),
        raw_narration="X",
        normalized_narration="X",
        source_account="",
        direction=Direction.DEBIT,
        needs_account=True,
        unresolved_marker_key="0001",
    )
    assert c.needs_account is True
    assert c.unresolved_marker_key == "0001"


def _row(direction: Direction) -> CandidateTxn:
    return CandidateTxn(
        date=date(2026, 9, 3),
        amount=Decimal("305.00"),
        raw_narration="IND*AMAZON.IN - GROCER",
        normalized_narration="AMAZON GROCER",
        source_account="Liabilities:CreditCard:Test",
        direction=direction,
    )


def test_candidate_id_includes_direction():
    assert _row(Direction.DEBIT).id != _row(Direction.CREDIT).id


def test_disambiguate_ids_numbers_repeats_in_statement_order():
    from backend.app.models.domain.candidate import disambiguate_ids

    rows = [_row(Direction.DEBIT), _row(Direction.DEBIT), _row(Direction.DEBIT)]
    base = rows[0].id
    disambiguate_ids(rows)
    assert rows[0].id == base  # the first occurrence keeps the content id
    assert len({r.id for r in rows}) == 3

    again = [_row(Direction.DEBIT), _row(Direction.DEBIT), _row(Direction.DEBIT)]
    disambiguate_ids(again)
    assert [r.id for r in again] == [r.id for r in rows]  # deterministic
    disambiguate_ids(again)
    assert [r.id for r in again] == [r.id for r in rows]  # idempotent


def test_avoid_taken_ids_moves_only_written_rows_off_recorded_ids():
    from backend.app.models.domain.candidate import avoid_taken_ids, disambiguate_ids

    rows = [_row(Direction.DEBIT) for _ in range(3)]
    disambiguate_ids(rows)
    a, a1, a2 = (r.id for r in rows)
    rows[0].dedup_status = DedupStatus.EXACT_DUP  # not written: keeps its id
    # A|1 is recorded; so is the first id a bump of A|1 would try.
    from backend.app.models.domain.candidate import SPENDSIFT_NAMESPACE
    import uuid

    first_try = uuid.uuid5(SPENDSIFT_NAMESPACE, f"{a1}|1").hex
    taken = {a, a1, first_try}
    avoid_taken_ids(rows, taken)
    assert rows[0].id == a
    assert rows[1].id not in taken | {a2}
    assert rows[2].id == a2  # not taken: untouched
    assert len({r.id for r in rows}) == 3

    again = [_row(Direction.DEBIT) for _ in range(3)]
    disambiguate_ids(again)
    again[0].dedup_status = DedupStatus.EXACT_DUP
    avoid_taken_ids(again, taken)
    assert [r.id for r in again] == [r.id for r in rows]  # deterministic
