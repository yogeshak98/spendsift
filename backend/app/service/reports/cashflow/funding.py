"""Which account funded a flow posting: card, bank or other.

The backend port of the frontend's `paidVia.ts` `fundingLeg`, with the
prefixes from `backend/app/models/domain/accounts.py`.

Rule (`funding_leg`): take the net of the transaction's `root` legs. Among its
postings NOT under `Expenses:` or `Income:` whose sign is opposite to that net
(a zero net counts as +), pick the best of card (`Liabilities:CreditCard:`) <
bank (`Assets:Checking:` or `Assets:Bank:`) < other; ties go to posting order.
None when there is no such posting (a pure reclass). Every root leg of the
transaction inherits that via.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from backend.app.models.domain.accounts import BANK_PREFIXES, CREDIT_CARD_PREFIX
from backend.app.service.reports.health.ledger_view import TransactionRow

FundingKind = Literal["card", "bank", "other"]
FundingFilter = Literal["all", "card", "bank", "other"]


@dataclass(frozen=True)
class Via:
    kind: FundingKind
    label: str  # last account segment
    account: str


_RANK: dict[FundingKind, int] = {"card": 0, "bank": 1, "other": 2}
_FLOW_ROOTS = ("Expenses:", "Income:")


def kind_of(account: str) -> FundingKind:
    if account.startswith(CREDIT_CARD_PREFIX):
        return "card"
    if account.startswith(BANK_PREFIXES):
        return "bank"
    return "other"


def funding_leg(txn: TransactionRow, root: str) -> Via | None:
    """`root` is "Expenses:" or "Income:"."""
    net = sum(
        (p.inr_amount for p in txn.postings if p.account.startswith(root)),
        Decimal(0),
    )
    positive = net >= 0
    best: Via | None = None
    for p in txn.postings:
        if p.account.startswith(_FLOW_ROOTS) or p.inr_amount == 0:
            continue
        # The funding leg moves the other way: a spend (+) is paid by a credit (-).
        if (p.inr_amount < 0) != positive:
            continue
        kind = kind_of(p.account)
        # Strictly better only, so a tie keeps the earlier posting.
        if best is None or _RANK[kind] < _RANK[best.kind]:
            best = Via(kind, p.account.rsplit(":", 1)[-1], p.account)
    return best


def matches(via: Via | None, funding: FundingFilter, account: str | None) -> bool:
    """`funding == "all"` or `(via.kind if via else "other") == funding`; with
    `account`, also `via.account == account`."""
    if funding != "all" and (via.kind if via else "other") != funding:
        return False
    if account is not None and (via is None or via.account != account):
        return False
    return True
