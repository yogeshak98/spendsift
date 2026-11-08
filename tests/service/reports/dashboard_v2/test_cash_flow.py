"""Dashboard cash-flow summary: month to date, FY so far, last 12 months.

Report date 2026-10-15. Every month Oct 2025 .. Oct 2026: salary 100,000 on
the 1st, rent 30,000 on the 3rd, a 20,000 SIP on the 10th; October 2026's SIP
is 150,000 instead, and pays 100 stamp duty (investment cost, not spending).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.service.reports.dashboard_v2.cash_flow import cash_flow_summary
from backend.app.service.reports.health.ledger_view import PostingRow

REPORT = date(2026, 10, 15)
CHK = "Assets:Checking:Acme"
MF = "Assets:Investment:MutualFund:X"


def _pair(d: date, debit: str, credit: str, amount: str) -> list[PostingRow]:
    amt = Decimal(amount)
    return [
        PostingRow(d, debit, amt, "x", frozenset()),
        PostingRow(d, credit, -amt, "x", frozenset()),
    ]


def _rows() -> list[PostingRow]:
    rows: list[PostingRow] = []
    for idx in range(2025 * 12 + 9, 2026 * 12 + 10):  # Oct 2025 .. Oct 2026
        y, m = idx // 12, idx % 12 + 1
        rows += _pair(date(y, m, 1), CHK, "Income:Salary", "100000")
        rows += _pair(date(y, m, 3), "Expenses:Rent", CHK, "30000")
        sip = "150000" if (y, m) == (2026, 10) else "20000"
        rows += _pair(date(y, m, 10), MF, CHK, sip)
    rows += _pair(date(2026, 10, 10), "Expenses:Investment:Charges", CHK, "100")
    # After the report date: never counted.
    rows += _pair(date(2026, 10, 20), "Expenses:Rent", CHK, "7777")
    return rows


def _by_key() -> dict[str, dict]:
    return {p["key"]: p for p in cash_flow_summary(_rows(), REPORT)}


def test_three_periods_with_their_bounds():
    periods = cash_flow_summary(_rows(), REPORT)
    assert [(p["key"], p["start"], p["end"]) for p in periods] == [
        ("month", "2026-10-01", "2026-10-15"),
        ("fy", "2026-04-01", "2026-10-15"),
        ("last_12_months", "2025-11-01", "2026-10-15"),
    ]


def test_month_invested_more_than_earned_draws_on_savings():
    m = _by_key()["month"]
    assert (m["income"], m["spent"], m["invested"]) == (
        "100000.00",
        "30000.00",
        "150100.00",  # the stamp duty is investing, not spending
    )
    assert m["net_cash_flow"] == "-80100.00"
    assert m["savings_rate_pct"] == "70.0"
    went = m["where_income_went"]
    assert (went["kept"], went["from_savings"]) == ("0.00", "80100.00")
    assert (went["spent_pct"], went["invested_pct"], went["kept_pct"]) == (
        "30.0",
        "150.1",
        "0.0",
    )
    assert went["from_savings_pct"] == "80.1"


def test_fy_so_far():
    fy = _by_key()["fy"]
    # Apr..Oct 2026: 7 salaries and rents, six 20,000 SIPs and October's.
    assert (fy["income"], fy["spent"], fy["invested"]) == (
        "700000.00",
        "210000.00",
        "270100.00",
    )
    assert fy["net_cash_flow"] == "219900.00"
    assert fy["where_income_went"]["kept"] == "219900.00"
    assert fy["where_income_went"]["from_savings"] == "0.00"


def test_last_12_months_starts_eleven_months_before_the_report_month():
    y = _by_key()["last_12_months"]
    # Nov 2025 .. Oct 2026: October 2025 is outside.
    assert (y["income"], y["spent"], y["invested"]) == (
        "1200000.00",
        "360000.00",
        "370100.00",
    )
    assert y["net_cash_flow"] == "469900.00"


def test_no_income_has_no_rate_or_shares():
    rows = _pair(date(2026, 10, 3), "Expenses:Rent", CHK, "30000")
    m = cash_flow_summary(rows, REPORT)[0]
    assert m["savings_rate_pct"] is None
    assert m["where_income_went"]["spent_pct"] is None
    assert m["net_cash_flow"] == "-30000.00"
