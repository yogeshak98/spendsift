from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.service.reports.networth.valuation import (
    FundInput,
    Lot,
    value_portfolio,
)

TODAY = date(2026, 6, 29)


def _priced_fund() -> FundInput:
    # 100 units, one lot @100 (cost 10000), nav 130 -> value 13000, gain 3000, +30%.
    return FundInput(
        symbol="MF111",
        name="Priced Fund",
        units=Decimal("100"),
        lots=[Lot(Decimal("100"), Decimal("100"), date(2024, 6, 29))],
        nav=Decimal("130"),
        nav_date=date(2026, 6, 29),
        cashflows=[(date(2024, 6, 29), Decimal("-10000"))],
    )


def test_priced_fund_arithmetic():
    out = value_portfolio([_priced_fund()], TODAY)
    h = out["holdings"][0]
    assert h["symbol"] == "MF111"
    assert h["current_value"] == "13000.00"
    assert h["cost_basis"] == "10000.00"
    assert h["unrealized_gain"] == "3000.00"
    assert h["abs_return_pct"] == "30.00"
    assert h["nav"] == "130"
    assert h["nav_date"] == "2026-06-29"
    assert out["totals"]["current_value"] == "13000.00"
    assert out["totals"]["cost_basis"] == "10000.00"
    assert out["totals"]["unrealized_gain"] == "3000.00"
    assert out["totals"]["abs_return_pct"] == "30.00"
    assert out["unpriced"] == []
    assert out["unpriced_count"] == 0


def test_unpriced_fund_excluded_from_totals_and_listed():
    unpriced = FundInput(
        symbol="MF999",
        name="Closed Fund",
        units=Decimal("10"),
        lots=[Lot(Decimal("10"), Decimal("100"), date(2024, 1, 1))],
        nav=None,
        nav_date=None,
        cashflows=[(date(2024, 1, 1), Decimal("-1000"))],
    )
    out = value_portfolio([_priced_fund(), unpriced], TODAY)
    assert [h["symbol"] for h in out["holdings"]] == ["MF111"]
    assert out["unpriced"] == [
        {
            "symbol": "MF999",
            "name": "Closed Fund",
            "units": "10",
            "cost_basis": "1000.00",
        }
    ]
    assert out["unpriced_count"] == 1
    # totals reflect only the priced fund
    assert out["totals"]["current_value"] == "13000.00"
    assert out["totals"]["cost_basis"] == "10000.00"


def test_cost_basis_zero_gives_none_abs_return():
    free = FundInput(
        symbol="MF000",
        name="Free Units",
        units=Decimal("5"),
        lots=[Lot(Decimal("5"), Decimal("0"), date(2024, 1, 1))],
        nav=Decimal("10"),
        nav_date=date(2026, 6, 29),
        cashflows=[(date(2024, 1, 1), Decimal("0"))],
    )
    out = value_portfolio([free], TODAY)
    assert out["holdings"][0]["abs_return_pct"] is None


def test_partial_redemption_current_xirr_diverges_from_lifetime_xirr():
    # Bought 100 @100 (2023), bought 100 @100 (2024); redeemed 100 @150 in 2025.
    # Surviving: 100 units (the 2024 lot @100). NAV 200 today -> value 20000.
    # Current XIRR uses surviving lot (2024 -10000) + 20000 today.
    # Lifetime XIRR uses full history: -10000, -10000, +15000, +20000 today.
    fund = FundInput(
        symbol="MF222",
        name="Partial",
        units=Decimal("100"),
        lots=[Lot(Decimal("100"), Decimal("100"), date(2024, 6, 29))],
        nav=Decimal("200"),
        nav_date=date(2026, 6, 29),
        cashflows=[
            (date(2023, 6, 29), Decimal("-10000")),
            (date(2024, 6, 29), Decimal("-10000")),
            (date(2025, 6, 29), Decimal("15000")),
        ],
    )
    out = value_portfolio([fund], TODAY)
    h = out["holdings"][0]
    assert h["current_xirr"] is not None
    assert h["lifetime_xirr"] is not None
    assert h["current_xirr"] != h["lifetime_xirr"]
    # portfolio mirrors the single fund's figures
    assert out["totals"]["current_xirr"] == h["current_xirr"]
    assert out["totals"]["lifetime_xirr"] == h["lifetime_xirr"]


