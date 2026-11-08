from __future__ import annotations

from decimal import Decimal

from backend.app.models.storage.budget import BudgetConfig
from backend.app.service.reports.dashboard import account_balances
from backend.app.service.reports.cashflow.expense import by_category
from backend.app.service.reports.health.ledger_view import PostingRow

_CHECKING_PREFIXES = ("Assets:Checking:", "Assets:Cash")


def _checking_balance(rows: list[PostingRow]) -> Decimal:
    bal = account_balances(rows)
    return sum(
        (Decimal(v) for a, v in bal.items() if a.startswith(_CHECKING_PREFIXES)),
        Decimal(0),
    )


def report(rows: list[PostingRow], config: BudgetConfig, year: int, month: int) -> dict:
    # actual spend this month, by top-level category
    spent_by_cat = {
        c["category"]: Decimal(c["amount"]) for c in by_category(rows, year, month)
    }

    cards: list[dict] = []
    total_budgeted = Decimal(0)
    for cat, target_str in config.targets.items():
        budgeted = Decimal(target_str)
        spent = spent_by_cat.get(cat, Decimal(0))
        total_budgeted += budgeted
        pct = float(spent / budgeted * 100) if budgeted > 0 else 0.0
        cards.append(
            {
                "category": cat,
                "budgeted": str(budgeted),
                "spent": str(spent),
                "pct": round(pct, 1),
                "over_budget": spent > budgeted,
            }
        )

    # most-at-risk first: over-budget, then by pct desc
    cards.sort(key=lambda c: (not c["over_budget"], -c["pct"]))

    total_spent = sum(
        (Decimal(c["spent"]) for c in cards), Decimal(0)
    )  # spend in budgeted categories only
    checking = _checking_balance(rows)
    available_for_budgeting = checking - total_budgeted
    available_for_spending = total_budgeted - total_spent
    projected_month_end = checking - available_for_spending

    return {
        "checking_balance": str(checking),
        "total_budgeted": str(total_budgeted),
        "total_spent": str(total_spent),
        "available_for_budgeting": str(available_for_budgeting),
        "available_for_spending": str(available_for_spending),
        "projected_month_end": str(projected_month_end),
        "targets": cards,
    }
