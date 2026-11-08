"""A persona is data: accounts plus the plans that drive six months of activity.

Every amount is a `Decimal`. Signs follow the ledger: on a bank, card or broker
account a debit is negative and a credit positive, so a card that is owed money
has a negative balance. Plans only say what happens; `simulate` turns them into
dated events and the closing state every later check compares against.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

# The fixed simulated period, so the same persona always yields the same files.
PERIOD_START = date(2025, 10, 1)
PERIOD_END = date(2026, 3, 31)

AccountKind = Literal["bank", "card", "broker_mf", "broker_stocks"]
EventKind = Literal[
    "salary",
    "interest",
    "spend",
    "fee",
    "gst_on_fee",
    "refund",
    "cashback",
    "transfer_out",
    "transfer_in",
    "card_bill_payment",
    "card_payment_received",
    "platform_funding",
    # The broker paying MF redemption proceeds into the bank: both legs.
    "mf_redemption_payout",
    "mf_buy",
    "mf_sell",
    "stock_buy",
    "stock_sell",
    "emi",  # reserved: loans are not simulated yet
]
Side = Literal["buy", "sell"]

# (scheme_code or NSE symbol, trade date) -> price per unit.
NavLookup = Callable[[str, date], Decimal]
PriceLookup = Callable[[str, date], Decimal]


@dataclass(frozen=True)
class Account:
    """One bank account, card or broker account. `number` is the full invented
    account or card number; statements show `last4`."""

    key: str
    kind: AccountKind
    institution: str
    product: str
    number: str
    # Bank/broker: cash on 2025-10-01. Card: the balance of the 2025-09-15
    # statement (negative when owed), which the first bill payment settles.
    opening_balance: Decimal

    @property
    def last4(self) -> str:
        return self.number[-4:]


@dataclass(frozen=True)
class Income:
    """Salary on the last working day of every month, or interest on the last
    day of each quarter-end month inside the period. `narration` may use
    `{month}` (e.g. `OCT 2025`) and `{ref}`."""

    account_key: str
    kind: Literal["salary", "interest"]
    amount: Decimal
    narration: str
    category: str


@dataclass(frozen=True)
class Recurring:
    """The same spend on the same day of every month (rent, a subscription)."""

    account_key: str
    merchant: str
    amount: Decimal
    day: int
    narration: str
    category: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpendPattern:
    """`per_month` spends at one merchant on random days, each amount drawn
    uniformly (to the paisa) from `amount_range`, each narration one of
    `narrations`. A narration containing `{ref}` gets a fresh reference."""

    account_key: str
    merchant: str
    category: str
    per_month: int
    amount_range: tuple[Decimal, Decimal]
    narrations: tuple[str, ...]
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransferPlan:
    """Money moved between two of the persona's own accounts. The receiving
    statement shows it `lag_days` (0 or 1) after the sender's. Either side
    being a broker makes it platform funding (or a withdrawal)."""

    from_key: str
    to_key: str
    amount: Decimal
    on: date
    lag_days: int = 0
    rail: Literal["UPI", "IMPS", "NEFT"] = "IMPS"


@dataclass(frozen=True)
class CardBillPlan:
    """Pay each statement of `card_key` in full from `from_key`, `days_early`
    days before its due date (the 5th of the month after the statement)."""

    card_key: str
    from_key: str
    days_early: int = 0


@dataclass(frozen=True)
class RefundPlan:
    """A purchase and its later refund, both on `account_key`."""

    account_key: str
    merchant: str
    category: str
    amount: Decimal
    purchase_on: date
    refund_on: date
    purchase_narration: str
    refund_narration: str


@dataclass(frozen=True)
class FeePlan:
    """A charge on an account; `gst_narration` adds 18% GST on it as its own
    row. The GST row's expected category is the fee's. `merchant` names the
    same-day purchase the fee is charged on (a fuel surcharge's fill): its
    rows sit right after that purchase's row, as an issuer prints them."""

    account_key: str
    on: date
    amount: Decimal
    narration: str
    category: str
    gst_narration: str | None = None
    merchant: str | None = None


@dataclass(frozen=True)
class CashbackPlan:
    """A credit that is neither income from work nor a refund."""

    account_key: str
    on: date
    amount: Decimal
    narration: str
    category: str | None = None


@dataclass(frozen=True)
class MfOrderPlan:
    """A mutual-fund order on a `broker_mf` account. A buy is `amount` rupees
    (a multiple of 100) and, with `funded_from`, is paid by a same-day bank
    transfer to the broker. A sell redeems `units`, or, with `leave_units`,
    everything held except that many: units bought depend on the NAV lookup,
    so "all but 0.004" is the only way to state a dust redemption as data.
    With `payout_to`, the broker pays the proceeds into that bank account two
    trading days after the redemption, as Groww does."""

    broker_key: str
    scheme_code: str
    scheme_name: str
    side: Side
    on: date
    amount: Decimal | None = None
    units: Decimal | None = None
    funded_from: str | None = None
    leave_units: Decimal | None = None
    payout_to: str | None = None


@dataclass(frozen=True)
class StockOrderPlan:
    """An equity delivery order on a `broker_stocks` account, filled at the
    day's price for `symbol`. Funding is an explicit `TransferPlan`."""

    broker_key: str
    symbol: str
    isin: str
    name: str
    side: Side
    quantity: int
    on: date


@dataclass(frozen=True)
class TripSpend:
    """`count` spends inside a trip's window, otherwise like `SpendPattern`."""

    account_key: str
    merchant: str
    category: str
    count: int
    amount_range: tuple[Decimal, Decimal]
    narrations: tuple[str, ...]


@dataclass(frozen=True)
class Trip:
    """Spends between `start` and `end`, each carrying the trip's `tag`."""

    tag: str
    start: date
    end: date
    spends: tuple[TripSpend, ...]


@dataclass(frozen=True)
class Persona:
    slug: str
    display_name: str
    city: str
    employer: str
    net_worth_target: Decimal
    seed: int
    accounts: list[Account]
    income: list[Income] = field(default_factory=list)
    recurring: list[Recurring] = field(default_factory=list)
    spends: list[SpendPattern] = field(default_factory=list)
    transfers: list[TransferPlan] = field(default_factory=list)
    card_bills: list[CardBillPlan] = field(default_factory=list)
    refunds: list[RefundPlan] = field(default_factory=list)
    mf_orders: list[MfOrderPlan] = field(default_factory=list)
    stock_orders: list[StockOrderPlan] = field(default_factory=list)
    trips: list[Trip] = field(default_factory=list)
    fees: list[FeePlan] = field(default_factory=list)
    cashbacks: list[CashbackPlan] = field(default_factory=list)

    def account(self, key: str) -> Account:
        for acct in self.accounts:
            if acct.key == key:
                return acct
        raise KeyError(f"{self.slug}: no account {key!r}")


@dataclass(frozen=True)
class Event:
    """One row on one account's statement.

    `pair_id` links the rows that are one real-world movement: both legs of a
    transfer, a bill payment and its payment-received, a refund and its
    purchase. `commodity`, `units`, `price` and `charges` are set on
    investment rows only; `charges` is the stamp duty on an MF purchase.
    """

    date: date
    account_key: str
    amount: Decimal
    narration: str
    ref: str | None
    kind: EventKind
    counter_account_key: str | None = None
    merchant: str | None = None
    expected_category: str | None = None
    expected_tags: tuple[str, ...] = ()
    pair_id: str | None = None
    commodity: str | None = None
    units: Decimal | None = None
    price: Decimal | None = None
    charges: Decimal | None = None

    @property
    def expense_amount(self) -> Decimal | None:
        """What the matching `Expenses:*` posting should carry: positive for a
        spend, fee or GST row (the account side is negative)."""
        if self.kind in ("spend", "fee", "gst_on_fee"):
            return -self.amount
        return None


@dataclass(frozen=True)
class CardCycle:
    """One card statement: activity after the previous statement date up to
    and including `statement_date`. `balance` is the statement's total
    (negative when owed); the bill paid by `due_date` settles it."""

    card_key: str
    start: date
    statement_date: date
    due_date: date
    opening: Decimal
    balance: Decimal


@dataclass(frozen=True)
class Truth:
    """What the app must end up showing after importing the persona's files."""

    events: list[Event]
    closing: dict[str, Decimal]
    net_worth: Decimal
    # account_key -> commodity (MF scheme code or NSE symbol) -> units held.
    holdings: dict[str, dict[str, Decimal]]
    cycles: list[CardCycle]