def test_unpriced_fund_does_not_contaminate_portfolio_xirr():
    # An unpriced fund must contribute NOTHING to portfolio XIRR — not even its
    # purchase cashflows.  Adding an unpriced fund alongside a priced fund must
    # leave both totals["lifetime_xirr"] and totals["current_xirr"] unchanged.
    unpriced = FundInput(
        symbol="MF999",
        name="Closed Fund",
        units=Decimal("10"),
        lots=[Lot(Decimal("10"), Decimal("100"), date(2024, 1, 1))],
        nav=None,
        nav_date=None,
        cashflows=[(date(2024, 1, 1), Decimal("-1000"))],
    )
    priced_only = value_portfolio([_priced_fund()], TODAY)
    with_unpriced = value_portfolio([_priced_fund(), unpriced], TODAY)

    assert (
        priced_only["totals"]["lifetime_xirr"]
        == with_unpriced["totals"]["lifetime_xirr"]
    )
    assert (
        priced_only["totals"]["current_xirr"] == with_unpriced["totals"]["current_xirr"]
    )


def test_empty_portfolio_zeroed_totals():
    out = value_portfolio([], TODAY)
    assert out["holdings"] == []
    assert out["unpriced"] == []
    assert out["unpriced_count"] == 0
    assert out["totals"]["current_value"] == "0.00"
    assert out["totals"]["cost_basis"] == "0.00"
    assert out["totals"]["unrealized_gain"] == "0.00"
    assert out["totals"]["abs_return_pct"] is None
    assert out["totals"]["current_xirr"] is None
    assert out["totals"]["lifetime_xirr"] is None


def test_exited_cashflows_feed_portfolio_lifetime_xirr_only():
    # No current funds; an exited fund's flows + a terminal value still yield a
    # lifetime XIRR but no holdings and no unpriced rows.
    exited = [
        (date(2023, 6, 29), Decimal("-10000")),
        (date(2025, 6, 29), Decimal("13000")),
    ]
    out = value_portfolio([], TODAY, exited_cashflows=exited)
    assert out["holdings"] == []
    assert out["unpriced"] == []
    assert out["unpriced_count"] == 0
    # totals: no priced value, but lifetime XIRR is defined over exited flows
    # + terminal value 0.
    assert out["totals"]["current_value"] == "0.00"
    assert out["totals"]["lifetime_xirr"] is not None


def test_money_fields_quantized_to_2dp_despite_arithmetic_noise():
    # units * nav with several decimal places each produces extra trailing
    # digits under raw Decimal multiplication; every money field must come
    # out at exactly 2dp regardless.
    fund = FundInput(
        symbol="MF333",
        name="Noisy Fund",
        units=Decimal("123.456"),
        lots=[Lot(Decimal("123.456"), Decimal("83.4821"), date(2024, 1, 1))],
        nav=Decimal("132.4763"),
        nav_date=date(2026, 6, 29),
        cashflows=[(date(2024, 1, 1), Decimal("-10305.87"))],
    )
    out = value_portfolio([fund], TODAY)
    h = out["holdings"][0]
    for field in ("current_value", "cost_basis", "unrealized_gain"):
        value = h[field]
        assert "." in value
        assert len(value.split(".")[1]) == 2, f"{field}={value!r} is not 2dp"
    for field in ("current_value", "cost_basis", "unrealized_gain"):
        value = out["totals"][field]
        assert len(value.split(".")[1]) == 2, f"totals.{field}={value!r} is not 2dp"
