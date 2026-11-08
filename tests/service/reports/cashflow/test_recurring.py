"""Tests for the recurring detection read model."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.reports.health.ledger_view import PostingRow, all_postings
from backend.app.service.reports.cashflow.recurring import (
    monthly_recurring,
    recurring_stream_keys,
    recurring_summary,
)


def _cand(d, narr, amount, category="Expenses:Subscriptions"):
    return CandidateTxn(
        date=d,
        amount=Decimal(amount),
        raw_narration=narr,
        normalized_narration=narr.upper(),
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
        suggested_category=category,
    )


@pytest.fixture
def multi_month_ledger(ledger, investment_ledger):
    """Ledger seeded with three months of a fictional subscription + a salary stream."""
    commit_candidates(
        ledger,
        investment_ledger,
        [
            # Streamflix: monthly ~499, ~3rd of month, Jan/Feb/Mar/Apr 2026
            _cand(date(2026, 1, 3), "Streamflix", "499"),
            _cand(date(2026, 2, 3), "Streamflix", "499"),
            _cand(date(2026, 3, 3), "Streamflix", "499"),
            _cand(date(2026, 4, 3), "Streamflix", "499"),
            # GymMembership: monthly ~1200, ~1st of month, Jan/Feb/Mar 2026
            _cand(date(2026, 1, 1), "GymMembership", "1200"),
            _cand(date(2026, 2, 1), "GymMembership", "1200"),
            _cand(date(2026, 3, 1), "GymMembership", "1200"),
        ],
    )
    return ledger


@pytest.fixture
def two_occurrence_ledger(ledger, investment_ledger):
    """Only 2 occurrences — should NOT be detected."""
    commit_candidates(
        ledger,
        investment_ledger,
        [
            _cand(date(2026, 1, 5), "OnceMore", "999"),
            _cand(date(2026, 2, 5), "OnceMore", "999"),
        ],
    )
    return ledger


@pytest.fixture
def same_month_ledger(ledger, investment_ledger):
    """Many hits in a single month — should NOT be detected (rule 2)."""
    commit_candidates(
        ledger,
        investment_ledger,
        [
            # 5 hits but all in April 2026
            _cand(date(2026, 4, 1), "DailyBread", "100"),
            _cand(date(2026, 4, 5), "DailyBread", "100"),
            _cand(date(2026, 4, 10), "DailyBread", "100"),
            _cand(date(2026, 4, 15), "DailyBread", "100"),
            _cand(date(2026, 4, 20), "DailyBread", "100"),
        ],
    )
    return ledger


# ── detection tests ──────────────────────────────────────────────────────────


def test_monthly_stream_detected(multi_month_ledger):
    rows = all_postings(multi_month_ledger)
    items = monthly_recurring(rows, 2026, 5, today=date(2026, 5, 1))
    labels = {i["label"] for i in items}
    assert "STREAMFLIX" in labels
    for item in items:
        assert item["cadence"] == "monthly"


def test_two_occurrences_not_detected(two_occurrence_ledger):
    rows = all_postings(two_occurrence_ledger)
    items = monthly_recurring(rows, 2026, 3, today=date(2026, 3, 1))
    assert all(i["label"] != "ONCEMORE" for i in items)


def test_single_month_hits_not_detected(same_month_ledger):
    rows = all_postings(same_month_ledger)
    # even querying a month after seeding, it should not appear
    items = monthly_recurring(rows, 2026, 5, today=date(2026, 5, 1))
    assert all(i["label"] != "DAILYBREAD" for i in items)


# ── status transition tests ───────────────────────────────────────────────────


def test_status_cleared(multi_month_ledger):
    """April 3rd hit, expected ~3rd, today is April 10 → cleared."""
    rows = all_postings(multi_month_ledger)
    items = monthly_recurring(rows, 2026, 4, today=date(2026, 4, 10))
    streamflix = next(i for i in items if i["label"] == "STREAMFLIX")
    assert streamflix["status"] == "cleared"
    assert streamflix["actual_dates"] == ["2026-04-03"]


def test_status_past_due(multi_month_ledger):
    """May: expected ~3rd, today is May 10, nothing paid yet → past_due."""
    rows = all_postings(multi_month_ledger)
    items = monthly_recurring(rows, 2026, 5, today=date(2026, 5, 10))
    streamflix = next(i for i in items if i["label"] == "STREAMFLIX")
    assert streamflix["status"] == "past_due"
    assert streamflix["actual_dates"] == []
    assert streamflix["amount"] == streamflix["expected_amount"]


def test_status_upcoming(multi_month_ledger):
    """May: expected ~3rd, today is May 1 → upcoming."""
    rows = all_postings(multi_month_ledger)
    items = monthly_recurring(rows, 2026, 5, today=date(2026, 5, 1))
    streamflix = next(i for i in items if i["label"] == "STREAMFLIX")
    assert streamflix["status"] == "upcoming"
    assert streamflix["actual_dates"] == []


def test_status_past_month_all_past_due(multi_month_ledger):
    """Querying a fully-elapsed past month with no recordings → past_due."""
    # June 2026 has nothing seeded; reference_day=31 (past month relative to July 2026)
    rows = all_postings(multi_month_ledger)
    items = monthly_recurring(rows, 2026, 6, today=date(2026, 7, 1))
    streamflix = next(i for i in items if i["label"] == "STREAMFLIX")
    assert streamflix["status"] == "past_due"


# ── type/shape tests ──────────────────────────────────────────────────────────


def test_amount_fields_are_strings(multi_month_ledger):
    rows = all_postings(multi_month_ledger)
    items = monthly_recurring(rows, 2026, 4, today=date(2026, 4, 10))
    for item in items:
        assert isinstance(item["amount"], str)
        assert isinstance(item["expected_amount"], str)
        # verify parseable as decimal
        Decimal(item["amount"])
        Decimal(item["expected_amount"])


def test_date_fields_are_iso(multi_month_ledger):
    rows = all_postings(multi_month_ledger)
    items = monthly_recurring(rows, 2026, 4, today=date(2026, 4, 10))
    for item in items:
        # these should parse as dates without raising
        date.fromisoformat(item["expected_date"])
        date.fromisoformat(item["last_seen"])
        for d in item["actual_dates"]:
            date.fromisoformat(d)


# ── summary tests ─────────────────────────────────────────────────────────────


def test_summary_counts_and_amounts(multi_month_ledger):
    rows = all_postings(multi_month_ledger)
    # April 2026: Streamflix (3rd) cleared; GymMembership (1st) past_due (today=10 > day=1, not seeded in Apr)
    # today=April 2 → Streamflix cleared (3rd not yet due), GymMembership past_due (1st passed, not seeded)
    # Use today=April 4 so Streamflix(3rd) is cleared and GymMembership(1st) is past_due
    summary = recurring_summary(rows, 2026, 4, today=date(2026, 4, 4))
    assert summary["stream_count"] == 2
    # Streamflix has actual Apr 3 → cleared
    assert summary["cleared"]["count"] == 1
    assert Decimal(summary["cleared"]["amount"]) == Decimal("499")
    # GymMembership expected day=1, no actual, today=4 > 1 → past_due
    assert summary["past_due"]["count"] == 1
    assert Decimal(summary["past_due"]["amount"]) == Decimal("1200")
    assert isinstance(summary["cleared"]["amount"], str)
    assert isinstance(summary["past_due"]["amount"], str)
    assert isinstance(summary["upcoming"]["amount"], str)


def test_summary_past_due_tone(multi_month_ledger):
    """May 2026: nothing seeded → both past_due if today > expected_day."""
    rows = all_postings(multi_month_ledger)
    summary = recurring_summary(rows, 2026, 5, today=date(2026, 5, 15))
    assert summary["past_due"]["count"] == 2
    assert summary["cleared"]["count"] == 0
    assert summary["upcoming"]["count"] == 0


# ── MINOR 3: cleared_late test ────────────────────────────────────────────────


@pytest.fixture
def late_payment_ledger(ledger, investment_ledger):
    """Ledger with a stream where the actual payment is after expected_day."""
    commit_candidates(
        ledger,
        investment_ledger,
        [
            # QuarterlyGym: expected day ~3; payment for Apr arrives on day 10
            _cand(date(2026, 1, 3), "QuarterlyGym", "600"),
            _cand(date(2026, 2, 3), "QuarterlyGym", "600"),
            _cand(date(2026, 3, 3), "QuarterlyGym", "600"),
            _cand(
                date(2026, 4, 10), "QuarterlyGym", "600"
            ),  # late payment, day 10 > expected 3
        ],
    )
    return ledger


def test_status_cleared_late_real(late_payment_ledger):
    """expected_day=3, payment on day 10, today=day 12 → cleared_late."""
    rows = all_postings(late_payment_ledger)
    items = monthly_recurring(rows, 2026, 4, today=date(2026, 4, 12))
    item = next(i for i in items if i["label"] == "QUARTERLYGYM")
    assert item["status"] == "cleared_late"
    assert item["actual_dates"] == ["2026-04-10"]


# ── IMPORTANT 1: phantom alignment guard ─────────────────────────────────────


@pytest.fixture
def quarterly_stream_ledger(ledger, investment_ledger):
    """Ledger with a quarterly stream starting 2026-03."""
    commit_candidates(
        ledger,
        investment_ledger,
        [
            _cand(date(2026, 3, 5), "QuarterlySub", "2000"),
            _cand(date(2026, 6, 5), "QuarterlySub", "2000"),
            _cand(date(2026, 9, 5), "QuarterlySub", "2000"),
        ],
    )
    return ledger


def test_quarterly_not_shown_before_first_occurrence(quarterly_stream_ledger):
    """A quarterly stream must NOT appear for months before its first occurrence."""
    rows = all_postings(quarterly_stream_ledger)
    # Dec 2025 is before the stream's first occurrence (Mar 2026)
    items = monthly_recurring(rows, 2025, 12, today=date(2026, 1, 1))
    assert all(
        i["label"] != "QUARTERLYSUB" for i in items
    ), "quarterly stream appeared in a month before first_seen"
    # Also check that a month that arithmetically aligns (e.g. 3 months before
    # last_seen) but is before first_seen is excluded.
    items_dec26 = monthly_recurring(rows, 2025, 12, today=date(2025, 12, 31))
    assert all(i["label"] != "QUARTERLYSUB" for i in items_dec26)


def test_quarterly_cadence_detected(quarterly_stream_ledger):
    """Three occurrences ~90 days apart across ≥2 months → cadence == quarterly."""
    rows = all_postings(quarterly_stream_ledger)
    items = monthly_recurring(rows, 2026, 6, today=date(2026, 6, 10))
    item = next((i for i in items if i["label"] == "QUARTERLYSUB"), None)
    assert item is not None, "quarterly stream not detected"
    assert item["cadence"] == "quarterly"


# ── MINOR 5: income direction test ───────────────────────────────────────────


@pytest.fixture
def income_stream_ledger(ledger, investment_ledger):
    """Ledger with a recurring income stream (salary)."""
    commit_candidates(
        ledger,
        investment_ledger,
        [
            _cand(date(2026, 1, 1), "SalaryIncome", "50000", category="Income:Salary"),
            _cand(date(2026, 2, 1), "SalaryIncome", "50000", category="Income:Salary"),
            _cand(date(2026, 3, 1), "SalaryIncome", "50000", category="Income:Salary"),
        ],
    )
    return ledger


def test_income_direction_in(income_stream_ledger):
    """Income account postings must have direction == 'in'."""
    rows = all_postings(income_stream_ledger)
    items = monthly_recurring(rows, 2026, 4, today=date(2026, 4, 5))
    income_items = [i for i in items if i["label"] == "SALARYINCOME"]
    assert income_items, "income stream not detected"
    assert all(i["direction"] == "in" for i in income_items)


# ── IMPORTANT 2: stream_count is all-history count, not per-month ─────────────


def test_stream_count_is_all_history(quarterly_stream_ledger):
    """stream_count should equal total detected streams, not per-month items."""
    rows = all_postings(quarterly_stream_ledger)
    # Query a month when the quarterly stream is NOT expected (e.g. Apr 2026,
    # between Mar and Jun occurrences) but the stream has been detected.
    summary = recurring_summary(rows, 2026, 4, today=date(2026, 4, 15))
    # stream_count is the total number of detected recurring streams (1 here)
    assert summary["stream_count"] == 1
    # but items should be empty since Apr doesn't align with the quarterly cycle
    from backend.app.service.reports.cashflow.recurring import monthly_recurring as mr

    items = mr(rows, 2026, 4, today=date(2026, 4, 15))
    assert len(items) == 0, "no items expected for a non-aligning month"
    # confirming second zero-state scenario: streams detected but none in this month
    assert summary["stream_count"] > 0


# ── yearly stream seen twice ──────────────────────────────────────────────────


def test_yearly_charge_paid_twice_twelve_months_apart_is_recurring(
    ledger, investment_ledger
):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            _cand(date(2025, 9, 10), "CodePrep Annual", "13000"),
            _cand(date(2026, 9, 10), "CodePrep Annual", "13000"),
        ],
    )
    rows = all_postings(ledger)
    (item,) = [
        i
        for i in monthly_recurring(rows, 2027, 9, today=date(2027, 9, 1))
        if i["label"] == "CODEPREP ANNUAL"
    ]
    assert item["cadence"] == "yearly"
    assert (
        "CODEPREP ANNUAL",
        "Expenses:Subscriptions",
    ) in recurring_stream_keys(rows)


def test_two_charges_six_months_apart_are_not_recurring(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            _cand(date(2026, 1, 10), "HalfYear", "5000"),
            _cand(date(2026, 7, 10), "HalfYear", "5000"),
        ],
    )
    assert recurring_stream_keys(all_postings(ledger)) == frozenset()


@pytest.mark.parametrize(
    ("gap_days", "recurring"),
    [
        (305, False),  # 31 Dec -> 1 Nov: 11 calendar months, but only 305 days
        (334, False),
        (335, True),
        (395, True),
        (396, False),
        (426, False),  # 14 months
    ],
)
def test_yearly_pair_is_measured_in_days(gap_days, recurring):
    first = date(2025, 1, 10)
    rows = [
        PostingRow(d, "Expenses:Travel", Decimal("9000"), "INDIGO", frozenset())
        for d in (first, first + timedelta(days=gap_days))
    ]
    keys = recurring_stream_keys(rows)
    assert (("INDIGO", "Expenses:Travel") in keys) is recurring
