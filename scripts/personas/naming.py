"""The app account each persona account imports into.

The persona import templates (`tests/fixtures/personas/templates/`) name
accounts by one rule, and the checker reads balances under the same names:

- a bank: `Assets:Checking:<Institution>-<last4>`;
- a card: `Liabilities:CreditCard:<Institution><Product>-<last4>`;
- a broker: `Assets:Broker:<Institution>`, its platform cash (wallet).

A Groww MF order lands in the app's MF clearing account and its units in a
per-scheme holding account; stock units land in `Assets:Investment:Stock:<SYM>`
whichever broker holds them.
"""

from __future__ import annotations

from scripts.personas.model import Account

OPENING_EQUITY = "Equity:Opening-Balances"
MF_HOLDING_PREFIX = "Assets:Investment:MutualFund:Groww:MF"
STOCK_HOLDING_PREFIX = "Assets:Investment:Stock:"
# The platform cash accounts QA and the live demo configure, through the
# app's own setting (`Settings.platform_cash_accounts`), so a Zerodha
# funding transfer lands in its wallet.
PLATFORM_ENV = "SPENDSIFT_PLATFORM_CASH_ACCOUNTS"
PLATFORM_CASH = {"groww": "Assets:Broker:Groww", "zerodha": "Assets:Broker:Zerodha"}


def account_name(account: Account) -> str:
    """The naming-rule account for a persona account."""
    if account.kind == "bank":
        return f"Assets:Checking:{account.institution}-{account.last4}"
    if account.kind == "card":
        product = account.product.replace(" ", "")
        return f"Liabilities:CreditCard:{account.institution}{product}-{account.last4}"
    return f"Assets:Broker:{account.institution}"


def platform_key(account: Account) -> str:
    """The `Settings.platform_cash_accounts` key of a broker account."""
    return account.institution.lower()
