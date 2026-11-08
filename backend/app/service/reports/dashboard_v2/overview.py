"""Dashboard v2 read model: net worth (figure, 12 month-ends, changes and the
breakdown equation), the cash-flow summary, spending with and without
one-time spends, the one-time spends, and the needs-action items.

The breakdown, its XIRR, gain, EMIs and cards come from the Net Worth
overview (`networth_overview`), never a second derivation, months of cover
included (its 3-month pace), so the Dashboard and Net Worth never disagree.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from backend.app.service.reports.cashflow import period
from backend.app.service.reports.cashflow.money import TENTH, q
from backend.app.service.reports.dashboard_v2 import cash_flow, needs_action, spending
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    all_flow_transactions,
    all_postings,
    all_transactions,
    closed_accounts,
    commodity_names,
)
from backend.app.service.reports.networth import liquidity, networth
from backend.app.service.reports.networth.overview import (
    OverviewSources,
    networth_overview,
)

NETWORTH_SERIES_MONTHS = 12

ROUTE_NETWORTH = "networth?section="

_ZERO = Decimal(0)


def _net_at(rows: list[PostingRow], when: date) -> Decimal:
    return Decimal(
        networth.current_position([r for r in rows if r.date <= when])["net"]
    )


def _month_end(year: int, month: int) -> date:
    nxt = date(year + month // 12, month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _pct(part: Decimal, whole: Decimal) -> str | None:
    if whole <= 0:
        return None
    return str((part / whole * 100).quantize(TENTH, rounding=ROUND_HALF_UP))


def _series(rows: list[PostingRow], report_date: date, current: Decimal) -> list[dict]:
    """Month-ends of the 11 months before the report month, then the report
    date itself (the current figure)."""
    months = spending.complete_months(report_date, NETWORTH_SERIES_MONTHS - 1)
    with_year = period.spans_years(months + [(report_date.year, report_date.month)])
    out = []
    for y, m in months:
        end = _month_end(y, m)
        out.append(
            {
                "month": f"{y:04d}-{m:02d}",
                "label": period.month_label(y, m, with_year),
                "date": end.isoformat(),
                "net_worth": q(_net_at(rows, end)),
            }
        )
    out.append(
        {
            "month": f"{report_date.year:04d}-{report_date.month:02d}",
            "label": period.month_label(report_date.year, report_date.month, with_year),
            "date": report_date.isoformat(),
            "net_worth": q(current),
        }
    )
    return out


def _group(body: dict, gid: str) -> dict | None:
    return next(
        (g for g in body["groups"] + body["liability_groups"] if g["id"] == gid), None
    )


def _reconciled(
    net: Decimal, inv: Decimal, bank: Decimal, other: Decimal, owed: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Make investments + bank + other - owed equal `net` to the paisa.

    The overview's assets are a sum of rounded groups while its net worth is
    rounded once, so sub-paisa balances can leave a few paise between them. The
    largest row absorbs that remainder, as the overview's groups do."""
    rows = [inv, bank, other, -owed]
    gap = net - sum(rows, _ZERO)
    if gap:
        i = max(range(len(rows)), key=lambda k: abs(rows[k]))
        rows[i] += gap
    return rows[0], rows[1], rows[2], -rows[3]


def _breakdown(body: dict) -> dict:
    net = Decimal(body["totals"]["net_worth"])
    inv = _group(body, "investments")
    bank = _group(body, "bank")
    loans = _group(body, "loans")
    cards = _group(body, "cards")
    other_groups = [g for g in body["groups"] if g["id"] not in ("investments", "bank")]

    inv_total, bank_total, other_total, owed = _reconciled(
        net,
        Decimal(inv["total"]) if inv else _ZERO,
        Decimal(bank["total"]) if bank else _ZERO,
        sum((Decimal(g["total"]) for g in other_groups), _ZERO),
        Decimal(body["totals"]["liabilities"]),
    )
    vehicles = body["investments"]["vehicles"]
    return {
        "investments": {
            "total": q(inv_total),
            "share_pct": _pct(inv_total, net),
            "route": ROUTE_NETWORTH + "investments",
            "value": q(sum((Decimal(v["value"]) for v in vehicles), _ZERO)),
            "gain": inv["facts"]["gain"] if inv else None,
            "xirr_pct": inv["facts"]["xirr_pct"] if inv else None,
            "xirr_basis": body["investments"]["xirr_basis"],
            "vehicles": len(vehicles),
        },
        "bank": {
            "total": q(bank_total),
            "share_pct": _pct(bank_total, net),
            "route": ROUTE_NETWORTH + "bank",
            "months_of_cover": bank["facts"]["months_of_cover"] if bank else None,
            "cover_basis_months": liquidity.COVER_WINDOW_MONTHS,
            "stale_count": bank["facts"]["stale_count"] if bank else None,
        },
        "other": {
            "total": q(other_total),
            "share_pct": _pct(other_total, net),
            "route": ROUTE_NETWORTH + "other",
            "groups": [g["id"] for g in other_groups],
        },
        "liabilities": {
            "total": q(owed),
            "share_pct": _pct(-owed, net),
            "route": ROUTE_NETWORTH + "liabilities",
            "emi_per_month": loans["facts"]["emi_per_month"] if loans else None,
            "emi_last_ends": loans["facts"]["last_ends"] if loans else None,
            "cards_net_of_in_transit": cards["facts"]["owed_net_of_in_transit"]
            if cards
            else None,
        },
    }


def _net_worth(body: dict, rows: list[PostingRow], report_date: date) -> dict:
    current = Decimal(body["totals"]["net_worth"])
    # The month-end just before the first series point: the end of the month
    # twelve months before the report month.
    first = spending.complete_months(report_date, NETWORTH_SERIES_MONTHS)[0]
    base = _net_at(rows, _month_end(*first))
    change = current - base
    return {
        "current": body["totals"]["net_worth"],
        "change_this_month": body["totals"]["change_this_month"],
        "change_12_months": q(change),
        "change_12_months_pct": _pct(change, base),
        "series": _series(rows, report_date, current),
        "breakdown": _breakdown(body),
    }


def dashboard_overview(
    src: OverviewSources, today: date, *, as_of: date | None = None
) -> dict:
    """The whole Dashboard v2 body. `as_of` (default: `today`) is the report
    date: nothing dated after it counts. Benchmarks play no part, so
    `src.benchmarks` is ignored and a bad benchmarks file cannot fail it."""
    report_date = as_of if as_of is not None else today
    body = networth_overview(
        replace(src, benchmarks=None, benchmarks_error=None),
        today,
        as_of=report_date,
    )
    ledger = src.ledger
    rows = [r for r in all_postings(ledger) if r.date <= report_date]
    names = commodity_names(ledger)
    rule = spending.one_time_rule(rows, report_date)
    flow_txns = [t for t in all_flow_transactions(ledger) if t.date <= report_date]
    txns = [t for t in all_transactions(ledger) if t.date <= report_date]
    return {
        "as_of": report_date.isoformat(),
        "net_worth": _net_worth(body, rows, report_date),
        "cash_flow": cash_flow.cash_flow_summary(rows, report_date),
        "spending": spending.spending(rows, report_date, rule),
        "one_time_spends": spending.one_time_spends(
            flow_txns, report_date, rule, names
        ),
        "needs_action": needs_action.needs_action(
            rows,
            flow_txns,
            txns,
            report_date,
            card_properties=dict(src.card_properties),
            card_clearing_account=src.card_clearing_account,
            card_expiry_soon_days=src.card_expiry_soon_days,
            closed=closed_accounts(ledger),
            names=names,
        ),
    }
