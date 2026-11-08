"""Inclusive date ranges for the period-driven reports (Month | FY | Custom).

`validate_range`, `optional_range` and `range_or_fy` raise
`PERIOD_RANGE_INVALID` (E1087).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from backend.app.core.errors.errors import PERIOD_RANGE_INVALID, CodedError
from backend.app.service.reports.health.ledger_view import PostingRow

# A range of at most this many days is reported per day; a longer one per month.
DAILY_MAX_DAYS = 92

Granularity = Literal["day", "month"]

_MONTH_ABBR = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date  # inclusive


# Years outside this window are rejected (E1087) rather than building a
# multi-century series or overflowing `date`.
MIN_YEAR = 1900
MAX_YEAR = 2200


def _invalid(reason: str) -> CodedError:
    return PERIOD_RANGE_INVALID.exc({"reason": reason})


def validate_range(start: date | None, end: date | None) -> DateRange:
    """Both given and `start <= end`, else E1087."""
    if start is None or end is None:
        raise _invalid("pass both from and to")
    for d in (start, end):
        if not MIN_YEAR <= d.year <= MAX_YEAR:
            raise _invalid(f"{d.isoformat()} is outside {MIN_YEAR}..{MAX_YEAR}")
    if start > end:
        raise _invalid(f"from {start.isoformat()} is after to {end.isoformat()}")
    return DateRange(start, end)


def optional_range(start: date | None, end: date | None) -> DateRange | None:
    """None when neither is given (an all-time report), else `validate_range`."""
    if start is None and end is None:
        return None
    return validate_range(start, end)


def fy_range(fy: int) -> DateRange:
    """India FY `fy`: 1 Apr `fy` .. 31 Mar `fy + 1`."""
    return DateRange(date(fy, 4, 1), date(fy + 1, 3, 31))


def range_or_fy(fy: int | None, start: date | None, end: date | None) -> DateRange:
    """Exactly one of `fy` or (`start`, `end`): `fy_range(fy)` or
    `validate_range(start, end)`. Neither, both, or a half range is E1087."""
    has_range = start is not None or end is not None
    if fy is not None and has_range:
        raise _invalid("pass fy or from and to, not both")
    if fy is not None:
        if not MIN_YEAR <= fy <= MAX_YEAR - 1:
            raise _invalid(f"fy {fy} is outside {MIN_YEAR}..{MAX_YEAR - 1}")
        return fy_range(fy)
    if not has_range:
        raise _invalid("pass fy, or both from and to")
    return validate_range(start, end)


def granularity(r: DateRange) -> Granularity:
    """`day` iff `(end - start).days + 1 <= DAILY_MAX_DAYS`, else `month`."""
    return "day" if (r.end - r.start).days + 1 <= DAILY_MAX_DAYS else "month"


def days_in(r: DateRange) -> list[date]:
    """Every day of `r`, chronological."""
    return [r.start + timedelta(days=i) for i in range((r.end - r.start).days + 1)]


def _month_index(year: int, month: int) -> int:
    return year * 12 + month - 1


def _from_index(idx: int) -> tuple[int, int]:
    year, m0 = divmod(idx, 12)
    return year, m0 + 1


def months_in(r: DateRange) -> list[tuple[int, int]]:
    """Every (year, month) `r` touches, chronological."""
    first = _month_index(r.start.year, r.start.month)
    last = _month_index(r.end.year, r.end.month)
    return [_from_index(i) for i in range(first, last + 1)]


def month_label(year: int, month: int, with_year: bool) -> str:
    """`Aug`, or `Aug 26` with the year."""
    abbr = _MONTH_ABBR[month - 1]
    return f"{abbr} {year % 100:02d}" if with_year else abbr


def day_label(d: date) -> str:
    """Like `3 Aug`."""
    return f"{d.day} {_MONTH_ABBR[d.month - 1]}"


def spans_years(months: list[tuple[int, int]]) -> bool:
    """True when `months` covers more than one calendar year."""
    return len({year for year, _ in months}) > 1


def in_range(rows: list[PostingRow], r: DateRange) -> list[PostingRow]:
    """Rows with `r.start <= row.date <= r.end`."""
    return [row for row in rows if r.start <= row.date <= r.end]
