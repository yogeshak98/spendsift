"""Opening detection over parsed entries: legacy shapes, earliest wins,
first postings, per-account statuses and paired balance assertions.

Every journal here is synthetic and parsed from a string."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from beancount import loader
from beancount.core import data

from backend.app.models.domain.accounts import INVESTMENT_MUTUAL_FUND, holding_account
from backend.app.service.ledger.openings.detect import (
    account_statuses,
    first_postings,
    is_opening_txn,
    opening_records,
)

_HEADER = 'option "operating_currency" "INR"\n2000-01-01 commodity INR\n'


def _entries(body: str) -> list:
    entries, errors, _ = loader.load_string(_HEADER + body)
    assert errors == [], errors
    return entries


def _txn(entries: list, narration: str) -> data.Transaction:
    [txn] = [
        e
        for e in entries
        if isinstance(e, data.Transaction) and e.narration == narration
    ]
    return txn


# ── legacy shapes ────────────────────────────────────────────────────────────
_LEGACY = """
2020-01-01 open Assets:Checking:Acme INR
2020-01-01 open Liabilities:People:Asha INR
2020-01-01 open Liabilities:People:Bala INR
2020-01-01 open Liabilities:People:Chitra INR
2020-01-01 open Liabilities:CreditCard:Acme1234 INR
2020-01-01 open Assets:Investment:FD:Acme INR
2020-01-01 open Assets:Investment:MutualFund INR
2020-01-01 open Expenses:Investment:Charges:Acme INR
2020-01-01 open Equity:Opening-Balances INR

2025-03-31 * "Initial Balance"
  Assets:Checking:Acme        52000.00 INR
  Equity:Opening-Balances    -52000.00 INR

2025-12-01 * "Liability Reset"
  txn-id: "lr-2025"
  Liabilities:People:Asha      1200.00 INR
  Liabilities:People:Bala      -450.50 INR
  Liabilities:People:Chitra     300.00 INR
  Equity:Opening-Balances    -1049.50 INR

2025-04-15 * "Opening balance: Acme 1234"
  Liabilities:CreditCard:Acme1234  -4210.37 INR
  Equity:Opening-Balances           4210.37 INR

2025-03-31 * "FD estimate"
  Assets:Investment:FD:Acme  100000.00 INR
  Equity:Opening-Balances   -100000.00 INR

2025-06-05 * "MF Purchase"
  Assets:Investment:MutualFund         4999.75 INR
  Expenses:Investment:Charges:Acme        0.25 INR
  Equity:Opening-Balances             -5000.00 INR
