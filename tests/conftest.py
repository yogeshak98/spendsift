from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import data
from beancount.core.amount import Amount

from backend.app.persistence.journal import Ledger


@pytest.fixture
def storage_dir(tmp_path: Path) -> Path:
    return tmp_path / "storage"


@pytest.fixture
def ledger(storage_dir: Path) -> Ledger:
    led = Ledger(storage_dir)
    led.bootstrap()
    return led


@pytest.fixture
def investment_ledger(storage_dir: Path) -> Ledger:
    led = Ledger(
        storage_dir,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    led.bootstrap(seed_accounts=())
    return led


def make_txn(
    txn_date: date,
    narration: str,
    debit_account: str,
    credit_account: str,
    amount: str,
    currency: str = "INR",
) -> data.Transaction:
    """A balanced two-posting transaction for tests."""
    meta = data.new_metadata("<test>", 0)
    postings = [
        data.Posting(
            debit_account, Amount(Decimal(amount), currency), None, None, None, None
        ),
        data.Posting(
            credit_account, Amount(-Decimal(amount), currency), None, None, None, None
        ),
    ]
    return data.Transaction(
        meta, txn_date, "*", None, narration, data.EMPTY_SET, data.EMPTY_SET, postings
    )
