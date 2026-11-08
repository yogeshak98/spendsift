from __future__ import annotations

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.persistence.journal import Ledger
from backend.app.service.ledger.accounts import AccountAdminService


def _svc(tmp_path) -> AccountAdminService:
    led = Ledger(tmp_path)
    led.bootstrap()
    return AccountAdminService(led)


@pytest.mark.parametrize(
    "name",
    [
        "Assets:Checking:HDFC",
        "Expenses:Health",
        "Income:Salary",
        "Liabilities:CreditCard:Axis-Magnus",
        "Equity:Opening-Balances",
    ],
)
def test_valid_names(tmp_path, name):
    assert _svc(tmp_path).is_valid_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "Bogus:Checking",
        "Assets:checking",
        "bogus name",
        "Assets",
        "",
        "Assets:Checking:",
        "Expenses:Travel\n",
    ],
)
def test_invalid_names(tmp_path, name):
    assert _svc(tmp_path).is_valid_name(name) is False


def test_create_partitions_three_buckets(tmp_path):
    svc = _svc(tmp_path)
    existing = svc.list_accounts()[0]
    result = svc.create([existing, "Assets:Checking:Axis", "bogus name"])
    assert result["opened"] == ["Assets:Checking:Axis"]
    assert result["skipped_existing"] == [existing]
    assert result["rejected"] == [
        {"name": "bogus name", "reason": "INVALID_ACCOUNT_NAME"}
    ]
    assert "Assets:Checking:Axis" in svc.list_accounts()


def test_create_all_invalid_writes_nothing(tmp_path):
    svc = _svc(tmp_path)
    before = set(svc.list_accounts())
    result = svc.create(["bogus name", "Assets"])
    assert result["opened"] == []
    assert set(svc.list_accounts()) == before


def test_rename_happy_path(tmp_path):
    svc = _svc(tmp_path)
    svc.create(["Assets:Checking:HDFC"])
    result = svc.rename("Assets:Checking:HDFC", "Assets:Checking:HDFC-Salary")
    assert result["renamed"] == "Assets:Checking:HDFC"
    assert result["to"] == "Assets:Checking:HDFC-Salary"
    assert result["postings_updated"] >= 1
    assert "Assets:Checking:HDFC-Salary" in svc.list_accounts()


def test_rename_malformed_new_name_raises(tmp_path):
    svc = _svc(tmp_path)
    svc.create(["Assets:Checking:HDFC"])
    with pytest.raises(CodedError) as ei:
        svc.rename("Assets:Checking:HDFC", "bad name")
    assert ei.value.code == "E1017"


def test_rename_missing_account_raises_lookup(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(LookupError):
        svc.rename("Assets:Checking:Nope", "Assets:Checking:New")


def test_rename_collision_raises(tmp_path):
    svc = _svc(tmp_path)
    svc.create(["Assets:Checking:HDFC", "Assets:Checking:Axis"])
    with pytest.raises(CodedError) as ei:
        svc.rename("Assets:Checking:HDFC", "Assets:Checking:Axis")
    assert ei.value.code == "E1018"


def test_close_settled_account(tmp_path):
    svc = _svc(tmp_path)
    svc.create(["Assets:Checking:Old"])
    result = svc.close("Assets:Checking:Old")
    assert result["closed"] == "Assets:Checking:Old"
    assert result["as_of"]


def test_close_missing_raises_lookup(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(LookupError):
        svc.close("Assets:Checking:Nope")


def test_close_nonzero_balance_raises(tmp_path):
    from datetime import date as _date
    from beancount.core import data
    from beancount.core.amount import Amount
    from beancount.core.number import D

    svc = _svc(tmp_path)
    svc.create(["Assets:Checking:Old", "Income:Salary"])
    txn = data.Transaction(
        meta={"txn-id": "t1"},
        date=_date(2026, 1, 5),
        flag="*",
        payee=None,
        narration="x",
        tags=frozenset(),
        links=frozenset(),
        postings=[
            data.Posting(
                "Assets:Checking:Old", Amount(D("100"), "INR"), None, None, None, None
            ),
            data.Posting(
                "Income:Salary", Amount(D("-100"), "INR"), None, None, None, None
            ),
        ],
    )
    svc._ledger.append([txn])
    with pytest.raises(CodedError) as ei:
        svc.close("Assets:Checking:Old")
    assert ei.value.code == "E1019"
    assert "100" in ei.value.description


def test_close_holds_the_write_lock_from_balance_check_to_close(tmp_path, monkeypatch):
    """Review m4: no write may land between the zero-balance check and the
    close directive (a posting there would be closed over)."""
    import threading

    from backend.app.persistence.journal import Ledger

    svc = _svc(tmp_path)
    svc.create(["Expenses:OneOff"])
    ledger = svc._ledger
    lock_free_during_check: list[bool] = []
    real_balance = Ledger.account_balance

    def balance(self, account):
        def probe() -> None:
            got = self.write_lock().acquire(blocking=False)
            lock_free_during_check.append(got)
            if got:
                self.write_lock().release()

        t = threading.Thread(target=probe)
        t.start()
        t.join(timeout=5)
        return real_balance(self, account)

    monkeypatch.setattr(Ledger, "account_balance", balance)
    svc.close("Expenses:OneOff")
    assert lock_free_during_check == [False]
    assert ledger.write_lock().acquire(blocking=False)
    ledger.write_lock().release()
