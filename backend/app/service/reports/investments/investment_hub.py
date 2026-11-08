"""Cross-vehicle investment hub rollup.

Groups every `Assets:Investment:<Vehicle>:...` account by its vehicle segment
and reports each vehicle's cost basis, and -- only where a valuation source
exists -- its current market value and unrealized gain.

An unvalued vehicle (today: FD, IndWealth -- anything with no valuation mode
wired up yet) reports `current_value: None` / `unrealized_gain: None`. It is
NEVER shown at cost dressed up as if it were market value: `totals` sums
`current_value` only across valued vehicles, and `is_partial` says whether any
vehicle was left out of that sum. This is the one invariant this module exists
to hold -- a total that silently absorbs cost basis as market value is exactly
the defect it must prevent.

Read-only: never writes to the journal, never raises (a bad/incomplete ledger
degrades to an honest partial answer, not a 500).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from beancount.core import data
from beancount.core.inventory import Inventory

from backend.app.models.domain.accounts import HOLDING_PREFIX, Vehicle
from backend.app.persistence.journal import Ledger
from backend.app.persistence.nav_store import NavStore
from backend.app.service.ingest.platform_accounts import (
    GROWW,
    platform_clearing_account,
)
from backend.app.service.reports.investments.equity_holdings import equity_holdings
from backend.app.service.reports.health.freshness import source_state
from backend.app.service.reports.health.ledger_view import (
    commodity_names_from_entries,
    display_account,
)
from backend.app.service.reports.networth.valuation import (
    FundInput,
    Lot,
    value_portfolio,
)

_PREFIX = "Assets:Investment:"
_MF_PREFIX = HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value]
_MONEY = Decimal("0.01")

# Camel-hump boundary: a LOWERCASE letter followed by an uppercase one.
# "MutualFund" -> "mutual_fund" / "Mutual Fund"; "IndWealth" -> "ind_wealth" /
# "Ind Wealth". All-caps segments like "FD" pass through untouched.
#
# A digit is deliberately NOT a left boundary. It used to be, and it mangled
# real instrument names: a tenor-suffixed fixed deposit "HDFC5Y" rendered as
# "HDFC5 Y", and "P2P" as "P2 P". A digit adjacent to an uppercase letter is a
# code or a tenor, not a word break -- only a lower-to-upper transition
# reliably is.
_HUMP = re.compile(r"(?<=[a-z])(?=[A-Z])")

# Vehicle segments that have a valuation mode wired up. Everything else is
# at-cost ("unvalued") -- today FD and IndWealth. Kept as one named set so the
# hub's dispatch below and `at_cost_holdings`'s "is this vehicle at-cost?"
# question can never drift apart into two different answers.
_VALUED_VEHICLES = frozenset({"MutualFund", "Stock"})


def _q(value: Decimal) -> str:
    return str(value.quantize(_MONEY))


def _key(vehicle: str) -> str:
    return _HUMP.sub("_", vehicle).lower()


def _label(vehicle: str) -> str:
    return _HUMP.sub(" ", vehicle)


def _vehicle_names(entries: list) -> list[str]:
    """Distinct vehicle segments declared under Assets:Investment:, in the
    order first opened -- derived from `open` directives so a vehicle with no
    current transactions (e.g. every FD fully closed) still surfaces rather
    than silently vanishing."""
    seen: list[str] = []
    for entry in entries:
        if not isinstance(entry, data.Open):
            continue
        if not entry.account.startswith(_PREFIX):
            continue
        rest = entry.account[len(_PREFIX) :]
        vehicle = rest.split(":", 1)[0]
        if vehicle and vehicle not in seen:
            seen.append(vehicle)
    return seen


def _mf_funds(
    entries: list,
) -> tuple[list[tuple[str, str, Decimal, list[Lot]]], dict, bool]:
    """Held mutual-fund symbols with positive units: (symbol, name, units,
    lots); plus whether ANY commodity posting was seen at all (regardless of
    the resulting net position) -- distinguishes "genuinely fully redeemed"
    from "this ledger has no postings for this vehicle at all"."""
    inventories: dict[str, Inventory] = defaultdict(Inventory)
    names: dict[str, str] = {}
    saw_any_posting = False
    clearing = platform_clearing_account(GROWW)
    for entry in entries:
        if isinstance(entry, data.Commodity):
            meta_name = (entry.meta or {}).get("name")
            if meta_name:
                names[entry.currency] = str(meta_name)
            continue
        if not isinstance(entry, data.Transaction):
            continue
        for p in entry.postings:
            if not p.account.startswith(_MF_PREFIX) or p.account == clearing:
                continue
            if p.units is None or p.units.currency == "INR":
                continue
            saw_any_posting = True
            inventories[p.units.currency].add_amount(p.units, p.cost)

    funds: list[tuple[str, str, Decimal, list[Lot]]] = []
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
        funds.append((symbol, names.get(symbol, symbol), units, lots))
    return funds, names, saw_any_posting


def _mutual_fund_vehicle(ledger: Ledger, entries: list, today: date) -> dict | None:
    funds, _names, saw_any_posting = _mf_funds(entries)

    if not saw_any_posting:
        # This ledger declares the MutualFund vehicle open (that's why this
        # function was called at all -- see `_vehicle_names`) but contains
        # not one matching per-scheme posting. This covers two real shapes,
        # not just the one with per-scheme accounts explicitly open:
        #   1. Per-scheme Groww accounts are open here but never posted to
        #      (the synthetic single-ledger test shape).
        #   2. Only the coarse `Assets:Investment:MutualFund` account is
        #      open here at all -- the actual production shape after the MF
        #      detach: `ensure_commodity_accounts` writes per-scheme opens
        #      only to investment_ledger, so main.beancount never opens (or
        #      posts to) a single Groww:<scheme> account, and the earlier
        #      `_mf_open_symbol_accounts(entries)` check here was empty and
        #      never caught this -- silently falling through to a fabricated
        #      priced ₹0 vehicle instead of being excluded.
        # Either way, the per-scheme lots live only in a separate
        # investment_ledger file this call was never given. Reporting a
        # priced zero here would look like a settled, fully-exited vehicle
        # when in truth this ledger simply cannot see the money. Excluded
        # entirely rather than shown at a fabricated cost/value; the caller
        # (route/service wiring) is responsible for combining ledgers before
        # invoking this function, the same way routes/reports.py's
        # `/investments` route already merges investment_ledger's rows in.
        return None

    nav_store = NavStore(ledger.storage_dir / "prices" / "nav_history.parquet")
    bare_codes = {symbol[2:] for symbol, *_ in funds if symbol.startswith("MF")}
    latest = nav_store.latest(bare_codes)
    nav_by_symbol = {f"MF{code}": row for code, row in latest.items()}

    fund_inputs = [
        FundInput(
            symbol=symbol,
            name=name,
            units=units,
            lots=lots,
            nav=nav_by_symbol[symbol].nav if symbol in nav_by_symbol else None,
            nav_date=nav_by_symbol[symbol].date if symbol in nav_by_symbol else None,
            cashflows=[],
        )
        for symbol, name, units, lots in funds
    ]
    result = value_portfolio(fund_inputs, today)
    # Newest AND oldest NAV date backing this vehicle -- see
    # equity_holdings.py's identical pair. The hub card's freshness badge is
    # classified from `stalest_as_of`, so it can never read fresher than the
    # vehicle screen one click below it, which derives its own header from the
    # oldest per-scheme nav_date.
    nav_dates = [r.date for r in nav_by_symbol.values()]
    as_of = max(nav_dates, default=None)
    stalest_as_of = min(nav_dates, default=None)
    return {
        "key": "mutual_fund",
        "label": "Mutual Fund",
        "holdings": len(fund_inputs),
        "cost_basis": result["totals"]["cost_basis"],
        "current_value": result["totals"]["current_value"],
        "unrealized_gain": result["totals"]["unrealized_gain"],
        "mode": "priced",
        "as_of": as_of.isoformat() if as_of is not None else None,
        "stalest_as_of": stalest_as_of.isoformat()
        if stalest_as_of is not None
        else None,
        # Propagated from value_portfolio: True when at least one held fund
        # inside this (otherwise "priced") vehicle has no NAV, so this
        # vehicle's own cost_basis/current_value already silently exclude
        # that fund's cost -- the hub total must not call itself complete
        # when that happened.
        "is_partial": result["unpriced_count"] > 0,
    }


def _stock_vehicle(ledger: Ledger, today: date) -> dict:
    nav_store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    result = equity_holdings(ledger, nav_store, today=today)
    return {
        "key": "stock",
        "label": "Stock",
        "holdings": len(result["holdings"]) + len(result["unpriced"]),
        "cost_basis": result["totals"]["cost_basis"],
        "current_value": result["totals"]["current_value"],
        "unrealized_gain": result["totals"]["unrealized_gain"],
        "mode": "priced",
        "as_of": result["as_of"],
        "stalest_as_of": result["stalest_as_of"],
        # See _mutual_fund_vehicle's "is_partial" -- equity_holdings' own
        # totals already exclude any unpriced holding's cost; propagate that
        # signal rather than silently dropping it.
        "is_partial": result["totals"]["is_partial"],
    }


def _unvalued_balances(entries: list, vehicle: str) -> dict[str, Decimal]:
    """Per-account INR balance under `Assets:Investment:<vehicle>`, dropping
    accounts that net to zero (a fully closed FD is not a holding).

    THE single walk behind every at-cost answer: `_unvalued_vehicle` (the
    hub's one-line-per-vehicle rollup) and `at_cost_holdings` (the vehicle
    screen's per-account list) both call it, so the count and total on the hub
    card can never disagree with the rows on the screen behind it. Per this
    module's own rule, never a second parallel mechanism.
    """
    prefix = _PREFIX + vehicle
    balances: dict[str, Decimal] = defaultdict(Decimal)
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            continue
        for p in entry.postings:
            if p.account != prefix and not p.account.startswith(prefix + ":"):
                continue
            if p.units is None or p.units.currency != "INR":
                continue
            balances[p.account] += p.units.number
    return {account: amount for account, amount in balances.items() if amount != 0}


def _unvalued_vehicle(entries: list, vehicle: str) -> dict:
    held = _unvalued_balances(entries, vehicle)
    cost_basis = sum(held.values(), Decimal(0))
    return {
        "key": _key(vehicle),
        "label": _label(vehicle),
        "holdings": len(held),
        "cost_basis": _q(cost_basis),
        "current_value": None,
        "unrealized_gain": None,
        "mode": "unvalued",
        "as_of": None,
        # No price behind an at-cost vehicle means no date at either end --
        # never a fabricated "oldest" to keep the field shape uniform.
        "stalest_as_of": None,
        # The whole vehicle is already unvalued (counted in unvalued_count
        # below) -- this flag is reserved for a *priced* vehicle that
        # silently excluded some of its own holdings from its totals.
        "is_partial": False,
    }


def _holding_name(account: str, vehicle: str) -> str:
    """The human-readable remainder of `account` below the vehicle prefix.

    `Assets:Investment:FD:HDFC` under vehicle "FD" -> "HDFC";
    `Assets:Investment:FD:HDFC:TaxSaver` -> "HDFC · Tax Saver". A posting made
    directly on the bare vehicle account has no remainder at all, so it is
    named for the vehicle itself rather than rendered as an empty cell.
    """
    prefix = _PREFIX + vehicle
    rest = account[len(prefix) :].lstrip(":")
    if not rest:
        return _label(vehicle)
    return " · ".join(_label(segment) for segment in rest.split(":"))


def _at_cost_holding(account: str, amount: Decimal, vehicle: str, names: dict) -> dict:
    """One at-cost row: the raw account, its scheme-resolved display path, the
    humanized label the UI shows, and the cost.

    `account_display` is the repo-wide contract for any raw account path
    reaching the UI (ledger_view.display_account / commodity_names) -- the same
    call `reports/assets.py` and `reports/dashboard.py` already make on these
    very account paths. This vehicle is at-cost, not code-free:
    `models/domain/accounts.py:investment()` sanctions
    `Assets:Investment:<vehicle>:<issuer>:<instrument>` for ANY vehicle, and
    `_unvalued_balances` filters on posting CURRENCY, never on account naming,
    so a trailing scheme code is entirely possible here.

    `name` is derived from the RESOLVED path, so the label the UI renders can
    never be a raw scheme code either -- `account_display` is the traceable
    path beside it, not a substitute for it.
    """
    resolved = display_account(account, names)
    return {
        "account": account,
        "account_display": resolved,
        "name": _holding_name(resolved, vehicle),
        "cost_basis": _q(amount),
    }


def at_cost_holdings(ledger: Ledger, vehicle_key: str) -> dict | None:
    """Per-account at-cost holdings for one unvalued vehicle (FD, IndWealth).

    The drill-down behind the hub's at-cost vehicle card. Deliberately carries
    NO current_value, no gain and no `as_of`: an at-cost figure is what was
    put in, never what it is worth today, and a price date it does not have
    must not be implied by a freshness field it does not populate. `mode` is
    always "unvalued" -- the field exists so a consumer can say WHY there is
    no value rather than having to infer it from missing keys.

    Returns None -- never an empty-but-successful body -- when `vehicle_key`
    names no vehicle in this ledger, or names one that IS valued (mutual_fund,
    stock): those have their own richer read models, and answering here with
    an at-cost-only view would quietly strip their valuation. The caller turns
    that None into a 404.
    """
    entries = ledger.load().entries
    vehicle = next((v for v in _vehicle_names(entries) if _key(v) == vehicle_key), None)
    if vehicle is None or vehicle in _VALUED_VEHICLES:
        return None

    held = _unvalued_balances(entries, vehicle)
    # The shared helper, fed the entries already parsed above -- never a
    # second ledger.load(), and never an open-coded copy of its loop.
    names = commodity_names_from_entries(entries)
    return {
        "key": _key(vehicle),
        "label": _label(vehicle),
        "mode": "unvalued",
        "as_of": None,
        "totals": {"cost_basis": _q(sum(held.values(), Decimal(0)))},
        "holdings": [
            _at_cost_holding(account, amount, vehicle, names)
            for account, amount in sorted(held.items())
        ],
    }


def investment_hub(ledger: Ledger, *, today: date | None = None) -> dict:
    resolved_today = today if today is not None else date.today()
    entries = ledger.load().entries
    vehicle_names = _vehicle_names(entries)

    vehicles: list[dict] = []
    # Vehicles this ledger declares accounts for but can resolve zero
    # postings against -- see `_mutual_fund_vehicle`. Never rendered as a row
    # (there is nothing honest to put in cost_basis/current_value for them),
    # but they must still make the hub totals say "incomplete".
    unresolved_count = 0
    for vehicle in vehicle_names:
        if vehicle == "MutualFund":
            row = _mutual_fund_vehicle(ledger, entries, resolved_today)
            if row is None:
                unresolved_count += 1
                continue
            vehicles.append(row)
        elif vehicle == "Stock":
            vehicles.append(_stock_vehicle(ledger, resolved_today))
        else:
            # Anything not in _VALUED_VEHICLES lands here -- the same
            # predicate `at_cost_holdings` uses to decide a vehicle is
            # drillable at cost.
            vehicles.append(_unvalued_vehicle(entries, vehicle))

    total_cost = sum((Decimal(v["cost_basis"]) for v in vehicles), Decimal(0))
    valued = [v for v in vehicles if v["current_value"] is not None]
    total_value = sum((Decimal(v["current_value"]) for v in valued), Decimal(0))
    # Gain is derived only from valued vehicles' own (value - cost) -- summing
    # ALL vehicles' cost basis against only the VALUED vehicles' value would
    # misattribute an unvalued vehicle's cost basis as an unrealized loss.
    total_gain = sum((Decimal(v["unrealized_gain"]) for v in valued), Decimal(0))
    # "Incomplete" happens two ways: a vehicle that is wholly unvalued (or
    # unresolved -- excluded above), or a *priced* vehicle whose own totals
    # already silently excluded one of its holdings (finding #2: propagate
    # equity_holdings'/value_portfolio's own partial signal instead of
    # dropping it on the floor).
    unvalued_count = (
        sum(1 for v in vehicles if v["mode"] == "unvalued") + unresolved_count
    )
    any_vehicle_partial = any(v["is_partial"] for v in vehicles)

    as_of = max(
        (date.fromisoformat(v["as_of"]) for v in valued if v["as_of"] is not None),
        default=None,
    )
    # The genuinely oldest holding date anywhere in the hub -- a MIN OF THE
    # PER-VEHICLE MINIMA, never a min of per-vehicle maxima. The latter is
    # what "oldest" used to mean here, and it could be months newer than the
    # oldest price actually backing a number on screen.
    stalest_as_of = min(
        (
            date.fromisoformat(v["stalest_as_of"])
            for v in valued
            if v["stalest_as_of"] is not None
        ),
        default=None,
    )

    return {
        "as_of": as_of.isoformat() if as_of is not None else None,
        "stalest_as_of": stalest_as_of.isoformat()
        if stalest_as_of is not None
        else None,
        # Classified from the STALEST date, not the newest: the headline must
        # never look fresher than the oldest price backing it.
        "source_state": source_state(stalest_as_of, resolved_today),
        "totals": {
            "cost_basis": _q(total_cost),
            "current_value": _q(total_value),
            "unrealized_gain": _q(total_gain),
            "unvalued_count": unvalued_count,
            "is_partial": unvalued_count > 0 or any_vehicle_partial,
        },
        "vehicles": vehicles,
    }
