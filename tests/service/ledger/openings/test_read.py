"""`read.statuses` over a real (synthetic, tmp_path) journal."""

from __future__ import annotations

from datetime import date

from backend.app.persistence.journal import Ledger
from backend.app.service.ledger.openings import keys, read

_HEADER = 'option "operating_currency" "INR"\n\n2000-01-01 commodity INR\n'

_OPENS = """
2020-01-01 open Assets:Checking:Acme INR
2020-01-01 open Assets:Cash INR
2020-01-01 open Expenses:Food INR
2020-01-01 open Equity:Opening-Balances INR

2025-03-31 * "Initial Balance"
  Assets:Checking:Acme        5000.00 INR
  Equity:Opening-Balances    -5000.00 INR

2025-04-03 * "Groceries"
  Assets:Checking:Acme        -120.00 INR
  Expenses:Food                120.00 INR
"""

_CASH_SPEND = """
2025-05-10 * "Cash spend"
  Assets:Cash                  -80.00 INR
  Expenses:Food                 80.00 INR
"""


def _ledger(tmp_path, body: str) -> Ledger:
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap(seed_accounts=())
    ledger.main_file.write_text(_HEADER + body, encoding="utf-8")
    assert ledger.load(fresh=True).errors == []
    return ledger


def test_statuses_response_counts_and_warning(tmp_path):
    resp = read.statuses(
        _ledger(tmp_path, _OPENS + _CASH_SPEND), today=date(2028, 1, 15)
    )
    assert resp.missing_count == 1
    assert resp.warning == keys.WARNING_TEXT
    assert [(r.account, r.status) for r in resp.accounts] == [
        ("Assets:Checking:Acme", "opening"),
        ("Assets:Cash", "missing"),
    ]
    assert all(r.account_display for r in resp.accounts)


def test_no_missing_account_means_no_warning(tmp_path):
    # Assets:Cash has no postings: "empty", which never warns.
    resp = read.statuses(_ledger(tmp_path, _OPENS), today=date(2028, 1, 15))
    assert resp.missing_count == 0
    assert resp.warning is None
    assert {r.account: r.status for r in resp.accounts}["Assets:Cash"] == "empty"
    assert not any(r.warn for r in resp.accounts)


def test_account_display_uses_commodity_names(tmp_path):
    body = (
        '2000-01-01 commodity FD777\n  name: "Acme Bank FD"\n'
        "2020-01-01 open Assets:Investment:FD:FD777 INR\n"
    )
    resp = read.statuses(_ledger(tmp_path, body), today=date(2028, 1, 15))
    [row] = resp.accounts
    assert row.account == "Assets:Investment:FD:FD777"
    assert row.account_display == "Assets:Investment:FD:Acme Bank FD"


_LATE_RESET = """
2020-01-01 open Liabilities:People:Asha INR

2025-05-10 * "Paid for Asha"
  Liabilities:People:Asha      600.00 INR
  Assets:Checking:Acme        -600.00 INR

2025-12-01 * "Liability Reset"
  Liabilities:People:Asha      250.00 INR
  Equity:Opening-Balances     -250.00 INR
"""


def test_late_opening_is_counted_and_warns(tmp_path):
    resp = read.statuses(
        _ledger(tmp_path, _OPENS + _LATE_RESET), today=date(2028, 1, 15)
    )
    rows = {r.account: r for r in resp.accounts}
    asha = rows["Liabilities:People:Asha"]
    assert (asha.status, asha.warn) == ("opening", True)
    assert asha.opening is not None  # still correctable
    # Asha is the only warning row: the bank opened before its activity,
    # and Assets:Cash has no postings.
    assert resp.missing_count == 1
    assert resp.warning == keys.WARNING_TEXT
    assert [r.account for r in resp.accounts if r.warn] == ["Liabilities:People:Asha"]


def test_missing_card_is_dated_the_day_before_its_first_posting(tmp_path):
    # A card's first posting on 16 Apr dates its opening 15 Apr, where a
    # bank's first posting on 16 Apr dates it 31 Mar.
    body = """
2020-01-01 open Assets:Checking:Acme INR
2020-01-01 open Liabilities:CreditCard:Zeta INR
2020-01-01 open Expenses:Food INR

2025-04-16 * "Groceries"
  Assets:Checking:Acme        -120.00 INR
  Expenses:Food                120.00 INR

2025-04-16 * "Groceries"
  Liabilities:CreditCard:Zeta -300.00 INR
  Expenses:Food                300.00 INR
"""
    resp = read.statuses(_ledger(tmp_path, body), today=date(2026, 1, 15))
    rows = {r.account: (r.status, r.suggested_date, r.april) for r in resp.accounts}
    assert rows["Assets:Checking:Acme"] == ("missing", date(2025, 3, 31), True)
    assert rows["Liabilities:CreditCard:Zeta"] == (
        "missing",
        date(2025, 4, 15),
        False,
    )


def test_card_opening_april_follows_the_card_cycle(tmp_path):
    # A card opening dated 15 Mar opens the 16 Mar cycle, which contains
    # 1 April; one dated 9 Apr opens a cycle that misses it. A bank opening
    # is "as of April" only on 31 Mar.
    body = """
2020-01-01 open Liabilities:CreditCard:Zeta INR
2020-01-01 open Liabilities:CreditCard:Yolo INR
2020-01-01 open Assets:Checking:Acme INR
2020-01-01 open Equity:Opening-Balances INR

2025-03-15 * "Opening balance"
  Liabilities:CreditCard:Zeta  -100.00 INR
  Equity:Opening-Balances       100.00 INR

2025-04-09 * "Opening balance"
  Liabilities:CreditCard:Yolo  -100.00 INR
  Equity:Opening-Balances       100.00 INR

2025-03-15 * "Opening balance"
  Assets:Checking:Acme          100.00 INR
  Equity:Opening-Balances      -100.00 INR
"""
    resp = read.statuses(_ledger(tmp_path, body), today=date(2026, 1, 15))
    rows = {r.account: (r.status, r.april) for r in resp.accounts}
    assert rows["Liabilities:CreditCard:Zeta"] == ("opening", True)
    assert rows["Liabilities:CreditCard:Yolo"] == ("opening", False)
    assert rows["Assets:Checking:Acme"] == ("opening", False)
