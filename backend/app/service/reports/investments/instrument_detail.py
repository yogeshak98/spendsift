"""Per-instrument detail read-model -- the drill-down behind a single row of
the investment hub (`Investments / Stock / SBIN`, `Investments / Mutual Fund
/ <scheme>`).

Lots are listed at their real, per-lot cost -- never averaged. `avg_cost` is
a derived display figure (`cost_basis / units`, quantized once at the end);
it is never fed back into any other computation. Booked lots are read from
`ledger.load()` via a beancount `Inventory`, so FIFO reduction has already
been resolved by beancount itself -- this module never re-derives lot
matching.

Read-only, and deliberately never raises: an unknown vehicle/instrument (or
one with no current holding) returns `None` so the caller (the Task 4 route)
can decide how to surface "not found" -- no read model under
`service/reports/` raises; raising coded errors is left to the services above
them (`service/investments/`, `service/ingest/`, ...).
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from beancount.core import data
from beancount.core.inventory import Inventory
from dateutil.relativedelta import relativedelta

from backend.app.models.domain.accounts import HOLDING_PREFIX, Vehicle
from backend.app.persistence.journal import Ledger
from backend.app.persistence.nav_store import NavStore
from backend.app.service.reports.health.freshness import source_state as _source_state

_MONEY = Decimal("0.01")
_PCT = Decimal("0.01")

# Mirrors the account conventions equity_holdings.py / investment_hub.py
# already use -- one leaf account per instrument, keyed by its own currency.
_STOCK_PREFIX = "Assets:Investment:Stock:"
_MF_PREFIX = HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value]


def _q(value: Decimal) -> str:
    return str(value.quantize(_MONEY, rounding=ROUND_HALF_UP))


def _pct(value: Decimal) -> str:
    return str(value.quantize(_PCT, rounding=ROUND_HALF_UP))


def _long_term_on(lot_date: date) -> date:
    """The date a lot crosses into long-term for equity / MF-equity: purchase
    + 12 months. Derived per lot -- an averaged holding date would blur the
    planning signal this column exists to give."""
    return lot_date + relativedelta(months=12)


def _inventory_and_name(
    entries: list, prefix: str, instrument: str
) -> tuple[Inventory, str | None]:
    """Beancount Inventory built only from postings on this instrument's
    account, keyed by its own currency -- so FIFO reductions already booked
    by beancount are replayed here, not re-derived."""
    inventory = Inventory()
    name: str | None = None
    for entry in entries:
        if isinstance(entry, data.Commodity) and entry.currency == instrument:
            meta_name = (entry.meta or {}).get("name")
            if meta_name:
                name = str(meta_name)
            continue
        if not isinstance(entry, data.Transaction):
            continue
        for posting in entry.postings:
            if not posting.account.startswith(prefix):
                continue
            if posting.units is None or posting.units.currency != instrument:
                continue
            inventory.add_amount(posting.units, posting.cost)
    return inventory, name


def _price_lookup(
    ledger: Ledger, vehicle_key: str, instrument: str
) -> tuple[NavStore, str] | None:
    if vehicle_key == "stock":
        return (
            NavStore(ledger.storage_dir / "prices" / "equity_history.parquet"),
            instrument,
        )
    if vehicle_key == "mutual_fund":
        code = instrument[2:] if instrument.startswith("MF") else instrument
        return NavStore(ledger.storage_dir / "prices" / "nav_history.parquet"), code
    return None


def instrument_detail(
    ledger: Ledger,
    vehicle_key: str,
    instrument: str,
    *,
    today: date | None = None,
) -> dict | None:
    if vehicle_key == "stock":
        prefix = _STOCK_PREFIX
    elif vehicle_key == "mutual_fund":
        prefix = _MF_PREFIX
    else:
        return None

    entries = ledger.load().entries
    inventory, name = _inventory_and_name(entries, prefix, instrument)
    positions = [pos for pos in inventory.get_positions() if pos.units.number > 0]

    lots: list[dict] = []
    units_total = Decimal(0)
    cost_basis_total = Decimal(0)
    for pos in positions:
        if pos.cost is None or pos.cost.date is None:
            # A costless holding posting is not reachable for FIFO-booked
            # buys, but if it ever happened, folding it into cost_basis
            # would understate cost and overstate gain with no signal
            # anything is wrong -- exclude it rather than fabricate a cost.
            continue
        lot_units = pos.units.number
        lot_cost = pos.cost.number
        lot_basis = lot_units * lot_cost
        units_total += lot_units
        cost_basis_total += lot_basis
        lots.append(
            {
                "date": pos.cost.date.isoformat(),
                "units": str(lot_units),
                "cost": _q(lot_cost),
                "cost_basis": _q(lot_basis),
                "long_term_on": _long_term_on(pos.cost.date).isoformat(),
            }
        )

    if not lots:
        # Nothing currently held under this instrument -- unknown symbol,
        # a fully exited holding, or an unsupported vehicle_key all look
        # the same from here: there is nothing honest to report.
        return None

    resolved_today = today if today is not None else date.today()
    avg_cost = cost_basis_total / units_total

    price_source = _price_lookup(ledger, vehicle_key, instrument)
    row = None
    if price_source is not None:
        nav_store, price_code = price_source
        row = nav_store.latest({price_code}).get(price_code)

    base: dict[str, object] = {
        "vehicle": vehicle_key,
        "symbol": instrument,
        "name": name or instrument,
        "units": str(units_total),
        "avg_cost": _q(avg_cost),
        "cost_basis": _q(cost_basis_total),
        "lots": lots,
    }

    if row is None:
        # No stored price -- report unvalued honestly rather than showing
        # cost dressed up as market value.
        return {
            **base,
            "mode": "unvalued",
            "source_state": _source_state(None, resolved_today),
            "as_of": None,
            "price": None,
            "price_date": None,
            "current_value": None,
            "unrealized_gain": None,
            "gain_pct": None,
        }

    current_value = units_total * row.nav
    unrealized_gain = current_value - cost_basis_total
    gain_pct = (
        None
        if cost_basis_total == 0
        else _pct(unrealized_gain / cost_basis_total * Decimal(100))
    )
    return {
        **base,
        "mode": "priced",
        "source_state": _source_state(row.date, resolved_today),
        "as_of": row.date.isoformat(),
        "price": _q(row.nav),
        "price_date": row.date.isoformat(),
        "current_value": _q(current_value),
        "unrealized_gain": _q(unrealized_gain),
        "gain_pct": gain_pct,
    }
