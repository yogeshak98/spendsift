"""Net Worth · Overview read model.

Totals come from `networth.current_position`, the same function behind the
Balance screen, so headline assets, liabilities and net worth are the Balance
figures to the paisa. The groups split the same postings by account tree
(`account_groups`) at ledger cost basis; investment value, gain and XIRR sit
beside that cost in `investments`. Liability detail (cards, EMIs) reuses the
liabilities read models, never a second derivation.

For other read models: call `networth_overview`, or use the pieces
`liquidity.average_monthly_spend` / `liquidity.months_of_cover`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from backend.app.models.storage.benchmarks import BenchmarksConfig
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.platform_accounts import all_platform_clearing_accounts
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    all_flow_transactions,
    all_postings,
    all_transactions,
    closed_accounts_from_entries,
    commodity_names,
    display_account,
)
from backend.app.service.reports.liabilities import in_transit
from backend.app.service.reports.networth import benchmarks as bm
from backend.app.service.reports.networth import networth, overview_groups
from backend.app.service.reports.networth.account_groups import (
    ASSET_GROUPS,
    LABELS,
    LIABILITY_GROUPS,
    group_of,
)
from backend.app.service.reports.networth.overview_groups import Ctx, Group, money
from backend.app.service.reports.networth.vehicles import investment_vehicles

DEFAULT_BENCHMARK = "nifty50"

_PREPAID_REASON = (
    "Tax already paid or withheld. It is settled at filing (a refund or more tax "
    "due), so it is not counted as spendable money."
)


@dataclass(frozen=True)
class OverviewSources:
    """What the overview needs from the running app (the route fills it)."""

    ledger: Ledger
    investment_ledger: Ledger
    # None when the benchmarks file cannot be used; `benchmarks_error` then
    # says why and the overview is returned without its benchmark parts.
    benchmarks: BenchmarksConfig | None
    card_properties: Mapping[str, Mapping]
    card_clearing_account: str
    card_emi_account: str | None
    card_expiry_soon_days: int
    benchmarks_error: dict | None = None


def _net_at(rows: list[PostingRow], when: date) -> Decimal:
    return Decimal(
        networth.current_position([r for r in rows if r.date <= when])["net"]
    )


def _totals(rows: list[PostingRow], names: dict[str, str], report_date: date) -> dict:
    position = networth.current_position(rows, names)
    fy_year = report_date.year if report_date.month >= 4 else report_date.year - 1
    last_month_end = report_date.replace(day=1) - timedelta(days=1)
    net = Decimal(position["net"])
    return {
        "net_worth": position["net"],
        "assets": position["assets"],
        # Balance reports liabilities signed (negative = owed); here positive = owed.
        "liabilities": money(-Decimal(position["liabilities"])),
        "change_this_fy": money(net - _net_at(rows, date(fy_year, 3, 31))),
        "change_this_month": money(net - _net_at(rows, last_month_end)),
        "excluded": [
            {
                "account": e["account"],
                "account_display": e["account_display"],
                "amount": e["amount"],
                "reason": _PREPAID_REASON,
            }
            for e in position["excluded"]
        ],
        "excluded_total": position["excluded_total"],
    }


def _vehicle_rows(
    inv: dict, config: BenchmarksConfig | None, selected: str, index: str | None
):
    total_value = inv["value"]
    out = []
    for v in inv["vehicles"]:
        key, bench = (
            bm.benchmark_for_vehicle(config, selected, index, v["key"])
            if config is not None
            else (None, None)
        )
        xirr = Decimal(v["xirr_pct"]) if v["xirr_pct"] is not None else None
        out.append(
            {
                "key": v["key"],
                "label": v["label"],
                "holdings": v["holdings"],
                "invested": money(v["invested"]),
                "value": money(v["value"]),
                "gain": money(v["gain"]) if v["gain"] is not None else None,
                "gain_pct": v["gain_pct"],
                "xirr_pct": v["xirr_pct"],
                "share_pct": bm.pct_str(v["value"] / total_value * 100)
                if total_value > 0
                else "0.00",
                "at_cost": v["at_cost"],
                "partial": v["partial"],
                "benchmark": key,
                "benchmark_pct": bm.pct_str(bench) if bench is not None else None,
                "vs_benchmark_pct": bm.pct_str(xirr - bench)
                if xirr is not None and bench is not None
                else None,
            }
        )
    return out


def _benchmark_block(
    inv: dict, config: BenchmarksConfig, selected: str, index: str | None
) -> dict:
    values = {v["key"]: v["value"] for v in inv["vehicles"]}
    blended, weights = bm.blended_return(config, values, index)
    chosen = (
        blended
        if selected == bm.BLENDED
        else Decimal(config.benchmarks[selected].annual_return_pct)
    )
    xirr = Decimal(inv["xirr_pct"]) if inv["xirr_pct"] is not None else None

    def pct(value: Decimal | None) -> str | None:
        return bm.pct_str(value) if value is not None else None

    return {
        "selected": selected,
        "equity_index": index,
        "selected_return_pct": pct(chosen),
        "investments_xirr_pct": inv["xirr_pct"],
        "vs_selected_pct": pct(xirr - chosen)
        if xirr is not None and chosen is not None
        else None,
        "blended_return_pct": pct(blended),
        "blend_weights": weights,
        "options": bm.options(config)
        + [
            {
                "key": bm.BLENDED,
                "label": "Blended",
                "kind": "blended",
                "annual_return_pct": pct(blended),
            }
        ],
    }


def _reconciled(groups: list[dict], headline: str) -> list[dict]:
    """Make the groups add up to the headline to the paisa.

    Each group is rounded on its own, and Balance rounds on a different split
    (and keeps sub-paisa accounts the groups drop), so the sums can differ by a
    few paise. The largest group absorbs that remainder."""
    if not groups:
        return groups
    gap = Decimal(headline) - sum((Decimal(g["total"]) for g in groups), Decimal(0))
    if gap:
        biggest = max(groups, key=lambda g: abs(Decimal(g["total"])))
        biggest["total"] = money(Decimal(biggest["total"]) + gap)
    return groups


def _finish(group: Group, names: dict[str, str]) -> dict:
    # The report-wide contract: a raw account path comes with its display form.
    # An Investments row is a vehicle key, not a path; it shows its own name.
    for row in group.accounts:
        row["account_display"] = (
            display_account(row["account"], names)
            if row["account"].startswith(("Assets:", "Liabilities:"))
            else row["name"]
        )
    return {
        "id": group.id,
        "label": LABELS[group.id],
        "total": money(group.total),
        "account_count": group.count,
        "accounts": group.accounts,
        "facts": group.facts,
    }


def networth_overview(
    src: OverviewSources,
    today: date,
    *,
    as_of: date | None = None,
    benchmark: str | None = None,
    equity_index: str | None = None,
) -> dict:
    """The whole Net Worth · Overview body.

    `as_of` (default: `today`, as on the Dashboard) counts only postings and
    transactions dated on or before it, so a forward-dated posting counts only
    when a later `as_of` is asked for. It also bounds mutual-fund and stock lots
    and values them at the latest stored price on or before it (counted at cost,
    with no XIRR, when there is none). `today` is the app clock; it dates cards'
    due dates. Raises E1096 for an unknown `benchmark` or `equity_index`."""
    if src.benchmarks is not None:
        if benchmark is None or equity_index is None:
            benchmark, equity_index = bm.default_selection(
                src.benchmarks, benchmark, equity_index
            )
        bm.check_selection(src.benchmarks, benchmark, equity_index)
    benchmark = benchmark or DEFAULT_BENCHMARK
    ledger = src.ledger
    report_date = as_of if as_of is not None else today
    rows = [r for r in all_postings(ledger) if r.date <= report_date]
    names = commodity_names(ledger)
    entries = ledger.load().entries
    closed = closed_accounts_from_entries(entries)

    def upto(txns):
        return [t for t in txns if t.date <= report_date]

    balances: dict[str, Decimal] = {}
    for r in rows:
        if group_of(r.account) is not None:
            balances[r.account] = balances.get(r.account, Decimal(0)) + r.inr_amount

    inv = investment_vehicles(
        rows,
        ledger,
        src.investment_ledger,
        report_date,
        all_platform_clearing_accounts(),
        report_date,
    )
    ctx = Ctx(
        rows=rows,
        names=names,
        report_date=report_date,
        balances=balances,
        transactions=upto(all_transactions(ledger)),
        closed=closed,
        entries=entries,
        ledger_storage=ledger.storage_dir,
        card_properties=dict(src.card_properties),
        card_clearing_account=src.card_clearing_account,
        card_emi_account=src.card_emi_account,
        card_expiry_soon_days=src.card_expiry_soon_days,
        payments=in_transit.payments_in_transit(
            upto(all_flow_transactions(ledger)),
            src.card_clearing_account,
            closed=closed,
            card_postings=rows,
            today=report_date,
        ),
        vehicles=inv,
    )
    builders = {
        "investments": overview_groups.investments_group,
        "property": overview_groups.property_group,
        "bank": overview_groups.bank_group,
        "other": overview_groups.other_group,
        "loans": overview_groups.loans_group,
        "cards": overview_groups.cards_group,
        "people": overview_groups.people_group,
        "other_liabilities": overview_groups.other_liabilities_group,
    }

    def built(order: list[tuple[str, str]], headline: str) -> list[dict]:
        groups = (builders[gid](ctx) for gid, _ in order)
        return _reconciled(
            [_finish(g, names) for g in groups if g is not None], headline
        )

    totals = _totals(rows, names, report_date)
    return {
        "as_of": report_date.isoformat(),
        "totals": totals,
        "groups": built(ASSET_GROUPS, totals["assets"]),
        "liability_groups": built(LIABILITY_GROUPS, totals["liabilities"]),
        "investments": {
            "xirr_basis": "held_lots",
            "vehicles": _vehicle_rows(inv, src.benchmarks, benchmark, equity_index),
        },
        "benchmarks": _benchmark_block(inv, src.benchmarks, benchmark, equity_index)
        if src.benchmarks is not None
        else None,
        "benchmarks_error": src.benchmarks_error if src.benchmarks is None else None,
    }
