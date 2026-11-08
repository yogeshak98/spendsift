"""The uncategorized backlog that replaces the commit gate.

Commit used to be disabled until every row was classified. With 96% of a real
bank statement arriving uncategorized that was ~250 decisions before a single
statement could land, and the journal went six weeks stale. Rows now commit
into Expenses:Uncategorized and show up here instead.

The grouping is the point: one decision about "SWIGGY" settles every Swiggy
row. Answering 250 transactions individually is the chore being removed, so a
backlog listed per transaction would reintroduce it in a new place.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.service.reports.health.uncategorized import (
    backlog_summary,
    merchant_backlog,
)
from tests.service.reports.conftest import _spend


def test_empty_journal_has_an_empty_backlog(ledger):
    assert merchant_backlog(ledger) == []
    assert backlog_summary(ledger)["transactions"] == 0


def test_groups_many_transactions_into_one_decision(ledger):
    # Three Swiggy rows with different UPI reference numbers normalize to one
    # merchant key -- which is the whole reason this is answerable at all.
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9912@ybl-YESB-1111")
    _spend(ledger, date(2026, 3, 9), "210.00", "UPI-SWIGGY-9912@ybl-YESB-2222")
    _spend(ledger, date(2026, 4, 2), "615.25", "UPI-SWIGGY-9912@ybl-YESB-3333")

    rows = merchant_backlog(ledger)
    assert len(rows) == 1
    assert rows[0].count == 3
    assert rows[0].total == Decimal("1245.75")
    assert rows[0].first == date(2026, 3, 5)
    assert rows[0].last == date(2026, 4, 2)


def test_ranks_by_rows_cleared_per_decision_not_by_rupees(ledger):
    # One big one-off versus a small recurring habit. Answering the habit
    # clears more of the backlog, so it comes first even though it is worth
    # far less.
    _spend(ledger, date(2026, 3, 1), "90000.00", "ONE OFF BIG PURCHASE")
    for day in range(1, 6):
        _spend(ledger, date(2026, 3, day), "100.00", f"UPI-SWIGGY-99@ybl-YESB-{day}")

    rows = merchant_backlog(ledger)
    assert rows[0].count == 5
    assert rows[0].total == Decimal("500.00")
    assert rows[1].count == 1


def test_ignores_postings_that_already_have_a_category(ledger):
    _spend(ledger, date(2026, 3, 5), "420.50", "SWIGGY", account="Expenses:Food")
    assert merchant_backlog(ledger) == []


def test_carries_a_recognisable_sample_narration(ledger):
    # The normalized key is a compact token; a human needs the raw text to
    # recognise what they are classifying.
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9912@ybl-YESB-1111")
    assert "SWIGGY" in merchant_backlog(ledger)[0].sample


def test_summary_totals_match_the_rows(ledger):
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-99@ybl-1")
    _spend(ledger, date(2026, 3, 6), "1000.00", "AMAZON ORDER")

    s = backlog_summary(ledger)
    assert s["transactions"] == 2
    assert s["merchants"] == 2
    assert s["total"] == "1420.50"
