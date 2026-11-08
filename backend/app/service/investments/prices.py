"""Orchestrates a NAV refresh: fetch -> parse -> filter held -> store ->
write latest prices. Framework-free (no FastAPI). Raises by code.

source: "fetched" (200), "cached" (304), "offline" (no network), "never"
(status() with an empty store and no recorded outcome). The last refresh's
(source, as_of) is persisted to the cache sidecar so status() is honest after a
reload.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date as _date
from decimal import Decimal

from beancount.core import data

from backend.app.core.errors.errors import EMPTY_DUMP_WHILE_HOLDING
from backend.app.persistence.journal import Ledger
from backend.app.models.domain.accounts import HOLDING_PREFIX, Vehicle
from backend.app.service.ingest.platform_accounts import (
    GROWW,
    platform_clearing_account,
)
from backend.app.clients import amfi as _amfi
from backend.app.clients import mfapi as _mfapi
from backend.app.clients.amfi import FetchStatus
from backend.app.persistence.price_cache import PriceCache
from backend.app.persistence.nav_store import NavStore
from backend.app.clients.navall import NavAllDump, parse_navall

# Module-level alias so tests can patch backend.app.service.investments.prices.mfapi_fetch
mfapi_fetch = _mfapi.fetch_history

_HOLDING_PREFIX = HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value]

# A stored history with consecutive NAVs further apart than this has a hole in
# it (a weekend plus a long holiday is at most ~5 days). The daily refresh only
# records the days it runs, so such holes are normal and backfill must fill them.
_MAX_HISTORY_GAP_DAYS = 5


def _has_gap(dates: list[_date], since: _date) -> bool:
    # Keep the last NAV before `since` too: it is what covers `since`, and a
    # hole starting right after it is still a hole.
    ordered = sorted(dates)
    before = [d for d in ordered if d < since]
    kept = before[-1:] + [d for d in ordered if d >= since]
    return any((b - a).days > _MAX_HISTORY_GAP_DAYS for a, b in zip(kept, kept[1:]))


_SOURCE = {
    FetchStatus.OK: "fetched",
    FetchStatus.NOT_MODIFIED: "cached",
    FetchStatus.OFFLINE: "offline",
}


class PriceService:
    def __init__(
        self,
        *,
        ledger: Ledger,
        nav_store: NavStore,
        cache: PriceCache,
        source_url: str,
        client=_amfi,
        transport=None,
        clock=_date.today,
    ) -> None:
        self._ledger = ledger
        self._nav_store = nav_store
        self._cache = cache
        self._source_url = source_url
        self._clock = clock
        self._client = client
        self._transport = transport

    # --- held set + transacted set + names -----------------------------------

    def held_symbols(self) -> set[str]:
        """Commodities with a POSITIVE unit balance under the Groww holding
        accounts. A fully-redeemed (zero-unit) fund nets to 0 and is NOT held."""
        balances: dict[str, Decimal] = defaultdict(Decimal)
        for entry in self._ledger.load().entries:
            if not isinstance(entry, data.Transaction):
                continue
            for posting in entry.postings:
                if not posting.account.startswith(_HOLDING_PREFIX):
                    continue
                currency = posting.units.currency
                if currency != "INR":  # the commodity symbol, e.g. MF122639
                    balances[currency] += posting.units.number
        return {sym for sym, qty in balances.items() if qty > 0}

    def transacted_symbols(self) -> dict[str, _date]:
        """All ever-transacted MF symbols (code without MF prefix) → earliest txn date."""
        earliest: dict[str, _date] = {}
        clearing = platform_clearing_account(GROWW)
        for entry in self._ledger.load().entries:
            if not isinstance(entry, data.Transaction):
                continue
            for p in entry.postings:
                if not p.account.startswith(_HOLDING_PREFIX):
                    continue
                if p.account == clearing:
                    continue
                if p.units is None or p.units.currency == "INR":
                    continue
                code = p.units.currency[2:]  # strip MF prefix
                if code not in earliest or entry.date < earliest[code]:
                    earliest[code] = entry.date
        return earliest

    def backfill(self, transport=None) -> dict:
        """Pull historical NAVs from mfapi.in for all ever-transacted funds.

        Skips a fund if the store already covers from before its first transaction
        date through at least 2 days before today (live refresh handles the recents)
        AND has no hole wider than `_MAX_HISTORY_GAP_DAYS` in between -- covering
        both ends is not coverage when the refresh skipped days in the middle.
        Rate-limits sequential fetches with a 0.2 s sleep (skipped in test mode).
        """
        today = self._clock()
        from datetime import timedelta

        recent_threshold = today - timedelta(days=2)

        transacted = self.transacted_symbols()
        fetched = 0
        skipped_covered = 0
        skipped_errors: list[dict] = []
        rows_added = 0

        for code, earliest_txn in transacted.items():
            dr = self._nav_store.date_range(code)
            if (
                dr is not None
                and dr[0] <= earliest_txn
                and dr[1] >= recent_threshold
                and not _has_gap(
                    [r.date for r in self._nav_store.history(code)], earliest_txn
                )
            ):
                skipped_covered += 1
                continue

            try:
                rows = mfapi_fetch(code, transport=transport)
            except Exception as exc:
                skipped_errors.append({"code": code, "reason": str(exc)})
                continue

            filtered = [r for r in rows if r.date >= earliest_txn]
            if filtered:
                self._nav_store.append(filtered)
                rows_added += len(filtered)
            fetched += 1
            if transport is None:
                time.sleep(0.2)

        return {
            "fetched": fetched,
            "skipped_already_covered": skipped_covered,
            "skipped_errors": skipped_errors,
            "rows_added": rows_added,
        }

    def fund_names(self) -> dict[str, str]:
        """Symbol -> commodity `name:` meta (written by ensure_commodity_accounts);
        falls back to the symbol when no meta exists."""
        names: dict[str, str] = {}
        for entry in self._ledger.load().entries:
            if isinstance(entry, data.Commodity):
                meta_name = (entry.meta or {}).get("name")
                if meta_name:
                    names[entry.currency] = str(meta_name)
        return names

    # --- core ----------------------------------------------------------------

    def refresh(self) -> dict:
        held = self.held_symbols()
        cached = self._cache.read()
        result = self._client.conditional_get(
            self._source_url,
            etag=cached.etag if cached else None,
            last_modified=cached.last_modified if cached else None,
            transport=self._transport,
        )

        if result.status is FetchStatus.OK and result.body is not None:
            dump = parse_navall(result.body)
            if held and not dump.schemes:
                # Bad feed: refuse to clobber a good cache / clear prices. The
                # fresh body is NOT persisted, so the cached dump survives.
                raise EMPTY_DUMP_WHILE_HOLDING.exc()
            # Only persist once the body has parsed to real schemes.
            self._cache.write(result.body, result.etag, result.last_modified)
            self._store_and_write(held, dump)
        else:
            # 304 / OFFLINE -> fall back to the cached dump (None on first run).
            if cached is not None:
                dump = parse_navall(cached.body)
                if held and not dump.schemes:
                    raise EMPTY_DUMP_WHILE_HOLDING.exc()
                self._store_and_write(held, dump)

        source = _SOURCE[result.status]
        out = self._snapshot(held, source)
        # #2: persist the last outcome so a later status() reload is honest.
        self._cache.write_outcome(source, out["as_of"])
        return out

    def status(self) -> dict:
        held = self.held_symbols()
        recorded_source, _recorded_as_of = self._cache.read_outcome()
        as_of = self._nav_store.as_of()
        source = recorded_source or ("never" if as_of is None else "cached")
        return self._snapshot(held, source)

    # --- helpers -------------------------------------------------------------

    def _store_and_write(self, held: set[str], dump: NavAllDump) -> None:
        codes = {s[2:] for s in held}  # strip "MF" prefix
        rows = [dump.navs[c] for c in codes if c in dump.navs]
        if rows:
            self._nav_store.append(rows)
        latest = self._nav_store.latest(codes)
        prices = {f"MF{code}": row for code, row in latest.items()}
        self._ledger.write_latest_prices(prices)

    def _snapshot(self, held: set[str], source: str) -> dict:
        codes = {s[2:] for s in held}
        latest = self._nav_store.latest(codes)
        priced_syms = {f"MF{code}" for code in latest}
        names = self.fund_names()

        def fund(sym: str) -> dict:
            return {"symbol": sym, "name": names.get(sym, sym)}

        priced = [fund(s) for s in sorted(held & priced_syms)]
        unpriced = [fund(s) for s in sorted(held - priced_syms)]
        as_of = self._nav_store.as_of()
        return {
            "as_of": as_of.isoformat() if as_of is not None else None,
            "source": source,
            "priced": priced,
            "unpriced": unpriced,
        }
