"""Which accounts take an opening, in which directions, and the sign rules."""

from __future__ import annotations

from decimal import Decimal
from typing import NoReturn

from backend.app.core.errors.errors import OPENING_NOT_APPLICABLE
from backend.app.core.settings import get_settings
from backend.app.models.api.openings import OpeningDirection, OpeningKind
from backend.app.models.domain.accounts import (
    BANK_PREFIXES,
    CASH,
    CREDIT_CARD_PREFIX,
    HOLDING_PREFIX,
    INTRANSIT,
    INVESTMENT_MUTUAL_FUND,
)
from backend.app.service.ingest.card_accounts import card_clearing_account
from backend.app.service.ingest.platform_accounts import (
    all_platform_clearing_accounts,
)
from backend.app.service.reports.health.ledger_view import PREPAID_TAX_PREFIXES

# Section order of the statuses response and the checklist screen.
KIND_ORDER: tuple[OpeningKind, ...] = (
    "bank",
    "card",
    "loan",
    "person",
    "cash",
    "other_asset",
    "other_liability",
)

_DIRECTIONS: dict[OpeningKind, list[OpeningDirection]] = {
    "bank": ["have", "owe"],
    "card": ["owe", "owed_to_me"],
    "loan": ["owe"],
    "person": ["owed_to_me", "owe"],
    "cash": ["have"],
    "other_asset": ["have"],
    "other_liability": ["owe", "owed_to_me"],
}

_ASSETS = "Assets"
_LIABILITIES = "Liabilities"
_TRANSFERS = "Assets:Transfers"
_RECEIVABLE = "Assets:Receivable"
_LOAN = "Liabilities:Loan"
_PEOPLE = "Liabilities:People"


def _in_subtree(account: str, root: str) -> bool:
    """`account` is `root` itself or one of its descendants."""
    root = root.rstrip(":")
    return account == root or account.startswith(root + ":")


def _excluded(account: str) -> bool:
    """The balance-sheet accounts that never take an opening: suspense and
    clearing accounts, prepaid tax, and the lot-holding investment trees."""
    settings = get_settings()
    if account in (card_clearing_account(), *all_platform_clearing_accounts()):
        return True
    if account.startswith(PREPAID_TAX_PREFIXES):
        return True
    subtrees = (
        INTRANSIT,
        _TRANSFERS,
        INVESTMENT_MUTUAL_FUND,
        *HOLDING_PREFIX.values(),
        settings.card_emi_account,
    )
    return any(_in_subtree(account, root) for root in subtrees)


def opening_kind(account: str) -> OpeningKind | None:
    """The account's opening kind, or None when it never takes an opening.

    bank: Assets:Checking:*, Assets:Bank:*. card: Liabilities:CreditCard:*.
    loan: Liabilities:Loan:* except the card EMI account and its children.
    person: Liabilities:People:*, and Assets:Receivable:* except the prepaid
    tax prefixes. cash: Assets:Cash and every platform cash account.
    other_asset / other_liability: any other Assets:* / Liabilities:*.
    None: Equity, Income, Expenses; the in-transit account and
    Assets:Transfers:*; the card clearing account; every platform clearing
    account; the prepaid tax prefixes; the mutual-fund and holding subtrees;
    the card EMI subtree. Platform and clearing accounts come from settings.
    """
    root = account.split(":", 1)[0]
    if root not in (_ASSETS, _LIABILITIES) or _excluded(account):
        return None
    if account.startswith(BANK_PREFIXES):
        return "bank"
    if account.startswith(CREDIT_CARD_PREFIX):
        return "card"
    if _in_subtree(account, _LOAN):
        return "loan"
    if _in_subtree(account, _PEOPLE) or _in_subtree(account, _RECEIVABLE):
        return "person"
    if account == CASH or account in get_settings().platform_cash_accounts.values():
        return "cash"
    return "other_asset" if root == _ASSETS else "other_liability"


def allowed_directions(account: str) -> list[OpeningDirection]:
    """The directions the account's kind allows, the default first: bank
    [have, owe]; card [owe, owed_to_me]; loan [owe]; person [owed_to_me,
    owe]; cash [have]; other_asset [have]; other_liability [owe,
    owed_to_me]. Empty when the kind is None."""
    kind = opening_kind(account)
    return [] if kind is None else list(_DIRECTIONS[kind])


def ledger_amount(
    account: str, amount: Decimal, direction: OpeningDirection
) -> Decimal:
    """The signed ledger amount for a magnitude and a direction.

    Assets:*: have -> +, owed_to_me -> +, owe -> -.
    Liabilities:*: owe -> -, owed_to_me -> +; have is refused with
    OPENING_NOT_APPLICABLE (E1090).

    `amount` is a magnitude: a negative one is a programming error (the
    write validates the client's figure first, E1093).
    """
    if amount < 0:
        raise ValueError(f"amount must be a magnitude, got {amount}")
    root = account.split(":", 1)[0]
    if root == _ASSETS:
        return -amount if direction == "owe" else amount
    if root == _LIABILITIES:
        if direction == "have":
            _refuse(account, "a liability cannot be something you have")
        return -amount if direction == "owe" else amount
    _refuse(account, "it is not an asset or liability account")


def direction_of(account: str, ledger_amount: Decimal) -> OpeningDirection:
    """The direction a signed ledger amount reads as (the reverse of
    `ledger_amount`).

    Assets:*: + -> have (owed_to_me for Assets:Receivable:*), - -> owe.
    Liabilities:*: - -> owe, + -> owed_to_me.

    Zero has no sign, so it reads as the kind's default direction (the
    first of `allowed_directions`: owed_to_me on a person account, owe on a
    card or loan, have on a bank or cash account); an account with no kind
    falls back to the sign rule (have on an asset, owe on a liability).
    """
    root = account.split(":", 1)[0]
    if ledger_amount == 0 and root in (_ASSETS, _LIABILITIES):
        defaults = allowed_directions(account)
        if defaults:
            return defaults[0]
    if root == _ASSETS:
        if ledger_amount < 0:
            return "owe"
        return "owed_to_me" if _in_subtree(account, _RECEIVABLE) else "have"
    if root == _LIABILITIES:
        return "owed_to_me" if ledger_amount > 0 else "owe"
    _refuse(account, "it is not an asset or liability account")


def _refuse(account: str, reason: str) -> NoReturn:
    params = {"account": account, "reason": reason}
    raise OPENING_NOT_APPLICABLE.exc(params, data=params)
