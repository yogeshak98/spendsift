"""Invested-vs-value history -- the series behind the chart on the scheme and
stock detail screens. The honesty rules are the whole point:
never interpolate a price backwards, invested is a step series reported
independently of price coverage, and no stored price history means an empty
value series with a reason, not a fabricated line.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from beancount.core import data
from beancount.core.amount import Amount

from backend.app.persistence.journal import Ledger
from backend.app.persistence.nav_store import NavStore
from backend.app.models.domain.nav import NavRow
from backend.app.service.reports.networth.value_history import value_history


def _open_commodity(ledger: Ledger, symbol: str, account: str) -> None:
    main = ledger.main_file.read_text(encoding="utf-8")
    lines = [
        f'2000-01-01 commodity {symbol}\n  name: "{symbol}"',
        f'2000-01-01 open {account} {symbol} "FIFO"',
    ]
    ledger.main_file.write_text(
        main.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )


def _buy(
    ledger: Ledger, account: str, symbol: str, qty: str, price: str, when: date
) -> None:
    cost = data.Cost(Decimal(price), "INR", when, None)
    txn = data.Transaction(
        data.new_metadata("<test>", 0),
        when,
        "*",
        None,
        f"buy {symbol}",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(account, Amount(Decimal(qty), symbol), cost, None, None, None),
            data.Posting(
                "Assets:Checking:HDFC",
                Amount(-Decimal(qty) * Decimal(price), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )
    ledger.append([txn])


@pytest.fixture
def sparse_nav_ledger(ledger: Ledger) -> Ledger:
    """Bought in 2019; the store's real NAV history only starts in 2024 --
    so a value series must start at the first stored price rather than
    carry it backwards to the purchase date."""
    _open_commodity(ledger, "MF122639", "Assets:Investment:MutualFund:Groww:MF122639")
    _buy(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF122639",
        "MF122639",
        "1000",
        "10.00",
        date(2019, 6, 1),
    )
    store = NavStore(ledger.storage_dir / "prices" / "nav_history.parquet")
    store.append(
        [
            NavRow("122639", Decimal("15.00"), date(2024, 1, 1)),
            NavRow("122639", Decimal("16.25"), date(2024, 6, 1)),
        ]
    )
    return ledger


@pytest.fixture
def two_purchase_ledger(ledger: Ledger) -> Ledger:
    _open_commodity(ledger, "MF122639", "Assets:Investment:MutualFund:Groww:MF122639")
    _buy(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF122639",
        "MF122639",
        "1000",
        "59.00",
        date(2024, 1, 1),
    )
    _buy(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF122639",
        "MF122639",
        "500",
        "60.00",
        date(2024, 3, 1),
    )
    store = NavStore(ledger.storage_dir / "prices" / "nav_history.parquet")
    store.append(
        [
            NavRow("122639", Decimal("59.00"), date(2024, 1, 1)),
            NavRow("122639", Decimal("61.00"), date(2024, 2, 1)),
            NavRow("122639", Decimal("62.00"), date(2024, 3, 1)),
            NavRow("122639", Decimal("64.00"), date(2024, 4, 1)),
        ]
    )
    return ledger


@pytest.fixture
def purchase_between_prices_ledger(ledger: Ledger) -> Ledger:
    """A purchase that falls strictly between two stored price dates -- the
    scenario a merged points-at-price-dates series would render as a
    diagonal ramp on `invested` under a default line chart."""
    _open_commodity(ledger, "MF122639", "Assets:Investment:MutualFund:Groww:MF122639")
    _buy(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF122639",
        "MF122639",
        "1000",
        "59.00",
        date(2024, 1, 1),
    )
    _buy(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF122639",
        "MF122639",
        "500",
        "62.00",
        date(2024, 2, 15),  # strictly between the Feb-1 and Mar-1 price rows
    )
    store = NavStore(ledger.storage_dir / "prices" / "nav_history.parquet")
    store.append(
        [
            NavRow("122639", Decimal("59.00"), date(2024, 1, 1)),
            NavRow("122639", Decimal("61.00"), date(2024, 2, 1)),
            NavRow("122639", Decimal("62.00"), date(2024, 3, 1)),
        ]
    )
    return ledger


@pytest.fixture
def no_history_ledger(ledger: Ledger) -> Ledger:
    _open_commodity(ledger, "SBIN", "Assets:Investment:Stock:SBIN")
    _buy(
        ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "10",
        "590.00",
        date(2024, 1, 15),
    )
    return ledger


@pytest.fixture
def late_and_stale_ledger(ledger: Ledger) -> Ledger:
    """Coverage starts after purchase AND the last stored price is old --
    both honesty signals should fire independently, not one swallowing the
    other."""
    _open_commodity(ledger, "MF122639", "Assets:Investment:MutualFund:Groww:MF122639")
    _buy(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF122639",
        "MF122639",
        "1000",
        "10.00",
        date(2019, 6, 1),
    )
    store = NavStore(ledger.storage_dir / "prices" / "nav_history.parquet")
    store.append([NavRow("122639", Decimal("15.00"), date(2024, 1, 1))])
    return ledger


def test_value_series_starts_where_prices_start_not_at_purchase(sparse_nav_ledger):
    out = value_history(sparse_nav_ledger, "mutual_fund", "MF122639")
    assert out["covers_from"] == "2024-01-01"  # first stored NAV
    assert out["value_points"][0]["date"] == "2024-01-01"
    assert out["coverage_starts_late"] is True
    assert out["truncated_reason"] is not None
    assert "2024-01-01" in out["truncated_reason"]


def test_never_interpolates_a_price_backwards(sparse_nav_ledger):
    dates = [
        p["date"]
        for p in value_history(sparse_nav_ledger, "mutual_fund", "MF122639")[
            "value_points"
        ]
    ]
    assert "2019-06-01" not in dates  # purchase predates price history


def test_value_series_uses_real_units_and_price_at_each_point(sparse_nav_ledger):
    out = value_history(sparse_nav_ledger, "mutual_fund", "MF122639")
    first, second = out["value_points"]
    assert first["value"] == "15000.00"  # 1000 units * 15.00 NAV
    assert second["date"] == "2024-06-01"
    assert second["value"] == "16250.00"  # 1000 * 16.25


def test_invested_is_a_step_series(two_purchase_ledger):
    pts = value_history(two_purchase_ledger, "mutual_fund", "MF122639")[
        "invested_points"
    ]
    invested = [p["invested"] for p in pts]
    assert invested == sorted(invested)  # monotonic, never dips between buys
    by_date = {p["date"]: p["invested"] for p in pts}
    assert by_date["2024-01-01"] == "59000.00"
    assert by_date["2024-03-01"] == "89000.00"  # 59000 + 500*60


def test_invested_points_do_not_ramp_between_stored_prices(
    purchase_between_prices_ledger,
):
    """The purchase on 2024-02-15 lands between the Feb-1 and Mar-1 stored
    prices. invested_points must carry an exact step at 2024-02-15 -- not
    something a merged points-at-price-dates series would produce (a
    diagonal ramp visible only at the Mar-1 sample)."""
    out = value_history(purchase_between_prices_ledger, "mutual_fund", "MF122639")
    invested_by_date = {p["date"]: p["invested"] for p in out["invested_points"]}
    assert invested_by_date["2024-01-01"] == "59000.00"
    assert invested_by_date["2024-02-15"] == "90000.00"  # 59000 + 500*62, exact step
    # value_points is sampled only at real stored price dates -- 2024-02-15
    # never appears there, so no chart series can draw a ramp through it.
    value_dates = {p["date"] for p in out["value_points"]}
    assert "2024-02-15" not in value_dates
    assert value_dates == {"2024-01-01", "2024-02-01", "2024-03-01"}


def test_no_price_history_returns_empty_value_series_with_a_reason(
    no_history_ledger,
):
    out = value_history(no_history_ledger, "stock", "SBIN")
    assert out["value_points"] == []
    assert "no stored price history" in out["truncated_reason"].lower()
    assert out["covers_from"] is None
    assert out["as_of"] is None
    assert out["coverage_starts_late"] is False
    # Invested is known independently of price coverage -- still reported.
    assert out["invested_points"] == [{"date": "2024-01-15", "invested": "5900.00"}]


def test_late_coverage_and_stale_tail_both_surface_independently(
    late_and_stale_ledger,
):
    out = value_history(
        late_and_stale_ledger,
        "mutual_fund",
        "MF122639",
        today=date(2026, 9, 7),  # ~2.6 years after the only stored price
    )
    assert out["coverage_starts_late"] is True
    assert out["source_state"] == "stale"
    assert out["as_of"] == "2024-01-01"
    # Both honesty signals show up in the prose, not just one.
    assert "2024-01-01" in out["truncated_reason"]
    assert "stale" in out["truncated_reason"].lower()


def test_unknown_vehicle_returns_empty_not_a_crash(no_history_ledger):
    out = value_history(no_history_ledger, "crypto", "SBIN")
    assert out["value_points"] == []
    assert out["invested_points"] == []
    assert out["truncated_reason"] is not None


def test_no_transactions_returns_empty_not_a_crash(ledger: Ledger):
    out = value_history(ledger, "stock", "NOPE")
    assert out["value_points"] == []
    assert out["invested_points"] == []
    assert out["truncated_reason"] is not None


# --- vehicle_value_history: the aggregate behind the MF screen's chart -------

from backend.app.service.reports.networth.value_history import (  # noqa: E402
    vehicle_value_history,
)

_PPFC = "Assets:Investment:MutualFund:Groww:MF122639"
_AXIS = "Assets:Investment:MutualFund:Groww:MF120465"


def _two_schemes(ledger: Ledger) -> NavStore:
    _open_commodity(ledger, "MF122639", _PPFC)
    _open_commodity(ledger, "MF120465", _AXIS)
    _buy(ledger, _PPFC, "MF122639", "100", "10.00", date(2024, 1, 1))
    _buy(ledger, _AXIS, "MF120465", "50", "20.00", date(2024, 1, 10))
    return NavStore(ledger.storage_dir / "prices" / "nav_history.parquet")


def test_aggregate_sums_units_times_nav_across_schemes(ledger: Ledger):
    store = _two_schemes(ledger)
    store.append(
        [
            NavRow("122639", Decimal("11.00"), date(2024, 1, 1)),
            NavRow("122639", Decimal("12.00"), date(2024, 1, 10)),
            NavRow("120465", Decimal("21.00"), date(2024, 1, 10)),
        ]
    )
    out = vehicle_value_history(ledger, "mutual_fund", today=date(2024, 1, 12))
    assert out["invested_points"] == [
        {"date": "2024-01-01", "invested": "1000.00"},
        {"date": "2024-01-10", "invested": "2000.00"},
    ]
    # Jan 1: only PPFC held -> 100 x 11. Jan 10: 100 x 12 + 50 x 21.
    assert out["value_points"] == [
        {"date": "2024-01-01", "value": "1100.00"},
        {"date": "2024-01-10", "value": "2250.00"},
    ]
    assert out["truncated_reason"] is None
    assert out["covers_from"] == "2024-01-01"


def test_aggregate_skips_a_date_rather_than_summing_without_a_held_scheme(
    ledger: Ledger,
):
    store = _two_schemes(ledger)
    # AXIS is held from Jan 10 but has no price within 7 days of Jan 25 --
    # summing PPFC alone that day would draw a fake dip.
    store.append(
        [
            NavRow("122639", Decimal("11.00"), date(2024, 1, 1)),
            NavRow("122639", Decimal("12.00"), date(2024, 1, 10)),
            NavRow("120465", Decimal("21.00"), date(2024, 1, 10)),
            NavRow("122639", Decimal("13.00"), date(2024, 1, 25)),
        ]
    )
    out = vehicle_value_history(ledger, "mutual_fund", today=date(2024, 1, 26))
    assert [p["date"] for p in out["value_points"]] == ["2024-01-01", "2024-01-10"]
    assert "1 date(s) skipped" in out["truncated_reason"]


def test_aggregate_carries_last_nav_over_a_weekend_gap(ledger: Ledger):
    store = _two_schemes(ledger)
    store.append(
        [
            NavRow("122639", Decimal("11.00"), date(2024, 1, 1)),
            NavRow("120465", Decimal("21.00"), date(2024, 1, 10)),
            # PPFC's last NAV (Jan 8) is 2 days old on Jan 10 -- a prior close.
            NavRow("122639", Decimal("12.00"), date(2024, 1, 8)),
        ]
    )
    out = vehicle_value_history(ledger, "mutual_fund", today=date(2024, 1, 11))
    assert out["value_points"][-1] == {"date": "2024-01-10", "value": "2250.00"}


def test_aggregate_excludes_an_unpriced_scheme_from_both_lines_and_names_it(
    ledger: Ledger,
):
    store = _two_schemes(ledger)
    store.append([NavRow("122639", Decimal("11.00"), date(2024, 1, 1))])
    out = vehicle_value_history(ledger, "mutual_fund", today=date(2024, 1, 2))
    assert out["invested_points"] == [{"date": "2024-01-01", "invested": "1000.00"}]
    assert out["value_points"] == [{"date": "2024-01-01", "value": "1100.00"}]
    assert "MF120465" in out["truncated_reason"]


def test_aggregate_ignores_the_cash_leg_under_the_vehicle_prefix(ledger: Ledger):
    _open_commodity(ledger, "MF122639", _PPFC)
    main = ledger.main_file.read_text(encoding="utf-8")
    ledger.main_file.write_text(
        main + "2000-01-01 open Assets:Investment:MutualFund:Groww:Cash INR\n",
        encoding="utf-8",
    )
    cost = data.Cost(Decimal("10.00"), "INR", date(2024, 1, 1), None)
    ledger.append(
        [
            data.Transaction(
                data.new_metadata("<test>", 0),
                date(2024, 1, 1),
                "*",
                None,
                "buy",
                data.EMPTY_SET,
                data.EMPTY_SET,
                [
                    data.Posting(
                        _PPFC,
                        Amount(Decimal("100"), "MF122639"),
                        cost,
                        None,
                        None,
                        None,
                    ),
                    data.Posting(
                        "Assets:Investment:MutualFund:Groww:Cash",
                        Amount(Decimal("-1000"), "INR"),
                        None,
                        None,
                        None,
                        None,
                    ),
                ],
            )
        ]
    )
    NavStore(ledger.storage_dir / "prices" / "nav_history.parquet").append(
        [NavRow("122639", Decimal("11.00"), date(2024, 1, 1))]
    )
    out = vehicle_value_history(ledger, "mutual_fund", today=date(2024, 1, 2))
    assert out["truncated_reason"] is None
    assert out["value_points"] == [{"date": "2024-01-01", "value": "1100.00"}]


def test_aggregate_unknown_vehicle_is_an_honest_empty(ledger: Ledger):
    out = vehicle_value_history(ledger, "real_estate", today=date(2024, 1, 2))
    assert out["value_points"] == [] and out["invested_points"] == []
    assert "unknown vehicle" in out["truncated_reason"]
