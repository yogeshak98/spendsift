from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.service.reports.cashflow.period import (
    DateRange,
    day_label,
    days_in,
    fy_range,
    granularity,
    in_range,
    month_label,
    months_in,
    optional_range,
    range_or_fy,
    spans_years,
    validate_range,
)
from backend.app.service.reports.health.ledger_view import PostingRow


def _e1087(fn, *args):
    with pytest.raises(CodedError) as exc:
        fn(*args)
    assert exc.value.code == "E1087"
    assert exc.value.status == 422
    return exc.value


def test_validate_range_accepts_ordered_and_single_day():
    r = validate_range(date(2026, 8, 1), date(2026, 8, 31))
    assert r == DateRange(date(2026, 8, 1), date(2026, 8, 31))
    assert validate_range(date(2026, 8, 1), date(2026, 8, 1)).end == date(2026, 8, 1)


def test_validate_range_rejects_from_after_to():
    err = _e1087(validate_range, date(2026, 8, 31), date(2026, 8, 1))
    assert "2026-08-31" in err.description


def test_validate_range_rejects_half_range():
    _e1087(validate_range, date(2026, 8, 1), None)
    _e1087(validate_range, None, date(2026, 8, 1))
    _e1087(validate_range, None, None)


def test_optional_range():
    assert optional_range(None, None) is None
    assert optional_range(date(2026, 8, 1), date(2026, 8, 2)) == DateRange(
        date(2026, 8, 1), date(2026, 8, 2)
    )
    _e1087(optional_range, date(2026, 8, 1), None)


def test_fy_range():
    assert fy_range(2026) == DateRange(date(2026, 4, 1), date(2027, 3, 31))


def test_range_or_fy_exactly_one_form():
    assert range_or_fy(2026, None, None) == fy_range(2026)
    assert range_or_fy(None, date(2026, 8, 1), date(2026, 8, 31)) == DateRange(
        date(2026, 8, 1), date(2026, 8, 31)
    )
    _e1087(range_or_fy, None, None, None)
    _e1087(range_or_fy, 2026, date(2026, 8, 1), date(2026, 8, 31))
    _e1087(range_or_fy, 2026, date(2026, 8, 1), None)
    _e1087(range_or_fy, None, None, date(2026, 8, 31))
    _e1087(range_or_fy, None, date(2026, 8, 31), date(2026, 8, 1))


def test_granularity_boundary():
    start = date(2026, 1, 1)
    assert granularity(DateRange(start, start + timedelta(days=91))) == "day"  # 92
    assert granularity(DateRange(start, start + timedelta(days=92))) == "month"  # 93
    assert granularity(DateRange(start, start)) == "day"


def test_days_in():
    r = DateRange(date(2026, 2, 27), date(2026, 3, 2))
    assert days_in(r) == [
        date(2026, 2, 27),
        date(2026, 2, 28),
        date(2026, 3, 1),
        date(2026, 3, 2),
    ]


def test_months_in_spans_fy_boundary():
    r = DateRange(date(2026, 2, 15), date(2026, 5, 10))
    assert months_in(r) == [(2026, 2), (2026, 3), (2026, 4), (2026, 5)]
    assert months_in(DateRange(date(2025, 12, 31), date(2026, 1, 1))) == [
        (2025, 12),
        (2026, 1),
    ]


def test_month_labels_gain_year_across_calendar_years():
    assert spans_years([(2025, 12), (2026, 1)]) is True
    assert spans_years([(2026, 1), (2026, 12)]) is False
    assert spans_years([]) is False
    assert month_label(2025, 12, True) == "Dec 25"
    assert month_label(2026, 8, False) == "Aug"
    assert day_label(date(2026, 8, 3)) == "3 Aug"


def test_in_range_is_inclusive():
    def row(d):
        return PostingRow(d, "Expenses:Food", Decimal(1), "x", frozenset())

    rows = [row(date(2026, 7, 31)), row(date(2026, 8, 1)), row(date(2026, 8, 31))]
    rows.append(row(date(2026, 9, 1)))
    got = in_range(rows, DateRange(date(2026, 8, 1), date(2026, 8, 31)))
    assert [r.date for r in got] == [date(2026, 8, 1), date(2026, 8, 31)]


def test_validate_range_bounds_the_years():
    _e1087(validate_range, date(1, 1, 1), date(1, 3, 1))
    _e1087(validate_range, date(2026, 1, 1), date(9999, 12, 31))
    _e1087(range_or_fy, 99999, None, None)
    _e1087(range_or_fy, 0, None, None)
    assert validate_range(date(1900, 1, 1), date(2200, 12, 31)).start.year == 1900