"""


def test_legacy_shapes_are_openings():
    entries = _entries(_LEGACY)
    records = opening_records(entries)

    # (a)-(d) open their accounts; (e), an equity-funded purchase with an
    # Expenses: charge leg, opens nothing, not even its asset leg.
    assert set(records) == {
        "Assets:Checking:Acme",
        "Liabilities:People:Asha",
        "Liabilities:People:Bala",
        "Liabilities:People:Chitra",
        "Liabilities:CreditCard:Acme1234",
        "Assets:Investment:FD:Acme",
    }
    assert INVESTMENT_MUTUAL_FUND not in records
    assert is_opening_txn(_txn(entries, "MF Purchase")) is False

    # Each record carries its own posting, never the equity leg's figure.
    amounts = {a: r.ledger_amount for a, r in records.items()}
    assert amounts == {
        "Assets:Checking:Acme": Decimal("52000.00"),
        "Liabilities:People:Asha": Decimal("1200.00"),
        "Liabilities:People:Bala": Decimal("-450.50"),
        "Liabilities:People:Chitra": Decimal("300.00"),
        "Liabilities:CreditCard:Acme1234": Decimal("-4210.37"),
        "Assets:Investment:FD:Acme": Decimal("100000.00"),
    }

    reset = _txn(entries, "Liability Reset")
    for person in ("Asha", "Bala", "Chitra"):
        rec = records[f"Liabilities:People:{person}"]
        assert rec.shared is True
        assert rec.source == "legacy"
        assert rec.txn_id == "lr-2025"  # from the transaction's metadata
        assert rec.entry is reset
        assert rec.date == date(2025, 12, 1)

    initial = records["Assets:Checking:Acme"]
    assert initial.shared is False
    assert initial.txn_id is None  # a legacy entry written without one
    assert initial.source == "legacy"
    assert initial.narration == "Initial Balance"
    assert records["Liabilities:CreditCard:Acme1234"].shared is False


@pytest.mark.parametrize(
    "legs",
    [
        # An Income: leg, another Equity: account, a child of the opening
        # equity account: none of them is an opening.
        "  Assets:Checking:Acme  10.00 INR\n  Income:Interest  -5.00 INR\n"
        "  Equity:Opening-Balances  -5.00 INR\n",
        "  Assets:Checking:Acme  10.00 INR\n  Equity:Adjustments  -5.00 INR\n"
        "  Equity:Opening-Balances  -5.00 INR\n",
        "  Assets:Checking:Acme  10.00 INR\n  Equity:Opening-Balances:Old  -10.00 INR\n",
        # No equity leg at all: an ordinary transfer.
        "  Assets:Checking:Acme  10.00 INR\n  Liabilities:People:Asha  -10.00 INR\n",
    ],
)
def test_non_opening_shapes(legs):
    body = (
        "2020-01-01 open Assets:Checking:Acme INR\n"
        "2020-01-01 open Liabilities:People:Asha INR\n"
        "2020-01-01 open Income:Interest INR\n"
        "2020-01-01 open Equity:Adjustments INR\n"
        "2020-01-01 open Equity:Opening-Balances INR\n"
        "2020-01-01 open Equity:Opening-Balances:Old INR\n\n"
        '2025-04-01 * "Odd"\n' + legs
    )
    entries = _entries(body)
    assert is_opening_txn(_txn(entries, "Odd")) is False
    assert opening_records(entries) == {}


# ── earliest wins, first posting ─────────────────────────────────────────────
_TWO_OPENINGS = """
2020-01-01 open Assets:Checking:Acme INR
2020-01-01 open Expenses:Food INR
2020-01-01 open Equity:Opening-Balances INR

2025-05-13 * "Payout booked against equity"
  Assets:Checking:Acme        2500.00 INR
  Equity:Opening-Balances    -2500.00 INR

2025-04-07 * "Groceries"
  Assets:Checking:Acme        -640.00 INR
  Expenses:Food                640.00 INR

2025-03-31 * "Initial Balance"
  Assets:Checking:Acme       10000.00 INR
  Equity:Opening-Balances   -10000.00 INR
"""


def test_earliest_opening_wins():
    record = opening_records(_entries(_TWO_OPENINGS))["Assets:Checking:Acme"]
    assert record.date == date(2025, 3, 31)
    assert record.ledger_amount == Decimal("10000.00")
    assert record.narration == "Initial Balance"


def test_first_posting_ignores_opening():
    firsts = first_postings(_entries(_TWO_OPENINGS))
    # Not 2025-03-31 (the opening) and not 2025-05-13 (opening-shaped too).
    assert firsts["Assets:Checking:Acme"] == date(2025, 4, 7)
    assert firsts["Expenses:Food"] == date(2025, 4, 7)
    # The equity account is posted only inside openings.
    assert "Equity:Opening-Balances" not in firsts


# ── statuses ─────────────────────────────────────────────────────────────────
_STATUSES = """
2020-01-01 open Assets:Checking:Zeta INR
2020-01-01 open Assets:Checking:Alpha INR
2020-01-01 open Liabilities:CreditCard:Acme INR
  opening: "zero"
