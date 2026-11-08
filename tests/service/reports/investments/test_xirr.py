from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.service.reports.investments.xirr import xirr


def test_doubling_in_one_year_is_about_100pct():
    r = xirr([(date(2024, 1, 1), Decimal("-100")), (date(2025, 1, 1), Decimal("200"))])
    assert r is not None
    assert abs(r - Decimal("1.0")) < Decimal("0.01")


def test_flat_no_gain_is_about_zero():
    r = xirr([(date(2024, 1, 1), Decimal("-100")), (date(2025, 1, 1), Decimal("100"))])
    assert r is not None
    assert abs(r) < Decimal("0.0001")


def test_monthly_sip_plus_terminal_is_finite_and_plausible():
    flows: list[tuple[date, Decimal]] = []
    for m in range(12):
        flows.append((date(2024, 1 + m, 1), Decimal("-1000")))
    flows.append((date(2025, 1, 1), Decimal("13000")))  # 12k in, 13k out
    r = xirr(flows)
    assert r is not None
    assert Decimal("0.05") < r < Decimal("0.40")


def test_single_cashflow_is_none():
    assert xirr([(date(2024, 1, 1), Decimal("-100"))]) is None


def test_empty_is_none():
    assert xirr([]) is None


def test_all_negative_no_sign_change_is_none():
    flows = [(date(2024, 1, 1), Decimal("-100")), (date(2025, 1, 1), Decimal("-50"))]
    assert xirr(flows) is None


def test_all_positive_no_sign_change_is_none():
    flows = [(date(2024, 1, 1), Decimal("100")), (date(2025, 1, 1), Decimal("50"))]
    assert xirr(flows) is None


def test_pathological_does_not_raise_returns_none_or_value():
    # Tiny opposite-sign flows far apart should not raise; result is None or finite.
    flows = [
        (date(2000, 1, 1), Decimal("-0.0000001")),
        (date(2050, 1, 1), Decimal("1000000000")),
    ]
    r = xirr(flows)
    assert r is None or r.is_finite()


# --- float solver: same displayed figure as the Decimal solver it replaced ----
# Expected percents were produced by the previous Decimal implementation and
# rendered exactly as the API does (valuation._pct_str, 2 dp half-up).

_D = Decimal


def _pct(fraction: Decimal | None) -> str | None:
    from backend.app.service.reports.networth.valuation import _pct_str

    return _pct_str(fraction)


def test_sip_with_gain_matches_the_decimal_solver():
    flows = [(date(2023, m, 5), _D("-5000")) for m in range(1, 13)]
    flows.append((date(2024, 6, 30), _D("71250.40")))
    assert _pct(xirr(flows)) == "18.05"


def test_sip_with_loss_matches_the_decimal_solver():
    flows = [(date(2022, m, 10), _D("-2500")) for m in range(1, 13)]
    flows.append((date(2023, 3, 31), _D("26100.00")))
    assert _pct(xirr(flows)) == "-16.84"


def test_crore_scale_flows_still_converge():
    # Float NPV cannot reach |NPV| < 1e-7 on flows this large; the step-size
    # stop must still converge to the Decimal solver's figure, not give None.
    flows = [
        (date(2018, 1, 1), _D("-25000000")),
        (date(2020, 7, 15), _D("-12500000")),
        (date(2021, 3, 1), _D("4000000")),
        (date(2025, 10, 31), _D("61234567.89")),
    ]
    assert _pct(xirr(flows)) == "8.61"


def test_short_hold_matches_the_decimal_solver():
    flows = [(date(2026, 10, 1), _D("-100000")), (date(2026, 10, 31), _D("101500"))]
    assert _pct(xirr(flows)) == "19.86"


def test_result_is_a_decimal_at_output_precision():
    from backend.app.service.reports.investments.xirr import OUTPUT_PRECISION

    r = xirr([(date(2025, 1, 1), _D("-100")), (date(2026, 1, 1), _D("112"))])
    assert isinstance(r, Decimal)
    assert r == r.quantize(OUTPUT_PRECISION)
    assert r == _D("0.1200")


def test_a_near_total_loss_still_gets_a_figure():
    # Review M6: the Decimal solver could not converge here (discounted flows
    # near r = -100% reach ~1e26) and reported None; the float solver finds the
    # real root. Pinned so the behaviour is a decision, not an accident.
    flows = [
        (date(2022, 9, 11), _D("-625.25")),
        (date(2023, 6, 10), _D("-2319.20")),
        (date(2023, 9, 8), _D("-1793.19")),
        (date(2027, 8, 11), _D("-405.64")),
        (date(2028, 6, 17), _D("-2640.66")),
        (date(2028, 9, 13), _D("-2894.99")),
        (date(2028, 9, 18), _D("2893.99")),
    ]
    assert _pct(xirr(flows)) == "-99.98"


@pytest.mark.parametrize(
    "flows",
    [
        [(date(2024, 1, 1), _D("-100")), (date(2025, 1, 1), _D("100"))],
        [(date(2023, 3, 1), _D("-1000")), (date(2026, 10, 31), _D("1000"))],
        [
            (date(2025, 1, 1), _D("-500")),
            (date(2025, 6, 1), _D("-500")),
            (date(2025, 12, 31), _D("1000")),
        ],
    ],
)
def test_break_even_renders_as_zero_not_negative_zero(flows):
    assert _pct(xirr(flows)) == "0.00"
