"""Which Net Worth · Overview group an account belongs to.

Pure naming rules over the account tree. Asset groups are Investments,
Property, Bank & cash and Other (everything else); liability groups are Loans,
Credit cards, People and Other liabilities. A group with no accounts is never
shown, so a user without (say) property simply has no Property card.
"""

from __future__ import annotations

import re

from backend.app.models.domain.accounts import (
    BANK_PREFIXES,
    CASH,
    CREDIT_CARD_PREFIX,
)
from backend.app.service.reports.health.ledger_view import PREPAID_TAX_PREFIXES
from backend.app.service.reports.liabilities.meta_keys import LOAN_PREFIX

ASSET_GROUPS: list[tuple[str, str]] = [
    ("investments", "Investments"),
    ("property", "Property"),
    ("bank", "Bank & cash"),
    ("other", "Other assets"),
]
LIABILITY_GROUPS: list[tuple[str, str]] = [
    ("loans", "Loans & EMIs"),
    ("cards", "Credit cards"),
    ("people", "People"),
    ("other_liabilities", "Other liabilities"),
]
LABELS = dict(ASSET_GROUPS + LIABILITY_GROUPS)

PEOPLE_PREFIX = "Liabilities:People:"
INVESTMENT_PREFIX = "Assets:Investment:"
DEBT_PREFIX = "Assets:Debt:"
NPS_ROOT = "Assets:NPS"
_PROPERTY_PREFIXES = (
    "Assets:House:",
    "Assets:Property:",
    "Assets:Investment:RealEstate",
)
_HUMP = re.compile(r"(?<=[a-z])(?=[A-Z])")


def is_prepaid_tax(account: str) -> bool:
    return account.startswith(PREPAID_TAX_PREFIXES)


def group_of(account: str) -> str | None:
    """The group id for a counted account; None for the accounts net worth
    ignores (prepaid tax, and roots that are neither assets nor liabilities)."""
    if account.startswith("Assets:"):
        if is_prepaid_tax(account):
            return None
        if account.startswith(_PROPERTY_PREFIXES):
            return "property"
        if (
            account.startswith(INVESTMENT_PREFIX)
            or account.startswith(DEBT_PREFIX)
            or account == NPS_ROOT
            or account.startswith(NPS_ROOT + ":")
        ):
            return "investments"
        if account.startswith(BANK_PREFIXES) or account == CASH:
            return "bank"
        return "other"
    if account.startswith("Liabilities:"):
        if account.startswith(LOAN_PREFIX):
            return "loans"
        if account.startswith(CREDIT_CARD_PREFIX):
            return "cards"
        if account.startswith(PEOPLE_PREFIX):
            return "people"
        return "other_liabilities"
    return None


def vehicle_of(account: str) -> tuple[str, str] | None:
    """(key, label) of the investment vehicle an Investments-group account
    belongs to: `Assets:Investment:MutualFund:...` -> ("mutual_fund",
    "Mutual Fund"), `Assets:Debt:PPF` -> ("ppf", "PPF"), `Assets:NPS` ->
    ("nps", "NPS"). None for anything else."""
    if account.startswith(INVESTMENT_PREFIX):
        segment = account[len(INVESTMENT_PREFIX) :].split(":", 1)[0]
    elif account.startswith(DEBT_PREFIX):
        segment = account[len(DEBT_PREFIX) :].split(":", 1)[0]
    elif account == NPS_ROOT or account.startswith(NPS_ROOT + ":"):
        segment = "NPS"
    else:
        return None
    if not segment:
        return None
    # All-caps segments (FD, PPF, NPS) pass through; CamelCase is split.
    return _HUMP.sub("_", segment).lower(), _HUMP.sub(" ", segment)
