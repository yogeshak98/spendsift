"""Liquid balance and months of cover: how long the bank & cash balance lasts
at the recent pace of spending."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from backend.app.service.reports.cashflow.expense import trailing_months
from backend.app.service.reports.health.ledger_view import PostingRow

COVER_WINDOW_MONTHS = 3


def average_monthly_spend(
    rows: list[PostingRow], as_of: date, months: int = COVER_WINDOW_MONTHS
) -> Decimal | None:
    """Mean spending (Expenses other than Expenses:Investment) over the
    `months` complete calendar months before `as_of`'s month. Months with no
    spending at all are left out, so a young ledger is not diluted by months it
    has no data for; None when there is no spending in the window."""
    year, month = (
        (as_of.year, as_of.month - 1) if as_of.month > 1 else (as_of.year - 1, 12)
    )
    window = trailing_months(rows, year, month, months)
    spends = [Decimal(m["amount"]) for m in window if Decimal(m["amount"]) > 0]
    if not spends:
        return None
    return sum(spends, Decimal(0)) / len(spends)


def months_of_cover(liquid: Decimal, average_spend: Decimal | None) -> str | None:
    """`liquid / average_spend` to 1 dp; None without a spending pace, and
    "0.0" for a zero or negative balance."""
    if average_spend is None or average_spend <= 0:
        return None
    if liquid <= 0:
        return "0.0"
    return str(
        (liquid / average_spend).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    )
