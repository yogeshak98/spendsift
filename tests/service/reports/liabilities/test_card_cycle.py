from __future__ import annotations

from datetime import date

import pytest

from backend.app.service.reports.liabilities.card_cycle import (
    cycle_dates,
    day_in_month,
    due_after,
    expiry_status,
    statement_expected,
)


def test_cycle_dates_mid_month():
    assert cycle_dates(15, 3, date(2026, 11, 1)) == {
        "last_statement_date": date(2026, 10, 15),
        "next_statement_date": date(2026, 11, 15),
        "current_due_date": date(2026, 11, 3),
        "next_due_date": date(2026, 12, 3),
    }


def test_cycle_dates_clamps_short_month():
    assert cycle_dates(31, None, date(2027, 2, 10)) == {
        "last_statement_date": date(2027, 1, 31),
        "next_statement_date": date(2027, 2, 28),
        "current_due_date": None,
        "next_due_date": None,
    }


def test_cycle_dates_today_is_statement_day():
    out = cycle_dates(15, 3, date(2026, 11, 15))
    assert out["last_statement_date"] == date(2026, 11, 15)
    assert out["next_statement_date"] == date(2026, 12, 15)


def test_cycle_dates_today_is_clamped_statement_day():
    # 28 Feb 2027 is the statement day for a statement day of 31.
    out = cycle_dates(31, None, date(2027, 2, 28))
    assert out["last_statement_date"] == date(2027, 2, 28)
    assert out["next_statement_date"] == date(2027, 3, 31)


def test_cycle_dates_across_year_end():
    out = cycle_dates(20, 5, date(2026, 12, 25))
    assert out["last_statement_date"] == date(2026, 12, 20)
    assert out["next_statement_date"] == date(2027, 1, 20)
    assert out["current_due_date"] == date(2027, 1, 5)
    assert out["next_due_date"] == date(2027, 2, 5)


def test_due_after_rolls_into_next_month():
    assert due_after(date(2026, 11, 21), 10) == date(2026, 12, 10)
    assert due_after(date(2026, 11, 5), 25) == date(2026, 11, 25)
    # Strictly after: a due day equal to the statement day is a month later.
    assert due_after(date(2026, 11, 15), 15) == date(2026, 12, 15)


def test_due_after_clamps_short_month():
    # due_day 31 with an April due month gives the 30th.
    assert due_after(date(2027, 3, 31), 31) == date(2027, 4, 30)
    assert due_after(date(2027, 4, 15), 31) == date(2027, 4, 30)


@pytest.mark.parametrize(
    ("year", "expected"), [(2027, date(2027, 2, 28)), (2028, date(2028, 2, 29))]
)
def test_day_in_month_clamps_february(year, expected):
    assert day_in_month(year, 2, 30) == expected


def test_day_in_month_keeps_existing_day():
    assert day_in_month(2026, 11, 15) == date(2026, 11, 15)


def test_statement_expected_when_latest_line_older_than_grace():
    assert statement_expected(date(2026, 11, 15), date(2026, 10, 20)) is True
    assert statement_expected(date(2026, 11, 15), date(2026, 11, 10)) is False
    # Exactly 7 days before is not older than the grace.
    assert statement_expected(date(2026, 11, 15), date(2026, 11, 8)) is False
    assert statement_expected(None, date(2026, 10, 1)) is False
    assert statement_expected(date(2026, 11, 15), None) is True


# --- expiry_status: "YYYY-MM" valid through the month's last day ---------------


@pytest.mark.parametrize(
    ("today", "status"),
    [
        (date(2026, 10, 31), "ok"),  # 61 days before 31 Dec
        (date(2026, 11, 1), "expiring_soon"),  # exactly 60 days before
        (date(2026, 12, 31), "expiring_soon"),  # the last valid day
        (date(2027, 1, 1), "expired"),  # the day after the month ends
        (date(2030, 6, 1), "expired"),
    ],
)
def test_expiry_status_boundaries(today, status):
    assert expiry_status("2026-12", today, 60) == status


def test_expiry_status_uses_the_month_end_of_a_short_month():
    # Feb 2028 is a leap month: valid through 29 Feb.
    assert expiry_status("2028-02", date(2028, 2, 29), 60) == "expiring_soon"
    assert expiry_status("2028-02", date(2028, 3, 1), 0) == "expired"
    assert expiry_status("2028-02", date(2027, 12, 30), 60) == "ok"  # 61 days
    assert expiry_status("2028-02", date(2028, 1, 1), 59) == "expiring_soon"


def test_expiry_status_threshold_is_a_parameter():
    assert expiry_status("2026-12", date(2026, 11, 1), 30) == "ok"
    assert expiry_status("2026-12", date(2026, 12, 1), 30) == "expiring_soon"


@pytest.mark.parametrize("expiry", [None, "abc", "2026-13"])
def test_expiry_status_is_null_without_a_valid_expiry(expiry):
    assert expiry_status(expiry, date(2026, 11, 1), 60) is None
