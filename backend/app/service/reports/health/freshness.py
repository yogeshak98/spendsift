"""Shared price-freshness rule for any read-model that shows a valued total.

Lifted out of `equity_holdings.py` so every valuation-bearing report (equity,
mutual fund, and the cross-vehicle investment hub) derives "how stale is this
number" the same way instead of each re-deriving its own staleness rule.
"""

from __future__ import annotations

from datetime import date

# Equities/MF NAVs update roughly five days a week. A close still this old
# means at least one, and plausibly several, refresh cycles were missed --
# beyond a long weekend or a single market holiday. Past this boundary the
# number on screen is treated as unmistakably stale rather than merely "a bit
# behind". This boundary is for feed prices only.
STALE_AFTER_DAYS = 7


def source_state(as_of: date | None, today: date) -> str:
    """Honest freshness of `as_of` relative to `today`. Never hardcoded --
    derived from the actual age of the newest stored close so a screen can
    never show a number without also being able to say where it came from."""
    if as_of is None:
        # No price exists for anything held; there is nothing "live" or
        # "cached" to claim. Treat as the most conservative state rather than
        # implying any freshness that isn't there.
        return "stale"
    age_days = (today - as_of).days
    if age_days <= 0:
        return "live"
    if age_days <= STALE_AFTER_DAYS:
        return "cached"
    return "stale"
