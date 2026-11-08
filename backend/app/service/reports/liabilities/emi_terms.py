"""Recover a card EMI plan's terms from its own postings.

A card EMI plan is booked as one conversion (the plan account is credited the
financed amount, a negative posting) followed by monthly principal
instalments (positive postings). On a reducing-balance plan the principal
part of a level EMI grows by the monthly rate each month, so the paid
instalments pin down the rate and, with the principal, the tenure.

Inference is deliberately conservative: a wrong rate or tenure is worse than
none, so anything that does not fit tightly and uniquely returns None.

Float exemption from the money-as-Decimal rule: the rate solve and the fit
check below use float maths on purpose. The rate and tenure they produce are
inferred display values (rounded to 2 decimals, labelled "inferred"); no
money is stored or posted from them. Every amount that is reported -- the
principal, the paid total, the no-cost check -- stays Decimal.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypedDict

from backend.app.service.reports.health.ledger_view import PostingRow

# Every paid instalment must sit within this fraction of the fitted curve.
_FIT_TOLERANCE = 0.005
# The bisection bracket for the monthly rate: 0% to 10% a month.
_MAX_MONTHLY_RATE = 0.1
# The longest tenure a running plan is tried against.
_MAX_TERM_MONTHS = 120
# Equal instalments within this many rupees read as a no-cost plan.
_NO_COST_RUPEES = Decimal(1)


class InferredTerms(TypedDict):
    principal: Decimal
    annual_rate_pct: Decimal  # nominal, 2 decimals
    term_months: int
    paid_instalments: int
    start_month: str  # YYYY-MM of the first instalment
    no_cost: bool


def _annuity_sum(first: float, n: int, r: float) -> float:
    """Sum of `n` principal parts that start at `first` and grow by `r`."""
    if r == 0:
        return first * n
    return first * ((1 + r) ** n - 1) / r


def _solve_rate(first: float, n: int, principal: float) -> float | None:
    """The monthly rate at which `n` growing principal parts starting at
    `first` repay `principal`, by bisection on [0, 10%]. None when no rate in
    that bracket does: the parts already overshoot at 0% (beyond a rupee of
    rounding per instalment), or fall short even at 10%."""
    at_zero = _annuity_sum(first, n, 0.0)
    if at_zero >= principal:
        return 0.0 if at_zero - principal <= n else None
    if _annuity_sum(first, n, _MAX_MONTHLY_RATE) < principal:
        return None
    lo, hi = 0.0, _MAX_MONTHLY_RATE
    for _ in range(200):
        mid = (lo + hi) / 2
        if _annuity_sum(first, n, mid) < principal:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _misfit(paid: list[float], r: float) -> float:
    """Worst relative distance of a paid instalment from the fitted curve."""
    first = paid[0]
    return max(abs(p - first * (1 + r) ** j) / p for j, p in enumerate(paid))


def _annual_pct(r: float) -> Decimal:
    return Decimal(repr(12 * r * 100)).quantize(Decimal("0.01"))


def infer_terms(postings: list[PostingRow]) -> InferredTerms | None:
    """Principal, nominal rate and tenure of ONE plan account, or None.

    `principal` is the summed magnitude of the negative postings (the
    conversions); the positive postings, in date order, are the instalments.
    A completed plan (net 0) has as many months as instalments; a running one
    (net < 0) needs 2+ instalments and exactly one tenure in k+1..120 whose
    curve fits every instalment within 0.5%. Equal instalments (within a
    rupee) are a no-cost plan at 0%.
    """
    ordered = sorted(postings, key=lambda p: p.date)
    principal = sum((-p.amount for p in ordered if p.amount < 0), Decimal(0))
    instalments = [p for p in ordered if p.amount > 0]
    if principal == 0 or not instalments:
        return None
    net = sum((p.amount for p in ordered), Decimal(0))
    if net > 0:
        return None
    completed = net == 0
    k = len(instalments)
    if not completed and k < 2:
        return None

    amounts = [p.amount for p in instalments]
    first = instalments[0].date
    start_month = f"{first.year:04d}-{first.month:02d}"

    def result(term: int, annual: Decimal, no_cost: bool) -> InferredTerms:
        return InferredTerms(
            principal=principal,
            annual_rate_pct=annual,
            term_months=term,
            paid_instalments=k,
            start_month=start_month,
            no_cost=no_cost,
        )

    if all(abs(a - amounts[0]) <= _NO_COST_RUPEES for a in amounts):
        term = int((principal / amounts[0]).to_integral_value())
        fits = abs(principal - term * amounts[0]) <= term * _NO_COST_RUPEES
        if fits and (term == k if completed else term > k):
            return result(term, Decimal("0.00"), True)
        if completed:
            return None

    paid = [float(a) for a in amounts]
    total = float(principal)

    def fit(n: int) -> tuple[float, float] | None:
        r = _solve_rate(paid[0], n, total)
        return None if r is None else (_misfit(paid, r), r)

    if completed:
        got = fit(k)
        if got is None or got[0] > _FIT_TOLERANCE:
            return None
        return result(k, _annual_pct(got[1]), False)

    scored = sorted(
        (got[0], n, got[1])
        for n in range(k + 1, _MAX_TERM_MONTHS + 1)
        if (got := fit(n)) is not None
    )
    if not scored or scored[0][0] > _FIT_TOLERANCE:
        return None
    if len(scored) > 1 and scored[1][0] <= _FIT_TOLERANCE:
        return None  # two tenures fit: ambiguous, so none
    _, term, r = scored[0]
    return result(term, _annual_pct(r), False)