2020-01-01 open Liabilities:Loan:Home INR
2020-01-01 open Assets:Receivable:Bob INR
2020-01-01 open Assets:Cash INR
2020-01-01 open Assets:Checking:Old INR
2020-01-01 open Assets:Transfers:InTransit INR
2020-01-01 open Expenses:Food INR
2020-01-01 open Equity:Opening-Balances INR

2025-03-31 * "Initial Balance"
  Assets:Checking:Zeta        8000.00 INR
  Equity:Opening-Balances    -8000.00 INR

2025-04-07 * "Groceries"
  Assets:Checking:Zeta        -300.00 INR
  Expenses:Food                300.00 INR

2025-06-01 * "Card spend"
  Liabilities:CreditCard:Acme -900.00 INR
  Expenses:Food                900.00 INR

2025-05-10 * "Lent to Bob"
  Assets:Receivable:Bob        700.00 INR
  Assets:Transfers:InTransit  -700.00 INR

2025-04-20 * "Cash spend"
  Assets:Cash                 -150.00 INR
  Expenses:Food                150.00 INR

2025-02-02 * "Old account activity"
  Assets:Checking:Old         -50.00 INR
  Expenses:Food                50.00 INR

2025-07-01 close Assets:Checking:Old
"""


@pytest.mark.parametrize(
    ("today", "default_date"),
    [
        # Neither is in the FY of the real current date, so a status that
        # read the wall clock instead of `today` gives another default.
        (date(2028, 1, 15), date(2027, 3, 31)),
        (date(2025, 3, 31), date(2024, 3, 31)),
    ],
)
def test_statuses(today, default_date):
    rows = account_statuses(_entries(_STATUSES), today=today)
    by = {r.account: r for r in rows}

    # Kind order, then by account; the closed, suspense, expense and equity
    # accounts are not considered.
    assert [(r.kind, r.account) for r in rows] == [
        ("bank", "Assets:Checking:Alpha"),
        ("bank", "Assets:Checking:Zeta"),
        ("card", "Liabilities:CreditCard:Acme"),
        ("loan", "Liabilities:Loan:Home"),
        ("person", "Assets:Receivable:Bob"),
        ("cash", "Assets:Cash"),
    ]
    assert {a: r.status for a, r in by.items()} == {
        "Assets:Checking:Alpha": "empty",
        "Assets:Checking:Zeta": "opening",
        # Zero beats missing: the card has postings but is marked zero.
        "Liabilities:CreditCard:Acme": "zero",
        "Liabilities:Loan:Home": "empty",
        "Assets:Receivable:Bob": "missing",
        "Assets:Cash": "missing",
    }
    assert {a for a, r in by.items() if r.warn} == {
        "Assets:Receivable:Bob",
        "Assets:Cash",
    }

    opened = by["Assets:Checking:Zeta"]
    assert opened.first_posting == date(2025, 4, 7)  # not the opening's date
    assert opened.suggested_date == date(2025, 3, 31)
    assert opened.opening is not None
    assert opened.opening.ledger_amount == "8000.00"
    assert opened.opening.amount == "8000.00"
    assert opened.opening.direction == "have"
    assert opened.opening.source == "legacy"
    assert opened.directions == ["have", "owe"]

    # missing: the date rule applied to first_posting.
    bob = by["Assets:Receivable:Bob"]
    assert bob.first_posting == date(2025, 5, 10)
    assert (bob.suggested_date, bob.april) == (date(2025, 5, 9), False)
    assert bob.opening is None
    assert bob.directions == ["owed_to_me", "owe"]
    cash = by["Assets:Cash"]
    assert (cash.suggested_date, cash.april) == (date(2025, 3, 31), True)

    # empty and zero: 31 Mar before the FY containing `today`.
    for account in ("Assets:Checking:Alpha", "Liabilities:Loan:Home"):
        assert by[account].suggested_date == default_date
        assert by[account].april is True
        assert by[account].first_posting is None
    card = by["Liabilities:CreditCard:Acme"]
    assert card.suggested_date == default_date
    assert card.first_posting == date(2025, 6, 1)


# ── paired balance and app-written source ────────────────────────────────────
_PAIRED = """
2020-01-01 open Assets:Checking:Acme INR
2020-01-01 open Assets:Checking:Beta INR
2020-01-01 open Assets:Checking:Gamma INR
2020-01-01 open Equity:Opening-Balances INR

