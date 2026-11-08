"""Dashboard cash-flow summary for three periods: the report month to date,
the FY so far and the last 12 months (the report month to date plus the 11
before it).

In, Spent and Invested are the Cash Flow · Overview totals
(`cashflow.overview`): income net of TDS, all spending (`Expenses:Investment*`
excluded), and the investment flow. Here:

- `net_cash_flow` = in - spent - invested (of the rounded figures);
- `savings_rate_pct` = (in - spent) / in, the overview's rate (null when
  in <= 0);
- `where_income_went` splits the income: spent, invested, kept (the positive
  net cash flow) and from_savings (the negative one, as a magnitude), each also
  as a percent of income, so `spent + invested + kept - from_savings = in`.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from backend.app.service.reports.cashflow import cashflow
from backend.app.service.reports.cashflow.money import TENTH, q
from backend.app.service.reports.cashflow.period import DateRange
from backend.app.service.reports.health.ledger_view import PostingRow

PeriodKey = Literal["month", "fy", "last_12_months"]

_ZERO = Decimal(0)


def periods(report_date: date) -> list[tuple[PeriodKey, DateRange]]:
    month_start = report_date.replace(day=1)
    fy = report_date.year if report_date.month >= 4 else report_date.year - 1
    idx = report_date.year * 12 + report_date.month - 1 - 11
    year, m0 = divmod(idx, 12)
    return [
        ("month", DateRange(month_start, report_date)),
        ("fy", DateRange(date(fy, 4, 1), report_date)),
        ("last_12_months", DateRange(date(year, m0 + 1, 1), report_date)),
    ]


def _share(part: Decimal, income: Decimal) -> str | None:
    if income <= 0:
        return None
    return str((part / income * 100).quantize(TENTH, rounding=ROUND_HALF_UP))


def _period(key: PeriodKey, r: DateRange, rows: list[PostingRow]) -> dict:
    totals = cashflow.overview(rows, r)["totals"]
    income = Decimal(totals["income"])
    spent = Decimal(totals["expenses"])
    invested = Decimal(totals["invested"])
    net = income - spent - invested
    kept = max(net, _ZERO)
    from_savings = max(-net, _ZERO)
    return {
        "key": key,
        "start": r.start.isoformat(),
        "end": r.end.isoformat(),
        "income": str(income),
        "spent": str(spent),
        "invested": str(invested),
        "net_cash_flow": q(net),
        "savings_rate_pct": totals["savings_rate"],
        "where_income_went": {
            "spent": str(spent),
            "invested": str(invested),
            "kept": q(kept),
            "from_savings": q(from_savings),
            "spent_pct": _share(spent, income),
            "invested_pct": _share(invested, income),
            "kept_pct": _share(kept, income),
            "from_savings_pct": _share(from_savings, income),
        },
    }


def cash_flow_summary(rows: list[PostingRow], report_date: date) -> list[dict]:
    return [_period(key, r, rows) for key, r in periods(report_date)]
