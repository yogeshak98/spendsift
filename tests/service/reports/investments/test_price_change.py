from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from backend.app.persistence.nav_store import NavStore
from backend.app.models.domain.nav import NavRow
from backend.app.service.reports.investments.price_change import (
    ChangeWindow,
    fund_change_windows,
    portfolio_windows,
    window_value_change,
)


def _store_with(rows_by_code: dict[str, list[tuple[date, str]]]) -> NavStore:
    td = Path(tempfile.mkdtemp())
    store = NavStore(td / "nav.parquet")
    all_rows = [
        NavRow(scheme_code=code, nav=Decimal(nav), date=dt)
        for code, entries in rows_by_code.items()
        for dt, nav in entries
    ]
    store.append(all_rows)
    return store


def test_fund_change_windows_computes_1m_return():
    store = _store_with(
        {"100": [(date(2025, 6, 1), "10.00"), (date(2026, 6, 30), "11.00")]}
    )
    nav_now = NavRow("100", Decimal("11.00"), date(2026, 6, 30))
    windows = fund_change_windows("100", nav_now, store, date(2026, 6, 30))
    month_w = next(w for w in windows if w.label == "1M")
    assert month_w.nav_then == Decimal("10.00")
    assert month_w.return_pct is not None
    # (11/10 - 1)*100 = 10.00
    assert abs(Decimal(month_w.return_pct) - Decimal("10.00")) < Decimal("0.01")


def test_fund_change_windows_null_return_when_no_prior_data():
    store = _store_with({"100": [(date(2026, 6, 30), "11.00")]})
    nav_now = NavRow("100", Decimal("11.00"), date(2026, 6, 30))
    windows = fund_change_windows("100", nav_now, store, date(2026, 6, 30))
    year_w = next(w for w in windows if w.label == "1Y")
    assert year_w.return_pct is None
    assert year_w.nav_then is None


def test_fund_change_windows_includes_all_four_labels():
    store = _store_with(
        {
            "100": [
                (date(2022, 3, 15), "5.00"),
                (date(2025, 6, 1), "8.00"),
                (date(2026, 6, 30), "11.00"),
            ]
        }
    )
    nav_now = NavRow("100", Decimal("11.00"), date(2026, 6, 30))
    windows = fund_change_windows("100", nav_now, store, date(2026, 6, 30))
    labels = [w.label for w in windows]
    assert labels == ["1D", "1M", "1Y", "SI"]


def test_fund_change_windows_si_uses_earliest_stored_date():
    store = _store_with(
        {
            "100": [
                (date(2022, 3, 15), "5.00"),
                (date(2026, 6, 30), "11.00"),
            ]
        }
    )
    nav_now = NavRow("100", Decimal("11.00"), date(2026, 6, 30))
    windows = fund_change_windows("100", nav_now, store, date(2026, 6, 30))
    si = next(w for w in windows if w.label == "SI")
    assert si.date_then == date(2022, 3, 15)
    assert si.nav_then == Decimal("5.00")


def test_fund_change_windows_1d_returns_null_without_prior_day():
    store = _store_with({"100": [(date(2026, 6, 30), "11.00")]})
    nav_now = NavRow("100", Decimal("11.00"), date(2026, 6, 30))
    windows = fund_change_windows("100", nav_now, store, date(2026, 6, 30))
    day_w = next(w for w in windows if w.label == "1D")
    assert day_w.return_pct is None


def test_fund_change_windows_1d_anchors_to_nav_date_not_calendar():
    # Latest NAV is yesterday's (today's not published yet). 1D must compare it
    # with the NAV before it, not with itself (which reported 0.00%).
    store = _store_with(
        {"100": [(date(2026, 10, 13), "89.5841"), (date(2026, 10, 14), "89.3598")]}
    )
    nav_now = NavRow("100", Decimal("89.3598"), date(2026, 10, 14))
    windows = fund_change_windows("100", nav_now, store, date(2026, 10, 15))
    day_w = next(w for w in windows if w.label == "1D")
    assert day_w.date_then == date(2026, 10, 13)
    assert day_w.return_pct == "-0.25"


def test_fund_change_windows_1d_spans_a_weekend():
    store = _store_with(
        {"100": [(date(2026, 10, 9), "10.00"), (date(2026, 10, 12), "10.10")]}
    )
    nav_now = NavRow("100", Decimal("10.10"), date(2026, 10, 12))  # Fri -> Mon
    windows = fund_change_windows("100", nav_now, store, date(2026, 10, 12))
    day_w = next(w for w in windows if w.label == "1D")
    assert day_w.date_then == date(2026, 10, 9)
    assert day_w.return_pct == "1.00"


