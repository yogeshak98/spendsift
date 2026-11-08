"""Card bill payments in transit ("Paid, awaiting card statement").

A bank-side bill payment is booked against the card clearing account
(`Settings.card_clearing_account`) and stays there until the card statement's
"payment received" row is imported against the same account. Until then the
card's ledger balance overstates what is owed. This read model finds those
payments; it never changes the journal, and net worth is unaffected (the
clearing balance is already an asset).

Rules (the response contract of `GET /api/reports/liabilities/credit-cards`):

- A *payment* (clearing debit) is a positive clearing-account posting in a
  transaction with no `Liabilities:CreditCard:` leg and no `Income:*` leg (the
  bank side; a fee/GST `Expenses:*` leg is fine, and the amount is the
  clearing posting). A clearing row from `Income:*` is a cashback or refund
  top-up, never a payment, with or without a txn-id. A *receipt* (clearing
  credit) is a negative clearing-account posting in a transaction with a card
  leg (the card side). Other clearing postings are ignored.
- Attribution comes first: a payment of amount A on date D goes to card C
  only when C is the one open card with `outstanding_C(D) > 0` and
  `|outstanding_C(D) - A| <= tolerance(A)`, where `outstanding_C(D)` is minus
  the sum of C's postings dated <= D, counting every posting
  (`card_postings`), including transactions without a txn-id such as opening
  balances. Zero or several such cards: `account` is None (counted only in
  the total). `outstanding_C(D)` is the ledger figure, so an earlier payment
  still in transit is not deducted from it: a second payment that covers
  only part of the bill is left unattributed.
- Matching: receipts in (date, txn_id) order each clear one unmatched payment
  that is attributed to the receipt's card or unattributed, dated from
  MATCH_LAG_DAYS before the receipt to MATCH_LEAD_DAYS after it: the one
  within `tolerance(payment)` closest in date, else the one the receipt
  exceeds by the least, by at most `cashback_cap(payment)` (the card credits
  cashback with the payment), then closest in date. Date ties go to the
  earlier payment. `match_clearing` also returns the cleared payments with
  their excess (`receipt - payment`).
- Stale: a payment left unmatched and dated more than STALE_DAYS before
  today is stale (its statement never arrived, or the bank history predates
  the card statements). It is left out of the payments in transit, so out of
  every total and of net owed. The response does not report it.
- Source: the route passes every transaction, those without a txn-id too
  (`ledger_view.all_flow_transactions`), so a hand-written payment or receipt
  is seen. A hand-written row follows the same rules: it is a payment only
  when it meets the payment rule, and its `txn_id` is a read-only
  `untracked-<hash>`.
- Known limit: one bank debit paying several cards (an aggregator payment)
  never matches its separate receipts; it stays unattributed until stale.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from backend.app.models.domain.accounts import CREDIT_CARD_PREFIX
from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow

TOLERANCE_ABS = Decimal("10")
TOLERANCE_RATE = Decimal("0.001")
# A card-side receipt may be dated up to this many days before its bank debit.
MATCH_LEAD_DAYS = 7
# ...and up to this many days after it (about one billing cycle plus grace).
MATCH_LAG_DAYS = 60
# A payment with no receipt dated more than this many days before today is
# stale: no longer counted as in transit.
STALE_DAYS = 60

# A card receipt may exceed its bank payment by cashback/refund credited with
# it: by up to ₹100 or 2% of the payment, whichever is larger.
CASHBACK_CAP_ABS = Decimal("100")
CASHBACK_CAP_RATE = Decimal("0.02")

_CENT = Decimal("0.01")
# A clearing debit with an income leg is a cashback/refund top-up, not a payment.
_INCOME_PREFIX = "Income:"


def tolerance(amount: Decimal) -> Decimal:
    """₹10 or 0.1% of `|amount|`, whichever is larger."""
    return max(TOLERANCE_ABS, abs(amount) * TOLERANCE_RATE)


def cashback_cap(amount: Decimal) -> Decimal:
    """₹100 or 2% of `|amount|`, whichever is larger."""
    return max(CASHBACK_CAP_ABS, abs(amount) * CASHBACK_CAP_RATE)


@dataclass(frozen=True)
class InTransitPayment:
    txn_id: str
    date: date
    amount: Decimal  # > 0
    account: str | None  # the attributed card, else None


@dataclass(frozen=True)
class ClearedPayment:
    """A bank payment that a card-side receipt has cleared."""

    txn_id: str
    date: date
    amount: Decimal  # the bank debit, > 0
    receipt_txn_id: str
    receipt_date: date
    excess: Decimal  # receipt - payment; > 0 is extra credit on the card
    cashback: bool  # cleared by the cashback rule, not within tolerance


def payments_in_transit(
    transactions: list[TransactionRow],
    clearing: str,
    *,
    closed: frozenset[str] = frozenset(),
    card_postings: list[PostingRow] | None = None,
    today: date | None = None,
) -> list[InTransitPayment]:
    """Every unmatched bank payment, date asc, attributed per the module rules.

    `closed` cards are never attribution candidates. `card_postings` (every
    posting in the ledger, including those of transactions without a txn-id)
    gives each card's ledger balance; without it, the card postings in
    `transactions` are used. With `today`, a payment dated more than
    STALE_DAYS before it is stale and left out."""
    pending, _ = match_clearing(
        transactions, clearing, closed=closed, card_postings=card_postings
    )
    if today is None:
        return pending
    oldest = today - timedelta(days=STALE_DAYS)
    return [p for p in pending if p.date >= oldest]


@dataclass(frozen=True)
class _Receipt:
    txn_id: str
    date: date
    amount: Decimal  # > 0
    cards: frozenset[str]


def match_clearing(
    transactions: list[TransactionRow],
    clearing: str,
    *,
    closed: frozenset[str] = frozenset(),
    card_postings: list[PostingRow] | None = None,
) -> tuple[list[InTransitPayment], list[ClearedPayment]]:
    """Attribute every bank payment, then let each card receipt clear one.
    Returns the payments left (attributed, date asc, stale ones included) and
    the cleared ones (in receipt order)."""
    debits: list[InTransitPayment] = []
    receipts: list[_Receipt] = []
    for txn in transactions:
        cards = frozenset(
            p.account for p in txn.postings if p.account.startswith(CREDIT_CARD_PREFIX)
        )
        from_income = any(p.account.startswith(_INCOME_PREFIX) for p in txn.postings)
        for p in txn.postings:
            if p.account != clearing:
                continue
            if p.amount > 0 and not cards and not from_income:
                debits.append(InTransitPayment(txn.txn_id, txn.date, p.amount, None))
            elif p.amount < 0 and cards:
                receipts.append(_Receipt(txn.txn_id, txn.date, -p.amount, cards))
    source = (
        card_postings
        if card_postings is not None
        else [p for txn in transactions for p in txn.postings]
    )
    history = _card_history(source, closed)
    payments = [
        replace(d, account=_attribute(d.amount, d.date, history))
        for d in sorted(debits, key=lambda d: (d.date, d.txn_id))
    ]
    matched = [False] * len(payments)
    cleared: list[ClearedPayment] = []
    for receipt in sorted(receipts, key=lambda r: (r.date, r.txn_id)):
        i = _pick(payments, matched, receipt)
        if i is None:
            continue
        matched[i] = True
        payment = payments[i]
        excess = receipt.amount - payment.amount
        cleared.append(
            ClearedPayment(
                txn_id=payment.txn_id,
                date=payment.date,
                amount=payment.amount,
                receipt_txn_id=receipt.txn_id,
                receipt_date=receipt.date,
                excess=excess,
                cashback=abs(excess) > tolerance(payment.amount),
            )
        )
    pending = [p for p, m in zip(payments, matched, strict=True) if not m]
    return pending, cleared


def _pick(
    payments: list[InTransitPayment], matched: list[bool], receipt: _Receipt
) -> int | None:
    """The payment a receipt clears. Candidates: unmatched, attributed to the
    receipt's card or unattributed, dated from MATCH_LAG_DAYS before the
    receipt to MATCH_LEAD_DAYS after it. First the one within tolerance
    closest in date; else the one the receipt exceeds by the least, within the
    cashback cap (then closest in date). Date ties go to the earlier one."""
    first = receipt.date - timedelta(days=MATCH_LAG_DAYS)
    last = receipt.date + timedelta(days=MATCH_LEAD_DAYS)
    near: tuple[tuple, int] | None = None
    cashback: tuple[tuple, int] | None = None
    for i, payment in enumerate(payments):
        if matched[i] or not first <= payment.date <= last:
            continue
        if payment.account is not None and payment.account not in receipt.cards:
            continue
        gap = (abs((payment.date - receipt.date).days), payment.date, payment.txn_id)
        excess = receipt.amount - payment.amount
        if abs(excess) <= tolerance(payment.amount):
            if near is None or gap < near[0]:
                near = (gap, i)
        elif 0 < excess <= cashback_cap(payment.amount):
            key = (excess, *gap)
            if cashback is None or key < cashback[0]:
                cashback = (key, i)
    if near is not None:
        return near[1]
    return None if cashback is None else cashback[1]


def _card_history(
    postings: list[PostingRow], closed: frozenset[str]
) -> dict[str, list[tuple[date, Decimal]]]:
    """Open card -> its postings as (date, amount)."""
    out: dict[str, list[tuple[date, Decimal]]] = {}
    for p in postings:
        if p.account.startswith(CREDIT_CARD_PREFIX) and p.account not in closed:
            out.setdefault(p.account, []).append((p.date, p.amount))
    return out


def _attribute(
    amount: Decimal, on: date, history: dict[str, list[tuple[date, Decimal]]]
) -> str | None:
    """The one open card that owes something on `on` (outstanding > 0) within
    tolerance of `amount`, else None (no such card, or more than one)."""
    limit = tolerance(amount)
    hits = []
    for card, rows in history.items():
        owed = -sum((a for d, a in rows if d <= on), Decimal(0))
        if owed > 0 and abs(owed - amount) <= limit:
            hits.append(card)
    return hits[0] if len(hits) == 1 else None


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def summary(payments: list[InTransitPayment]) -> dict:
    """`InTransitSummary`: amount, attributed, unattributed (2 dp) and the
    payments (date asc), `amount == attributed + unattributed`.

    Each payment is rounded first and the totals sum the rounded amounts, so
    the totals always equal the sum of the listed payments."""
    ordered = sorted(payments, key=lambda p: (p.date, p.txn_id))
    attributed = Decimal("0.00")
    unattributed = Decimal("0.00")
    rows: list[dict] = []
    for p in ordered:
        amount = _money(p.amount)
        if p.account is None:
            unattributed += amount
        else:
            attributed += amount
        rows.append(
            {
                "txn_id": p.txn_id,
                "date": p.date.isoformat(),
                "amount": str(amount),
                "account": p.account,
            }
        )
    return {
        "amount": str(attributed + unattributed),
        "attributed": str(attributed),
        "unattributed": str(unattributed),
        "payments": rows,
    }


def per_card(payments: list[InTransitPayment]) -> dict[str, dict]:
    """Card -> `CardInTransit` (amount sum at 2 dp, latest date, count) for
    every card with at least one attributed payment."""
    totals: dict[str, Decimal] = {}
    latest: dict[str, date] = {}
    counts: dict[str, int] = {}
    for p in payments:
        if p.account is None:
            continue
        card = p.account
        totals[card] = totals.get(card, Decimal("0.00")) + _money(p.amount)
        latest[card] = max(latest.get(card, p.date), p.date)
        counts[card] = counts.get(card, 0) + 1
    return {
        card: {
            "amount": str(totals[card]),
            "date": latest[card].isoformat(),
            "count": counts[card],
        }
        for card in sorted(totals)
    }
