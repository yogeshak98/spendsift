from datetime import date
from decimal import Decimal

from backend.app.core.settings import Settings
from backend.app.service.reports.cashflow.funding import (
    Via,
    funding_leg,
    kind_of,
    matches,
)
from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow

CARD = "Liabilities:CreditCard:HDFC4321"
BANK = "Assets:Checking:IDFC"
D = date(2026, 8, 3)


def _txn(*legs: tuple[str, str]) -> TransactionRow:
    return TransactionRow(
        txn_id="t1",
        date=D,
        narration="x",
        postings=[
            PostingRow(D, a, Decimal(v), "x", frozenset(), inr_amount=Decimal(v))
            for a, v in legs
        ],
        year_file_hash="h",
    )


def test_kind_of():
    broker = Settings().platform_cash_accounts["groww"]
    assert kind_of(CARD) == "card"
    assert kind_of(BANK) == "bank"
    assert kind_of("Assets:Bank:HDFC") == "bank"
    assert kind_of(broker) == "other"
    assert kind_of("Liabilities:People:X") == "other"


def test_funding_leg_card_purchase():
    via = funding_leg(_txn(("Expenses:Food", "103.80"), (CARD, "-103.80")), "Expenses:")
    assert via == Via("card", "HDFC4321", CARD)


def test_funding_leg_refund_takes_positive_card_leg():
    via = funding_leg(_txn(("Expenses:Shopping", "-500"), (CARD, "500")), "Expenses:")
    assert via == Via("card", "HDFC4321", CARD)


def test_funding_leg_split_prefers_card():
    txn = _txn(("Expenses:Food", "1000"), (BANK, "-400"), (CARD, "-600"))
    assert funding_leg(txn, "Expenses:") == Via("card", "HDFC4321", CARD)


def test_funding_leg_ties_go_to_posting_order():
    txn = _txn(("Expenses:Food", "1000"), (BANK, "-400"), ("Assets:Bank:X", "-600"))
    assert funding_leg(txn, "Expenses:") == Via("bank", "IDFC", BANK)


def test_funding_leg_salary_with_tds_is_bank():
    txn = _txn(
        ("Income:Salary", "-100000"),
        ("Income:Salary:TDS", "10000"),
        ("Assets:Checking:HDFC", "90000"),
    )
    assert funding_leg(txn, "Income:") == Via("bank", "HDFC", "Assets:Checking:HDFC")


def test_funding_leg_ignores_same_sign_legs():
    # Only legs opposite in sign to the root net qualify.
    txn = _txn(("Expenses:Food", "100"), (CARD, "50"), (BANK, "-150"))
    assert funding_leg(txn, "Expenses:") == Via("bank", "IDFC", BANK)


def test_funding_leg_none_for_pure_reclass():
    txn = _txn(("Expenses:A", "50"), ("Expenses:B", "-50"))
    assert funding_leg(txn, "Expenses:") is None


def test_funding_leg_zero_net_counts_as_positive():
    # Net 0 is treated as +, so a negative non-P&L leg qualifies.
    txn = _txn(("Expenses:A", "50"), ("Expenses:B", "-50"), (CARD, "-1"), (BANK, "1"))
    assert funding_leg(txn, "Expenses:") == Via("card", "HDFC4321", CARD)


def test_matches_null_via_is_other():
    assert matches(None, "other", None) is True
    assert matches(None, "card", None) is False
    assert matches(None, "all", None) is True


def test_matches_account_filter():
    via = Via("card", "X", "Liabilities:CreditCard:X")
    assert matches(via, "card", "Liabilities:CreditCard:X") is True
    assert matches(via, "card", "Liabilities:CreditCard:Y") is False
    assert matches(via, "all", "Liabilities:CreditCard:Y") is False
    assert matches(None, "all", "Liabilities:CreditCard:X") is False
    assert matches(via, "bank", None) is False
