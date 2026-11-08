"""Ledger-merge dedup is sign-aware and count-aware (synthetic data only)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.models.domain.ledger_cli import ParsedPosting, ParsedTxn
from backend.app.service.ingest.ledger_merge.service import (
    convert_txn,
    entry_dedup_keys,
    mark_duplicates,
)
from backend.app.service.ingest.pipeline.dedup import ExistingKeys
from tests.service.ingest.ledger_merge.test_service import _service

AMAP = {
    "Expenses:Food": "Expenses:Food",
    "Assets:Checking:HDFC": "Assets:Checking:HDFC",
}


def _txn(food: str, source_line: int = 1) -> ParsedTxn:
    amount = Decimal(food)
    return ParsedTxn(
        date(2024, 1, 5),
        "Coffee",
        [
            ParsedPosting("Expenses:Food", amount, "INR", None, None, None),
            ParsedPosting("Assets:Checking:HDFC", -amount, "INR", None, None, None),
        ],
        source_line,
    )


def test_refund_after_a_recorded_charge_is_not_a_duplicate():
    """Keys are signed: the refund's legs carry the opposite signs."""
    existing = entry_dedup_keys(convert_txn(_txn("250"), AMAP, {}))
    refund = convert_txn(_txn("-250"), AMAP, {})
    assert mark_duplicates([refund], existing) == set()


def test_reimporting_a_recorded_entry_is_a_duplicate():
    existing = entry_dedup_keys(convert_txn(_txn("250"), AMAP, {}))
    again = convert_txn(_txn("250"), AMAP, {})
    assert mark_duplicates([again], existing) == {again.meta["txn-id"]}


def test_twin_entries_need_two_recorded_entries_to_both_be_duplicates():
    recorded = ExistingKeys()
    recorded.add_unit(entry_dedup_keys(convert_txn(_txn("250"), AMAP, {})))
    first, second = (convert_txn(_txn("250"), AMAP, {}) for _ in range(2))
    assert mark_duplicates([first, second], recorded) == {first.meta["txn-id"]}

    recorded.add_unit(entry_dedup_keys(convert_txn(_txn("250"), AMAP, {})))
    assert mark_duplicates([first, second], recorded) == {
        first.meta["txn-id"],
        second.meta["txn-id"],
    }


_CHARGE = """\
2024/02/03 Shop
    Expenses:Shopping       305.00 INR
    Assets:Checking:HDFC   -305.00 INR
"""
_REFUND = """\
2024/02/03 Shop
    Expenses:Shopping      -305.00 INR
    Assets:Checking:HDFC    305.00 INR
"""


def _merge(service, text: str) -> None:
    out = service.preview("x.ledger", text.encode())
    service.commit(
        out["batch_id"],
        account_map={a["raw"]: a["suggested"] for a in out["accounts"]},
        commodity_map={c["raw"]: c["suggested"] for c in out["commodities"]},
        include_duplicates=False,
    )


def test_merge_reimport_is_dup_but_a_same_day_refund_is_new(tmp_path):
    service, _ = _service(tmp_path)
    _merge(service, _CHARGE)
    out = service.preview("y.ledger", (_CHARGE + "\n" + _REFUND).encode())
    assert [t["duplicate"] for t in out["transactions"]] == [True, False]
    assert out["summary"]["duplicates"] == 1


def test_merge_twin_charges_one_recorded_flags_only_one(tmp_path):
    service, _ = _service(tmp_path)
    _merge(service, _CHARGE)
    out = service.preview("y.ledger", (_CHARGE + "\n" + _CHARGE).encode())
    assert [t["duplicate"] for t in out["transactions"]] == [True, False]


def test_the_entry_matching_every_leg_is_the_duplicate_not_an_earlier_one():
    """Journal holds Shopping/Bank. The file has Food/Bank then Shopping/Bank,
    same day, wording and amount. Both match on the bank leg; only the second
    matches on every leg, so it is the re-import and the first is new."""

    def txn(expense: str) -> ParsedTxn:
        return ParsedTxn(
            date(2024, 1, 5),
            "Coffee",
            [
                ParsedPosting(expense, Decimal("250"), "INR", None, None, None),
                ParsedPosting(
                    "Assets:Checking:HDFC", Decimal("-250"), "INR", None, None, None
                ),
            ],
            1,
        )

    amap = {**AMAP, "Expenses:Shopping": "Expenses:Shopping"}
    recorded = ExistingKeys()
    recorded.add_unit(entry_dedup_keys(convert_txn(txn("Expenses:Shopping"), amap, {})))
    food = convert_txn(txn("Expenses:Food"), amap, {})
    shopping = convert_txn(txn("Expenses:Shopping"), amap, {})
    assert mark_duplicates([food, shopping], recorded) == {shopping.meta["txn-id"]}
