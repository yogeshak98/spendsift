"""Dashboard spending: the monthly spend of the last 12 complete months, the
3-, 6- and 12-month averages and lifestyle drift, each with and without
one-time spends, and the one-time spends of the last 3 months.

**Spending** is every `Expenses:` posting except `Expenses:Investment*`
(`is_spending_account`), at `inr_amount`; investing is never spending.

**One-time** (user decision 2026-11-01, ruling R21): a positive spending
posting that is not recurring AND stands out against its category's usual
spend. Amount alone never decides.

- Recurring is the Cash Flow · Recurring detector (`cashflow.recurring`):
  postings grouped by (normalized narration, top category) form a stream with
  at least 3 postings over at least 2 calendar months whose median gap is
  monthly (25-35 days), quarterly (80-100) or yearly (350-380), or with
  exactly 2 postings 11-13 calendar months apart (yearly).
- Stands out: the amount is at least `ONE_TIME_MULTIPLE` times the usual row,
  the median amount of the category's positive spending rows in the trailing
  window (the 12 complete months plus the report month to date). A category
  with fewer than `MIN_CATEGORY_ROWS` rows there is judged against the median
  of all positive spending rows in the window instead.
- A refund (a negative spending posting) is never one-time.

**Complete months** are the calendar months before the report date's month,
as in `liquidity.average_monthly_spend`. An average over n months leaves out
the months with no spending at all (and a month whose refunds outweigh its
spending), so a young ledger is not diluted; both variants divide by the same
months, and each average reports `months_counted`. That is the
`liquidity.average_monthly_spend` convention, so the "all" 3-month average
equals the months-of-cover pace. Lifestyle drift needs at least
`DRIFT_MIN_MONTHS` counted months in the 12-month window.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Literal

from backend.app.models.domain.accounts import is_spending_account
from backend.app.service.reports.cashflow import period
from backend.app.service.reports.cashflow.funding import Via, funding_leg
from backend.app.service.reports.cashflow.money import TENTH, q
from backend.app.service.reports.cashflow.recurring import (
    recurring_stream_keys,
    stream_key,
)
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    TransactionRow,
    display_account,
    top_category,
)

AVERAGE_WINDOWS = (3, 6, 12)
SERIES_MONTHS = 12
ONE_TIME_LIST_MONTHS = 3
ONE_TIME_MULTIPLE = Decimal(3)
MIN_CATEGORY_ROWS = 3
# Drift compares against the 12-month pace only when that pace covers at
# least this many months with spending.
DRIFT_MIN_MONTHS = 6

_ZERO = Decimal(0)

StreamKey = tuple[str, str]
Month = tuple[int, int]


UsualBasis = Literal["category", "all_spending"]


@dataclass(frozen=True)
class Usual:
    amount: Decimal  # the median row amount it is judged against
    basis: UsualBasis


@dataclass(frozen=True)
class OneTimeRule:
    """What one report needs to tell a one-time spend: the recurring spending
    streams and the usual (median) row per category and overall."""

    recurring: frozenset[StreamKey]
    by_category: dict[str, Decimal]  # categories with >= MIN_CATEGORY_ROWS rows
    overall: Decimal | None

    def usual(self, row: PostingRow) -> Usual | None:
        category = self.by_category.get(top_category(row.account))
        if category is not None:
            return Usual(category, "category")
        if self.overall is not None:
            return Usual(self.overall, "all_spending")
        return None

    def stands_out_against(self, row: PostingRow) -> Usual | None:
        """The usual row a one-time spend stands out against; None when `row`
        is not one-time."""
        if (
            not is_spending_account(row.account)
            or row.inr_amount <= 0
            or stream_key(row) in self.recurring
        ):
            return None
        usual = self.usual(row)
        if (
            usual is not None
            and usual.amount > 0
            and row.inr_amount >= ONE_TIME_MULTIPLE * usual.amount
        ):
            return usual
        return None

    def is_one_time(self, row: PostingRow) -> bool:
        return self.stands_out_against(row) is not None


def one_time_rule(rows: list[PostingRow], report_date: date) -> OneTimeRule:
    """`rows` must already be bounded to the report date; the detector sees
    all of them, the usual rows only the trailing window."""
    first = complete_months(report_date, SERIES_MONTHS)[0]
    start = date(first[0], first[1], 1)
    by_cat: dict[str, list[Decimal]] = {}
    every: list[Decimal] = []
    for r in rows:
        if (
            start <= r.date <= report_date
            and is_spending_account(r.account)
            and r.inr_amount > 0
        ):
            by_cat.setdefault(top_category(r.account), []).append(r.inr_amount)
            every.append(r.inr_amount)
    return OneTimeRule(
        recurring=recurring_stream_keys(rows),
        by_category={
            cat: median(amounts)
            for cat, amounts in by_cat.items()
            if len(amounts) >= MIN_CATEGORY_ROWS
        },
        overall=median(every) if every else None,
    )


def complete_months(report_date: date, count: int) -> list[Month]:
    """The `count` calendar months before `report_date`'s month, oldest first."""
    current = report_date.year * 12 + report_date.month - 1
    out: list[Month] = []
    for idx in range(current - count, current):
        year, m0 = divmod(idx, 12)
        out.append((year, m0 + 1))
    return out


