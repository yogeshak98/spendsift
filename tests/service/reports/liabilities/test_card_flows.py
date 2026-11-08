from datetime import date
from decimal import Decimal

from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow
from backend.app.service.reports.liabilities.card_flows import classify_card_posting

CARD = "Liabilities:CreditCard:X"
DAY = date(2026, 7, 1)


def _txn(*legs: tuple[str, str]) -> TransactionRow:
    return TransactionRow(
        txn_id="t1",
        date=DAY,
        narration="n",
        postings=[
            PostingRow(
                date=DAY,
                account=account,
                amount=Decimal(amount),
                narration="n",
                tags=frozenset(),
            )
            for account, amount in legs
        ],
        year_file_hash="h",
    )


def test_classify_purchase():
    txn = _txn((CARD, "-500"), ("Expenses:Food", "500"))
    assert classify_card_posting(txn, CARD) == ("purchase", Decimal("-500"))


def test_classify_fee_is_charge():
    txn = _txn((CARD, "-45.26"), ("Expenses:Fees:CreditCard", "45.26"))
    assert classify_card_posting(txn, CARD) == ("charge", Decimal("-45.26"))


def test_classify_interest_is_charge():
    txn = _txn((CARD, "-120"), ("Expenses:Interest:CreditCard", "120"))
    assert classify_card_posting(txn, CARD)[0] == "charge"


def test_classify_debit_vs_clearing_is_negative_payment():
    txn = _txn((CARD, "-12345.67"), ("Assets:Transfer:CreditCard", "12345.67"))
    assert classify_card_posting(txn, CARD) == ("payment", Decimal("-12345.67"))


def test_classify_debit_vs_bank_is_negative_payment():
    txn = _txn((CARD, "-500"), ("Assets:Bank:HDFC", "500"))
    assert classify_card_posting(txn, CARD) == ("payment", Decimal("-500"))


def test_classify_debit_vs_checking_is_negative_payment():
    txn = _txn((CARD, "-500"), ("Assets:Checking:HDFC", "500"))
    assert classify_card_posting(txn, CARD)[0] == "payment"


def test_classify_debit_follows_the_clearing_account_passed_in():
    txn = _txn((CARD, "-500"), ("Assets:Test:Clear", "500"))
    assert (
        classify_card_posting(txn, CARD, clearing_account="Assets:Test:Clear")[0]
        == "payment"
    )
    assert classify_card_posting(txn, CARD)[0] == "purchase"


def test_classify_cash_advance_is_purchase():
    txn = _txn((CARD, "-1500"), ("Assets:Cash", "1500"))
    assert classify_card_posting(txn, CARD) == ("purchase", Decimal("-1500"))


def test_classify_wallet_load_is_purchase():
    txn = _txn((CARD, "-2000"), ("Assets:Wallet:AmazonPay", "2000"))
    assert classify_card_posting(txn, CARD)[0] == "purchase"


def test_classify_credit_from_someone_else_is_payment():
    txn = _txn((CARD, "41234"), ("Liabilities:Cash:Meera", "-41234"))
    assert classify_card_posting(txn, CARD) == ("payment", Decimal("41234"))


def test_classify_debit_for_someone_else_is_purchase():
    txn = _txn((CARD, "-41234"), ("Liabilities:Cash:Meera", "41234"))
    assert classify_card_posting(txn, CARD)[0] == "purchase"


def test_classify_opening_debit_is_adjustment():
    txn = _txn((CARD, "-4210.37"), ("Equity:Opening-Balances", "4210.37"))
    assert classify_card_posting(txn, CARD) == ("adjustment", Decimal("-4210.37"))


def test_classify_credit_vs_equity_is_payment():
    txn = _txn((CARD, "4210.37"), ("Equity:Opening-Balances", "-4210.37"))
    assert classify_card_posting(txn, CARD)[0] == "payment"


def test_classify_emi_conversion_credit_is_emi():
    txn = _txn((CARD, "21234.56"), ("Liabilities:Loan:CardEMI", "-21234.56"))
    assert classify_card_posting(txn, CARD) == ("emi", Decimal("21234.56"))


def test_classify_emi_principal_debit_is_emi():
    txn = _txn((CARD, "-2000"), ("Liabilities:Loan:CardEMI", "2000"))
    assert classify_card_posting(txn, CARD)[0] == "emi"


def test_classify_cashback_is_refund():
    txn = _txn((CARD, "542"), ("Income:Refund:CreditCard:UNIFIED", "-542"))
    assert classify_card_posting(txn, CARD) == ("refund", Decimal("542"))


def test_classify_merchant_reversal_is_refund():
    txn = _txn((CARD, "300"), ("Expenses:Shopping", "-300"))
    assert classify_card_posting(txn, CARD)[0] == "refund"


def test_classify_credit_vs_unknown_root_is_refund():
    txn = _txn((CARD, "10"), ("Liabilities:CreditCard:Y", "-10"))
    assert classify_card_posting(txn, CARD)[0] == "refund"


def test_classify_split_purchase_uses_largest_leg():
    txn = _txn(
        (CARD, "-1000"),
        ("Expenses:Shopping", "900"),
        ("Expenses:Fees:CreditCard", "100"),
    )
    assert classify_card_posting(txn, CARD) == ("purchase", Decimal("-1000"))


def test_classify_emi_with_gst_uses_largest_leg():
    txn = _txn(
        (CARD, "-2180"),
        ("Liabilities:Loan:CardEMI", "2000"),
        ("Expenses:Fees:CreditCard", "180"),
    )
    assert classify_card_posting(txn, CARD)[0] == "emi"


def test_classify_tie_takes_first_leg_in_posting_order():
    txn = _txn(
        (CARD, "-200"),
        ("Expenses:Fees:CreditCard", "100"),
        ("Expenses:Shopping", "100"),
    )
    assert classify_card_posting(txn, CARD)[0] == "charge"


# --- credits mirror debits: only a bank, the clearing account, a person or
# --- equity pays the card; any other asset is money handed back -------------


def test_classify_credit_vs_bank_is_payment():
    for bank in ("Assets:Bank:HDFC", "Assets:Checking:HDFC"):
        txn = _txn((CARD, "5000"), (bank, "-5000"))
        assert classify_card_posting(txn, CARD) == ("payment", Decimal("5000")), bank


def test_classify_credit_vs_clearing_is_payment():
    txn = _txn((CARD, "5000"), ("Assets:Transfer:CreditCard", "-5000"))
    assert classify_card_posting(txn, CARD)[0] == "payment"


def test_classify_credit_follows_the_clearing_account_passed_in():
    txn = _txn((CARD, "500"), ("Assets:Test:Clear", "-500"))
    assert (
        classify_card_posting(txn, CARD, clearing_account="Assets:Test:Clear")[0]
        == "payment"
    )
    assert classify_card_posting(txn, CARD)[0] == "refund"


def test_classify_credit_from_a_person_is_payment():
    txn = _txn((CARD, "800"), ("Liabilities:People:Ravi", "-800"))
    assert classify_card_posting(txn, CARD)[0] == "payment"


def test_classify_cash_advance_reversal_is_refund():
    txn = _txn((CARD, "1500"), ("Assets:Cash", "-1500"))
    assert classify_card_posting(txn, CARD) == ("refund", Decimal("1500"))


def test_classify_wallet_reversal_is_refund():
    txn = _txn((CARD, "2000"), ("Assets:Wallet:AmazonPay", "-2000"))
    assert classify_card_posting(txn, CARD)[0] == "refund"
