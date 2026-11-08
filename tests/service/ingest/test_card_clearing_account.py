"""Card bill payments clear through `Settings.card_clearing_account`.

The bank-side debit and the card-side credit of one bill payment both book
against this account, so the pair nets to zero there instead of either side
having to guess the other's account.
"""

from __future__ import annotations

import pytest

from backend.app.core import settings
from backend.app.service.ingest.card_accounts import card_clearing_account


@pytest.fixture(autouse=True)
def _fresh_settings(request):
    settings.get_settings.cache_clear()
    request.addfinalizer(settings.get_settings.cache_clear)


def test_default_card_clearing_account(monkeypatch):
    monkeypatch.delenv("SPENDSIFT_CARD_CLEARING_ACCOUNT", raising=False)
    assert card_clearing_account() == "Assets:Transfer:CreditCard"


def test_card_clearing_account_follows_the_env_override(monkeypatch):
    monkeypatch.setenv("SPENDSIFT_CARD_CLEARING_ACCOUNT", "Assets:Test:Clear")
    settings.get_settings.cache_clear()
    assert card_clearing_account() == "Assets:Test:Clear"
