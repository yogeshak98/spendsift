"""Orchestrates an equity price refresh: fetch bhavcopy -> parse -> filter held
-> store -> write price directives. Framework-free. Raises by code.

The sibling of PriceService, with three deliberate differences:

  * it reads the MAIN ledger. Mutual funds were detached into
    investments.beancount, but Assets:Investment:Stock:* was not part of that
    migration and still lives in main.beancount;
  * it writes prices-equity.beancount, never prices.beancount, so the two feeds
    cannot erase each other (each rewrites its own file wholesale);
  * there is no conditional GET. The bhavcopy is a dated immutable artifact --
    a given day's file never changes -- so ETag/If-Modified-Since buy nothing.
    "cached" therefore means "the feed gave us nothing new today and we kept
    what we had", which the walk-back already expresses.

source: "fetched" (a new trading day's file landed), "offline" (no network or
unreadable archive), "never" (empty store and no prior refresh).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as _date
from decimal import Decimal

from beancount.core import data

from backend.app.core.errors.errors import EQUITY_QUOTES_EMPTY_WHILE_HOLDING
from backend.app.persistence.journal import Ledger
from backend.app.clients.amfi import FetchStatus
from backend.app.clients.bhavcopy import parse_bhavcopy
from backend.app.persistence.nav_store import NavStore
from backend.app.models.domain.nav import NavRow
from backend.app.clients.nse import fetch_bhavcopy

_HOLDING_PREFIX = "Assets:Investment:Stock:"
_PRICES_FILE = "prices-equity.beancount"
_logger = logging.getLogger("backend")


class EquityPriceService:
    def __init__(
        self,
        *,
        ledger: Ledger,
        nav_store: NavStore,
        fetcher=fetch_bhavcopy,
        transport=None,
        clock=_date.today,
    ) -> None:
        self._ledger = ledger
        self._nav_store = nav_store
        self._fetcher = fetcher
        self._transport = transport
        self._clock = clock

    def held_symbols(self) -> set[str]:
        """Commodities with a POSITIVE unit balance under the Stock accounts.
        A fully-sold holding nets to 0 and is NOT held."""
        balances: dict[str, Decimal] = defaultdict(Decimal)
        for entry in self._ledger.load().entries:
            if not isinstance(entry, data.Transaction):
                continue
            for posting in entry.postings:
                if not posting.account.startswith(_HOLDING_PREFIX):
                    continue
                if posting.units is None:
                    continue
                currency = posting.units.currency
                if currency != "INR":  # the commodity symbol, e.g. SBIN
                    balances[currency] += posting.units.number
        return {sym for sym, qty in balances.items() if qty > 0}

    def refresh(self, on: _date | None = None) -> dict:
        held = self.held_symbols()
        result = self._fetcher(on=on or self._clock(), transport=self._transport)

        if result.status is FetchStatus.OK and result.text is not None:
            parsed = parse_bhavcopy(result.text)
            quotes = parsed.quotes
            if parsed.duplicate_tickers:
                # Ambiguous rows (same TckrSymb, e.g. differing SctySrs) were
                # refused by the parser rather than resolved by file order.
                # Surface it even when the colliding symbol isn't held today,
                # since a held-tomorrow symbol silently losing its price is
                # exactly the failure mode this guards against.
                _logger.warning(
                    "bhavcopy: duplicate ticker rows refused pricing: %s",
                    ", ".join(sorted(parsed.duplicate_tickers)),
                )
            if held and not quotes:
                # Bad feed: refuse to clobber good prices with nothing.
                raise EQUITY_QUOTES_EMPTY_WHILE_HOLDING.exc()
            rows = [
                NavRow(scheme_code=sym, nav=q.close, date=q.date)
                for sym, q in quotes.items()
                if sym in held
            ]
            if rows:
                self._nav_store.append(rows)
            source = "fetched"
        else:
            source = "offline"

        self._write(held)
        return self._snapshot(held, source)

    def status(self) -> dict:
        held = self.held_symbols()
        source = "never" if self._nav_store.as_of() is None else "cached"
        return self._snapshot(held, source)

    # --- helpers -------------------------------------------------------------

    def _write(self, held: set[str]) -> None:
        self._ledger.write_prices_file(_PRICES_FILE, self._nav_store.latest(held))

    def _snapshot(self, held: set[str], source: str) -> dict:
        latest = self._nav_store.latest(held)
        priced = sorted(held & set(latest))
        unpriced = sorted(held - set(latest))
        as_of = max((r.date for r in latest.values()), default=None)
        return {
            "as_of": as_of.isoformat() if as_of is not None else None,
            "source": source,
            "priced": [{"symbol": s, "name": s} for s in priced],
            "unpriced": [{"symbol": s, "name": s} for s in unpriced],
        }
