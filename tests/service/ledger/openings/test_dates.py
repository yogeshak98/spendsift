"""The opening-date rule and the April advice."""

from __future__ import annotations

from datetime import date

import pytest

from backend.app.service.ledger.openings.dates import (
    april_advice,
    fy_start_year,
    opening_date_for,
)


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (date(2025, 4, 1), (date(2025, 3, 31), True)),
        (date(2025, 4, 16), (date(2025, 3, 31), True)),
        (date(2025, 4, 30), (date(2025, 3, 31), True)),
        (date(2025, 5, 10), (date(2025, 5, 9), False)),
        (date(2025, 3, 31), (date(2025, 3, 30), False)),
        (date(2026, 1, 1), (date(2025, 12, 31), False)),
    ],
)
def test_opening_date_for(start, expected):
    assert opening_date_for(start) == expected
    assert opening_date_for(start, "bank") == expected


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        # A card is dated the day before its cycle, the date its printed
        # previous balance applies to; April applies to the cycle that
        # contains 1 April or begins on it (1 April at most 31 days on).
        (date(2025, 3, 16), (date(2025, 3, 15), True)),
        (date(2025, 4, 1), (date(2025, 3, 31), True)),
        (date(2025, 4, 10), (date(2025, 4, 9), False)),
        (date(2025, 4, 16), (date(2025, 4, 15), False)),
        (date(2025, 3, 2), (date(2025, 3, 1), True)),  # 30 days
        (date(2025, 3, 1), (date(2025, 2, 28), True)),  # 31 days: the edge
        (date(2025, 2, 28), (date(2025, 2, 27), False)),  # 32 days
        (date(2025, 5, 10), (date(2025, 5, 9), False)),
        # After 1 April the next one is a year away.
        (date(2025, 4, 2), (date(2025, 4, 1), False)),
    ],
)
def test_card_opening_date_is_the_day_before_its_cycle(start, expected):
    assert opening_date_for(start, "card") == expected


@pytest.mark.parametrize(
    ("d", "fy"),
    [
        (date(2026, 2, 1), 2025),
        (date(2026, 3, 31), 2025),
        (date(2026, 4, 1), 2026),
        (date(2026, 12, 31), 2026),
    ],
)
def test_fy_start_year(d, fy):
    assert fy_start_year(d) == fy


def test_april_advice_sentence():
    assert april_advice(date(2025, 5, 10)) == (
        "This statement starts on 10 May 2025. For a full FY 2025-26, import "
        "the statement that starts in April 2025 first; otherwise the opening "
        "is dated 09 May 2025."
    )


def test_april_advice_uses_the_fy_of_the_start_not_its_calendar_year():
    # February 2026 belongs to FY 2025-26: the advice names April 2025.
    assert april_advice(date(2026, 2, 1)) == (
        "This statement starts on 01 Feb 2026. For a full FY 2025-26, import "
        "the statement that starts in April 2025 first; otherwise the opening "
        "is dated 31 Jan 2026."
    )


def test_no_april_advice_when_april_applies():
    assert april_advice(date(2025, 4, 3)) is None


def _card_advice(start: str, fy: str, year: int) -> str:
    return (
        f"This card statement's cycle starts {start}. For FY {fy}, import the "
        f"earlier statement whose cycle covers 1 April {year} first, so the "
        "opening is as of the start of the year."
    )


def test_card_advice_when_the_cycle_misses_1_april():
    assert april_advice(date(2025, 4, 10), "card") == _card_advice(
        "10 Apr 2025", "2025-26", 2025
    )
    assert april_advice(date(2025, 4, 16), "card") == _card_advice(
        "16 Apr 2025", "2025-26", 2025
    )
    assert april_advice(date(2025, 5, 10), "card") == _card_advice(
        "10 May 2025", "2025-26", 2025
    )
    # February 2026 is in FY 2025-26: the cycle to import covers 1 Apr 2025.
    assert april_advice(date(2026, 2, 1), "card") == _card_advice(
        "01 Feb 2026", "2025-26", 2025
    )


@pytest.mark.parametrize(
    "start", [date(2025, 3, 16), date(2025, 4, 1), date(2025, 3, 2)]
)
def test_no_card_advice_when_the_cycle_covers_1_april(start):
    assert april_advice(start, "card") is None


def test_other_kinds_keep_the_whole_of_april_rule():
    assert april_advice(date(2025, 4, 16), "bank") is None
    assert april_advice(date(2025, 3, 16), "bank") == april_advice(date(2025, 3, 16))
    assert april_advice(date(2025, 3, 16)) is not None
