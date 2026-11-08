"""The range + funding read model behind Expense, Card and Income overviews.

One engine for both P&L sides; its output is `FlowOverviewResponse`
(`backend/app/models/api/reports.py`).

- Lines: expense = spending postings (`is_spending_account`) at `inr_amount`;
  income = `Income:` postings at `-inr_amount`, so money received is positive.
- Each line inherits its transaction's funding leg (`funding.funding_leg`), and
  the funding/account filter scopes every section: headline, series, groups
  and lines.
- Sums run at full precision; amounts are quantized to 0.01 (ROUND_HALF_UP)
  only when serialized here. A rounded net is `gross - reversals` of the
  rounded parts, and the rounded `funding_split` is allocated so it sums to
  the rounded net exactly (the remainder goes to the largest part).
- Zero-amount postings are not lines.

`expense_overview` adds the period's one-time spends (the Dashboard's rule,
`dashboard_v2.spending`) and, on request, leaves them out of every figure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from backend.app.models.domain.accounts import is_spending_account
from backend.app.service.reports.cashflow import period
from backend.app.service.reports.cashflow.money import TENTH, allocate, cents, q
from backend.app.service.reports.cashflow.funding import (
    FundingFilter,
    Via,
    funding_leg,
    matches,
)
from backend.app.service.reports.cashflow.period import DateRange
from backend.app.service.reports.dashboard_v2.spending import (
    OneTimeRule,
    is_listed_one_time,
    one_time_items,
)
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    TransactionRow,
    display_account,
    top_category,
)

FlowSide = Literal["expense", "income"]

_ZERO = Decimal(0)


@dataclass(frozen=True)
class FlowLine:
    txn_id: str
    date: date
    account: str
    group: str  # top_category(account)
    name: str  # narration (merchant / payer)
    amount: Decimal  # side-normalised: + spend / + received
    via: Via | None
    editable: bool = True  # False for a hand-authored (txn-id-less) transaction


def _pct(amount: Decimal, positive_total: Decimal) -> str:
    if amount <= 0 or positive_total <= 0:
        return "0.0"
    return str((amount / positive_total * 100).quantize(TENTH, rounding=ROUND_HALF_UP))


LeaveOut = Callable[[TransactionRow, PostingRow], bool]


def flow_lines(
    txns: list[TransactionRow],
    side: FlowSide,
    r: DateRange,
    *,
    leave_out: LeaveOut | None = None,
) -> list[FlowLine]:
    """expense: spending postings (`is_spending_account`), `amount = inr_amount`;
    income: `Income:` postings, `amount = -inr_amount`. Only dates in `r`, and
    not the postings `leave_out` names."""
    root = "Expenses:" if side == "expense" else "Income:"
    out: list[FlowLine] = []
    for txn in txns:
        if not r.start <= txn.date <= r.end:
            continue
        legs = [
            p
            for p in txn.postings
            if p.inr_amount != 0
            and (
                is_spending_account(p.account)
                if side == "expense"
                else p.account.startswith(root)
            )
            and not (leave_out is not None and leave_out(txn, p))
        ]
        if not legs:
            continue
        via = funding_leg(txn, root)
        for p in legs:
            out.append(
                FlowLine(
                    txn_id=txn.txn_id,
                    date=txn.date,
                    account=p.account,
                    group=top_category(p.account),
                    name=txn.narration,
                    amount=p.inr_amount if side == "expense" else -p.inr_amount,
                    via=via,
                    editable=txn.tracked,
                )
            )
    return out


def _sums(lines: list[FlowLine]) -> tuple[Decimal, Decimal, Decimal]:
    """(net, gross, reversals) at 0.01: gross and reversals (a positive
    magnitude) are rounded, and net is their difference, so it always holds
    on the strings."""
    gross = sum((ln.amount for ln in lines if ln.amount > 0), _ZERO)
    reversals = -sum((ln.amount for ln in lines if ln.amount < 0), _ZERO)
    gross, reversals = cents(gross), cents(reversals)
    return gross - reversals, gross, reversals


def _series_point(key: str, label: str, lines: list[FlowLine]) -> dict:
    net, gross, reversals = _sums(lines)
    return {
        "key": key,
        "label": label,
        "amount": str(net),
        "gross": str(gross),
        "reversals": str(reversals),
    }


def _series(lines: list[FlowLine], r: DateRange) -> list[dict]:
    if period.granularity(r) == "day":
        by_day: dict[date, list[FlowLine]] = {}
        for ln in lines:
            by_day.setdefault(ln.date, []).append(ln)
        return [
            _series_point(d.isoformat(), period.day_label(d), by_day.get(d, []))
            for d in period.days_in(r)
        ]
    by_month: dict[tuple[int, int], list[FlowLine]] = {}
    for ln in lines:
        by_month.setdefault((ln.date.year, ln.date.month), []).append(ln)
    months = period.months_in(r)
    with_year = period.spans_years(months)
    return [
        _series_point(
            f"{y:04d}-{m:02d}",
            period.month_label(y, m, with_year),
            by_month.get((y, m), []),
        )
        for y, m in months
    ]


def _groups(lines: list[FlowLine]) -> list[dict]:
    totals: dict[str, Decimal] = {}
    items: dict[str, dict[str, Decimal]] = {}
    txn_ids: dict[tuple[str, str], set[str]] = {}
    for ln in lines:
        totals[ln.group] = totals.get(ln.group, _ZERO) + ln.amount
        group_items = items.setdefault(ln.group, {})
        group_items[ln.name] = group_items.get(ln.name, _ZERO) + ln.amount
        txn_ids.setdefault((ln.group, ln.name), set()).add(ln.txn_id)
    positive = sum((amt for amt in totals.values() if amt > 0), _ZERO)
    ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {
            "group": group,
            "amount": q(amount),
            "pct": _pct(amount, positive),
            "items": [
                {
                    "name": name,
                    "amount": q(amt),
                    "count": len(txn_ids[(group, name)]),
                }
                for name, amt in sorted(
                    items[group].items(), key=lambda kv: (-kv[1], kv[0])
                )
            ],
        }
        for group, amount in ordered
    ]


def _via_out(via: Via | None, names: dict[str, str]) -> dict | None:
    if via is None:
        return None
    shown = display_account(via.account, names)
    return {
        "kind": via.kind,
        "label": shown.rsplit(":", 1)[-1],
        "account": via.account,
        "account_display": shown,
    }


def flow_overview(
    txns: list[TransactionRow],
    side: FlowSide,
    r: DateRange,
    funding: FundingFilter,
    account: str | None,
    *,
    names: dict[str, str] | None = None,
    leave_out: LeaveOut | None = None,
) -> dict:
    """The `FlowOverviewResponse` shape. `txns` must cover `r`. `names` is
    `ledger_view.commodity_names` for every `account_display`. `leave_out`
    drops postings before anything is summed."""
    names = names or {}
    lines = [
        ln
        for ln in flow_lines(txns, side, r, leave_out=leave_out)
        if matches(ln.via, funding, account)
    ]

    net, gross, reversals = _sums(lines)
    split: dict[str, Decimal] = {"card": _ZERO, "bank": _ZERO, "other": _ZERO}
    for ln in lines:
        split[ln.via.kind if ln.via else "other"] += ln.amount

    ordered = sorted(lines, key=lambda ln: (ln.date, ln.amount), reverse=True)
    return {
        "side": side,
        "start": r.start.isoformat(),
        "end": r.end.isoformat(),
        "funding": funding,
        "account": account,
        "granularity": period.granularity(r),
        "headline": {
            "net": str(net),
            "gross": str(gross),
            "reversals": str(reversals),
            "count": len({ln.txn_id for ln in lines}),
            "funding_split": allocate(split, net),
        },
        "series": _series(lines, r),
        "groups": _groups(lines),
        "lines": [
            {
                "txn_id": ln.txn_id,
                "date": ln.date.isoformat(),
                "account": ln.account,
                "account_display": display_account(ln.account, names),
                "group": ln.group,
                "name": ln.name,
                "amount": q(ln.amount),
                "via": _via_out(ln.via, names),
                "editable": ln.editable,
            }
            for ln in ordered
        ],
    }


def expense_overview(
    txns: list[TransactionRow],
    r: DateRange,
    funding: FundingFilter,
    account: str | None,
    *,
    rule: OneTimeRule,
    report_date: date,
    exclude_one_time: bool = False,
    names: dict[str, str] | None = None,
) -> dict:
    """The `ExpenseOverviewResponse` shape: the expense `flow_overview` plus
    `one_time`, the one-time spends dated `r.start` to min(`r.end`,
    `report_date`) that pass the funding/account filter, largest first.

    `rule` must be `one_time_rule` over the whole ledger up to `report_date`,
    so the list agrees with the Dashboard's for the same date. With
    `exclude_one_time`, exactly those postings (`is_listed_one_time`, the
    list's own test) are left out of every figure; `one_time` itself does
    not change. `exclude_one_time` is echoed, like the other query params."""
    names = names or {}
    end = min(r.end, report_date)

    def keep(via: Via | None) -> bool:
        return matches(via, funding, account)

    def is_listed(txn: TransactionRow, p: PostingRow) -> bool:
        return is_listed_one_time(txn, p, r.start, end, rule, keep=keep)

    body = flow_overview(
        txns,
        "expense",
        r,
        funding,
        account,
        names=names,
        leave_out=is_listed if exclude_one_time else None,
    )
    items = one_time_items(txns, r.start, end, rule, names, keep=keep)
    body["exclude_one_time"] = exclude_one_time
    body["one_time"] = {
        "report_date": report_date.isoformat(),
        "total": q(sum((Decimal(i["amount"]) for i in items), _ZERO)),
        "count": len(items),
        "items": items,
    }
    return body
