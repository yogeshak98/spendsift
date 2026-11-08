"""Equity holdings valued at the latest stored close.

Deliberately narrow: this is NOT the generalised valuation engine. That arrives
in U4 with the asset-class registry. This module answers one question -- what
are the Assets:Investment:Stock:* holdings worth today -- so the equity price
feed has a visible surface the day it lands.

An unpriced holding is reported separately at cost and is NEVER folded into the
valued totals: a total that silently mixes market value with cost is a number
nobody can act on.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from beancount.core import data

from backend.app.persistence.journal import Ledger
from backend.app.persistence.nav_store import NavStore
from backend.app.service.reports.health.freshness import source_state as _source_state

_HOLDING_PREFIX = "Assets:Investment:Stock:"
_MONEY = Decimal("0.01")


def _q(value: Decimal) -> str:
    return str(value.quantize(_MONEY))


def _positions(
    ledger: Ledger,
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, str], set[str]]:
    """(units by symbol, cost basis by symbol, commodity display names,
    symbols with at least one costless holding posting).

    A costless posting on a holding account is not reachable for FIFO-booked
    equity buys today, but if it ever happens the summed cost for that symbol
    is understated (and any gain computed against it overstated) without any
    signal that anything is wrong. Track it explicitly rather than silently
    dropping the posting's contribution to cost -- the caller routes any
    symbol in this set to `unpriced`, matching how a holding with no market
    price is already handled, instead of ever valuing it.
    """
    units: dict[str, Decimal] = defaultdict(Decimal)
    cost: dict[str, Decimal] = defaultdict(Decimal)
    names: dict[str, str] = {}
    incomplete_cost: set[str] = set()
    for entry in ledger.load().entries:
        if isinstance(entry, data.Commodity):
            meta_name = (entry.meta or {}).get("name")
            if meta_name:
                names[entry.currency] = str(meta_name)
            continue
        if not isinstance(entry, data.Transaction):
            continue
        for posting in entry.postings:
            if not posting.account.startswith(_HOLDING_PREFIX):
                continue
            if posting.units is None or posting.units.currency == "INR":
                continue
            symbol = posting.units.currency
            units[symbol] += posting.units.number
            if posting.cost is not None:
                cost[symbol] += posting.units.number * posting.cost.number
            else:
                incomplete_cost.add(symbol)
    return units, cost, names, incomplete_cost


def equity_holdings(
    ledger: Ledger, nav_store: NavStore, *, today: date | None = None
) -> dict:
    units, cost, names, incomplete_cost = _positions(ledger)
    held = {s for s, q in units.items() if q > 0}
    # Symbols with an incomplete cost basis are never priced, even when a
    # market close exists for them -- see `_positions`.
    latest = nav_store.latest(held - incomplete_cost)

    holdings: list[dict] = []
    unpriced: list[dict] = []
    total_value = Decimal(0)
    total_cost = Decimal(0)
    unpriced_count = 0

    for symbol in sorted(held):
        basis = cost[symbol]
        row = None if symbol in incomplete_cost else latest.get(symbol)
        if row is None:
            unpriced_count += 1
            unpriced.append(
                {
                    "symbol": symbol,
                    "name": names.get(symbol, symbol),
                    "units": str(units[symbol]),
                    "cost_basis": _q(basis),
                }
            )
            continue
        value = units[symbol] * row.nav
        total_value += value
        total_cost += basis
        holdings.append(
            {
                "symbol": symbol,
                "name": names.get(symbol, symbol),
                "units": str(units[symbol]),
                "price": _q(row.nav),
                "price_date": row.date.isoformat(),
                "current_value": _q(value),
                "cost_basis": _q(basis),
                "unrealized_gain": _q(value - basis),
            }
        )

    # Two dates, deliberately: `as_of` is the NEWEST close backing any priced
    # holding, `stalest_as_of` the OLDEST. A consumer that renders one badge
    # for the whole vehicle must classify freshness from the oldest one --
    # otherwise a symbol priced this morning makes a symbol priced three
    # months ago read "live". Exposing both here is what lets the hub card and
    # this vehicle's own screen give the SAME verdict for the same data
    # instead of each deriving its own.
    #
    # `latest` was looked up for exactly the priced-and-held symbols
    # (`held - incomplete_cost`), so these are the dates of the rows in
    # `holdings` and nothing else -- an unpriced holding contributes no date
    # because it has none, not because it was skipped.
    priced_dates = [r.date for r in latest.values()]
    as_of = max(priced_dates, default=None)
    stalest_as_of = min(priced_dates, default=None)
    resolved_today = today if today is not None else date.today()
    return {
        "as_of": as_of.isoformat() if as_of is not None else None,
        "stalest_as_of": stalest_as_of.isoformat()
        if stalest_as_of is not None
        else None,
        # Valuation mode "priced": units of a commodity priced by a dated
        # `price` directive from a feed. Fixed for this report -- every row
        # here is a Stock holding, never a mark or an accrual.
        "mode": "priced",
        # Honest, derived-not-hardcoded freshness of `as_of` -- see
        # `_source_state`. A screen must never show `current_value` /
        # `unrealized_gain` without also being able to say how fresh they are.
        "source_state": _source_state(as_of, resolved_today),
        "totals": {
            "current_value": _q(total_value),
            "cost_basis": _q(total_cost),
            "unrealized_gain": _q(total_value - total_cost),
            # Explicit signal that `totals` excludes any holding listed in
            # `unpriced` -- a consumer that renders only `totals` must not be
            # able to mistake an understated portfolio for a complete one.
            "unpriced_count": unpriced_count,
            "is_partial": unpriced_count > 0,
        },
        "holdings": holdings,
        "unpriced": unpriced,
    }
