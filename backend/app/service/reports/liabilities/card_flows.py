"""What a credit-card posting is: a purchase, charge, payment, refund, EMI leg
or adjustment.

The card leg's sign alone cannot tell these apart -- a cashback credit and a
bill payment both reduce the debt, an EMI conversion credit and a refund both
look like money back. The dominant counter-leg (the non-card posting with the
largest |amount|) says which it is.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from backend.app.service.ingest.card_accounts import card_clearing_account
from backend.app.service.reports.health.ledger_view import TransactionRow

CardFlow = Literal["purchase", "charge", "payment", "refund", "emi", "adjustment"]

_CHARGE_PREFIXES = ("Expenses:Fees:", "Expenses:Interest:")
_LOAN_PREFIX = "Liabilities:Loan:"
# The accounts a card bill is paid from, besides the clearing account.
_BANK_PREFIXES = ("Assets:Bank:", "Assets:Checking:")
# Who else can pay the card: someone settling their share, or an entry from
# before the journal began.
_OTHER_PAYERS = ("Liabilities:Cash:", "Liabilities:People:", "Equity:")


def _counter_account(txn: TransactionRow, card_account: str) -> str:
    """The non-card posting with the largest |amount|; first wins a tie.

    Empty string when the transaction has no non-card posting.

    Ranked by `inr_amount`, not `amount`: a commodity leg (units of a fund or
    a foreign currency) carries units in `amount`, which are not comparable
    with the INR legs beside it; its INR cost is."""
    best = ""
    best_abs = Decimal(-1)
    for p in txn.postings:
        if p.account == card_account:
            continue
        size = abs(p.inr_amount)
        if size > best_abs:
            best, best_abs = p.account, size
    return best


def classify_card_posting(
    txn: TransactionRow, card_account: str, clearing_account: str | None = None
) -> tuple[CardFlow, Decimal]:
    """Kind of `card_account`'s posting in `txn`, and its signed amount.

    The amount keeps the ledger sign (a charge on the card is negative) and is
    the sum of every `card_account` posting in the transaction.
    `clearing_account` is the card bill-payment clearing account; it defaults
    to the configured one.
    """
    if clearing_account is None:
        clearing_account = card_clearing_account()
    # The card's own postings are summed by `amount`: a card account holds
    # INR, and `amount` is what moves its balance (inr_amount equals it there).
    amount = sum(
        (p.amount for p in txn.postings if p.account == card_account), Decimal(0)
    )
    counter = _counter_account(txn, card_account)

    if amount < 0:
        if counter.startswith(_CHARGE_PREFIXES):
            return "charge", amount
        if counter.startswith("Expenses:"):
            return "purchase", amount
        if counter.startswith(_LOAN_PREFIX):
            return "emi", amount
        if counter.startswith("Equity:"):
            return "adjustment", amount
        if counter.startswith(_BANK_PREFIXES) or counter == clearing_account:
            # Money returned to the bank (an excess payment refunded): it
            # undoes a payment rather than adding spend.
            return "payment", amount
        # Any other asset (a cash advance, a wallet load) is money taken out
        # on the card: spend, like a purchase. So is Liabilities:Cash:* --
        # bought on the card for someone else.
        return "purchase", amount

    # Symmetric with debits: a bank, the clearing account, a person or equity
    # pays the card. Any other asset (cash, a wallet) is a cash advance or a
    # wallet load handed back -- a refund of spend, not a payment.
    if (
        counter.startswith(_BANK_PREFIXES + _OTHER_PAYERS)
        or counter == clearing_account
    ):
        return "payment", amount
    if counter.startswith(_LOAN_PREFIX):
        return "emi", amount
    return "refund", amount
