"""Terms inference from one EMI plan account's postings. Synthetic amounts only."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.service.reports.health.ledger_view import PostingRow
from backend.app.service.reports.liabilities import amortize
from backend.app.service.reports.liabilities.emi_terms import infer_terms

PLAN = "Liabilities:Loan:CardEMI:Acme1234-20260722"


def _p(d: date, amount: Decimal | str) -> PostingRow:
    return PostingRow(
        date=d,
        account=PLAN,
        amount=Decimal(amount),
        narration="x",
        tags=frozenset(),
    )


def _month(start: date, offset: int) -> date:
    m = start.year * 12 + start.month - 1 + offset
    return date(m // 12, m % 12 + 1, 21)


def _plan(principal: str, rate: str, months: int, paid: int, first: date) -> list:
    """The conversion plus the first `paid` principal instalments of a
    reducing-balance schedule from `amortize.schedule`."""
    rows = [_p(_month(first, -1), -Decimal(principal))]
    sched = amortize.schedule(Decimal(principal), Decimal(rate), months)
    for i, s in enumerate(sched[:paid]):
        rows.append(_p(_month(first, i), s["principal"]))
    return rows


def test_infers_rate_and_tenure_for_running_reducing_balance_plan():
    got = infer_terms(_plan("12000", "15", 12, 3, date(2026, 8, 21)))
    assert got is not None
    assert got["term_months"] == 12
    assert abs(got["annual_rate_pct"] - Decimal(15)) <= Decimal("0.25")
    assert got["paid_instalments"] == 3
    assert got["principal"] == Decimal(12000)
    assert got["no_cost"] is False


def test_infers_from_count_when_fully_repaid():
    got = infer_terms(_plan("9000", "18", 3, 3, date(2026, 2, 21)))
    assert got is not None
    assert got["term_months"] == 3
    assert abs(got["annual_rate_pct"] - Decimal(18)) <= Decimal("0.25")
    assert got["paid_instalments"] == 3


def test_equal_instalments_are_no_cost_zero_rate():
    rows = [
        _p(date(2026, 1, 5), "-10000"),
        _p(date(2026, 2, 5), "2500"),
        _p(date(2026, 3, 5), "2500"),
    ]
    got = infer_terms(rows)
    assert got is not None
    assert got["annual_rate_pct"] == 0
    assert got["term_months"] == 4
    assert got["no_cost"] is True


def test_single_instalment_running_plan_is_not_inferred():
    assert infer_terms(_plan("12000", "15", 12, 1, date(2026, 8, 21))) is None


def test_no_conversion_is_not_inferred():
    rows = [_p(date(2026, 2, 5), "900"), _p(date(2026, 3, 5), "910")]
    assert infer_terms(rows) is None


def test_no_instalment_is_not_inferred():
    assert infer_terms([_p(date(2026, 1, 5), "-12000")]) is None


def test_inconsistent_instalments_are_not_inferred():
    rows = [
        _p(date(2026, 1, 5), "-12000"),
        _p(date(2026, 2, 5), "1000"),
        _p(date(2026, 3, 5), "1300"),
        _p(date(2026, 4, 5), "900"),
    ]
    assert infer_terms(rows) is None


def test_completed_plan_that_does_not_fit_is_not_inferred():
    rows = [
        _p(date(2026, 1, 5), "-3000"),
        _p(date(2026, 2, 5), "1000"),
        _p(date(2026, 3, 5), "1500"),
        _p(date(2026, 4, 5), "500"),
    ]
    assert infer_terms(rows) is None


def test_start_month_is_first_instalment_month():
    rows = list(reversed(_plan("12000", "15", 12, 3, date(2026, 8, 21))))
    got = infer_terms(rows)
    assert got is not None
    assert got["start_month"] == "2026-08"
