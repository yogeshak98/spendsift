"""The configured clearing account for credit-card bill payments.

Every site that books a card bill payment (the bank-side debit or the
card-side credit) reads it here, at call time, so all of them follow
`Settings.card_clearing_account` together and none spells the account out.
"""

from __future__ import annotations

from backend.app.core.settings import get_settings


def card_clearing_account() -> str:
    """The account both legs of a card bill payment book against."""
    return get_settings().card_clearing_account
