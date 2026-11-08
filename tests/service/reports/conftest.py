from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from beancount.core import data
from beancount.core.amount import Amount

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.persistence.journal import Ledger


def _spend(
    ledger: Ledger,
    when: date,
    amount: str,
    narration: str,
    account: str = "Expenses:Uncategorized",
    txn_id: str | None = None,
) -> None:
    """Append a balanced two-posting spend -- shared by the uncategorized-
    backlog tests (backend/app/service/reports/health/uncategorized.py,
    backend/app/service/reports/health/doctor.py, and the route test in tests/server/routes/) so the shape of an
    Uncategorized posting lives in exactly one place.

    `txn_id`, when given, is stamped as the `txn-id` meta key -- real imports
    (backend/app/service/ingest/pipeline/entry_writer.py) always set one; callers that need
    `Ledger.recategorize`-by-txn-id to find a fixture row (the classify-a-
    merchant route tests) must opt in explicitly rather than rely on the
    default, no-txn-id fixtures every other `_spend` caller already depends on."""
    meta = data.new_metadata("<test>", 0)
    if txn_id is not None:
        meta["txn-id"] = txn_id
    ledger.append(
        [
            data.Transaction(
                meta,
                when,
                "*",
                None,
                narration,
                data.EMPTY_SET,
                data.EMPTY_SET,
                [
                    data.Posting(
                        account, Amount(Decimal(amount), "INR"), None, None, None, None
                    ),
                    data.Posting(
                        "Assets:Checking:HDFC",
                        Amount(-Decimal(amount), "INR"),
                        None,
                        None,
                        None,
                        None,
                    ),
                ],
            )
        ]
    )


def _cand(
    d,
    narr,
    amount,
    account="Assets:Bank:HDFC",
    direction=Direction.DEBIT,
    category="Expenses:Food",
    other=None,
    tags=None,
):
    c = CandidateTxn(
        date=d,
        amount=Decimal(amount),
        raw_narration=narr,
        normalized_narration=narr.upper(),
        source_account=account,
        direction=direction,
        suggested_category=category,
        suggested_other_leg=other,
    )
    return c


@pytest.fixture
def loaded_ledger(ledger, investment_ledger):
    """Ledger seeded with April-2026 transactions for report tests."""
    commit_candidates(
        ledger,
        investment_ledger,
        [
            _cand(
                date(2026, 4, 3),
                "Salary HeyLeo",
                "280000",
                direction=Direction.CREDIT,
                category="Income:Salary",
            ),
            _cand(date(2026, 4, 4), "Rent Landlord", "35000", category="Expenses:Rent"),
            _cand(date(2026, 4, 1), "Swiggy", "412", category="Expenses:Food"),
            _cand(date(2026, 4, 8), "Swiggy", "888", category="Expenses:Food"),
            _cand(date(2026, 4, 2), "Amazon", "4299", category="Expenses:Shopping"),
        ],
    )
    return ledger
