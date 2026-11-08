"""Invested-vs-value history -- the series behind the chart on the scheme and
stock detail screens.

The honesty rules are the entire point of this module:

1. The value series is emitted **only over the range where a real stored
   price exists**. A price is never interpolated backwards from today or
   forwards from an old close to fill a purchase date that predates stored
   history -- if a fund was bought in 2019 but stored NAV history only
   starts in 2024, the series starts in 2024 and `covers_from` says so.
2. `invested` is a **step** series: it only changes on a transaction date
   and holds flat between them. It is never smoothed or interpolated.
3. If there is no stored price history at all for the instrument (true for
   almost every stock today -- the equity feed only began accumulating
   closes on 2026-09-04), `value_points` is `[]` plus a `truncated_reason`
   that says so. An empty chart with an honest reason beats a fabricated
   line.

`invested` and `value` are genuinely known at different times: invested is
known exactly at every instant and changes only on a transaction date, value
is known only where a stored price exists. Sampling both at price dates would
make a purchase that falls between two stored prices render as a diagonal
ramp under a default line chart -- money appearing to trickle in over weeks
when it arrived in one day. So the two series are returned as separate
arrays (`invested_points`, `value_points`) rather than one merged `points`
list, keeping both honesty properties structural rather than a convention
downstream chart code has to remember.

`coverage_starts_late` (value coverage begins after the first purchase) and
`source_state`/`as_of` (how stale the last stored price is, reusing
`freshness.source_state` -- the same rule `instrument_detail.py` uses, not a
second one) are evaluated independently of each other, so a series that
starts late *and* whose last stored price is old surfaces both signals
rather than one silently swallowing the other.

Read-only and never raises, like every read model under `service/reports/`: an unknown
vehicle/instrument, or one with no booked transactions, returns an empty
series with a `truncated_reason` rather than blowing up -- the Task 4 route
decides how (or whether) to surface that as a 404.
"""

from __future__ import annotations

import bisect
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from beancount.core import data

from backend.app.models.domain.accounts import HOLDING_PREFIX, Vehicle
from backend.app.persistence.journal import Ledger
from backend.app.persistence.nav_store import NavStore
from backend.app.models.domain.nav import NavRow
from backend.app.service.reports.health.freshness import source_state as _source_state

_MONEY = Decimal("0.01")

# Mirrors the account conventions instrument_detail.py / investment_hub.py /
# equity_holdings.py already use -- one leaf account per instrument, keyed by
# its own currency.
_STOCK_PREFIX = "Assets:Investment:Stock:"
_MF_PREFIX = HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value]


def _q(value: Decimal) -> str:
    return str(value.quantize(_MONEY, rounding=ROUND_HALF_UP))


