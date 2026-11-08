from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.transfer_match import (
    find_already_recorded_transfer,
    find_direct_card_payment,
)
from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow


def _txn(txn_id, d, account_a, amount_a, account_b, amount_b):
    return TransactionRow(
        txn_id=txn_id,
        date=d,
        narration="x",
        postings=[
            PostingRow(
                date=d,
                account=account_a,
                amount=Decimal(amount_a),
                narration="x",
                tags=frozenset(),
            ),
            PostingRow(
                date=d,
                account=account_b,
                amount=Decimal(amount_b),
                narration="x",
                tags=frozenset(),
            ),
        ],
        year_file_hash="h",
    )


def _candidate(d, amount, source, other, direction=Direction.DEBIT):
    return CandidateTxn(
        date=d,
        amount=Decimal(amount),
        raw_narration="CARD PAYMENT",
        normalized_narration="CARD PAYMENT",
        source_account=source,
        direction=direction,
        suggested_other_leg=other,
    )


def test_finds_match_within_window():
    txns = [
        _txn(
            "t1",
            date(2026, 4, 1),
            "Assets:Bank:HDFC",
            "-5000",
            "Liabilities:CreditCard:HDFC",
            "5000",
        )
    ]
    cand = _candidate(
        date(2026, 4, 3), "5000", "Assets:Bank:HDFC", "Liabilities:CreditCard:HDFC"
    )
    assert find_already_recorded_transfer(txns, cand) == "t1"


def test_no_match_outside_window():
    txns = [
        _txn(
            "t1",
            date(2026, 4, 1),
            "Assets:Bank:HDFC",
            "-5000",
            "Liabilities:CreditCard:HDFC",
            "5000",
        )
    ]
    cand = _candidate(
        date(2026, 4, 10), "5000", "Assets:Bank:HDFC", "Liabilities:CreditCard:HDFC"
    )
    assert find_already_recorded_transfer(txns, cand) is None


def test_no_match_when_amount_differs():
    txns = [
        _txn(
            "t1",
            date(2026, 4, 1),
            "Assets:Bank:HDFC",
            "-5000",
            "Liabilities:CreditCard:HDFC",
            "5000",
        )
    ]
    cand = _candidate(
        date(2026, 4, 1), "4999", "Assets:Bank:HDFC", "Liabilities:CreditCard:HDFC"
    )
    assert find_already_recorded_transfer(txns, cand) is None


def test_no_match_when_existing_transaction_has_a_third_leg():
    txn = TransactionRow(
        txn_id="t1",
        date=date(2026, 4, 1),
        narration="x",
        postings=[
            PostingRow(
                date=date(2026, 4, 1),
                account="Assets:Bank:HDFC",
                amount=Decimal("-5000"),
                narration="x",
                tags=frozenset(),
            ),
            PostingRow(
                date=date(2026, 4, 1),
                account="Liabilities:CreditCard:HDFC",
                amount=Decimal("4000"),
                narration="x",
                tags=frozenset(),
            ),
            PostingRow(
                date=date(2026, 4, 1),
                account="Expenses:Fees",
                amount=Decimal("1000"),
                narration="x",
                tags=frozenset(),
            ),
        ],
        year_file_hash="h",
    )
    cand = _candidate(
        date(2026, 4, 1), "5000", "Assets:Bank:HDFC", "Liabilities:CreditCard:HDFC"
    )
    assert find_already_recorded_transfer([txn], cand) is None


def test_works_card_first_order():
    """The already-committed transaction can be the card-side leg too — matching
    is symmetric, since it only compares the account *set*, not which side is
    candidate.source_account vs. candidate.suggested_other_leg."""
    txns = [
        _txn(
            "t1",
            date(2026, 4, 2),
            "Liabilities:CreditCard:HDFC",
            "5000",
            "Assets:Bank:HDFC",
            "-5000",
        )
    ]
    cand = _candidate(
        date(2026, 4, 2), "5000", "Assets:Bank:HDFC", "Liabilities:CreditCard:HDFC"
    )
    assert find_already_recorded_transfer(txns, cand) == "t1"


def test_no_match_when_suggested_other_leg_is_none():
    txns = [
        _txn(
            "t1",
            date(2026, 4, 1),
            "Assets:Bank:HDFC",
            "-5000",
            "Liabilities:CreditCard:HDFC",
            "5000",
        )
    ]
    cand = _candidate(date(2026, 4, 1), "5000", "Assets:Bank:HDFC", None)
    assert find_already_recorded_transfer(txns, cand) is None


# --- a card bill payment booked directly, before the clearing account -------
#
# A bill payment whose one side was committed straight bank <-> card (the
# model before Settings.card_clearing_account) must still count as recorded
# when its other side now suggests the clearing account; else the payment is
# booked twice and the clearing account never nets to zero.

CLEARING = "Assets:Transfer:CreditCard"
DIRECT = _txn(
    "t1",
    date(2026, 4, 1),
    "Liabilities:CreditCard:HDFC",
    "5000",
    "Assets:Bank:HDFC",
    "-5000",
)


def test_bank_side_clearing_payment_matches_a_direct_card_entry():
    cand = _candidate(date(2026, 4, 2), "5000", "Assets:Bank:HDFC", CLEARING)
    assert find_direct_card_payment([DIRECT], cand, CLEARING) == "t1"


