from datetime import date
from pathlib import Path

from backend.app.persistence.journal import Ledger
from tests.conftest import make_txn


def _investment_ledger(storage_dir: Path) -> Ledger:
    led = Ledger(
        storage_dir,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    led.bootstrap(seed_accounts=())
    return led


def test_investment_ledger_bootstraps_alongside_main_ledger(storage_dir, ledger):
    inv_ledger = _investment_ledger(storage_dir)

    assert inv_ledger.main_file.name == "investments.beancount"
    assert inv_ledger.main_file.exists()
    assert inv_ledger.main_file != ledger.main_file
    # both files live in the same shared journal dir
    assert inv_ledger.journal_dir == ledger.journal_dir


def test_investment_ledger_appends_and_validates_independently(storage_dir, ledger):
    inv_ledger = _investment_ledger(storage_dir)
    # investment ledger has no seed accounts, so it needs its own accounts opened
    inv_ledger.open_named_accounts(
        [
            "Assets:Investment:MutualFund:Groww:Cash",
            "Assets:Investment:MutualFund:Groww:FOO",
        ]
    )

    txn = make_txn(
        date(2026, 4, 1),
        "Buy FOO",
        "Assets:Investment:MutualFund:Groww:FOO",
        "Assets:Investment:MutualFund:Groww:Cash",
        "100.00",
    )
    inv_ledger.append([txn])

    assert inv_ledger.validate() == []
    year_file = inv_ledger.journal_dir / "investments-2026.beancount"
    assert year_file.exists()
    # main ledger's own year file convention is untouched
    assert not (inv_ledger.journal_dir / "2026.beancount").exists()


def test_investment_ledger_snapshot_and_restore_covers_both_files_independently(
    storage_dir, ledger
):
    inv_ledger = _investment_ledger(storage_dir)
    inv_ledger.open_named_accounts(
        [
            "Assets:Investment:MutualFund:Groww:Cash",
            "Assets:Investment:MutualFund:Groww:FOO",
        ]
    )

    # append to main ledger first
    main_txn = make_txn(
        date(2026, 4, 1),
        "Groceries",
        "Expenses:Food",
        "Assets:Bank:HDFC",
        "100.00",
    )
    ledger.append([main_txn])
    main_text_before = ledger.main_file.read_text(encoding="utf-8")
    main_year_text_before = (ledger.journal_dir / "2026.beancount").read_text(
        encoding="utf-8"
    )

    # snapshot from the investment ledger instance -- it should capture both
    # files' current state since both share the same journal_dir
    snapshot = inv_ledger.snapshot()
    inv_text_before = inv_ledger.main_file.read_text(encoding="utf-8")

    # corrupt investments.beancount only
    inv_ledger.main_file.write_text("this is not valid beancount {{{", encoding="utf-8")
    assert inv_ledger.main_file.read_text(encoding="utf-8") != inv_text_before

    # restore from the snapshot taken via the investment ledger instance
    inv_ledger._restore(snapshot)

    assert inv_ledger.main_file.read_text(encoding="utf-8") == inv_text_before
    # main.beancount and its year file were unaffected by the corruption+restore
    assert ledger.main_file.read_text(encoding="utf-8") == main_text_before
    assert (ledger.journal_dir / "2026.beancount").read_text(
        encoding="utf-8"
    ) == main_year_text_before
