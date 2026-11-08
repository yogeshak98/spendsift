from datetime import date

import pytest

from backend.app.core.errors.ledger import LedgerValidationError
from tests.conftest import make_txn


def test_append_writes_to_year_file_and_includes_it(ledger):
    txn = make_txn(
        date(2026, 4, 1), "Swiggy", "Expenses:Food", "Assets:Bank:HDFC", "412.00"
    )
    ledger.append([txn])

    year_file = ledger.journal_dir / "2026.beancount"
    assert year_file.exists()
    assert "Swiggy" in year_file.read_text(encoding="utf-8")
    assert 'include "2026.beancount"' in ledger.main_file.read_text(encoding="utf-8")
    assert ledger.validate() == []


def test_append_is_recorded_once_per_year_include(ledger):
    ledger.append(
        [make_txn(date(2026, 4, 1), "A", "Expenses:Food", "Assets:Bank:HDFC", "10.00")]
    )
    ledger.append(
        [make_txn(date(2026, 5, 1), "B", "Expenses:Food", "Assets:Bank:HDFC", "20.00")]
    )
    main = ledger.main_file.read_text(encoding="utf-8")
    assert main.count('include "2026.beancount"') == 1


def test_append_unbalanced_is_rejected_and_rolled_back(ledger):
    before = ledger.main_file.read_text(encoding="utf-8")

    from datetime import date as _date
    from decimal import Decimal

    from beancount.core import data
    from beancount.core.amount import Amount

    meta = data.new_metadata("<test>", 0)
    bad = data.Transaction(
        meta,
        _date(2026, 4, 1),
        "*",
        None,
        "Broken",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(
                "Expenses:Food",
                Amount(Decimal("100.00"), "INR"),
                None,
                None,
                None,
                None,
            ),
            data.Posting(
                "Assets:Bank:HDFC",
                Amount(Decimal("-90.00"), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )

    with pytest.raises(LedgerValidationError):
        ledger.append([bad])

    # journal restored: no 2026 file, main unchanged, still valid
    assert not (ledger.journal_dir / "2026.beancount").exists()
    assert ledger.main_file.read_text(encoding="utf-8") == before
    assert ledger.validate() == []


def test_append_multi_year_batch_writes_all_years(ledger):
    ledger.append(
        [
            make_txn(
                date(2024, 1, 1), "Y2024", "Expenses:Food", "Assets:Bank:HDFC", "11.00"
            ),
            make_txn(
                date(2025, 6, 1), "Y2025", "Expenses:Food", "Assets:Bank:HDFC", "22.00"
            ),
            make_txn(
                date(2026, 3, 1), "Y2026", "Expenses:Food", "Assets:Bank:HDFC", "33.00"
            ),
        ]
    )

    main = ledger.main_file.read_text(encoding="utf-8")
    for year, narration in ((2024, "Y2024"), (2025, "Y2025"), (2026, "Y2026")):
        year_file = ledger.journal_dir / f"{year}.beancount"
        assert year_file.exists()
        assert narration in year_file.read_text(encoding="utf-8")
        assert main.count(f'include "{year}.beancount"') == 1
    assert ledger.validate() == []


def test_append_partial_batch_last_invalid_rolls_back_all(ledger):
    before = ledger.main_file.read_text(encoding="utf-8")

    from datetime import date as _date
    from decimal import Decimal

    from beancount.core import data
    from beancount.core.amount import Amount

    meta = data.new_metadata("<test>", 0)
    bad = data.Transaction(
        meta,
        _date(2026, 3, 1),
        "*",
        None,
        "Broken",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(
                "Expenses:Food",
                Amount(Decimal("100.00"), "INR"),
                None,
                None,
                None,
                None,
            ),
            data.Posting(
                "Assets:Bank:HDFC",
                Amount(Decimal("-90.00"), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )

    batch = [
        make_txn(
            date(2024, 1, 1), "Y2024", "Expenses:Food", "Assets:Bank:HDFC", "11.00"
        ),
        make_txn(
            date(2025, 6, 1), "Y2025", "Expenses:Food", "Assets:Bank:HDFC", "22.00"
        ),
        bad,
    ]

    with pytest.raises(LedgerValidationError):
        ledger.append(batch)

    # wholesale restore: no year files for any year in the batch, main byte-identical
    for year in (2024, 2025, 2026):
        assert not (ledger.journal_dir / f"{year}.beancount").exists()
    assert ledger.main_file.read_text(encoding="utf-8") == before
    assert ledger.validate() == []


def test_append_opens_an_account_that_prefixes_an_open_one(ledger):
    ledger.open_named_accounts(["Expenses:Subscriptions:Claude"])
    ledger.append(
        [
            make_txn(
                date(2026, 4, 1),
                "Sub",
                "Expenses:Subscriptions",
                "Assets:Bank:HDFC",
                "10.00",
            )
        ]
    )
    main = ledger.main_file.read_text(encoding="utf-8")
    assert ledger._is_open(main, "Expenses:Subscriptions")
    opens = [
        ln
        for ln in main.splitlines()
        if ln.split()[1:3] == ["open", "Expenses:Subscriptions"]
    ]
    assert len(opens) == 1
    assert ledger.validate() == []


def test_append_returns_snapshot_dir(ledger):
    before = set(ledger.snapshots_dir.iterdir())
    snapshot = ledger.append(
        [make_txn(date(2026, 4, 1), "A", "Expenses:Food", "Assets:Bank:HDFC", "10.00")]
    )

    assert snapshot is not None
    assert snapshot.is_dir()
    assert snapshot.parent == ledger.snapshots_dir
    assert set(ledger.snapshots_dir.iterdir()) - before == {snapshot}
    # The snapshot is the journal as it was before the write.
    snapped_main = snapshot / "journal" / "main.beancount"
    assert snapped_main.is_file()
    assert not (snapshot / "journal" / "2026.beancount").exists()

    after_first = set(ledger.snapshots_dir.iterdir())
    assert ledger.append([]) is None
    assert set(ledger.snapshots_dir.iterdir()) == after_first
