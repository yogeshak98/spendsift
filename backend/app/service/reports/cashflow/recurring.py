from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from statistics import median

from backend.app.service.reports.health.ledger_view import PostingRow, top_category


def _norm(narration: str) -> str:
    """Normalize narration: upper-case, collapse whitespace."""
    return " ".join(narration.upper().split())


def stream_key(row: PostingRow) -> tuple[str, str]:
    """The stream a posting belongs to: (normalized narration, top category)."""
    return (_norm(row.narration), top_category(row.account))


# A yearly stream seen only twice: two postings this many days apart (about
# 11 to 13 months).
YEARLY_PAIR_MIN_DAYS = 335
YEARLY_PAIR_MAX_DAYS = 395


def _yearly_pair(postings_sorted: list[PostingRow]) -> bool:
    """Exactly two postings 335-395 days apart: a yearly stream seen twice."""
    if len(postings_sorted) != 2:
        return False
    first, second = postings_sorted
    gap = (second.date - first.date).days
    return YEARLY_PAIR_MIN_DAYS <= gap <= YEARLY_PAIR_MAX_DAYS


def _clamp_day(year: int, month: int, day: int) -> int:
    max_day = calendar.monthrange(year, month)[1]
    return min(day, max_day)


def _detect_streams(
    rows: list[PostingRow],
) -> dict[tuple[str, str], dict]:
    """Return a mapping of stream_key -> stream info for all recurring streams."""
    # Collect expense/income postings only
    relevant = [
        r
        for r in rows
        if r.account.startswith("Expenses:") or r.account.startswith("Income:")
    ]

    # Group by stream key
    groups: dict[tuple[str, str], list[PostingRow]] = {}
    for r in relevant:
        key = stream_key(r)
        groups.setdefault(key, []).append(r)

    streams: dict[tuple[str, str], dict] = {}

    for key, postings in groups.items():
        # Sort by date
        postings_sorted = sorted(postings, key=lambda p: p.date)

        # Rule 1: ≥ 3 occurrences, or a yearly stream seen twice (2 postings
        # 335-395 days apart).
        yearly_pair = _yearly_pair(postings_sorted)
        if len(postings_sorted) < 3 and not yearly_pair:
            continue

        # Rule 2: ≥ 2 distinct calendar months
        months = {(p.date.year, p.date.month) for p in postings_sorted}
        if len(months) < 2:
            continue

        # Compute consecutive gaps in days
        dates = [p.date for p in postings_sorted]
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        med_gap = median(gaps)

        # Rule 3: gap must classify into a cadence bucket
        if yearly_pair:
            cadence = "yearly"
        elif 25 <= med_gap <= 35:
            cadence = "monthly"
        elif 80 <= med_gap <= 100:
            cadence = "quarterly"
        elif 350 <= med_gap <= 380:
            cadence = "yearly"
        else:
            continue

        narr_key, account = key
        # expected_amount = median of observed amounts
        amounts = [p.amount for p in postings_sorted]
        expected_amount = median(amounts)
        # expected_day = median day-of-month
        days = [p.date.day for p in postings_sorted]
        expected_day = int(median(days))
        last_seen = max(p.date for p in postings_sorted)
        first_seen = min(p.date for p in postings_sorted)
        direction = "out" if account.startswith("Expenses:") else "in"

        streams[key] = {
            "key": f"{narr_key}|{account}",
            "label": narr_key,
            "account": account,
            "cadence": cadence,
            "direction": direction,
            "expected_amount": str(expected_amount),
            "expected_day": expected_day,
            "first_seen": first_seen.isoformat(),
            "last_seen": last_seen.isoformat(),
            # store raw postings for per-month lookup
            "_postings": postings_sorted,
        }

    return streams


def recurring_stream_keys(
    rows: list[PostingRow], direction: str = "out"
) -> frozenset[tuple[str, str]]:
    """The `stream_key`s of the recurring streams in `rows` that flow
    `direction` ("out" = spending, "in" = income)."""
    return frozenset(
        key for key, s in _detect_streams(rows).items() if s["direction"] == direction
    )


