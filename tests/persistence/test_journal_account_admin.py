from __future__ import annotations

from datetime import date

import pytest

from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.persistence.journal import Ledger


def _ledger(tmp_path) -> Ledger:
    led = Ledger(tmp_path)
    led.bootstrap()
    return led


def test_open_named_accounts_writes_open_directives(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts(["Assets:Checking:Axis", "Expenses:Health"])
    main = led.main_file.read_text(encoding="utf-8")
    assert "open Assets:Checking:Axis INR" in main
    assert "open Expenses:Health INR" in main
    assert led.validate() == []


def test_open_named_accounts_skips_already_open(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts(["Assets:Checking:Axis"])
    before = led.main_file.read_text(encoding="utf-8")
    led.open_named_accounts(["Assets:Checking:Axis"])
    after = led.main_file.read_text(encoding="utf-8")
    assert before == after  # idempotent, no duplicate directive


def test_open_named_accounts_empty_is_noop(tmp_path):
    led = _ledger(tmp_path)
    before = led.main_file.read_text(encoding="utf-8")
    led.open_named_accounts([])
    assert led.main_file.read_text(encoding="utf-8") == before


def _post(led: Ledger, acct_a: str, acct_b: str) -> None:
    from beancount.core import data
    from beancount.core.amount import Amount
    from beancount.core.number import D

    txn = data.Transaction(
        meta={"txn-id": "t1"},
        date=date(2026, 1, 5),
        flag="*",
        payee=None,
        narration="test",
        tags=frozenset(),
        links=frozenset(),
        postings=[
            data.Posting(acct_a, Amount(D("100"), "INR"), None, None, None, None),
            data.Posting(acct_b, Amount(D("-100"), "INR"), None, None, None, None),
        ],
    )
    led.append([txn])


def test_rename_updates_open_and_postings(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts(["Assets:Checking:HDFC", "Expenses:Food"])
    _post(led, "Expenses:Food", "Assets:Checking:HDFC")
    count = led.rename_account("Assets:Checking:HDFC", "Assets:Checking:HDFC-Salary")
    main = led.main_file.read_text(encoding="utf-8")
    year = (led.journal_dir / "2026.beancount").read_text(encoding="utf-8")
    assert "open Assets:Checking:HDFC-Salary INR" in main
    assert "open Assets:Checking:HDFC INR" not in main
    assert "Assets:Checking:HDFC-Salary" in year
    assert count >= 2  # open directive + at least one posting
    assert led.validate() == []


def test_rename_does_not_touch_prefix_sibling(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts(["Assets:Checking:HDFC", "Assets:Checking:HDFCBank"])
    led.rename_account("Assets:Checking:HDFC", "Assets:Checking:HDFC-Salary")
    main = led.main_file.read_text(encoding="utf-8")
    assert "open Assets:Checking:HDFCBank INR" in main  # sibling untouched
    assert "open Assets:Checking:HDFC-Salary INR" in main


def test_rename_does_not_touch_child_account(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts(["Assets:Checking:HDFC", "Assets:Checking:HDFC:Sub"])
    led.rename_account("Assets:Checking:HDFC", "Assets:Checking:HDFC-Salary")
    main = led.main_file.read_text(encoding="utf-8")
    assert "open Assets:Checking:HDFC:Sub INR" in main  # child untouched
    assert "open Assets:Checking:HDFC-Salary INR" in main  # target WAS renamed
    assert "open Assets:Checking:HDFC INR" not in main  # old name gone


def test_rename_rolls_back_on_invalidity(tmp_path):
    # Renaming onto a child path would leave an account opened under a now-renamed
    # parent only if validation fails; force invalidity by renaming to a malformed
    # token the validate() step rejects, then assert the snapshot was restored.
    led = _ledger(tmp_path)
    led.open_named_accounts(["Assets:Checking:HDFC"])
    before = led.main_file.read_text(encoding="utf-8")
    with pytest.raises(LedgerValidationError):
        led.rename_account("Assets:Checking:HDFC", "Assets:checking:bad")
    assert led.main_file.read_text(encoding="utf-8") == before  # rolled back


def test_rename_rolls_back_year_file_on_invalidity(tmp_path):
    # Verify that when rename_account fails validation, both main and year
    # files are fully restored to their pre-rename state.
    led = _ledger(tmp_path)
    led.open_named_accounts(["Assets:Checking:HDFC", "Expenses:Food"])
    _post(led, "Expenses:Food", "Assets:Checking:HDFC")
    main_before = led.main_file.read_text(encoding="utf-8")
    year_file = led.journal_dir / "2026.beancount"
    year_before = year_file.read_text(encoding="utf-8")
    with pytest.raises(LedgerValidationError):
        led.rename_account("Assets:Checking:HDFC", "Assets:checking:bad")
    # Assert both files are byte-identical to pre-rename state
    assert led.main_file.read_text(encoding="utf-8") == main_before
    assert year_file.read_text(encoding="utf-8") == year_before


def test_account_balance_empty_for_unused_account(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts(["Assets:Checking:Old"])
    inv = led.account_balance("Assets:Checking:Old")
    assert inv.is_empty()


def test_account_balance_nonzero_when_holding(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts(["Assets:Checking:Old", "Income:Salary"])
    _post(led, "Assets:Checking:Old", "Income:Salary")  # +100 INR into Old
    inv = led.account_balance("Assets:Checking:Old")
    assert not inv.is_empty()


def test_close_account_appends_close_directive(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts(["Assets:Checking:Old"])
    led.close_account("Assets:Checking:Old", date(2026, 6, 29))
    year = (led.journal_dir / "2026.beancount").read_text(encoding="utf-8")
    assert "2026-06-29 close Assets:Checking:Old" in year
    assert led.validate() == []
