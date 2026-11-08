"""Orchestrates investment valuation: reads surviving FIFO lots + full buy/redeem
cashflows from the ledger, the latest NAV from the (duck-typed) NavStore, and
assembles the gains response via the pure valuation core. Framework-free.

Read-only on the journal. No network IO.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from beancount.core import data
from beancount.core.inventory import Inventory

from backend.app.core.errors.errors import INVESTMENT_VALUATION_INCONSISTENT
from backend.app.models.domain.accounts import HOLDING_PREFIX, Vehicle
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.platform_accounts import (
    GROWW,
    platform_clearing_account,
)
from backend.app.service.reports.networth.valuation import (
    FundInput,
    Lot,
    value_portfolio,
)

_HOLDING_PREFIX = HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value]
# Redemptions can leave a fractional-unit rounding residue (e.g. 0.0002 units)
# that is not a real holding — treat anything below this as fully exited.
_DUST_UNITS = Decimal("0.1")


class InvestmentService:
    def __init__(self, ledger: Ledger, nav_store, *, clock=date.today) -> None:
        self._ledger = ledger
        self._nav_store = nav_store
        # The app's single source of "today" (see create_app). Held as a
        # callable, not a date, so a long-lived process does not freeze its
        # valuation date at boot.
        self._clock = clock

    # --- ledger extraction ---------------------------------------------------

    def _commodity_postings(self, entry: data.Transaction) -> list:
        clearing = platform_clearing_account(GROWW)
        return [
            p
            for p in entry.postings
            if p.account.startswith(_HOLDING_PREFIX)
            and p.account != clearing
            and p.units is not None
            and p.units.currency != "INR"
        ]

    def _cash_amount(self, entry: data.Transaction) -> Decimal | None:
        clearing = platform_clearing_account(GROWW)
        for p in entry.postings:
            if p.account == clearing and p.units is not None:
                return p.units.number
        return None

    def _held_units(self, entries: list) -> dict[str, Decimal]:
        units: dict[str, Decimal] = defaultdict(Decimal)
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            for p in self._commodity_postings(entry):
                units[p.units.currency] += p.units.number
        return dict(units)

    def _names(self, entries: list) -> dict[str, str]:
        names: dict[str, str] = {}
        for entry in entries:
            if isinstance(entry, data.Commodity):
                meta_name = (entry.meta or {}).get("name")
                if meta_name:
                    names[entry.currency] = str(meta_name)
        return names

    def _lots_and_cashflows(
        self,
        entries: list,
    ) -> tuple[dict[str, Inventory], dict[str, list[tuple[date, Decimal]]]]:
        inventories: dict[str, Inventory] = defaultdict(Inventory)
        cashflows: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            commodity_postings = self._commodity_postings(entry)
            if not commodity_postings:
                continue  # dividend-only / non-MF txn: not a buy/redeem flow
            cash = self._cash_amount(entry)
            symbols_in_txn = {p.units.currency for p in commodity_postings}
            for p in commodity_postings:
                inventories[p.units.currency].add_amount(p.units, p.cost)
            # The MF import books one symbol per buy/redeem txn; attribute the
            # cash flow to that single symbol. (Guarded: only when exactly one symbol.)
            if cash is not None and len(symbols_in_txn) == 1:
                sym = next(iter(symbols_in_txn))
                cashflows[sym].append((entry.date, cash))
        return inventories, cashflows

    # --- public --------------------------------------------------------------

    def gains(self) -> dict:
        entries = self._ledger.load().entries  # load the journal once per request
        held_units = self._held_units(entries)
        for sym, qty in held_units.items():
            if qty < 0:
                raise INVESTMENT_VALUATION_INCONSISTENT.exc({"symbol": sym})

        held = {sym for sym, qty in held_units.items() if qty >= _DUST_UNITS}
        inventories, cashflows = self._lots_and_cashflows(entries)
        names = self._names(entries)

        bare = {s[2:] for s in held}
        latest = self._nav_store.latest(bare)
        nav_by_symbol = {f"MF{code}": row for code, row in latest.items()}
        as_of = self._nav_store.as_of()

        funds: list[FundInput] = []
        exited_cashflows: list[tuple[date, Decimal]] = []

        all_symbols = set(cashflows) | set(inventories) | held
        for sym in sorted(all_symbols):
            if sym not in held:
                # Fully-exited: contribute only to portfolio lifetime XIRR.
                exited_cashflows.extend(cashflows.get(sym, []))
                continue
            lots = [
                Lot(
                    units=pos.units.number,
                    cost=pos.cost.number,
                    lot_date=pos.cost.date,
                )
                for pos in inventories[sym].get_positions()
                if pos.units.number > 0
                and pos.cost is not None
                and pos.cost.date is not None
            ]
            nav_row = nav_by_symbol.get(sym)
            funds.append(
                FundInput(
                    symbol=sym,
                    name=names.get(sym, sym),
                    units=held_units[sym],
                    lots=lots,
                    nav=nav_row.nav if nav_row else None,
                    nav_date=nav_row.date if nav_row else None,
                    cashflows=cashflows.get(sym, []),
                )
            )

        today = as_of if as_of is not None else self._clock()
        result = value_portfolio(funds, today, exited_cashflows=exited_cashflows)
        result["as_of"] = as_of.isoformat() if as_of is not None else None
        return result
