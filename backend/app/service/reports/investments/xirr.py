"""Pure money-weighted return (XIRR) solver. No IO, no framework imports.

Solves sum(amount / (1+r)**years) = 0 for r, where years = (d - earliest)/365.
Newton-Raphson from r=0.1; falls back to bisection on a bracketed sign change
in [-0.9999, 10.0]. Returns None (NOT 0.0) when undefined: < 2 cashflows, no
sign change among amounts, or no convergence.

The iteration runs in float: XIRR is a display figure, and Decimal Newton
steps made it the slowest part of the investments screens. The root is handed
back as a Decimal rounded (half-up) to OUTPUT_PRECISION -- 4 places of the
fraction, i.e. exactly the 2-place percent the API renders -- so callers see
the same figure the Decimal solver produced.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

OUTPUT_PRECISION = Decimal("0.0001")  # fraction; 0.1234 renders as "12.34"

_MAX_ITERS = 100
_TOL = 1e-7  # |NPV| tolerance
# Float NPV over large rupee flows cannot always get |NPV| under _TOL; a Newton
# step this small (relative to 1+r) is converged far below OUTPUT_PRECISION.
# Bisection likewise stops once the bracket is narrower than _STEP_TOL. Unlike
# the old Decimal solver, that also finds a root where the discounted flows are
# too large for 28 digits to reach |NPV| < _TOL (a near-total loss, rate ~ -100%).
_STEP_TOL = 1e-12
_LOW = -0.9999
_HIGH = 10.0
_BISECT_ITERS = 200

_Terms = list[tuple[float, float]]


def _years(flows: list[tuple[date, Decimal]]) -> _Terms:
    earliest = min(d for d, _ in flows)
    return [((d - earliest).days / 365.0, float(amt)) for d, amt in flows]


def _npv(rate: float, terms: _Terms) -> float:
    one_plus = 1.0 + rate
    return math.fsum(amt / (one_plus**years) for years, amt in terms)


def _npv_derivative(rate: float, terms: _Terms) -> float:
    one_plus = 1.0 + rate
    return math.fsum(
        -years * amt / (one_plus ** (years + 1.0)) for years, amt in terms if years
    )


def _has_sign_change(flows: list[tuple[date, Decimal]]) -> bool:
    signs = {(amt > 0) for _, amt in flows if amt != 0}
    return len(signs) == 2


def _out(rate: float) -> Decimal:
    out = Decimal(repr(rate)).quantize(OUTPUT_PRECISION, rounding=ROUND_HALF_UP)
    # A break-even root a hair below zero must not render as "-0.00".
    return out.copy_abs() if out.is_zero() else out


def xirr(flows: list[tuple[date, Decimal]]) -> Decimal | None:
    if len(flows) < 2:
        return None
    if not _has_sign_change(flows):
        return None

    terms = _years(flows)

    # --- Newton-Raphson ---
    rate = 0.1
    try:
        for _ in range(_MAX_ITERS):
            value = _npv(rate, terms)
            if not math.isfinite(value):
                break
            if abs(value) < _TOL:
                return _out(rate)
            deriv = _npv_derivative(rate, terms)
            if deriv == 0 or not math.isfinite(deriv):
                break
            step = value / deriv
            new_rate = rate - step
            if not math.isfinite(new_rate):
                break
            if new_rate <= _LOW:  # keep base (1+r) positive for fractional pow
                break
            if new_rate >= _HIGH:  # symmetric guard: upward divergence -> bisection
                break
            rate = new_rate
            if abs(step) < _STEP_TOL * (1.0 + abs(rate)):
                return _out(rate)
    except (ArithmeticError, ValueError):
        pass  # fall through to bisection

    # --- Bisection fallback on a bracketed sign change in [_LOW, _HIGH] ---
    try:
        lo, hi = _LOW, _HIGH
        f_lo = _npv(lo, terms)
        f_hi = _npv(hi, terms)
    except (ArithmeticError, ValueError):
        return None
    if not (math.isfinite(f_lo) and math.isfinite(f_hi)):
        return None
    if f_lo == 0:
        return _out(lo)
    if f_hi == 0:
        return _out(hi)
    if (f_lo > 0) == (f_hi > 0):
        return None  # no bracketed root in range

    for _ in range(_BISECT_ITERS):
        mid = (lo + hi) / 2.0
        try:
            f_mid = _npv(mid, terms)
        except (ArithmeticError, ValueError):
            return None
        if not math.isfinite(f_mid):
            return None
        if abs(f_mid) < _TOL or hi - lo < _STEP_TOL:
            return _out(mid)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi, f_hi = mid, f_mid
    return None
