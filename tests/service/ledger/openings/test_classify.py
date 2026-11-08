"""Opening kinds, allowed directions and the sign rules."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.core.settings import get_settings
from backend.app.models.domain.accounts import (
    EQUITY_OPENING,
    INTRANSIT,
    holding_account,
)
from backend.app.service.ingest.card_accounts import card_clearing_account
from backend.app.service.ingest.platform_accounts import (
    all_platform_clearing_accounts,
)
from backend.app.service.ledger.openings.classify import (
    allowed_directions,
    direction_of,
    ledger_amount,
    opening_kind,
)


def _kind_rows() -> list[tuple[str, str | None]]:
    settings = get_settings()
    emi = settings.card_emi_account
    rows: list[tuple[str, str | None]] = [
        ("Assets:Checking:X", "bank"),
        ("Assets:Bank:X", "bank"),
        ("Liabilities:CreditCard:X", "card"),
        ("Liabilities:Loan:Home", "loan"),
        ("Liabilities:People:A", "person"),
        ("Assets:Receivable:A", "person"),
        ("Assets:Cash", "cash"),
        ("Assets:Investment:FD:X", "other_asset"),
        ("Liabilities:Tax:Due", "other_liability"),
        # Excluded: configured accounts come from settings, never literals.
        (emi, None),
        (f"{emi}:Plan1", None),
        (card_clearing_account(), None),
        ("Assets:Receivable:TDS", None),
        ("Assets:Receivable:AdvanceTax", None),
        (INTRANSIT, None),
        ("Assets:Transfers:Other", None),
        ("Assets:Investment:Stock:ABC", None),
        (holding_account("MF100001"), None),
        ("Assets:Investment:MutualFund", None),
        (EQUITY_OPENING, None),
        ("Expenses:Food", None),
        ("Income:Salary", None),
    ]
    rows += [(a, None) for a in sorted(all_platform_clearing_accounts())]
    rows += [(a, "cash") for a in sorted(settings.platform_cash_accounts.values())]
    return rows


@pytest.mark.parametrize(("account", "kind"), _kind_rows())
def test_opening_kind_table(account, kind):
    assert opening_kind(account) == kind


def test_kind_follows_configured_accounts(monkeypatch):
    """The excluded and cash accounts are read from settings at call time:
    reconfigure them and the kinds move with them."""
    from backend.app.core import settings as settings_module

    monkeypatch.setenv("SPENDSIFT_CARD_EMI_ACCOUNT", "Liabilities:Loan:Instalments")
    monkeypatch.setenv("SPENDSIFT_CARD_CLEARING_ACCOUNT", "Assets:Clearing:Cards")
    monkeypatch.setenv(
        "SPENDSIFT_PLATFORM_CASH_ACCOUNTS", '{"acme": "Assets:Wallet:Acme"}'
    )
    settings_module.get_settings.cache_clear()
    try:
        assert opening_kind("Liabilities:Loan:Instalments:P1") is None
        assert opening_kind("Assets:Clearing:Cards") is None
        assert opening_kind("Assets:Wallet:Acme") == "cash"
        # The default EMI account is now an ordinary loan.
        assert opening_kind("Liabilities:Loan:CardEMI") == "loan"
    finally:
        settings_module.get_settings.cache_clear()


@pytest.mark.parametrize(
    ("account", "directions"),
    [
        ("Liabilities:Loan:Home", ["owe"]),
        ("Liabilities:CreditCard:X", ["owe", "owed_to_me"]),
        ("Assets:Cash", ["have"]),
        ("Assets:Checking:X", ["have", "owe"]),
        ("Liabilities:People:A", ["owed_to_me", "owe"]),
        ("Assets:Investment:FD:X", ["have"]),
        ("Liabilities:Tax:Due", ["owe", "owed_to_me"]),
        ("Expenses:Food", []),
    ],
)
def test_allowed_directions_table(account, directions):
    assert allowed_directions(account) == directions


@pytest.mark.parametrize(
    ("account", "direction", "signed"),
    [
        ("Assets:Checking:X", "have", Decimal("100")),
        ("Assets:Checking:X", "owe", Decimal("-100")),
        ("Liabilities:CreditCard:X", "owe", Decimal("-100")),
        ("Liabilities:CreditCard:X", "owed_to_me", Decimal("100")),
        ("Liabilities:People:A", "owed_to_me", Decimal("100")),
        ("Liabilities:People:A", "owe", Decimal("-100")),
        ("Assets:Receivable:A", "owed_to_me", Decimal("100")),
        ("Assets:Receivable:A", "owe", Decimal("-100")),
    ],
)
def test_ledger_amount_and_direction_round_trip(account, direction, signed):
    got = ledger_amount(account, Decimal("100"), direction)
    assert got == signed
    assert direction_of(account, got) == direction


def test_have_on_a_liability_is_refused():
    with pytest.raises(CodedError) as exc:
        ledger_amount("Liabilities:CreditCard:X", Decimal("100"), "have")
    assert exc.value.code == "E1090"
    assert exc.value.data["account"] == "Liabilities:CreditCard:X"


def test_receivable_plus_reads_owed_to_me_bank_plus_reads_have():
    assert direction_of("Assets:Receivable:A", Decimal("5")) == "owed_to_me"
    assert direction_of("Assets:Checking:X", Decimal("5")) == "have"


@pytest.mark.parametrize(
    ("account", "direction"),
    [
        ("Liabilities:People:A", "owed_to_me"),
        ("Assets:Receivable:A", "owed_to_me"),
        ("Liabilities:CreditCard:X", "owe"),
        ("Liabilities:Loan:Home", "owe"),
        ("Assets:Checking:X", "have"),
    ],
)
def test_zero_reads_as_the_kinds_default_direction(account, direction):
    assert direction_of(account, Decimal("0")) == direction
