"""Pure investment valuation: per-fund + portfolio figures at latest NAV.

No ledger or NavStore IO — the service hands in already-extracted inputs.
Money/percent fields are emitted as Decimal strings (or None). Percentages are
human percents (e.g. "18.40"). XIRR fractions are converted to percents via
*100; None stays None. Unpriced funds (nav is None) are quarantined: listed in
`unpriced`, excluded from all totals (never valued at 0 or at cost).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from backend.app.service.reports.investments.xirr import xirr

_PCT_Q = Decimal("0.01")  # percents rendered to 2 dp
_MONEY_Q = Decimal("0.01")  # INR money fields rendered to 2 dp (paise)


def _money_str(amount: Decimal) -> str:
    """Quantize an INR money Decimal to 2dp before serializing.

    Multiplying/summing Decimals (units * nav, cost-basis totals, etc.) can
    accumulate far more than 2 significant decimal digits; without this,
    that noise flows straight into the API response and the UI."""
    return str(amount.quantize(_MONEY_Q, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Lot:
    units: Decimal
    cost: Decimal  # per-unit acquisition cost (INR)
    lot_date: date


@dataclass(frozen=True)
class FundInput:
    symbol: str
    name: str
    units: Decimal
    lots: list[Lot]
    nav: Decimal | None
    nav_date: date | None
    cashflows: list[tuple[date, Decimal]]


def _pct_str(fraction: Decimal | None) -> str | None:
    """Decimal fraction (e.g. 0.184) -> human percent string '18.40', or None."""
    if fraction is None:
        return None
    return str((fraction * Decimal(100)).quantize(_PCT_Q, rounding=ROUND_HALF_UP))


def _cost_basis(lots: list[Lot]) -> Decimal:
    return sum((lot.units * lot.cost for lot in lots), Decimal(0))


def _abs_return(unrealized_gain: Decimal, cost_basis: Decimal) -> str | None:
    if cost_basis == 0:
        return None
    pct = unrealized_gain / cost_basis * Decimal(100)
    return str(pct.quantize(_PCT_Q, rounding=ROUND_HALF_UP))


def value_portfolio(
    funds: list[FundInput],
    today: date,
    exited_cashflows: list[tuple[date, Decimal]] | None = None,
) -> dict:
    holdings: list = []  # built as list[tuple[Decimal, dict]], sorted and unwrapped below
    unpriced: list[dict] = []

    total_value = Decimal(0)
    total_cost = Decimal(0)
    portfolio_current_flows: list[tuple[date, Decimal]] = []
    # Lifetime portfolio flows = every PRICED held fund's cashflows (extended
    # inside the priced branch below) PLUS fully-exited funds' history passed
    # via the exited_cashflows param. Unpriced held funds contribute nothing.
    # NOTE: exited funds must NOT be passed in `funds` — they lack a current NAV
    # and would land in `unpriced` (silently dropped from all flows).
    portfolio_lifetime_flows: list[tuple[date, Decimal]] = []
    portfolio_lifetime_flows.extend(exited_cashflows or [])

    for f in funds:
        cost_basis = _cost_basis(f.lots)

        if f.nav is None:
            # Quarantine: cost basis only, excluded from totals.
            unpriced.append(
                {
                    "symbol": f.symbol,
                    "name": f.name,
                    "units": str(f.units),
                    "cost_basis": _money_str(cost_basis),
                }
            )
            continue

        # Only priced funds contribute to portfolio XIRR streams.
        portfolio_lifetime_flows.extend(f.cashflows)

        current_value = f.units * f.nav
        unrealized_gain = current_value - cost_basis

        current_flows = [(lot.lot_date, -(lot.units * lot.cost)) for lot in f.lots] + [
            (today, current_value)
        ]
        lifetime_flows = list(f.cashflows) + [(today, current_value)]

        total_value += current_value
        total_cost += cost_basis
        portfolio_current_flows.extend(
            (lot.lot_date, -(lot.units * lot.cost)) for lot in f.lots
        )

        holdings.append(
            (
                current_value,  # raw Decimal for sort key
                {
                    "symbol": f.symbol,
                    "name": f.name,
                    "units": str(f.units),
                    "nav": str(f.nav),
                    "nav_date": f.nav_date.isoformat() if f.nav_date else None,
                    "current_value": _money_str(current_value),
                    "cost_basis": _money_str(cost_basis),
                    "unrealized_gain": _money_str(unrealized_gain),
                    "abs_return_pct": _abs_return(unrealized_gain, cost_basis),
                    "current_xirr": _pct_str(xirr(current_flows)),
                    "lifetime_xirr": _pct_str(xirr(lifetime_flows)),
                },
            )
        )

    holdings.sort(key=lambda item: item[0], reverse=True)
    holdings = [item[1] for item in holdings]

    portfolio_current_flows.append((today, total_value))
    portfolio_lifetime_flows.append((today, total_value))
    total_gain = total_value - total_cost

    totals = {
        "current_value": _money_str(total_value),
        "cost_basis": _money_str(total_cost),
        "unrealized_gain": _money_str(total_gain),
        "abs_return_pct": _abs_return(total_gain, total_cost),
        "current_xirr": _pct_str(xirr(portfolio_current_flows)),
        "lifetime_xirr": _pct_str(xirr(portfolio_lifetime_flows)),
    }

    return {
        "totals": totals,
        "holdings": holdings,
        "unpriced": unpriced,
        "unpriced_count": len(unpriced),
    }
