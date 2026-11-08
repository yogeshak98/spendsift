"""Pure price-change computation: NAV-based returns for standard windows.

No pyarrow, no FastAPI. Reads via the NavStore duck-type interface.
Returns are price-only (NAV-based), not portfolio-value delta — so fresh SIP
contributions do not inflate the change percentage.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from backend.app.models.domain.nav import NavRow

_PCT_Q = Decimal("0.01")
_MONEY_Q = Decimal("0.01")
_WINDOWS = ["1D", "1M", "1Y", "SI"]
# Longest calendar gap that still reads as "one trading day": a weekend plus a
# market holiday (Thu -> Mon). Anything wider is a hole in the NAV history, and
# presenting it as 1D would report a multi-day move as a single day's.
_MAX_1D_GAP_DAYS = 4


@dataclass(frozen=True)
class ChangeWindow:
    label: str
    date_then: date | None
    nav_then: Decimal | None
    nav_now: Decimal
    return_pct: str | None  # human percent string ("2.30"), None if unavailable


def _boundary_date(label: str, today: date, si_date: date | None) -> date | None:
    if label == "1M":
        m = today.month - 1 or 12
        y = today.year if today.month > 1 else today.year - 1
        day = min(today.day, calendar.monthrange(y, m)[1])
        return date(y, m, day)
    if label == "1Y":
        try:
            return today.replace(year=today.year - 1)
        except ValueError:
            return today.replace(year=today.year - 1, day=28)
    if label == "SI":
        return si_date
    return None


def _pct(nav_now: Decimal, nav_then: Decimal) -> str:
    return str(
        ((nav_now / nav_then - 1) * 100).quantize(_PCT_Q, rounding=ROUND_HALF_UP)
    )


def fund_change_windows(
    code: str,
    nav_now_row: NavRow,
    nav_store,
    today: date,
) -> list[ChangeWindow]:
    """Compute 1D/1M/1Y/SI NAV-price return windows for a single fund."""
    dr = nav_store.date_range(code)
    si_date = dr[0] if dr else None

    windows: list[ChangeWindow] = []
    for label in _WINDOWS:
        if label == "1D":
            windows.append(_one_day_window(code, nav_now_row, nav_store))
            continue
        bd = _boundary_date(label, today, si_date)
        if bd is None or bd >= today:
            windows.append(ChangeWindow(label, None, None, nav_now_row.nav, None))
            continue
        prior = nav_store.nav_as_of({code}, bd)
        row_then = prior.get(code)
        if row_then is None:
            windows.append(ChangeWindow(label, bd, None, nav_now_row.nav, None))
        else:
            windows.append(
                ChangeWindow(
                    label,
                    row_then.date,
                    row_then.nav,
                    nav_now_row.nav,
                    _pct(nav_now_row.nav, row_then.nav),
                )
            )
    return windows


def _one_day_window(code: str, nav_now_row: NavRow, nav_store) -> ChangeWindow:
    """1D = the NAV shown against the stored NAV immediately before it.

    Anchored to the NAV's own date, not the calendar: a fund whose latest NAV
    is yesterday's would otherwise be compared with itself and report 0.00%.
    None when the previous stored NAV is more than `_MAX_1D_GAP_DAYS` older --
    a gap in the history, not a trading day.
    """
    nav_date = nav_now_row.date
    prior = nav_store.nav_as_of({code}, nav_date - timedelta(days=1)).get(code)
    if prior is None or (nav_date - prior.date).days > _MAX_1D_GAP_DAYS:
        return ChangeWindow("1D", None, None, nav_now_row.nav, None)
    return ChangeWindow(
        "1D", prior.date, prior.nav, nav_now_row.nav, _pct(nav_now_row.nav, prior.nav)
    )


def window_value_change(units: Decimal, window: ChangeWindow) -> str | None:
    """Rupee value moved by a window's NAV change, for `units` held today.

    This is the amount beside the percent on the holdings table -- "-1,922.77"
    under "-0.14%". It exists in Python, not in the frontend, because the
    frontend may not do arithmetic on money: units and NAVs arrive as Decimal
    strings, and `units * (nav_now - nav_then)` in JavaScript would silently
    turn a 4-decimal unit count and a 4-decimal NAV into a float before
    rendering it as rupees.

    Price-only, matching the rest of this module: it answers "what did the
    price move do to what I hold now", so a purchase made inside the window
    does not appear as a gain. That makes it comparable with `return_pct`
    beside it, and it is why the same units are applied to both ends.

    None when the window has no `nav_then` -- there is no baseline, so there is
    no change to state. Never 0, which would claim the price did not move.
    """
    if window.nav_then is None:
        return None
    return str(
        ((window.nav_now - window.nav_then) * units).quantize(
            _MONEY_Q, rounding=ROUND_HALF_UP
        )
    )


def portfolio_windows(
    fund_windows: list[tuple[str, list[ChangeWindow]]],
    values_now: dict[str, Decimal],
) -> list[ChangeWindow]:
    """Weighted-average portfolio NAV return across funds with known values."""
    by_label: dict[str, list[tuple[Decimal, Decimal]]] = {lbl: [] for lbl in _WINDOWS}

    for sym, windows in fund_windows:
        val = values_now.get(sym)
        if val is None or val <= 0:
            continue
        for w in windows:
            if w.return_pct is not None and w.nav_then is not None:
                by_label[w.label].append((val, Decimal(w.return_pct)))

    result: list[ChangeWindow] = []
    for label in _WINDOWS:
        pairs = by_label[label]
        if not pairs:
            result.append(ChangeWindow(label, None, None, Decimal(0), None))
            continue
        total_val = sum(v for v, _ in pairs)
        weighted = Decimal(sum(v * r for v, r in pairs)) / Decimal(total_val)
        result.append(
            ChangeWindow(
                label,
                None,
                None,
                Decimal(0),
                str(weighted.quantize(_PCT_Q, rounding=ROUND_HALF_UP)),
            )
        )
    return result
