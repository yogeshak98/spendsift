from decimal import Decimal

from backend.app.service.reports.liabilities.amortize import emi, schedule


def test_emi_formula():
    # 10,00,000 @ 12% p.a. over 12 months ≈ 88,849
    value = emi(Decimal("1000000"), Decimal("12"), 12)
    assert abs(value - Decimal("88849")) < Decimal("5")


def test_schedule_reduces_balance_to_zero():
    rows = schedule(Decimal("100000"), Decimal("12"), 12)
    assert len(rows) == 12
    assert rows[0]["interest"] > rows[-1]["interest"]  # interest front-loaded
    assert rows[0]["principal"] < rows[-1]["principal"]  # principal back-loaded
    assert abs(rows[-1]["balance"]) < Decimal("1")  # paid off
