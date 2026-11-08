"""Grouping the lumped card EMI account into per-plan proposals.

Synthetic narrations in both issuer shapes, synthetic refs and amounts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.core.settings import Settings
from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow
from backend.app.service.reports.liabilities import amortize
from backend.app.service.reports.liabilities.emi_plans import (
    compile_patterns,
    normalize_ref,
    parse_instalment,
    plan_account_name,
    propose_split,
)

PARENT = "Liabilities:Loan:CardEMI"
X = "Liabilities:CreditCard:Acme1234"
Y = "Liabilities:CreditCard:Beta5678"
PATTERNS = compile_patterns(Settings().card_emi_instalment_patterns)


def _txn(txn_id: str, d: date, narration: str, legs: dict[str, str]) -> TransactionRow:
    postings = [
        PostingRow(
            date=d,
            account=account,
            amount=Decimal(amount),
            narration=narration,
            tags=frozenset(),
        )
        for account, amount in legs.items()
    ]
    return TransactionRow(
        txn_id=txn_id, date=d, narration=narration, postings=postings, year_file_hash=""
    )


def _conversion(txn_id: str, d: date, card: str, amount: str) -> TransactionRow:
    return _txn(
        txn_id, d, "EMI CONVERSION CREDIT", {card: amount, PARENT: f"-{amount}"}
    )


def _bank(
    txn_id: str, d: date, card: str, ref: str, seq: int, amount
) -> TransactionRow:
    narration = f"OFFUS EMI,PRIN NB:{seq:02d},0000{ref}"
    return _txn(txn_id, d, narration, {PARENT: str(amount), card: f"-{amount}"})


def _amort(txn_id: str, d: date, card: str, merchant: str, seq: int, of: int, amount):
    narration = f"Principal Amount Amortization - <{seq}/{of}>{merchant}"
    return _txn(txn_id, d, narration, {PARENT: str(amount), card: f"-{amount}"})


def _propose(txns, taken=frozenset()):
    return propose_split(txns, PARENT, PATTERNS, set(taken))


# ── parsing ──────────────────────────────────────────────────────────────────
def test_parse_instalment_bank_style_ref_is_normalized():
    a = parse_instalment("OFFUS EMI,PRIN NB:02,0 0000555000111 (Ref# 1)", PATTERNS)
    b = parse_instalment("OFFUS EMI,PRIN NB:01,00000555000111", PATTERNS)
    assert a == ("555000111", 2, None)
    assert b == ("555000111", 1, None)


def test_parse_instalment_amortization_style():
    got = parse_instalment("Principal Amount Amortization - <1/3>ACME", PATTERNS)
    assert got == ("ACME", 1, 3)


def test_parse_instalment_no_match():
    assert parse_instalment("EMI ADJ", PATTERNS) is None


def test_normalize_ref():
    assert normalize_ref(" 00 0ab12 ") == "AB12"
    assert normalize_ref("000") == "0"


def test_compile_patterns_requires_ref_group():
    with pytest.raises(ValueError):
        compile_patterns([r"EMI (?P<seq>\d+)"])


def test_plan_account_name_strips_and_suffixes():
    taken = {f"{PARENT}:Acme1234-20260722"}
    got = plan_account_name(
        PARENT, "Liabilities:CreditCard:Ac_me1234", None, "X.1", set()
    )
    assert got == f"{PARENT}:Acme1234-X1"
    assert (
        plan_account_name(PARENT, X, date(2026, 7, 22), None, taken)
        == f"{PARENT}:Acme1234-20260722-2"
    )


# ── proposal ─────────────────────────────────────────────────────────────────
def test_propose_split_groups_by_card_and_ref_and_pairs_in_order():
    txns = [
        _conversion("c1", date(2025, 8, 20), X, "3000"),
        _conversion("c2", date(2025, 9, 6), X, "900"),
        _conversion("c3", date(2025, 6, 9), Y, "1500"),
        _bank("i1", date(2025, 8, 21), X, "111", 1, 1000),
        _bank("i2", date(2025, 9, 21), X, "111", 2, 1000),
        _bank("i3", date(2025, 9, 21), X, "222", 1, 300),
        _amort("i4", date(2025, 7, 9), Y, "SHOP", 1, 3, 500),
    ]
    out = _propose(txns)
    accounts = [p["account"] for p in out["plans"]]
    assert accounts == [
        f"{PARENT}:Acme1234-20250820",
        f"{PARENT}:Acme1234-20250906",
        f"{PARENT}:Beta5678-20250609",
    ]
    first, second, third = out["plans"]
    assert first["conversion"]["txn_id"] == "c1"
    assert [i["txn_id"] for i in first["instalments"]] == ["i1", "i2"]
    assert first["ref"] == "111"
    assert second["conversion"]["txn_id"] == "c2"
    assert second["ref"] == "222"
    assert third["card"] == Y and third["ref"] == "SHOP"
    assert first["principal"] == "3000" and first["repaid"] == "2000"
    assert first["outstanding"] == "1000" and first["fully_repaid"] is False
    assert first["instalments"][0] == {
        "txn_id": "i1",
        "date": "2025-08-21",
        "amount": "1000",
        "seq": 1,
    }
    assert out["unassigned"] == []


def test_completed_plan_is_confident_when_sum_matches():
    txns = [
        _conversion("c1", date(2025, 6, 9), Y, "1500"),
        _amort("i1", date(2025, 7, 9), Y, "SHOP", 1, 3, 495),
        _amort("i2", date(2025, 8, 9), Y, "SHOP", 2, 3, 500),
        _amort("i3", date(2025, 9, 9), Y, "SHOP", 3, 3, 505),
    ]
    (plan,) = _propose(txns)["plans"]
    assert plan["confident"] is True
    assert plan["reasons"] == []
    assert plan["fully_repaid"] is True
    assert plan["outstanding"] == "0"


def test_completed_plan_sum_mismatch_is_not_confident():
    txns = [
        _conversion("c1", date(2025, 6, 9), Y, "1500"),
        _amort("i1", date(2025, 7, 9), Y, "SHOP", 1, 3, 495),
        _amort("i2", date(2025, 8, 9), Y, "SHOP", 2, 3, 500),
        _amort("i3", date(2025, 9, 9), Y, "SHOP", 3, 3, 400),
    ]
    (plan,) = _propose(txns)["plans"]
    assert plan["confident"] is False
    assert plan["reasons"]
    assert plan["fully_repaid"] is False


def test_running_plan_that_cannot_be_inferred_is_not_confident():
    txns = [
        _conversion("c1", date(2026, 1, 5), X, "12000"),
        _bank("i1", date(2026, 2, 5), X, "111", 1, 1000),
        _bank("i2", date(2026, 3, 5), X, "111", 2, 1300),
    ]
    (plan,) = _propose(txns)["plans"]
    assert plan["confident"] is False
    assert plan["reasons"]


def test_running_plan_that_fits_is_confident():
    sched = amortize.schedule(Decimal(12000), Decimal(15), 12)
    txns = [_conversion("c1", date(2026, 7, 22), X, "12000")] + [
        _bank(f"i{m}", date(2026, 7 + m, 21), X, "111", m, sched[m - 1]["principal"])
        for m in (1, 2, 3)
    ]
    (plan,) = _propose(txns)["plans"]
    assert plan["confident"] is True, plan["reasons"]


def test_unmatched_positive_posting_goes_to_unassigned():
    txns = [_txn("u1", date(2025, 9, 1), "EMI ADJ", {PARENT: "50", X: "-50"})]
    out = _propose(txns)
    assert out["plans"] == []
    assert out["unassigned"] == [
        {
            "txn_id": "u1",
            "date": "2025-09-01",
            "narration": "EMI ADJ",
            "amount": "50",
            "reason": "no instalment pattern matched",
        }
    ]


def test_posting_without_card_leg_goes_to_unassigned():
    txns = [
        _txn(
            "u1",
            date(2025, 9, 1),
            "OFFUS EMI,PRIN NB:01,0000111",
            {PARENT: "50", "Assets:Checking:Bank": "-50"},
        )
    ]
    (row,) = _propose(txns)["unassigned"]
    assert row["reason"] == "no card leg"


def test_new_conversion_without_instalments_is_its_own_plan():
    (plan,) = _propose([_conversion("c1", date(2026, 10, 2), X, "4000")])["plans"]
    assert plan["account"] == f"{PARENT}:Acme1234-20261002"
    assert plan["ref"] is None
    assert plan["instalments"] == []
    assert plan["confident"] is True
    assert plan["reasons"] == ["awaiting first instalment"]
    assert (plan["principal"], plan["repaid"], plan["outstanding"]) == (
        "4000",
        "0",
        "4000",
    )


def test_instalments_without_conversion_are_not_confident():
    txns = [_bank("i1", date(2026, 2, 5), X, "777", 1, 1000)]
    (plan,) = _propose(txns)["plans"]
    assert plan["conversion"] is None
    assert plan["account"] == f"{PARENT}:Acme1234-777"
    assert (plan["principal"], plan["outstanding"]) == (None, None)
    assert plan["confident"] is False
    assert plan["reasons"] == ["no conversion found"]


def test_label_hint_from_same_amount_purchase():
    txns = [
        _txn(
            "p1", date(2026, 7, 10), "ACME STORE", {X: "-4000", "Expenses:Home": "4000"}
        ),
        _txn("p2", date(2026, 7, 11), "OTHER", {X: "-3999", "Expenses:Home": "3999"}),
        _conversion("c1", date(2026, 7, 22), X, "4000"),
    ]
    (plan,) = _propose(txns)["plans"]
    assert plan["label_hint"] == "ACME STORE"


def test_label_hint_outside_window_is_null():
    txns = [
        _txn(
            "p1", date(2026, 5, 1), "ACME STORE", {X: "-4000", "Expenses:Home": "4000"}
        ),
        _conversion("c1", date(2026, 7, 22), X, "4000"),
    ]
    (plan,) = _propose(txns)["plans"]
    assert plan["label_hint"] is None


def test_name_collision_gets_suffix():
    txns = [
        _conversion("c1", date(2026, 7, 22), X, "4000"),
        _conversion("c2", date(2026, 7, 22), X, "5000"),
    ]
    taken = {f"{PARENT}:Acme1234-20260722"}
    names = sorted(p["account"] for p in _propose(txns, taken)["plans"])
    assert names == [
        f"{PARENT}:Acme1234-20260722-2",
        f"{PARENT}:Acme1234-20260722-3",
    ]


def test_every_parent_posting_appears_exactly_once():
    txns = [
        _conversion("c1", date(2025, 8, 20), X, "3000"),
        _conversion("c2", date(2025, 9, 6), X, "900"),
        _conversion("c3", date(2026, 10, 1), Y, "700"),
        _bank("i1", date(2025, 8, 21), X, "111", 1, 1000),
        _bank("i2", date(2025, 9, 21), X, "111", 2, 1000),
        _bank("i3", date(2025, 9, 21), X, "222", 1, 300),
        _bank("i4", date(2026, 1, 21), X, "999", 1, 50),
        _txn("u1", date(2025, 9, 1), "EMI ADJ", {PARENT: "50", X: "-50"}),
        _txn("u2", date(2025, 9, 2), "OFFUS EMI,PRIN NB:01,1", {PARENT: "5"}),
        _txn("n1", date(2025, 9, 3), "LUNCH", {X: "-5", "Expenses:Food": "5"}),
    ]
    out = _propose(txns)
    seen: list[str] = [u["txn_id"] for u in out["unassigned"]]
    for plan in out["plans"]:
        if plan["conversion"]:
            seen.append(plan["conversion"]["txn_id"])
        seen.extend(i["txn_id"] for i in plan["instalments"])
    on_parent = [t.txn_id for t in txns if any(p.account == PARENT for p in t.postings)]
    assert sorted(seen) == sorted(on_parent)


def test_empty_parent_gives_empty_proposal():
    out = _propose(
        [_txn("n1", date(2025, 9, 3), "LUNCH", {X: "-5", "Expenses:Food": "5"})]
    )
    assert out == {"parent": PARENT, "plans": [], "unassigned": []}


# ── Ambiguity and label hints ────────────────────────────────────────────────
def test_more_than_one_conversion_fits_makes_every_plan_on_the_card_unsure():
    # Two equal conversions before one completed group: either could be its
    # conversion. The pick is the nearest before the first instalment, and the
    # leftover conversion is not confident either.
    txns = [
        _conversion("c1", date(2025, 6, 1), Y, "1500"),
        _conversion("c2", date(2025, 6, 9), Y, "1500"),
        _amort("i1", date(2025, 7, 9), Y, "SHOP", 1, 3, 500),
        _amort("i2", date(2025, 8, 9), Y, "SHOP", 2, 3, 500),
        _amort("i3", date(2025, 9, 9), Y, "SHOP", 3, 3, 500),
        # Another card is unaffected.
        _conversion("c3", date(2025, 6, 9), X, "900"),
    ]
    plans = _propose(txns)["plans"]
    on_y = [p for p in plans if p["card"] == Y]
    paired = next(p for p in on_y if p["instalments"])
    leftover = next(p for p in on_y if not p["instalments"])
    assert paired["conversion"]["txn_id"] == "c2"
    assert paired["confident"] is False
    assert "more than one conversion fits these instalments" in paired["reasons"]
    assert leftover["conversion"]["txn_id"] == "c1"
    assert leftover["confident"] is False
    assert (
        "another plan on this card had more than one conversion that fits"
        in leftover["reasons"]
    )
    (on_x,) = [p for p in plans if p["card"] == X]
    assert on_x["confident"] is True


def test_greedy_pass_uses_the_same_nearest_tie_break():
    # No conversion sums to the group, so the greedy pass pairs it: with two
    # in the window it takes the nearest before the first instalment.
    txns = [
        _conversion("c1", date(2026, 1, 1), X, "5000"),
        _conversion("c2", date(2026, 1, 20), X, "6000"),
        _bank("i1", date(2026, 2, 5), X, "111", 1, 1000),
    ]
    plans = _propose(txns)["plans"]
    paired = next(p for p in plans if p["instalments"])
    assert paired["conversion"]["txn_id"] == "c2"
    assert all(p["confident"] is False for p in plans)


def test_posting_with_more_than_one_card_leg_goes_to_unassigned():
    txns = [
        _txn(
            "u1",
            date(2025, 9, 1),
            "OFFUS EMI,PRIN NB:01,0000111",
            {PARENT: "50", X: "-25", Y: "-25"},
        )
    ]
    out = _propose(txns)
    assert out["plans"] == []
    (row,) = out["unassigned"]
    assert (row["txn_id"], row["reason"]) == ("u1", "more than one card leg")


def test_label_hint_is_cleaned_to_a_valid_label():
    narration = 'ACME  "MEGA"\nSTORE ' + "X" * 80
    txns = [
        _txn("p1", date(2026, 7, 10), narration, {X: "-4000", "Expenses:Home": "4000"}),
        _conversion("c1", date(2026, 7, 22), X, "4000"),
    ]
    (plan,) = _propose(txns)["plans"]
    hint = plan["label_hint"]
    assert hint.startswith("ACME MEGA STORE X")
    assert len(hint) <= 60
    assert '"' not in hint and "\n" not in hint and "  " not in hint
