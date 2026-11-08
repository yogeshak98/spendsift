from __future__ import annotations

from collections.abc import Collection

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.reports.health.ledger_view import TransactionRow


def find_already_recorded_transfer(
    ledger_transactions: list[TransactionRow],
    candidate: CandidateTxn,
    window_days: int = 3,
) -> str | None:
    """Returns the txn_id of an already-committed pure 2-leg transfer between
    candidate.source_account and candidate.suggested_other_leg, equal in
    magnitude to candidate.amount, dated within window_days of candidate.date
    — or None if no such transaction exists. Order-independent: works whether
    the bank-side or the card-side import happened first."""
    if not candidate.suggested_other_leg:
        return None
    wanted = {candidate.source_account, candidate.suggested_other_leg}
    for txn in ledger_transactions:
        if len(txn.postings) != 2:
            continue
        if {p.account for p in txn.postings} != wanted:
            continue
        if any(abs(p.amount) != candidate.amount for p in txn.postings):
            continue
        if abs((txn.date - candidate.date).days) > window_days:
            continue
        return txn.txn_id
    return None


_CARD_ROOT = "Liabilities:CreditCard:"
# The accounts a card bill is paid from. Cash, wallets and investments are not:
# a card credit against them is something else (a cash advance reversal, a
# wallet refund) and must not be read as the other side of a bill payment.
_BANK_ROOTS = ("Assets:Bank:", "Assets:Checking:")


def find_direct_card_payment(
    ledger_transactions: list[TransactionRow],
    candidate: CandidateTxn,
    clearing_account: str,
    window_days: int = 3,
    used: Collection[str] = (),
) -> str | None:
    """The txn_id of a card bill payment already booked straight between a
    bank and a card -- or None.

    Applies only to a candidate whose suggested leg is `clearing_account`: a
    bank-side DEBIT, or a card-side CREDIT. It matches a pure 2-leg entry
    between its source and a counter-leg -- any card for a bank row, a bank or
    checking account for a card row -- of the same magnitude, within
    window_days, where the money went bank -> card (the bank posting negative,
    the card posting positive). Entries whose txn_id is in `used` are skipped,
    so a caller can let each entry stand for one candidate only.

    Such an entry is a bill payment whose other side was committed before the
    clearing account existed; booking this row through the clearing account
    too would record the payment twice."""
    if candidate.suggested_other_leg != clearing_account:
        return None
    source = candidate.source_account
    on_card = source.startswith(_CARD_ROOT)
    wanted_direction = Direction.CREDIT if on_card else Direction.DEBIT
    if candidate.direction != wanted_direction:
        return None
    for txn in ledger_transactions:
        if txn.txn_id in used or len(txn.postings) != 2:
            continue
        first, second = txn.postings
        if source not in (first.account, second.account):
            continue
        own, counter = (first, second) if first.account == source else (second, first)
        if not counter.account.startswith(_BANK_ROOTS if on_card else _CARD_ROOT):
            continue
        card, bank = (own, counter) if on_card else (counter, own)
        if card.amount <= 0 or bank.amount >= 0:
            continue
        if any(abs(p.amount) != candidate.amount for p in txn.postings):
            continue
        if abs((txn.date - candidate.date).days) > window_days:
            continue
        return txn.txn_id
    return None