def test_fund_change_windows_1d_null_across_a_history_gap():
    # 9 Oct -> 15 Oct is a hole in the stored history, not one trading day:
    # reporting it as 1D showed a 4-day move (+2.64%) as a single day's.
    store = _store_with(
        {"100": [(date(2026, 10, 9), "30.4347"), (date(2026, 10, 15), "31.2387")]}
    )
    nav_now = NavRow("100", Decimal("31.2387"), date(2026, 10, 15))
    windows = fund_change_windows("100", nav_now, store, date(2026, 10, 15))
    day_w = next(w for w in windows if w.label == "1D")
    assert day_w.return_pct is None
    assert day_w.nav_then is None
    assert window_value_change(Decimal("100"), day_w) is None


def test_portfolio_windows_weighted_average():
    w1 = [ChangeWindow("1M", date(2026, 5, 30), Decimal("10"), Decimal("11"), "10.00")]
    w2 = [ChangeWindow("1M", date(2026, 5, 30), Decimal("20"), Decimal("22"), "10.00")]
    result = portfolio_windows(
        [("f1", w1), ("f2", w2)],
        {"f1": Decimal("1000"), "f2": Decimal("1000")},
    )
    m = next(w for w in result if w.label == "1M")
    assert m.return_pct is not None
    assert abs(Decimal(m.return_pct) - Decimal("10.00")) < Decimal("0.01")


def test_portfolio_windows_unequal_weights():
    # f1 has 3x the value of f2; combined return ≈ (3*5 + 1*20)/4 = 8.75%
    w1 = [
        ChangeWindow("1M", date(2026, 5, 30), Decimal("10"), Decimal("10.50"), "5.00")
    ]
    w2 = [
        ChangeWindow("1M", date(2026, 5, 30), Decimal("10"), Decimal("12.00"), "20.00")
    ]
    result = portfolio_windows(
        [("f1", w1), ("f2", w2)],
        {"f1": Decimal("3000"), "f2": Decimal("1000")},
    )
    m = next(w for w in result if w.label == "1M")
    assert m.return_pct is not None
    assert abs(Decimal(m.return_pct) - Decimal("8.75")) < Decimal("0.01")


def test_portfolio_windows_null_when_no_funds_have_data():
    w1 = [ChangeWindow("1Y", None, None, Decimal("11"), None)]
    result = portfolio_windows([("f1", w1)], {"f1": Decimal("1000")})
    y = next(w for w in result if w.label == "1Y")
    assert y.return_pct is None


# --- window_value_change ----------------------------------------------------
#
# The rupee figure beside each percent on the holdings table. It lives here and
# not in the frontend because units and NAVs are Decimal strings, and doing
# `units * (nav_now - nav_then)` in JavaScript floats them before display.


def test_value_change_prices_a_rise_against_units_held():
    w = ChangeWindow(
        "1D", date(2026, 9, 8), Decimal("100.0000"), Decimal("101.5000"), "1.50"
    )
    assert window_value_change(Decimal("250.500"), w) == "375.75"


def test_value_change_is_negative_when_the_nav_fell():
    w = ChangeWindow(
        "1D", date(2026, 9, 8), Decimal("102.0000"), Decimal("100.0000"), "-1.96"
    )
    assert window_value_change(Decimal("10"), w) == "-20.00"


def test_value_change_is_none_without_a_baseline_never_zero():
    # A window with no nav_then has no baseline to measure from. Zero would be
    # a claim that the price did not move, which is a different statement and
    # would render as a confident "0.00" beside a blank percent.
    w = ChangeWindow("1Y", None, None, Decimal("11"), None)
    assert window_value_change(Decimal("100"), w) is None


def test_value_change_quantizes_to_paise_half_up():
    # 3 units x 0.3333 = 0.9999 -> 1.00. Rounding happens once, at the end, on
    # the Decimal -- the exact product is never truncated mid-computation.
    w = ChangeWindow(
        "1D", date(2026, 9, 8), Decimal("10.0000"), Decimal("10.3333"), "3.33"
    )
    assert window_value_change(Decimal("3"), w) == "1.00"


def test_value_change_keeps_full_precision_of_fractional_units():
    # MF unit counts carry 3-4 decimals. 1234.5678 units x a 0.4321 NAV move is
    # 533.45674638 -- exact in Decimal, and 533.46 after the single rounding.
    w = ChangeWindow(
        "1D", date(2026, 9, 8), Decimal("50.0000"), Decimal("50.4321"), "0.86"
    )
    assert window_value_change(Decimal("1234.5678"), w) == "533.46"