def _pct(value: Decimal) -> str:
    return str(value.quantize(TENTH, rounding=ROUND_HALF_UP))


def _month_totals(
    rows: list[PostingRow], months: list[Month], rule: OneTimeRule
) -> tuple[dict[Month, Decimal], dict[Month, Decimal]]:
    """(all spending, one-time spending) per month of `months`."""
    wanted = set(months)
    spent = dict.fromkeys(months, _ZERO)
    one_time = dict.fromkeys(months, _ZERO)
    for r in rows:
        ym = (r.date.year, r.date.month)
        if ym not in wanted or not is_spending_account(r.account):
            continue
        spent[ym] += r.inr_amount
        if rule.is_one_time(r):
            one_time[ym] += r.inr_amount
    return spent, one_time


def _averages(values: dict[Month, Decimal], counted: list[Month]) -> Decimal | None:
    if not counted:
        return None
    return sum((values[m] for m in counted), _ZERO) / len(counted)


def _variant(
    values: dict[Month, Decimal], spent: dict[Month, Decimal], months: list[Month]
) -> dict:
    avgs: dict[int, Decimal | None] = {}
    counts: dict[int, int] = {}
    for n in AVERAGE_WINDOWS:
        counted = [m for m in months[-n:] if spent[m] > 0]
        avgs[n] = _averages(values, counted)
        counts[n] = len(counted)
    a3, a12 = avgs[3], avgs[12]
    drift: str | None = None
    if a3 is None or a12 is None or a12 <= 0:
        basis = "no_spending"
    elif counts[12] < DRIFT_MIN_MONTHS:
        basis = "too_few_months"
    else:
        basis = "ok"
        drift = _pct((a3 / a12 - 1) * 100)
    out: dict = {}
    for n in AVERAGE_WINDOWS:
        avg = avgs[n]
        out[f"avg_{n}"] = {
            "amount": q(avg) if avg is not None else None,
            "months_counted": counts[n],
        }
    out["lifestyle_drift_pct"] = drift
    out["drift_basis"] = basis
    return out


def spending(rows: list[PostingRow], report_date: date, rule: OneTimeRule) -> dict:
    """Monthly series, averages and drift, all and without one-time spends."""
    months = complete_months(report_date, SERIES_MONTHS)
    spent, one_time = _month_totals(rows, months, rule)
    core = {m: spent[m] - one_time[m] for m in months}
    with_year = period.spans_years(months)
    return {
        "months": [
            {
                "month": f"{y:04d}-{m:02d}",
                "label": period.month_label(y, m, with_year),
                "all": q(spent[(y, m)]),
                "one_time": q(one_time[(y, m)]),
                "without_one_time": q(core[(y, m)]),
            }
            for y, m in months
        ],
        "all": _variant(spent, spent, months),
        "without_one_time": _variant(core, spent, months),
    }