def _price_source(
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


def _events(
    entries: list, prefix: str, instrument: str
) -> tuple[list[tuple[date, Decimal, Decimal]], int]:
    """One (date, signed units delta, signed cost-basis delta) entry per
    transaction touching this instrument's account -- a buy contributes
    +units and +units*cost, a beancount FIFO-resolved sell contributes
    -units and -units*cost (units is already negative on the reducing
    posting; cost is the per-unit cost of the lot(s) actually consumed).
    Summed cumulatively in date order this reproduces both units held and
    cost basis outstanding at any point in time, without this module
    re-deriving FIFO matching itself.

    Also returns a count of postings on this instrument's account that carry
    no cost at all -- not reachable for FIFO-booked buys/sells in practice,
    but if it ever happened, folding it in would fabricate a cost. Excluded,
    same as `instrument_detail.py`'s costless-position handling, but the
    count is surfaced (not silent) so a caller can see the series
    understates units/invested rather than assuming it's complete."""
    events: dict[date, tuple[Decimal, Decimal]] = {}
    skipped_costless = 0
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            continue
        units_delta = Decimal(0)
        cost_delta = Decimal(0)
        touched = False
        for posting in entry.postings:
            if not posting.account.startswith(prefix):
                continue
            if posting.units is None or posting.units.currency != instrument:
                continue
            if posting.cost is None:
                skipped_costless += 1
                continue
            touched = True
            units_delta += posting.units.number
            cost_delta += posting.units.number * posting.cost.number
        if touched:
            prev_units, prev_cost = events.get(entry.date, (Decimal(0), Decimal(0)))
            events[entry.date] = (prev_units + units_delta, prev_cost + cost_delta)
    return [(d, u, c) for d, (u, c) in sorted(events.items())], skipped_costless


def _as_of(
    events: list[tuple[date, Decimal, Decimal]], as_of: date
) -> tuple[Decimal, Decimal]:
    """Cumulative (units, invested cost-basis) for every event on or before
    `as_of` -- the step-series value at that date."""
    dates = [d for d, _u, _c in events]
    idx = bisect.bisect_right(dates, as_of)
    units = sum((u for _d, u, _c in events[:idx]), Decimal(0))
    invested = sum((c for _d, _u, c in events[:idx]), Decimal(0))
    return units, invested


def _empty(reason: str, today: date) -> dict:
    return {
        "invested_points": [],
        "value_points": [],
        "covers_from": None,
        "truncated_reason": reason,
        "source_state": _source_state(None, today),
        "as_of": None,
        "coverage_starts_late": False,
    }


def value_history(
    ledger: Ledger,
    vehicle_key: str,
    instrument: str | None = None,
    *,
    today: date | None = None,
) -> dict:
    resolved_today = today if today is not None else date.today()

    if vehicle_key == "stock":
        prefix = _STOCK_PREFIX
    elif vehicle_key == "mutual_fund":
        prefix = _MF_PREFIX
    else:
        return _empty(f"unknown vehicle {vehicle_key!r}", resolved_today)

    if not instrument:
        return _empty("no instrument specified", resolved_today)

    entries = ledger.load().entries
    events, skipped_costless = _events(entries, prefix, instrument)
    if not events:
        return _empty(f"no booked transactions for {instrument}", resolved_today)
    first_purchase = events[0][0]

    # Rule 2: invested is known exactly at every instant and is reported
    # independently of price coverage -- a point at every transaction date.
    invested_points = [
        {"date": d.isoformat(), "invested": _q(_as_of(events, d)[1])}
        for d, _u, _c in events
    ]

    # vehicle_key was already validated to be "stock" or "mutual_fund" above,
    # so this is always non-None here.
    source = _price_source(ledger, vehicle_key, instrument)
    assert source is not None
    nav_store, price_code = source

    history: list[NavRow] = [
        row for row in nav_store.history(price_code) if row.date <= resolved_today
    ]

    # Rule 1: never show a value before real stored coverage begins, and
    # never show one before the instrument was actually held -- whichever
    # is later.
    usable = [row for row in history if row.date >= first_purchase]

    reasons: list[str] = []
    if skipped_costless:
        reasons.append(
            f"{skipped_costless} costless posting(s) on {instrument} excluded "
            "from invested/value -- a cost was never fabricated for them"
        )

    if not usable:
        if not history:
            reasons.append(f"no stored price history for {instrument}")
        else:
            reasons.append(
                f"no stored price history for {instrument} on or after its "
                f"{first_purchase.isoformat()} purchase date"
            )
        return {
            "invested_points": invested_points,
            "value_points": [],
            "covers_from": None,
            "truncated_reason": "; ".join(reasons),
            "source_state": _source_state(None, resolved_today),
            "as_of": None,
            "coverage_starts_late": False,
        }

    covers_from = usable[0].date
    value_points = []
    for row in usable:
        units_held, _invested = _as_of(events, row.date)
        value_points.append(
            {"date": row.date.isoformat(), "value": _q(units_held * row.nav)}
        )

    # Independently-evaluated honesty signals -- neither one suppresses the
    # other, so a series that starts late AND has a stale tail surfaces both.
    coverage_starts_late = covers_from > first_purchase
    if coverage_starts_late:
        reasons.append(
            f"stored price history begins {covers_from.isoformat()}, after "
            f"the {first_purchase.isoformat()} purchase -- earlier value is "
            "not shown"
        )

    latest_price_date = usable[-1].date
    state = _source_state(latest_price_date, resolved_today)
    if state == "stale":
        reasons.append(
            f"price history is stale as of {latest_price_date.isoformat()}; "
            "more recent prices have not been fetched yet"
        )

    return {
        "invested_points": invested_points,
        "value_points": value_points,
        "covers_from": covers_from.isoformat(),
        "truncated_reason": "; ".join(reasons) if reasons else None,
        "source_state": state,
        "as_of": latest_price_date.isoformat(),
        "coverage_starts_late": coverage_starts_late,
    }


# How far back a scheme's last stored price may sit and still value a date in
# the aggregate. Covers weekends and exchange holidays (no NAV is published,
# so every scheme's "latest" is the prior close -- standard valuation, not
# interpolation). A gap any wider means that scheme genuinely has no price
# near that date, and the date is dropped rather than summed without it.
_AGGREGATE_MAX_PRICE_AGE_DAYS = 7


def _instruments(entries: list, prefix: str) -> list[str]:
    """Every currency held AT COST under `prefix` -- the holdings. A vehicle
    prefix can also hold a plain cash leg (e.g. `...:MutualFund:Groww:Cash`
    in INR); that is money awaiting allotment, not an instrument, and is
    never priced."""
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            continue
        for posting in entry.postings:
            if (
                posting.account.startswith(prefix)
                and posting.units is not None
                and posting.cost is not None
            ):
                seen.add(posting.units.currency)
    return sorted(seen)


def vehicle_value_history(
    ledger: Ledger, vehicle_key: str, *, today: date | None = None
) -> dict:
    """Invested-vs-value history summed across every instrument of one
    vehicle -- the chart on the vehicle screen (e.g. the MF screen as a
    whole), same shape and same honesty rules as `value_history`.

    The aggregate adds one rule of its own: a value point on date D is the
    sum over every instrument HELD on D (units > 0) of units x its latest
    stored price on or before D, and is emitted only when EVERY held
    instrument has such a price within `_AGGREGATE_MAX_PRICE_AGE_DAYS`.
    Summing whichever schemes happen to be priced would draw a dip that is
    really a missing price, so such a date is skipped (a gap) instead.

    An instrument with no stored price history at all can never be valued,
    and keeping it would blank every date it is held. It is excluded from
    BOTH series (so invested and value stay comparable) and named in
    `truncated_reason` -- stated, never silent.
    """
    resolved_today = today if today is not None else date.today()

    if vehicle_key == "stock":
        prefix = _STOCK_PREFIX
    elif vehicle_key == "mutual_fund":
        prefix = _MF_PREFIX
    else:
        return _empty(f"unknown vehicle {vehicle_key!r}", resolved_today)

    entries = ledger.load().entries
    reasons: list[str] = []
    per_instrument: list[tuple[list[tuple[date, Decimal, Decimal]], list[NavRow]]] = []
    unpriced: list[str] = []
    skipped_costless = 0
    for instrument in _instruments(entries, prefix):
        events, skipped = _events(entries, prefix, instrument)
        skipped_costless += skipped
        if not events:
            continue
        source = _price_source(ledger, vehicle_key, instrument)
        assert source is not None
        nav_store, code = source
        history = [r for r in nav_store.history(code) if r.date <= resolved_today]
        if not history:
            unpriced.append(instrument)
            continue
        per_instrument.append((events, history))

    if skipped_costless:
        reasons.append(
            f"{skipped_costless} costless posting(s) excluded from invested/value "
            "-- a cost was never fabricated for them"
        )
    if unpriced:
        reasons.append(
            f"{len(unpriced)} holding(s) with no stored price history excluded "
            f"from both lines: {', '.join(unpriced)}"
        )
    if not per_instrument:
        return _empty(
            "; ".join(reasons) or "no booked transactions for this vehicle",
            resolved_today,
        )

    all_events: dict[date, Decimal] = {}
    for events, _h in per_instrument:
        for d, _u, c in events:
            all_events[d] = all_events.get(d, Decimal(0)) + c
    event_dates = sorted(all_events)
    invested_points = []
    running = Decimal(0)
    for d in event_dates:
        running += all_events[d]
        invested_points.append({"date": d.isoformat(), "invested": _q(running)})
    first_purchase = event_dates[0]

    price_dates = sorted(
        {r.date for _e, h in per_instrument for r in h if r.date >= first_purchase}
    )
    value_points = []
    gaps = 0
    for d in price_dates:
        total = Decimal(0)
        held_any = False
        complete = True
        for events, history in per_instrument:
            units, _inv = _as_of(events, d)
            if units == 0:
                continue
            held_any = True
            idx = bisect.bisect_right([r.date for r in history], d)
            if (
                idx == 0
                or (d - history[idx - 1].date).days > _AGGREGATE_MAX_PRICE_AGE_DAYS
            ):
                complete = False
                break
            total += units * history[idx - 1].nav
        if not held_any:
            continue
        if not complete:
            gaps += 1
            continue
        value_points.append({"date": d.isoformat(), "value": _q(total)})

    if gaps:
        reasons.append(
            f"{gaps} date(s) skipped where a held scheme had no stored price "
            "nearby -- never summed without it"
        )
    if not value_points:
        reasons.append("no date where every held holding has a stored price")
        return {
            "invested_points": invested_points,
            "value_points": [],
            "covers_from": None,
            "truncated_reason": "; ".join(reasons),
            "source_state": _source_state(None, resolved_today),
            "as_of": None,
            "coverage_starts_late": False,
        }

    covers_from = date.fromisoformat(value_points[0]["date"])
    coverage_starts_late = covers_from > first_purchase
    if coverage_starts_late:
        reasons.append(
            f"value can only be summed from {covers_from.isoformat()}, after the "
            f"{first_purchase.isoformat()} first purchase -- earlier value is not shown"
        )
    latest = date.fromisoformat(value_points[-1]["date"])
    state = _source_state(latest, resolved_today)
    if state == "stale":
        reasons.append(
            f"price history is stale as of {latest.isoformat()}; more recent "
            "prices have not been fetched yet"
        )
    return {
        "invested_points": invested_points,
        "value_points": value_points,
        "covers_from": covers_from.isoformat(),
        "truncated_reason": "; ".join(reasons) if reasons else None,
        "source_state": state,
        "as_of": latest.isoformat(),
        "coverage_starts_late": coverage_starts_late,
    }