def test_card_side_clearing_payment_matches_a_direct_bank_entry():
    cand = _candidate(
        date(2026, 4, 2),
        "5000",
        "Liabilities:CreditCard:HDFC",
        CLEARING,
        direction=Direction.CREDIT,
    )
    assert find_direct_card_payment([DIRECT], cand, CLEARING) == "t1"


def test_direct_card_payment_needs_the_clearing_suggestion():
    cand = _candidate(date(2026, 4, 2), "5000", "Assets:Bank:HDFC", "Expenses:Food")
    assert find_direct_card_payment([DIRECT], cand, CLEARING) is None


def test_direct_card_payment_ignores_entries_through_the_clearing_account():
    # Under the clearing model each side is its own entry; the card leg
    # against the clearing account is not this bank row already recorded.
    via_clearing = _txn(
        "t2",
        date(2026, 4, 1),
        "Liabilities:CreditCard:HDFC",
        "5000",
        CLEARING,
        "-5000",
    )
    cand = _candidate(date(2026, 4, 2), "5000", "Assets:Bank:HDFC", CLEARING)
    assert find_direct_card_payment([via_clearing], cand, CLEARING) is None


def test_direct_card_payment_respects_amount_and_window():
    late = _candidate(date(2026, 4, 9), "5000", "Assets:Bank:HDFC", CLEARING)
    other = _candidate(date(2026, 4, 2), "4999", "Assets:Bank:HDFC", CLEARING)
    assert find_direct_card_payment([DIRECT], late, CLEARING) is None
    assert find_direct_card_payment([DIRECT], other, CLEARING) is None


def test_direct_card_payment_rejects_a_reversed_sign():
    # Money moved card -> bank (bank +, card -) is not a bill payment.
    reversed_sign = _txn(
        "t3",
        date(2026, 4, 1),
        "Liabilities:CreditCard:HDFC",
        "-5000",
        "Assets:Bank:HDFC",
        "5000",
    )
    bank = _candidate(date(2026, 4, 2), "5000", "Assets:Bank:HDFC", CLEARING)
    card = _candidate(
        date(2026, 4, 2),
        "5000",
        "Liabilities:CreditCard:HDFC",
        CLEARING,
        direction=Direction.CREDIT,
    )
    assert find_direct_card_payment([reversed_sign], bank, CLEARING) is None
    assert find_direct_card_payment([reversed_sign], card, CLEARING) is None


def test_direct_card_payment_rejects_a_candidate_of_the_wrong_direction():
    bank_credit = _candidate(
        date(2026, 4, 2),
        "5000",
        "Assets:Bank:HDFC",
        CLEARING,
        direction=Direction.CREDIT,
    )
    card_debit = _candidate(
        date(2026, 4, 2), "5000", "Liabilities:CreditCard:HDFC", CLEARING
    )
    assert find_direct_card_payment([DIRECT], bank_credit, CLEARING) is None
    assert find_direct_card_payment([DIRECT], card_debit, CLEARING) is None


def test_direct_card_payment_card_side_ignores_a_cash_counter_leg():
    cash = _txn(
        "t4",
        date(2026, 4, 1),
        "Liabilities:CreditCard:HDFC",
        "5000",
        "Assets:Cash",
        "-5000",
    )
    card = _candidate(
        date(2026, 4, 2),
        "5000",
        "Liabilities:CreditCard:HDFC",
        CLEARING,
        direction=Direction.CREDIT,
    )
    assert find_direct_card_payment([cash], card, CLEARING) is None


def test_direct_card_payment_card_side_accepts_a_checking_counter_leg():
    checking = _txn(
        "t5",
        date(2026, 4, 1),
        "Liabilities:CreditCard:HDFC",
        "5000",
        "Assets:Checking:HDFC",
        "-5000",
    )
    card = _candidate(
        date(2026, 4, 2),
        "5000",
        "Liabilities:CreditCard:HDFC",
        CLEARING,
        direction=Direction.CREDIT,
    )
    assert find_direct_card_payment([checking], card, CLEARING) == "t5"


def test_direct_card_payment_skips_an_already_used_entry():
    first = _candidate(date(2026, 4, 2), "5000", "Assets:Bank:HDFC", CLEARING)
    second = _candidate(date(2026, 4, 3), "5000", "Assets:Bank:HDFC", CLEARING)
    used: set[str] = set()
    assert find_direct_card_payment([DIRECT], first, CLEARING, used=used) == "t1"
    used.add("t1")
    assert find_direct_card_payment([DIRECT], second, CLEARING, used=used) is None


def test_bbps_utility_row_cannot_reach_the_direct_payment_match():
    from backend.app.service.ingest.pipeline.dedup import Deduper

    row = CandidateTxn(
        date=date(2026, 4, 2),
        amount=Decimal("5000"),
        raw_narration="BBPS/BESCOM ELECTRICITY",
        normalized_narration="BBPS/BESCOM ELECTRICITY",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
    )
    Deduper(set()).mark([row])
    assert row.suggested_other_leg is None
    assert find_direct_card_payment([DIRECT], row, CLEARING) is None
