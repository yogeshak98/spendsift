"""A credit card's statement cycle from its statement day and due day.

Both are days of the month, 1-31. In a month without that day the date is
clamped to the month's last day (`statement_day 31` gives 30 Apr, 28 Feb or
29 Feb in a leap year). A statement's due date is the first clamped `due_day`
strictly after the statement date.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Literal, TypedDict

ExpiryStatus = Literal["ok", "expiring_soon", "expired"]


class CycleDates(TypedDict):
    last_statement_date: date
    next_statement_date: date
    current_due_date: date | None
    next_due_date: date | None


def day_in_month(year: int, month: int, day: int) -> date:
    """`day` of the month, clamped to the month's last day."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift(year: int, month: int, by: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + by
    return index // 12, index % 12 + 1


def due_after(statement: date, due_day: int) -> date:
    """The first clamped `due_day` strictly after `statement`."""
    candidate = day_in_month(statement.year, statement.month, due_day)
    if candidate > statement:
        return candidate
    year, month = _shift(statement.year, statement.month, 1)
    return day_in_month(year, month, due_day)


def cycle_dates(statement_day: int, due_day: int | None, today: date) -> CycleDates:
    """The latest statement date on or before `today`, the next one after it,
    and (with a due day) the due date of each."""
    this_month = day_in_month(today.year, today.month, statement_day)
    if this_month <= today:
        last = this_month
        year, month = _shift(today.year, today.month, 1)
        upcoming = day_in_month(year, month, statement_day)
    else:
        year, month = _shift(today.year, today.month, -1)
        last = day_in_month(year, month, statement_day)
        upcoming = this_month
    return {
        "last_statement_date": last,
        "next_statement_date": upcoming,
        "current_due_date": None if due_day is None else due_after(last, due_day),
        "next_due_date": None if due_day is None else due_after(upcoming, due_day),
    }


def statement_expected(
    last_statement_date: date | None,
    latest_line_date: date | None,
    grace_days: int = 7,
) -> bool:
    """True when a statement has closed but the card's latest line predates it
    by more than `grace_days` (or there is no line): the statement's lines have
    probably not been imported yet."""
    if last_statement_date is None:
        return False
    if latest_line_date is None:
        return True
    return latest_line_date < last_statement_date - timedelta(days=grace_days)


def expiry_status(
    expiry: str | None, today: date, soon_days: int
) -> ExpiryStatus | None:
    """A `"YYYY-MM"` card expiry against `today`. The card is valid through
    the month's last day: `"expired"` after it, `"expiring_soon"` when that
    day is at most `soon_days` away (the last day itself included), else
    `"ok"`. None without a valid expiry."""
    if expiry is None or len(expiry) != 7 or expiry[4] != "-":
        return None
    try:
        year, month = int(expiry[:4]), int(expiry[5:])
        last_day = day_in_month(year, month, 31)
    except ValueError:
        return None
    if today > last_day:
        return "expired"
    if (last_day - today).days <= soon_days:
        return "expiring_soon"
    return "ok"
