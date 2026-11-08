"""Dashboard needs-action items, each with the route that resolves it.

- `card_due`: one per card that is overdue or due soon, with the payments
  already made to it that its statement has not shown yet (and the latest
  one's date).
  - Overdue (`overdue: true`, listed first): the latest statement's due date
    has passed, and the card's credits after that statement (payments,
    refunds) plus its payments in transit dated after it fall short of the
    statement balance by more than the in-transit tolerance. `date` is the
    missed due date and `days` is negative.
  - Due soon: an amount outstanding and a next due date (the latest
    statement's due date if still ahead, else the next one) within
    `CARD_DUE_DAYS` of the report date.
- `uncategorized`: the `Expenses:Uncategorized` postings of the report month
  to date (count and amount).
- `statements_expected`: the cards whose next statement date (from card
  properties) falls within `STATEMENT_EXPECTED_DAYS` after the report date.
- `stale_import`: one per open bank account with a balance whose last posting
  is more than `STALE_IMPORT_DAYS` before the report date (the Net Worth
  Bank & cash stale rule).

The cards come from `liabilities.credit_card_summary` and the payments in
transit from `in_transit`, the same read models the Net Worth overview uses.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.app.service.reports.cashflow.money import q
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    TransactionRow,
    display_account,
)
from backend.app.service.reports.health.uncategorized import UNCATEGORIZED_ACCOUNT
from backend.app.service.reports.liabilities import in_transit, liabilities
from backend.app.service.reports.networth.overview_groups import stale_bank_accounts

CARD_DUE_DAYS = 7
STATEMENT_EXPECTED_DAYS = 21

ROUTE_CARDS = "networth/liabilities"
ROUTE_UNCATEGORIZED = "expense/uncategorized"
ROUTE_IMPORT = "ledger/import"

_ZERO = Decimal(0)


def _item(kind: str, route: str, **fields) -> dict:
    base = {
        "kind": kind,
        "route": route,
        "account": None,
        "account_display": None,
        "date": None,
        "days": None,
        "amount": None,
        "paid_awaiting_statement": None,
        "paid_on": None,
        "overdue": None,
        "count": None,
        "statements": [],
    }
    base.update(fields)
    return base


def _next_due(card: dict, report_date: date) -> date | None:
    for key in ("current_due_date", "next_due_date"):
        value = card.get(key)
        if value and date.fromisoformat(value) >= report_date:
            return date.fromisoformat(value)
    return None


def _missed_due(
    card: dict,
    report_date: date,
    card_rows: list[PostingRow],
    payments: list[in_transit.InTransitPayment],
) -> date | None:
    """The latest statement's due date when it has passed with the statement
    balance unpaid, else None."""
    due_value, statement_value = (
        card.get("current_due_date"),
        card.get("last_statement_date"),
    )
    if not due_value or not statement_value:
        return None
    due, statement = date.fromisoformat(due_value), date.fromisoformat(statement_value)
    if due >= report_date:
        return None
    balance = -sum((r.inr_amount for r in card_rows if r.date <= statement), _ZERO)
    if balance <= 0:
        return None
    credited = sum(
        (r.inr_amount for r in card_rows if r.date > statement and r.inr_amount > 0),
        _ZERO,
    ) + sum(
        (
            p.amount
            for p in payments
            if p.account == card["account"] and p.date > statement
        ),
        _ZERO,
    )
    if credited >= balance - in_transit.tolerance(balance):
        return None
    return due


def _card_items(
    cards: list[dict],
    rows: list[PostingRow],
    payments: list[in_transit.InTransitPayment],
    report_date: date,
    names: dict,
) -> list[dict]:
    paid = in_transit.per_card(payments)
    out = []
    for card in cards:
        account = card["account"]
        outstanding = Decimal(card["outstanding"])
        card_rows = [r for r in rows if r.account == account]
        missed = _missed_due(card, report_date, card_rows, payments)
        due = missed or _next_due(card, report_date)
        if due is None:
            continue
        days = (due - report_date).days
        if missed is None and (outstanding <= 0 or days > CARD_DUE_DAYS):
            continue
        out.append(
            _item(
                "card_due",
                ROUTE_CARDS,
                account=account,
                account_display=display_account(account, names),
                date=due.isoformat(),
                days=days,
                amount=q(outstanding),
                paid_awaiting_statement=paid[account]["amount"]
                if account in paid
                else "0.00",
                paid_on=paid[account]["date"] if account in paid else None,
                overdue=missed is not None,
            )
        )
    return sorted(out, key=lambda i: (not i["overdue"], i["days"], i["account"]))


def _statements_item(cards: list[dict], report_date: date, names: dict) -> dict | None:
    horizon = report_date + timedelta(days=STATEMENT_EXPECTED_DAYS)
    expected = []
    for card in cards:
        value = card.get("next_statement_date")
        if not value:
            continue
        when = date.fromisoformat(value)
        if report_date < when <= horizon:
            expected.append(
                {
                    "account": card["account"],
                    "account_display": display_account(card["account"], names),
                    "date": when.isoformat(),
                }
            )
    if not expected:
        return None
    expected.sort(key=lambda s: (s["date"], s["account"]))
    return _item(
        "statements_expected",
        ROUTE_IMPORT,
        count=len(expected),
        date=expected[0]["date"],
        statements=expected,
    )


def _uncategorized_item(rows: list[PostingRow], report_date: date) -> dict | None:
    month = [
        r
        for r in rows
        if r.account == UNCATEGORIZED_ACCOUNT
        and r.date.year == report_date.year
        and r.date.month == report_date.month
        and r.date <= report_date
    ]
    if not month:
        return None
    return _item(
        "uncategorized",
        ROUTE_UNCATEGORIZED,
        count=len(month),
        amount=q(sum((r.inr_amount for r in month), _ZERO)),
    )


def _stale_items(
    rows: list[PostingRow], report_date: date, closed: frozenset[str], names: dict
) -> list[dict]:
    out = [
        _item(
            "stale_import",
            ROUTE_IMPORT,
            account=account,
            account_display=display_account(account, names),
            date=seen.isoformat(),
            days=(report_date - seen).days,
        )
        for account, seen in stale_bank_accounts(rows, report_date, closed).items()
    ]
    return sorted(out, key=lambda i: (-i["days"], i["account"]))


def needs_action(
    rows: list[PostingRow],
    flow_txns: list[TransactionRow],
    txns: list[TransactionRow],
    report_date: date,
    *,
    card_properties: dict,
    card_clearing_account: str,
    card_expiry_soon_days: int,
    closed: frozenset[str],
    names: dict[str, str],
) -> list[dict]:
    """`rows`, `flow_txns` (with untracked) and `txns` (tracked) must already be
    bounded to the report date."""
    cards = liabilities.credit_card_summary(
        txns,
        properties=card_properties,
        today=report_date,
        closed=closed,
        postings=rows,
        expiry_soon_days=card_expiry_soon_days,
    )
    payments = in_transit.payments_in_transit(
        flow_txns,
        card_clearing_account,
        closed=closed,
        card_postings=rows,
        today=report_date,
    )
    items = _card_items(cards, rows, payments, report_date, names)
    for single in (
        _uncategorized_item(rows, report_date),
        _statements_item(cards, report_date, names),
    ):
        if single is not None:
            items.append(single)
    items.extend(_stale_items(rows, report_date, closed, names))
    return items