2026-03-31 * "Opening balance"
  txn-id: "op-acme"
  opening-source: "statement"
  Assets:Checking:Acme        12500.00 INR
  Equity:Opening-Balances    -12500.00 INR

2026-04-01 balance Assets:Checking:Acme 12500.00 INR
  opening-txn: "op-acme"

2026-03-31 * "Opening balance"
  txn-id: "op-beta"
  opening-source: "manual"
  Assets:Checking:Beta          400.00 INR
  Equity:Opening-Balances      -400.00 INR

2026-04-01 balance Assets:Checking:Beta 400.00 INR

2026-04-01 balance Assets:Checking:Gamma 75.00 INR
  opening-txn: "op-beta"

2026-03-31 * "Opening balance"
  txn-id: "op-gamma"
  opening-source: "invented"
  Assets:Checking:Gamma          75.00 INR
  Equity:Opening-Balances       -75.00 INR
"""


def test_paired_balance_detected():
    records = opening_records(_entries(_PAIRED))
    acme = records["Assets:Checking:Acme"]
    assert acme.balance is not None
    assert acme.balance.meta["opening-txn"] == "op-acme"
    assert acme.source == "statement"
    # An ordinary assertion with no opening-txn is not paired.
    assert records["Assets:Checking:Beta"].balance is None
    assert records["Assets:Checking:Beta"].source == "manual"
    # Gamma's assertion names another opening's txn-id: not Gamma's pair.
    assert records["Assets:Checking:Gamma"].balance is None
    # An unknown opening-source value reads as legacy.
    assert records["Assets:Checking:Gamma"].source == "legacy"

    rows = {
        r.account: r
        for r in account_statuses(_entries(_PAIRED), today=date(2028, 1, 15))
    }
    acme_opening = rows["Assets:Checking:Acme"].opening
    assert acme_opening is not None
    assert acme_opening.balance_check is True
    assert acme_opening.txn_id == "op-acme"
    beta_opening = rows["Assets:Checking:Beta"].opening
    assert beta_opening is not None
    assert beta_opening.balance_check is False


def test_owed_opening_reads_as_magnitude_and_direction():
    rows = {
        r.account: r
        for r in account_statuses(_entries(_LEGACY), today=date(2028, 1, 15))
    }
    card = rows["Liabilities:CreditCard:Acme1234"].opening
    assert card is not None
    assert (card.amount, card.ledger_amount, card.direction) == (
        "4210.37",
        "-4210.37",
        "owe",
    )
    bala = rows["Liabilities:People:Bala"].opening
    assert bala is not None
    assert (bala.amount, bala.direction, bala.shared) == ("450.50", "owe", True)
    asha = rows["Liabilities:People:Asha"].opening
    assert asha is not None
    assert asha.direction == "owed_to_me"


# ── late openings ────────────────────────────────────────────────────────────
def _people_journal(reset_date: str) -> str:
    return f"""
2020-01-01 open Assets:Checking:Acme INR
2020-01-01 open Liabilities:People:Asha INR
2020-01-01 open Equity:Opening-Balances INR

2025-03-31 * "Initial Balance"
  Assets:Checking:Acme        9000.00 INR
  Equity:Opening-Balances    -9000.00 INR

2025-05-10 * "Paid for Asha"
  Liabilities:People:Asha      600.00 INR
  Assets:Checking:Acme        -600.00 INR

{reset_date} * "Liability Reset"
  Liabilities:People:Asha      250.00 INR
  Equity:Opening-Balances     -250.00 INR
