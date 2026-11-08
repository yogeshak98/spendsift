"""Card bill payments in transit: the locked tolerance rule and the read model
(`payments_in_transit`, `summary`, `per_card`). Synthetic data only."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow
from backend.app.service.reports.liabilities.in_transit import (
    CASHBACK_CAP_ABS,
    CASHBACK_CAP_RATE,
    MATCH_LAG_DAYS,
    MATCH_LEAD_DAYS,
    STALE_DAYS,
    TOLERANCE_ABS,
    TOLERANCE_RATE,
    ClearedPayment,
    InTransitPayment,
    cashback_cap,
    match_clearing,
    payments_in_transit,
    per_card,
    summary,
    tolerance,
)

CLEARING = "Assets:Transfer:CreditCard"
BANK = "Assets:Bank:Synth"
CARD_A = "Liabilities:CreditCard:SynthA1111"
CARD_B = "Liabilities:CreditCard:SynthB2222"
CARD_C = "Liabilities:CreditCard:SynthC3333"
REFUND = "Income:Refund:CreditCard:Synth"


def _txn(txn_id: str, d: date, *legs: tuple[str, str]) -> TransactionRow:
    return TransactionRow(
        txn_id=txn_id,
        date=d,
        narration=txn_id,
        postings=[
            PostingRow(
                date=d, account=a, amount=Decimal(v), narration=txn_id, tags=frozenset()
            )
            for a, v in legs
        ],
        year_file_hash="h",
    )


def _spend(txn_id: str, d: date, card: str, amount: str) -> TransactionRow:
    return _txn(txn_id, d, ("Expenses:Shopping", amount), (card, f"-{amount}"))


def _bank_payment(txn_id: str, d: date, amount: str) -> TransactionRow:
    return _txn(txn_id, d, (CLEARING, amount), (BANK, f"-{amount}"))


def _adjustment(txn_id: str, d: date, amount: str) -> TransactionRow:
    """A cashback/refund top-up booked into the clearing account."""
    return _txn(txn_id, d, (CLEARING, amount), (REFUND, f"-{amount}"))


def _postings(*txns: TransactionRow) -> list[PostingRow]:
    return [p for t in txns for p in t.postings]


def _card_receipt(txn_id: str, d: date, card: str, amount: str) -> TransactionRow:
    return _txn(txn_id, d, (card, amount), (CLEARING, f"-{amount}"))


def test_tolerance_constants_are_locked():
    assert TOLERANCE_ABS == Decimal("10")
    assert TOLERANCE_RATE == Decimal("0.001")
    assert MATCH_LEAD_DAYS == 7


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (Decimal("0"), Decimal("10")),
        (Decimal("5000"), Decimal("10")),
        (Decimal("10000"), Decimal("10")),
        (Decimal("31860"), Decimal("31.860")),
        (Decimal("-31860"), Decimal("31.860")),
    ],
)
def test_tolerance_is_ten_rupees_or_a_tenth_of_a_percent_whichever_larger(
    amount, expected
):
    assert tolerance(amount) == expected


def test_three_payments_attribute_to_three_cards():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "31872"),
        _spend("b", date(2026, 10, 2), CARD_B, "12000"),
        _spend("c", date(2026, 10, 3), CARD_C, "780"),
        _bank_payment("pa", date(2026, 10, 20), "31860"),
        _bank_payment("pb", date(2026, 10, 20), "12000"),
        _bank_payment("pc", date(2026, 10, 21), "780"),
    ]
    got = payments_in_transit(txns, CLEARING)
    assert {(p.txn_id, p.account) for p in got} == {
        ("pa", CARD_A),
        ("pb", CARD_B),
        ("pc", CARD_C),
    }


def test_ambiguous_pair_stays_unattributed():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "20000"),
        _spend("b", date(2026, 10, 1), CARD_B, "20004"),
        _bank_payment("p", date(2026, 10, 20), "20002"),
    ]
    (payment,) = payments_in_transit(txns, CLEARING)
    assert payment.account is None
    assert payment.amount == Decimal("20002")


def test_payment_leaves_transit_once_the_card_receipt_is_imported():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _bank_payment("p", date(2026, 10, 20), "5000"),
        _card_receipt("r", date(2026, 10, 21), CARD_A, "5000"),
    ]
    assert payments_in_transit(txns, CLEARING) == []


# ── Matching ────────────────────────────────────────────────────────────────


def test_no_clearing_postings_means_nothing_in_transit():
    assert (
        payments_in_transit([_spend("a", date(2026, 10, 1), CARD_A, "5000")], CLEARING)
        == []
    )


def test_receipt_within_tolerance_and_up_to_seven_days_early_matches():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        # The card posts the receipt 7 days before the bank debit, 5 off.
        _card_receipt("r", date(2026, 10, 13), CARD_A, "4995"),
        _bank_payment("p", date(2026, 10, 20), "5000"),
    ]
    assert payments_in_transit(txns, CLEARING) == []


def test_receipt_more_than_seven_days_before_the_debit_does_not_match():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _card_receipt("r", date(2026, 10, 12), CARD_A, "5000"),
        _bank_payment("p", date(2026, 10, 20), "5000"),
    ]
    (payment,) = payments_in_transit(txns, CLEARING)
    assert payment.txn_id == "p"


def test_receipt_outside_tolerance_does_not_match():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _bank_payment("p", date(2026, 10, 20), "5000"),
        _card_receipt("r", date(2026, 10, 21), CARD_A, "4989"),
    ]
    assert [p.txn_id for p in payments_in_transit(txns, CLEARING)] == ["p"]


def test_each_receipt_clears_the_payment_closest_in_date_once():
    txns = [
        _spend("a", date(2026, 9, 1), CARD_A, "3000"),
        _bank_payment("p1", date(2026, 9, 20), "3000"),
        _bank_payment("p2", date(2026, 10, 20), "3000"),
        _card_receipt("r1", date(2026, 10, 22), CARD_A, "3000"),
    ]
    # r1 clears the payment closest to it (p2); p1 is still in transit.
    assert [p.txn_id for p in payments_in_transit(txns, CLEARING)] == ["p1"]


def test_bank_payment_booked_straight_to_the_card_is_never_in_transit():
    # No clearing leg: the bank row was booked directly against the card.
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _txn("p", date(2026, 10, 20), (CARD_A, "5000"), (BANK, "-5000")),
    ]
    assert payments_in_transit(txns, CLEARING) == []


def test_other_clearing_postings_are_ignored():
    txns = [
        # Positive clearing leg alongside a card leg: neither debit nor credit.
        _txn("odd", date(2026, 10, 5), (CLEARING, "700"), (CARD_A, "-700")),
        # Negative clearing leg with no card leg: the bank side reversing.
        _txn("back", date(2026, 10, 6), (BANK, "700"), (CLEARING, "-700")),
    ]
    assert payments_in_transit(txns, CLEARING) == []


def test_payments_come_back_date_ascending():
    txns = [
        _bank_payment("late", date(2026, 10, 25), "100"),
        _bank_payment("early", date(2026, 10, 2), "200"),
    ]
    assert [p.txn_id for p in payments_in_transit(txns, CLEARING)] == [
        "early",
        "late",
    ]


# ── Attribution ─────────────────────────────────────────────────────────────


def test_outstanding_is_taken_as_of_the_payment_date():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _bank_payment("p", date(2026, 10, 20), "5000"),
        # Later spend does not change the as-of-D outstanding.
        _spend("a2", date(2026, 10, 25), CARD_A, "900"),
    ]
    (payment,) = payments_in_transit(txns, CLEARING)
    assert payment.account == CARD_A


def test_overpayment_within_tolerance_is_attributed():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _bank_payment("p", date(2026, 10, 20), "5008"),
    ]
    (payment,) = payments_in_transit(txns, CLEARING)
    assert (payment.account, payment.amount) == (CARD_A, Decimal("5008"))
    tile = per_card([payment])[CARD_A]
    # The card's net owed can go negative: 5000 - 5008.
    assert tile["amount"] == "5008.00"


def test_overpayment_beyond_tolerance_is_unattributed():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _bank_payment("p", date(2026, 10, 20), "5100"),
    ]
    (payment,) = payments_in_transit(txns, CLEARING)
    assert payment.account is None


def test_two_payments_to_one_card_in_the_same_cycle():
    # The full bill is paid, then a second payment covers a spend made after
    # it. Attribution compares each payment with the ledger outstanding as of
    # its date; the first payment is not on the card's ledger yet, so the
    # second payment (300) is compared with 5300 and stays unattributed.
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _bank_payment("p1", date(2026, 10, 20), "5000"),
        _spend("a2", date(2026, 10, 22), CARD_A, "300"),
        _bank_payment("p2", date(2026, 10, 25), "300"),
    ]
    got = payments_in_transit(txns, CLEARING)
    assert [(p.txn_id, p.account) for p in got] == [("p1", CARD_A), ("p2", None)]
    assert summary(got)["amount"] == "5300.00"
    assert summary(got)["unattributed"] == "300.00"


def test_the_same_amount_paid_twice_counts_twice_on_the_card():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _bank_payment("p1", date(2026, 10, 20), "5000"),
        _bank_payment("p2", date(2026, 10, 21), "5000"),
    ]
    got = payments_in_transit(txns, CLEARING)
    assert [p.account for p in got] == [CARD_A, CARD_A]
    assert per_card(got) == {
        CARD_A: {"amount": "10000.00", "date": "2026-10-21", "count": 2}
    }


def test_a_card_closed_later_is_not_a_candidate():
    # The card was closed after the payment; a closed card is never an
    # attribution candidate, so a payment still in transit stays unattributed.
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _bank_payment("p", date(2026, 10, 20), "5000"),
    ]
    (payment,) = payments_in_transit(txns, CLEARING, closed=frozenset({CARD_A}))
    assert payment.account is None


def test_a_card_closed_later_after_its_receipt_has_nothing_in_transit():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _bank_payment("p", date(2026, 10, 20), "5000"),
        _card_receipt("r", date(2026, 10, 22), CARD_A, "5000"),
    ]
    assert payments_in_transit(txns, CLEARING, closed=frozenset({CARD_A})) == []


def test_closed_card_does_not_make_an_open_card_ambiguous():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _spend("b", date(2026, 10, 1), CARD_B, "5000"),
        _bank_payment("p", date(2026, 10, 20), "5000"),
    ]
    (payment,) = payments_in_transit(txns, CLEARING, closed=frozenset({CARD_B}))
    assert payment.account == CARD_A


def test_a_payment_older_than_sixty_days_with_no_statement_is_stale():
    # No card-side receipt ever arrived. Past STALE_DAYS it is stale: left
    # out of the payments in transit (and so out of net owed).
    today = date(2026, 11, 1)
    txns = [
        _spend("a", date(2026, 8, 1), CARD_A, "4200"),
        _bank_payment("old", today - timedelta(days=STALE_DAYS + 1), "4200"),
    ]
    assert payments_in_transit(txns, CLEARING, today=today) == []
    # Without `today` nothing is aged out.
    assert [p.txn_id for p in payments_in_transit(txns, CLEARING)] == ["old"]


def test_a_payment_exactly_sixty_days_old_is_still_in_transit():
    today = date(2026, 11, 1)
    paid = today - timedelta(days=STALE_DAYS)
    txns = [
        _spend("a", date(2026, 8, 1), CARD_A, "4200"),
        _bank_payment("p", paid, "4200"),
    ]
    got = payments_in_transit(txns, CLEARING, today=today)
    assert got == [InTransitPayment("p", paid, Decimal("4200"), CARD_A)]
    assert per_card(got)[CARD_A]["date"] == paid.isoformat()


# ── summary / per_card ──────────────────────────────────────────────────────


def test_summary_of_nothing_is_zero():
    assert summary([]) == {
        "amount": "0.00",
        "attributed": "0.00",
        "unattributed": "0.00",
        "payments": [],
    }


def test_summary_splits_attributed_and_unattributed():
    payments = [
        InTransitPayment("p1", date(2026, 10, 20), Decimal("31860"), CARD_A),
        InTransitPayment("p2", date(2026, 10, 21), Decimal("100.005"), None),
    ]
    assert summary(payments) == {
        "amount": "31960.01",
        "attributed": "31860.00",
        "unattributed": "100.01",
        "payments": [
            {
                "txn_id": "p1",
                "date": "2026-10-20",
                "amount": "31860.00",
                "account": CARD_A,
            },
            {
                "txn_id": "p2",
                "date": "2026-10-21",
                "amount": "100.01",
                "account": None,
            },
        ],
    }


def test_per_card_skips_unattributed_and_groups_by_card():
    payments = [
        InTransitPayment("p1", date(2026, 10, 2), Decimal("100"), CARD_A),
        InTransitPayment("p2", date(2026, 10, 9), Decimal("50.5"), CARD_A),
        InTransitPayment("p3", date(2026, 10, 3), Decimal("7"), CARD_B),
        InTransitPayment("p4", date(2026, 10, 4), Decimal("9"), None),
    ]
    assert per_card(payments) == {
        CARD_A: {"amount": "150.50", "date": "2026-10-09", "count": 2},
        CARD_B: {"amount": "7.00", "date": "2026-10-03", "count": 1},
    }


# ── Rework: bank-only payments, cashback-aware matching, ledger outstanding ──


def test_cashback_cap_constants():
    assert CASHBACK_CAP_ABS == Decimal("100")
    assert CASHBACK_CAP_RATE == Decimal("0.02")
    assert cashback_cap(Decimal("1000")) == Decimal("100")
    assert cashback_cap(Decimal("20000")) == Decimal("400")


def test_clearing_debit_from_income_is_never_a_payment():
    # The cashback/refund top-up has a txn-id but no bank leg.
    txns = [_adjustment("adj", date(2026, 10, 20), "15")]
    assert payments_in_transit(txns, CLEARING) == []


def test_bank_payment_with_a_fee_leg_is_a_payment_of_the_clearing_amount():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _txn(
            "p",
            date(2026, 10, 20),
            (CLEARING, "5000"),
            ("Expenses:BankFees", "18"),
            (BANK, "-5018"),
        ),
    ]
    (payment,) = payments_in_transit(txns, CLEARING)
    assert (payment.txn_id, payment.amount, payment.account) == (
        "p",
        Decimal("5000"),
        CARD_A,
    )


def test_fee_leg_payment_receipt_clears_it_not_another_payment():
    txns = [
        _bank_payment("other", date(2026, 10, 18), "5005"),
        _txn(
            "p",
            date(2026, 10, 20),
            (CLEARING, "5000"),
            ("Expenses:BankFees", "18"),
            (BANK, "-5018"),
        ),
        _card_receipt("r", date(2026, 10, 21), CARD_A, "5000"),
    ]
    assert [p.txn_id for p in payments_in_transit(txns, CLEARING)] == ["other"]


def test_receipt_larger_by_cashback_clears_the_payment():
    txns = [
        _spend("a", date(2026, 4, 1), CARD_A, "12345.67"),
        _bank_payment("p", date(2026, 4, 17), "12330.67"),
        _adjustment("adj", date(2026, 4, 17), "15"),
        _card_receipt("r", date(2026, 4, 17), CARD_A, "12345.67"),
    ]
    pending, cleared = match_clearing(txns, CLEARING)
    assert pending == []
    assert cleared == [
        ClearedPayment(
            txn_id="p",
            date=date(2026, 4, 17),
            amount=Decimal("12330.67"),
            receipt_txn_id="r",
            receipt_date=date(2026, 4, 17),
            excess=Decimal("15.00"),
            cashback=True,
        )
    ]


def test_within_tolerance_clear_is_not_cashback():
    txns = [
        _bank_payment("p", date(2026, 10, 20), "5000"),
        _card_receipt("r", date(2026, 10, 21), CARD_A, "4995"),
    ]
    (cleared,) = match_clearing(txns, CLEARING)[1]
    assert (cleared.excess, cleared.cashback) == (Decimal("-5"), False)


def test_receipt_beyond_the_cashback_cap_does_not_clear():
    txns = [
        _bank_payment("p", date(2026, 10, 20), "5000"),
        _card_receipt("r", date(2026, 10, 21), CARD_A, "5100.01"),
    ]
    assert [p.txn_id for p in payments_in_transit(txns, CLEARING)] == ["p"]


def test_receipt_smaller_than_the_payment_is_never_cashback():
    txns = [
        _bank_payment("p", date(2026, 10, 20), "5000"),
        _card_receipt("r", date(2026, 10, 21), CARD_A, "4950"),
    ]
    assert [p.txn_id for p in payments_in_transit(txns, CLEARING)] == ["p"]


def test_within_tolerance_match_beats_an_earlier_cashback_match():
    txns = [
        _bank_payment("early", date(2026, 10, 1), "5000"),
        _bank_payment("close", date(2026, 10, 20), "5045"),
        _card_receipt("r", date(2026, 10, 21), CARD_A, "5050"),
    ]
    assert [p.txn_id for p in payments_in_transit(txns, CLEARING)] == ["early"]


def test_cashback_match_takes_the_smallest_excess_first():
    txns = [
        _bank_payment("big_gap", date(2026, 10, 1), "5000"),
        _bank_payment("small_gap", date(2026, 10, 20), "5030"),
        _card_receipt("r", date(2026, 10, 21), CARD_A, "5080"),
    ]
    pending, cleared = match_clearing(txns, CLEARING)
    assert [p.txn_id for p in pending] == ["big_gap"]
    assert [(c.txn_id, c.excess) for c in cleared] == [("small_gap", Decimal("50"))]


def test_attribution_counts_card_postings_without_a_txn_id():
    # The opening balance has no txn-id, so it is missing from `transactions`
    # but present in the card postings: outstanding is 4000 + 300.
    opening = _txn(
        "", date(2026, 7, 1), ("Equity:Opening-Balances", "4000"), (CARD_A, "-4000")
    )
    spend = _spend("a", date(2026, 10, 1), CARD_A, "300")
    pay = _bank_payment("p", date(2026, 10, 20), "4300")
    txns = [spend, pay]
    (without,) = payments_in_transit(txns, CLEARING)
    assert without.account is None
    (payment,) = payments_in_transit(
        txns, CLEARING, card_postings=_postings(opening, spend, pay)
    )
    assert payment.account == CARD_A


def test_card_postings_respect_closed_cards():
    spend = _spend("a", date(2026, 10, 1), CARD_A, "300")
    pay = _bank_payment("p", date(2026, 10, 20), "300")
    (payment,) = payments_in_transit(
        [spend, pay],
        CLEARING,
        closed=frozenset({CARD_A}),
        card_postings=_postings(spend, pay),
    )
    assert payment.account is None


# ── Review follow-ups: per-card matching, date bounds, tiny payments ────────


def test_receipt_clears_only_its_own_cards_payment():
    # Reviewer case: A owes 10000, B owes 10060. A's receipt (10000 + 80
    # cashback) must clear A's payment, not B's closer-in-amount one.
    txns = [
        _spend("oa", date(2026, 9, 1), CARD_A, "10000"),
        _spend("ob", date(2026, 9, 1), CARD_B, "10060"),
        _bank_payment("pb", date(2026, 10, 10), "10060"),
        _bank_payment("pa", date(2026, 10, 12), "10000"),
        _card_receipt("ra", date(2026, 10, 13), CARD_A, "10080"),
    ]
    pending, cleared = match_clearing(txns, CLEARING)
    assert [(p.txn_id, p.account) for p in pending] == [("pb", CARD_B)]
    assert [(c.txn_id, c.receipt_txn_id, c.excess) for c in cleared] == [
        ("pa", "ra", Decimal("80"))
    ]
    assert per_card(pending) == {
        CARD_B: {"amount": "10060.00", "date": "2026-10-10", "count": 1}
    }


def test_receipt_may_clear_an_unattributed_payment():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "20000"),
        _spend("b", date(2026, 10, 1), CARD_B, "20004"),
        _bank_payment("p", date(2026, 10, 20), "20002"),  # ambiguous: None
        _card_receipt("r", date(2026, 10, 22), CARD_B, "20002"),
    ]
    assert payments_in_transit(txns, CLEARING) == []


def test_receipt_does_not_clear_another_cards_payment():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5000"),
        _bank_payment("p", date(2026, 10, 20), "5000"),
        _card_receipt("r", date(2026, 10, 21), CARD_B, "5000"),
    ]
    assert [(p.txn_id, p.account) for p in payments_in_transit(txns, CLEARING)] == [
        ("p", CARD_A)
    ]


def test_old_uncleared_payment_does_not_take_a_new_receipt():
    # Reviewer case: a 2025 payment whose receipt was never imported must not
    # take the Oct-2026 receipt from the Oct-2026 payment.
    today = date(2026, 11, 1)
    txns = [
        _spend("o", date(2025, 1, 1), CARD_A, "5000"),
        _bank_payment("p_old", date(2025, 1, 10), "5000"),
        _spend("o2", date(2026, 9, 1), CARD_A, "5000"),
        _bank_payment("p_new", date(2026, 10, 10), "5000"),
        _card_receipt("r_new", date(2026, 10, 11), CARD_A, "5000"),
    ]
    pending, cleared = match_clearing(txns, CLEARING)
    assert [c.txn_id for c in cleared] == ["p_new"]
    assert [p.txn_id for p in pending] == ["p_old"]
    # p_old is stale, so nothing is in transit today.
    assert payments_in_transit(txns, CLEARING, today=today) == []


def test_receipt_only_looks_back_sixty_days():
    receipt_day = date(2026, 10, 30)
    txns = [
        _bank_payment("p", receipt_day - timedelta(days=MATCH_LAG_DAYS + 1), "5000"),
        _card_receipt("r", receipt_day, CARD_A, "5000"),
    ]
    assert [p.txn_id for p in payments_in_transit(txns, CLEARING)] == ["p"]
    txns[0] = _bank_payment("p", receipt_day - timedelta(days=MATCH_LAG_DAYS), "5000")
    assert payments_in_transit(txns, CLEARING) == []


def test_bank_history_before_card_coverage_is_not_in_transit():
    # Bank statements go back to 2025; card statements start in Sep 2026.
    # The early bill payments have no receipts and age out as stale.
    today = date(2026, 11, 1)
    txns = [
        _bank_payment("p1", date(2025, 5, 20), "4000"),
        _bank_payment("p2", date(2025, 6, 20), "3500"),
        _bank_payment("p3", date(2026, 1, 20), "6100"),
        _spend("s", date(2026, 9, 5), CARD_A, "2500"),
        _bank_payment("p4", date(2026, 10, 20), "2500"),
    ]
    got = payments_in_transit(txns, CLEARING, today=today)
    assert [(p.txn_id, p.account) for p in got] == [("p4", CARD_A)]
    assert summary(got)["amount"] == "2500.00"


def test_closest_date_wins_within_tolerance_and_ties_go_before_the_receipt():
    txns = [
        _bank_payment("before", date(2026, 10, 18), "3000"),
        _bank_payment("after", date(2026, 10, 22), "3000"),
        _card_receipt("r", date(2026, 10, 20), CARD_A, "3000"),
    ]
    assert [p.txn_id for p in payments_in_transit(txns, CLEARING)] == ["after"]


def test_tiny_payment_is_never_attributed_to_a_card_with_nothing_owed():
    txns = [
        _spend("a", date(2026, 9, 1), CARD_A, "500"),
        _card_receipt("ra", date(2026, 9, 20), CARD_A, "500"),  # paid off
        _bank_payment("tiny", date(2026, 10, 20), "5"),
    ]
    (payment,) = payments_in_transit(txns, CLEARING)
    assert payment.account is None


def test_tiny_payment_still_attributes_to_a_card_that_owes_it():
    txns = [
        _spend("a", date(2026, 10, 1), CARD_A, "5"),
        _bank_payment("tiny", date(2026, 10, 20), "5"),
    ]
    (payment,) = payments_in_transit(txns, CLEARING)
    assert payment.account == CARD_A
