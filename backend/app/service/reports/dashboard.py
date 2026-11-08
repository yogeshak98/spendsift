from __future__ import annotations

from decimal import Decimal

from backend.app.service.reports.cashflow.expense import by_day
from backend.app.service.reports.health.ledger_view import (
    PREPAID_TAX_PREFIXES,
    PostingRow,
    display_account,
)

_LIQUID_PREFIXES = ("Assets:Checking:", "Assets:Cash")
_INVESTMENT_PREFIX = "Assets:Investment:"


def account_balances(rows: list[PostingRow]) -> dict[str, str]:
    totals: dict[str, Decimal] = {}
    for r in rows:
        totals[r.account] = totals.get(r.account, Decimal(0)) + r.inr_amount
    return {acct: str(amt) for acct, amt in totals.items()}


def summary(
    rows: list[PostingRow], year: int, month: int, names: dict[str, str] | None = None
) -> dict:
    names = names or {}
    balances = account_balances(rows)
    assets = sum(
        (
            Decimal(v)
            for a, v in balances.items()
            if a.startswith("Assets:") and not a.startswith(PREPAID_TAX_PREFIXES)
        ),
        Decimal(0),
    )
    liabilities = sum(
        (Decimal(v) for a, v in balances.items() if a.startswith("Liabilities:")),
        Decimal(0),
    )
    spent = sum((Decimal(v) for v in by_day(rows, year, month).values()), Decimal(0))

    liquid = sum(
        (Decimal(v) for a, v in balances.items() if a.startswith(_LIQUID_PREFIXES)),
        Decimal(0),
    )
    # Any Assets:Investment:* account can legitimately swing negative — a
    # clearing/settlement leg, an unswept redemption, etc. — not just Groww's.
    # Drop non-positive balances, matching holdings()/Allocation, so the
    # dashboard total never disagrees with those surfaces over the same data.
    investment_balances = {
        a: Decimal(v)
        for a, v in balances.items()
        if a.startswith(_INVESTMENT_PREFIX) and Decimal(v) > 0
    }
    investments = sum(investment_balances.values(), Decimal(0))
    checking_count = sum(1 for a in balances if a.startswith("Assets:Checking:"))
    investment_kinds = len(investment_balances)
    # Non-positive Investment balances are dropped from `investments` above (to
    # match holdings()/Allocation) but a *negative* one still counts toward
    # `cash_net_worth` via `assets`. Surface those explicitly so a large negative
    # account never silently pulls net worth below what "Liquid + Investments −
    # Liabilities" visibly implies — the user needs to know which account to
    # investigate. A closed/fully-redeemed lot sitting at exactly zero is not
    # an anomaly, so it's excluded here (unlike the `<= 0` drop above, which is
    # just "not a holding to display").
    flagged_investment_accounts = sorted(
        (
            {"account": a, "account_display": display_account(a, names), "amount": v}
            for a, v in balances.items()
            if a.startswith(_INVESTMENT_PREFIX) and Decimal(v) < 0
        ),
        key=lambda x: Decimal(x["amount"]),
    )
    accounts = sorted(
        (
            {"account": a, "account_display": display_account(a, names), "amount": v}
            for a, v in balances.items()
            if a.startswith("Assets:") and not a.startswith(PREPAID_TAX_PREFIXES)
        ),
        key=lambda x: Decimal(x["amount"]),
        reverse=True,
    )

    return {
        "cash_net_worth": str(assets + liabilities),
        "liabilities": str(-liabilities),
        "spent_this_month": str(spent),
        "liquid": str(liquid),
        "investments": str(investments),
        "checking_count": checking_count,
        "investment_kinds": investment_kinds,
        "accounts": accounts,
        "flagged_investment_accounts": flagged_investment_accounts,
    }
