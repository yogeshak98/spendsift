"""Investments by vehicle, judged by XIRR.

Two vehicles carry per-lot holdings and a price feed: mutual funds (lots in the
investment ledger, NAVs in nav_history) and stocks (lots in the main ledger,
closes in equity_history). Their XIRR is cash-flow dated: each held lot is a
dated outflow of its cost and today's value is the inflow, solved by the shared
`xirr`. It is the XIRR of what is held now (lots already sold do not enter),
the same `current_xirr` the investment screens show. Every other vehicle (FD,
PPF, NPS, insurance, anything else under Assets:Investment: or Assets:Debt:)
has no valuation source, so it is reported at cost with `at_cost` true, no gain
and no XIRR -- cost is never dressed up as a value or a return.

Read-only; never raises on a thin ledger (an empty one yields no vehicles).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from beancount.core import data
from beancount.core.inventory import Inventory

from backend.app.models.domain.accounts import HOLDING_PREFIX, Vehicle
from backend.app.persistence.journal import Ledger
from backend.app.persistence.nav_store import NavStore
from backend.app.service.reports.health.ledger_view import PostingRow
from backend.app.service.reports.networth.account_groups import (
    group_of,
    vehicle_of,
)
from backend.app.service.reports.networth.valuation import (
    FundInput,
    Lot,
    value_portfolio,
)

_MF_KEY = Vehicle.MUTUAL_FUND.value
_STOCK_KEY = Vehicle.STOCK.value
_MF_PREFIX = HOLDING_PREFIX[_MF_KEY]
_STOCK_PREFIX = HOLDING_PREFIX[_STOCK_KEY]
_CASH_SUFFIX = ":Cash"


def _funds(
    ledger: Ledger,
    prefix: str,
    store: NavStore,
    *,
    strip_mf: bool,
    as_of: date | None,
) -> list[FundInput]:
    """Held symbols under `prefix` as FundInputs priced at the store's latest
    close on or before `as_of` (the latest close when `as_of` is None); lots
    bought after `as_of` are not held yet. A symbol with no such price, or with a costless posting (its cost basis
    would be understated), is left unpriced (`nav=None`)."""
    inventories: dict[str, Inventory] = defaultdict(Inventory)
    names: dict[str, str] = {}
    costless: set[str] = set()
    for entry in ledger.load().entries:
        if isinstance(entry, data.Commodity):
            name = (entry.meta or {}).get("name")
            if name:
                names[entry.currency] = str(name)
            continue
        if not isinstance(entry, data.Transaction):
            continue
        if as_of is not None and entry.date > as_of:
            continue
        for p in entry.postings:
            if not p.account.startswith(prefix):
                continue
            if p.units is None or p.units.currency == "INR":
                continue
            if p.cost is None:
                costless.add(p.units.currency)
            inventories[p.units.currency].add_amount(p.units, p.cost)

    held: list[tuple[str, Decimal, list[Lot]]] = []
    for symbol in sorted(inventories):
        positions = [
            pos for pos in inventories[symbol].get_positions() if pos.units.number > 0
        ]
        units = sum((pos.units.number for pos in positions), Decimal(0))
        if units <= 0:
            continue
        lots = [
            Lot(units=pos.units.number, cost=pos.cost.number, lot_date=pos.cost.date)
            for pos in positions
            if pos.cost is not None and pos.cost.date is not None
        ]
        held.append((symbol, units, lots))

    def code(symbol: str) -> str:
        return symbol[2:] if strip_mf and symbol.startswith("MF") else symbol

    codes = {code(s) for s, _, _ in held}
    latest = store.latest(codes) if as_of is None else store.nav_as_of(codes, as_of)
    out: list[FundInput] = []
    for symbol, units, lots in held:
        row = None if symbol in costless else latest.get(code(symbol))
        out.append(
            FundInput(
                symbol=symbol,
                name=names.get(symbol, symbol),
                units=units,
                lots=lots,
                nav=row.nav if row else None,
                nav_date=row.date if row else None,
                cashflows=[],
            )
        )
    return out


def _priced_row(
    key: str, label: str, funds: list[FundInput], today: date
) -> dict | None:
    if not funds:
        return None
    result = value_portfolio(funds, today)
    totals = result["totals"]
    unpriced_cost = sum(
        (Decimal(u["cost_basis"]) for u in result["unpriced"]), Decimal(0)
    )
    priced_cost = Decimal(totals["cost_basis"])
    priced = len(funds) - result["unpriced_count"]
    return {
        "key": key,
        "label": label,
        "holdings": len(funds),
        "invested": Decimal(totals["cost_basis"]) + unpriced_cost,
        "value": Decimal(totals["current_value"]) + unpriced_cost,
        "gain": Decimal(totals["unrealized_gain"]) if priced else None,
        "gain_pct": totals["abs_return_pct"] if priced and priced_cost else None,
        "xirr_pct": totals["current_xirr"] if priced else None,
        "at_cost": priced == 0,
        "partial": 0 < priced < len(funds),
    }


def _at_cost_rows(rows: list[PostingRow], clearing: frozenset[str]) -> list[dict]:
    """Vehicles with no valuation source: positive account balances at cost."""
    balances: dict[str, Decimal] = {}
    for r in rows:
        if group_of(r.account) != "investments":
            continue
        balances[r.account] = balances.get(r.account, Decimal(0)) + r.inr_amount
    by_vehicle: dict[str, tuple[str, Decimal, int]] = {}
    for account, amount in balances.items():
        vehicle = vehicle_of(account)
        if (
            vehicle is None
            or vehicle[0] in (_MF_KEY, _STOCK_KEY)
            or account in clearing
            or account.endswith(_CASH_SUFFIX)
            or amount <= 0
        ):
            continue
        key, label = vehicle
        _, total, count = by_vehicle.get(key, (label, Decimal(0), 0))
        by_vehicle[key] = (label, total + amount, count + 1)
    return [
        {
            "key": key,
            "label": label,
            "holdings": count,
            "invested": total,
            "value": total,
            "gain": None,
            "gain_pct": None,
            "xirr_pct": None,
            "at_cost": True,
            "partial": False,
        }
        for key, (label, total, count) in by_vehicle.items()
    ]


def investment_vehicles(
    rows: list[PostingRow],
    ledger: Ledger,
    investment_ledger: Ledger,
    today: date,
    clearing_accounts: frozenset[str] = frozenset(),
    as_of: date | None = None,
) -> dict:
    """`{"vehicles": [...], "xirr_pct", "gain", "invested", "value"}`.

    Vehicles are sorted by value, largest first; `value` is market value where
    priced, cost where not (see `at_cost`). `xirr_pct` is the pooled XIRR of
    every priced holding (mutual funds and stocks together), None when nothing
    is priced; `as_of` bounds the lots and picks the latest price on or before
    it, and a holding with no such price is counted at cost, never at a later
    price; `gain` sums only the priced vehicles' gains. Amounts are
    Decimals here; the caller serializes."""
    mf_funds = _funds(
        investment_ledger,
        _MF_PREFIX,
        NavStore(investment_ledger.storage_dir / "prices" / "nav_history.parquet"),
        strip_mf=True,
        as_of=as_of,
    )
    stock_funds = _funds(
        ledger,
        _STOCK_PREFIX,
        NavStore(ledger.storage_dir / "prices" / "equity_history.parquet"),
        strip_mf=False,
        as_of=as_of,
    )
    vehicles = [
        row
        for row in (
            _priced_row(_MF_KEY, "Mutual Fund", mf_funds, today),
            _priced_row(_STOCK_KEY, "Stock", stock_funds, today),
        )
        if row is not None
    ] + _at_cost_rows(rows, clearing_accounts)
    vehicles.sort(key=lambda v: v["value"], reverse=True)

    pooled = value_portfolio(mf_funds + stock_funds, today)["totals"]
    any_priced = any(not v["at_cost"] for v in vehicles)
    return {
        "vehicles": vehicles,
        "xirr_pct": pooled["current_xirr"] if any_priced else None,
        "gain": sum((v["gain"] for v in vehicles if v["gain"] is not None), Decimal(0)),
        "invested": sum((v["invested"] for v in vehicles), Decimal(0)),
        "value": sum((v["value"] for v in vehicles), Decimal(0)),
    }