def one_time_window_start(report_date: date) -> date:
    """First day of the month `ONE_TIME_LIST_MONTHS - 1` months before the
    report date's month: the report month to date plus the two before it."""
    idx = report_date.year * 12 + report_date.month - 1 - (ONE_TIME_LIST_MONTHS - 1)
    year, m0 = divmod(idx, 12)
    return date(year, m0 + 1, 1)


def _listed_usual(
    txn: TransactionRow,
    p: PostingRow,
    start: date,
    end: date,
    rule: OneTimeRule,
    keep: Callable[[Via | None], bool] | None,
) -> Usual | None:
    """The usual row `p` stands out against when it is one of
    `one_time_items(..., start, end, rule, keep=keep)`; else None."""
    if not start <= txn.date <= end:
        return None
    usual = rule.stands_out_against(p)
    if usual is None:
        return None
    if keep is not None and not keep(funding_leg(txn, "Expenses:")):
        return None
    return usual


def is_listed_one_time(
    txn: TransactionRow,
    p: PostingRow,
    start: date,
    end: date,
    rule: OneTimeRule,
    *,
    keep: Callable[[Via | None], bool] | None = None,
) -> bool:
    """True exactly when posting `p` of `txn` is an item of `one_time_items`
    with the same arguments: the one test both the list and anything that
    leaves the listed spends out (Expense · Overview's `exclude_one_time`)
    use, so the two cannot drift apart."""
    return _listed_usual(txn, p, start, end, rule, keep) is not None


def one_time_items(
    txns: list[TransactionRow],
    start: date,
    end: date,
    rule: OneTimeRule,
    names: dict[str, str],
    *,
    keep: Callable[[Via | None], bool] | None = None,
) -> list[dict]:
    """The one-time spends dated `start` to `end` (inclusive), largest first.
    `keep` (default: all) filters on the transaction's funding leg. `usual` is
    the median row they were judged against and `multiple_of_usual` =
    amount / it, 1 dp. A posting is listed iff `is_listed_one_time`."""
    items = []
    for txn in txns:
        legs = [
            (p, usual)
            for p in txn.postings
            if (usual := _listed_usual(txn, p, start, end, rule, keep)) is not None
        ]
        if not legs:
            continue
        via = funding_leg(txn, "Expenses:")
        for p, usual in legs:
            items.append(
                {
                    "txn_id": txn.txn_id,
                    "date": txn.date.isoformat(),
                    "name": txn.narration,
                    "category": top_category(p.account),
                    "account": p.account,
                    "account_display": display_account(p.account, names),
                    "paid_from": via.account if via else None,
                    "paid_from_display": display_account(via.account, names)
                    if via
                    else None,
                    "amount": q(p.inr_amount),
                    "usual": q(usual.amount),
                    "usual_basis": usual.basis,
                    "multiple_of_usual": _pct(p.inr_amount / usual.amount),
                    "_sort": p.inr_amount,
                }
            )
    items.sort(key=lambda i: (-i["_sort"], i["date"], i["txn_id"]))
    for item in items:
        del item["_sort"]
    return items


def one_time_spends(
    txns: list[TransactionRow],
    report_date: date,
    rule: OneTimeRule,
    names: dict[str, str],
) -> dict:
    """The Dashboard list: `one_time_items` from `one_time_window_start` to
    the report date."""
    start = one_time_window_start(report_date)
    return {
        "start": start.isoformat(),
        "end": report_date.isoformat(),
        "items": one_time_items(txns, start, report_date, rule, names),
    }
