from datetime import date

from tests.conftest import make_txn


def test_append_opens_unknown_accounts(ledger):
    txn = make_txn(
        date(2026, 4, 1), "Trip", "Expenses:Travel:Goa", "Assets:Bank:HDFC", "1000.00"
    )
    ledger.append([txn])
    assert ledger.validate() == []
    assert (
        "open Expenses:Travel:Goa"
        in (ledger.journal_dir / "main.beancount").read_text()
    )
