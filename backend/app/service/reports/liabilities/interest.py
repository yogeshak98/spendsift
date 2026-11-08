from __future__ import annotations

from decimal import Decimal

from backend.app.service.reports.health.ledger_view import TransactionRow


def _is_interest(account: str) -> bool:
    """Interest expense, flat or canonical sub-account (Expenses:Interest:HomeLoan)."""
    return account == "Expenses:Interest" or account.startswith("Expenses:Interest:")


def by_account_by_month(transactions: list[TransactionRow]) -> list[dict]:
    """Rows of {account, month, amount} -- for each transaction with exactly one
    interest-expense posting (Expenses:Interest or an Expenses:Interest:* sub-account),
    attribute the charge to the sibling Liabilities:* posting, grouped by calendar
    month (YYYY-MM)."""
    totals: dict[tuple[str, str], Decimal] = {}
    for txn in transactions:
        interest_postings = [p for p in txn.postings if _is_interest(p.account)]
        if len(interest_postings) != 1:
            continue
        interest = interest_postings[0]
        month = f"{txn.date.year:04d}-{txn.date.month:02d}"
        for p in txn.postings:
            if _is_interest(p.account) or not p.account.startswith("Liabilities:"):
                continue
            key = (p.account, month)
            totals[key] = totals.get(key, Decimal(0)) + abs(interest.amount)
    return [
        {"account": account, "month": month, "amount": str(amount)}
        for (account, month), amount in sorted(totals.items())
    ]
