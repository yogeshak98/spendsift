"""The opening-date rule and the April advice."""

from __future__ import annotations

from datetime import date, timedelta

from backend.app.models.api.openings import OpeningKind


def fy_start_year(d: date) -> int:
    """The calendar year the Indian financial year containing `d` starts in
    (April to March): 2026 for any date from 1 Apr 2026 to 31 Mar 2027."""
    return d.year if d.month >= 4 else d.year - 1


def day_before(d: date) -> date:
    """The day before `d`: the date an opening as of the start of `d` is
    booked on, so it comes strictly before every posting from `d` on."""
    return d - timedelta(days=1)


def _card_covers_april(start: date) -> bool:
    """The card cycle starting on `start` contains 1 April or begins on it:
    the first 1 April on or after `start` is at most 31 days away. Card
    cycles do not align to 1 Apr, so this is the cycle whose opening is the
    balance at the start of the financial year."""
    april = date(start.year, 4, 1)
    if start > april:
        april = date(start.year + 1, 4, 1)
    return (april - start).days <= 31


def _fy_label(fy: int) -> str:
    return f"{fy}-{(fy + 1) % 100:02d}"


def opening_date_for(start: date, kind: OpeningKind | None = None) -> tuple[date, bool]:
    """The opening date for a statement or first posting starting on
    `start`, and whether the April rule applied.

    card: always the day before `start` (the date the printed previous
    balance applies to); the flag is True when the cycle contains 1 April
    or begins on it (`_card_covers_april`). Any other kind: when `start` is
    in April, (31 Mar of that year, True); otherwise (the day before
    `start`, False).
    """
    if kind == "card":
        return day_before(start), _card_covers_april(start)
    if start.month == 4:
        return date(start.year, 3, 31), True
    return day_before(start), False


def april_advice(start: date, kind: OpeningKind | None = None) -> str | None:
    """None when the April rule applies to `start`. Otherwise, with
    fy = fy_start_year(start), exactly:

    card: "This card statement's cycle starts {start:%d %b %Y}. For FY
    {fy}-{(fy+1)%100:02d}, import the earlier statement whose cycle covers
    1 April {fy} first, so the opening is as of the start of the year."

    any other kind: "This statement starts on {start:%d %b %Y}. For a full
    FY {fy}-{(fy+1)%100:02d}, import the statement that starts in April
    {fy} first; otherwise the opening is dated {start-1:%d %b %Y}."
    """
    fy = fy_start_year(start)
    if kind == "card":
        if _card_covers_april(start):
            return None
        return (
            f"This card statement's cycle starts {start:%d %b %Y}. For FY "
            f"{_fy_label(fy)}, import the earlier statement whose cycle covers "
            f"1 April {fy} first, so the opening is as of the start of the year."
        )
    if start.month == 4:
        return None
    before = day_before(start)
    return (
        f"This statement starts on {start:%d %b %Y}. For a full FY "
        f"{_fy_label(fy)}, import the statement that starts in April "
        f"{fy} first; otherwise the opening is dated {before:%d %b %Y}."
    )