"""


def test_late_opening_warns():
    """A re-statement dated after the account's first activity is still its
    opening (it can be corrected), but the activity before it has no
    opening, so the row warns."""
    rows = {
        r.account: r
        for r in account_statuses(
            _entries(_people_journal("2025-12-01")), today=date(2028, 1, 15)
        )
    }
    asha = rows["Liabilities:People:Asha"]
    assert asha.status == "opening"
    assert asha.opening is not None
    assert asha.opening.date == date(2025, 12, 1)
    assert asha.first_posting == date(2025, 5, 10)
    assert asha.warn is True
    # The bank's 31 Mar opening precedes its first activity: no warning.
    bank = rows["Assets:Checking:Acme"]
    assert (bank.status, bank.warn) == ("opening", False)
    assert bank.first_posting == date(2025, 5, 10)
    # A correction must move the opening before the earlier activity: the
    # suggestion is the date rule on first_posting, not the opening's date.
    assert (asha.suggested_date, asha.april) == (date(2025, 5, 9), False)
    assert (bank.suggested_date, bank.april) == (date(2025, 3, 31), True)


def test_late_opening_suggested_date_follows_the_date_rule():
    """April activity before a late bank opening suggests 31 Mar; a card's
    suggestion is always the day before its first posting."""
    body = """
2020-01-01 open Assets:Checking:Acme INR
2020-01-01 open Liabilities:CreditCard:Zeta INR
2020-01-01 open Expenses:Food INR
2020-01-01 open Equity:Opening-Balances INR

2025-04-12 * "Groceries"
  Assets:Checking:Acme         -40.00 INR
  Liabilities:CreditCard:Zeta  -60.00 INR
  Expenses:Food                100.00 INR

2025-09-01 * "Restated"
  Assets:Checking:Acme         500.00 INR
  Liabilities:CreditCard:Zeta -700.00 INR
  Equity:Opening-Balances      200.00 INR
"""
    rows = {
        r.account: r for r in account_statuses(_entries(body), today=date(2026, 1, 15))
    }
    bank, card = rows["Assets:Checking:Acme"], rows["Liabilities:CreditCard:Zeta"]
    assert (bank.warn, bank.suggested_date, bank.april) == (
        True,
        date(2025, 3, 31),
        True,
    )
    assert (card.warn, card.suggested_date) == (True, date(2025, 4, 11))
    assert all(r.suggested_date < r.first_posting for r in (bank, card))


def test_same_day_opening_is_not_late():
    rows = {
        r.account: r
        for r in account_statuses(
            _entries(_people_journal("2025-05-10")), today=date(2028, 1, 15)
        )
    }
    asha = rows["Liabilities:People:Asha"]
    assert asha.status == "opening"
    assert asha.first_posting == asha.opening.date == date(2025, 5, 10)
    assert asha.warn is False


def test_equity_funded_mf_holding_is_not_a_row():
    """An equity-funded MF purchase with no charge leg is opening-shaped,
    but MF and holding accounts never take an opening: no row."""
    holding = holding_account("MF100001")
    body = f"""
2020-01-01 open Assets:Checking:Acme INR
2020-01-01 open {INVESTMENT_MUTUAL_FUND} INR
2020-01-01 open {holding}
2020-01-01 open Equity:Opening-Balances INR
2000-01-01 commodity MF100001

2025-06-05 * "MF Purchase"
  {INVESTMENT_MUTUAL_FUND}         5000.00 INR
  Equity:Opening-Balances         -5000.00 INR

2025-06-05 * "MF lot"
  {holding}   10 MF100001 {{500.00 INR}}
  Equity:Opening-Balances         -5000.00 INR
"""
    entries = _entries(body)
    # The purchases are opening-shaped, so detection does record them ...
    assert {INVESTMENT_MUTUAL_FUND, holding} <= set(opening_records(entries))
    # ... but the statuses never consider those accounts.
    rows = account_statuses(entries, today=date(2028, 1, 15))
    assert [r.account for r in rows] == ["Assets:Checking:Acme"]