def _month_aligns(stream: dict, year: int, month: int) -> bool:
    """Does the cadence place an expected occurrence in (year, month)?"""
    cadence = stream["cadence"]
    first_seen = date.fromisoformat(stream["first_seen"])
    last_seen = date.fromisoformat(stream["last_seen"])

    # Never report a stream for a month before it first appeared.
    candidate_ym = year * 12 + month
    first_ym = first_seen.year * 12 + first_seen.month
    if candidate_ym < first_ym:
        return False

    if cadence == "monthly":
        return True

    if cadence == "quarterly":
        # aligns if diff-in-months from last_seen is a multiple of 3.
        # The candidate_ym >= first_ym guard above already prevents phantom
        # alignments before the stream's first occurrence.
        diff = (year - last_seen.year) * 12 + (month - last_seen.month)
        return diff % 3 == 0

    if cadence == "yearly":
        # aligns if same month-of-year as last_seen and year >= first_seen.year
        return month == last_seen.month and year >= first_seen.year

    return False


def monthly_recurring(
    rows: list[PostingRow],
    year: int,
    month: int,
    today: date | None = None,
) -> list[dict]:
    """Return per-stream card dicts for recurring items expected in (year, month)."""
    if today is None:
        today = date.today()

    streams = _detect_streams(rows)
    result: list[dict] = []

    # Determine reference_day for status computation
    if (year, month) == (today.year, today.month):
        reference_day = today.day
    elif (year * 12 + month) < (today.year * 12 + today.month):
        # past month — treat as fully elapsed
        reference_day = 31
    else:
        # future month — nothing due yet
        reference_day = 0

    for key, stream in streams.items():
        if not _month_aligns(stream, year, month):
            continue

        expected_day = stream["expected_day"]
        clamped_day = _clamp_day(year, month, expected_day)
        expected_date = date(year, month, clamped_day)

        # Find actual occurrences in this month
        actual = [
            p
            for p in stream["_postings"]
            if p.date.year == year and p.date.month == month
        ]
        actual_dates = sorted({p.date.isoformat() for p in actual})

        if actual:
            earliest_actual_day = min(p.date.day for p in actual)
            if earliest_actual_day <= expected_day:
                status = "cleared"
            else:
                status = "cleared_late"
            amount = str(sum(p.amount for p in actual))
        else:
            if reference_day > expected_day:
                status = "past_due"
            else:
                status = "upcoming"
            amount = stream["expected_amount"]

        result.append(
            {
                "key": stream["key"],
                "label": stream["label"],
                "account": stream["account"],
                "cadence": stream["cadence"],
                "status": status,
                "direction": stream["direction"],
                "amount": amount,
                "expected_amount": stream["expected_amount"],
                "expected_day": expected_day,
                "expected_date": expected_date.isoformat(),
                "last_seen": stream["last_seen"],
                "actual_dates": actual_dates,
            }
        )

    return result


def recurring_summary(
    rows: list[PostingRow],
    year: int,
    month: int,
    today: date | None = None,
) -> dict:
    """Aggregate summary for the recurring items in (year, month).

    ``stream_count`` is the total number of recurring streams detected across
    all history — it is month-agnostic.  ``items`` may be empty for a given
    month even when streams exist (e.g. a quarterly stream whose cycle does not
    land in the requested month), making both zero-states on the frontend
    meaningful: ``stream_count == 0`` means no patterns detected at all;
    ``stream_count > 0 && items.length == 0`` means patterns exist but none
    are expected in the selected month.
    """
    items = monthly_recurring(rows, year, month, today=today)
    # total streams detected over all history (month-agnostic)
    all_streams = _detect_streams(rows)
    stream_count = len(all_streams)

    cleared_count = 0
    cleared_amount = Decimal(0)
    past_due_count = 0
    past_due_amount = Decimal(0)
    upcoming_count = 0
    upcoming_amount = Decimal(0)

    for item in items:
        amt = Decimal(item["amount"])
        if item["status"] in ("cleared", "cleared_late"):
            cleared_count += 1
            cleared_amount += amt
        elif item["status"] == "past_due":
            past_due_count += 1
            past_due_amount += amt
        elif item["status"] == "upcoming":
            upcoming_count += 1
            upcoming_amount += amt

    return {
        "stream_count": stream_count,
        "cleared": {"count": cleared_count, "amount": str(cleared_amount)},
        "past_due": {"count": past_due_count, "amount": str(past_due_amount)},
        "upcoming": {"count": upcoming_count, "amount": str(upcoming_amount)},
    }
