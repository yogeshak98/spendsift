from __future__ import annotations

import re
from enum import Enum

OPERATING_CURRENCY = "INR"

# A well-formed beancount account name: a root type, then capitalised components.
ACCOUNT_RE = re.compile(
    r"^(Assets|Liabilities|Equity|Income|Expenses)(:[A-Z0-9][A-Za-z0-9-]*)+$"
)

# Canonical fixed-name accounts (no slugging — literal strings).
CASH = "Assets:Cash"
INTRANSIT = "Assets:Transfers:InTransit"
EQUITY_OPENING = "Equity:Opening-Balances"
EQUITY_ADJUSTMENTS = "Equity:Adjustments"
INVESTMENT_MUTUAL_FUND = "Assets:Investment:MutualFund"
# Every credit card is a leaf under this prefix (`Liabilities:CreditCard:HDFC4321`).
CREDIT_CARD_PREFIX = "Liabilities:CreditCard:"
# Bank balances: `checking()` writes `Assets:Checking:*`; older imports used `Assets:Bank:*`.
BANK_PREFIXES = ("Assets:Checking:", "Assets:Bank:")
# Investment cost booked as an expense (the units x NAV rounding plug, stamp
# duty). It is the cost of investing, so it counts as invested, never as spending.
INVESTMENT_EXPENSE = "Expenses:Investment"


def is_spending_account(account: str) -> bool:
    """An `Expenses:` account whose top category is not `Expenses:Investment`.

    Matched on the top category (the first two segments), so a sibling such as
    `Expenses:InvestmentAdvice` is still spending."""
    if not account.startswith("Expenses:"):
        return False
    return ":".join(account.split(":")[:2]) != INVESTMENT_EXPENSE


class Vehicle(str, Enum):
    """Which per-instrument holding tree a commodity lot lives under.

    A closed set, so it is an Enum here rather than a bare string -- matching
    `Direction`/`DedupStatus` in backend/app/models/domain/candidate.py. It subclasses
    `str` so every pre-existing `vehicle="mutual_fund"` / `vehicle="stock"`
    call site keeps working unchanged (and so `HOLDING_PREFIX[...]` accepts
    either form)."""

    MUTUAL_FUND = "mutual_fund"
    STOCK = "stock"


# The single source of truth for per-commodity holding-account prefixes.
# Before this existed the same two strings were hardcoded independently in
# entry_writer.py (`_holding_account`, `_ledger_unit_holdings`) and journal.py
# (`ensure_commodity_accounts`); each copy was a separate place a new vehicle
# could be forgotten, and `_ledger_unit_holdings`' copy was exactly that bug.
HOLDING_PREFIX: dict[str, str] = {
    Vehicle.MUTUAL_FUND.value: "Assets:Investment:MutualFund:Groww:",
    Vehicle.STOCK.value: "Assets:Investment:Stock:",
}


def holding_account(commodity: str, vehicle: str = Vehicle.MUTUAL_FUND) -> str:
    """Per-commodity holding account for `commodity` under `vehicle`."""
    return f"{HOLDING_PREFIX[vehicle]}{commodity}"


def _slug(name: str) -> str:
    """beancount account components must start with an uppercase letter and contain no spaces.
    Preserve acronyms (HDFC stays HDFC); only force the first character up. Hyphens are allowed."""
    return "-".join(part[:1].upper() + part[1:] for part in name.split())


def checking(name: str) -> str:
    # Bank and wallet balances both live here, keyed by provider label.
    return f"Assets:Checking:{_slug(name)}"


def receivable(counterparty: str) -> str:
    # SIGNED: positive = they owe me, negative = I owe them.
    return f"Assets:Receivable:{_slug(counterparty)}"


def investment(vehicle: str, issuer: str, instrument: str | None = None) -> str:
    # vehicle in {Equity, MutualFund, FD, RealEstate, Crypto}; instrument omitted for FD/RealEstate.
    base = f"Assets:Investment:{_slug(vehicle)}:{_slug(issuer)}"
    return f"{base}:{_slug(instrument)}" if instrument else base


def credit_card(name: str) -> str:
    return f"Liabilities:CreditCard:{_slug(name)}"


def loan(name: str) -> str:
    return f"Liabilities:Loan:{_slug(name)}"


def income(name: str) -> str:
    return f"Income:{_slug(name)}"


def expense(name: str) -> str:
    return f"Expenses:{_slug(name)}"


# Accounts the initial journal opens so the foundation (and its tests) can post.
SEED_ACCOUNTS: tuple[str, ...] = (
    "Assets:Checking:HDFC",
    "Assets:Checking:ICICI",
    "Assets:Checking:IDFC",  # day-one institution
    "Assets:Cash",
    "Assets:Transfers:InTransit",  # permanent suspense
    "Liabilities:CreditCard:HDFC",
    "Income:Salary",
    # Realized capital gains the import path books from FIFO lots; seeded so the
    # tax workspace's CG buckets exist before the first sale is imported.
    "Income:CapitalGains:LTCG",
    "Income:CapitalGains:STCG",
    "Income:CapitalGains:Crypto",
    "Income:CapitalGains:Debt",
    "Income:CapitalGains:Unclassified",
    "Expenses:Food",
    "Expenses:Rent",
    "Expenses:Travel",
    "Expenses:Uncategorized",
    "Income:Uncategorized",  # fallback unified on Uncategorized
    "Equity:Opening-Balances",
)
