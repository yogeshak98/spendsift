from __future__ import annotations

from decimal import Decimal

from backend.app.service.reports.health.ledger_view import PostingRow, display_account


def balance_rows(
    postings: list[PostingRow], names: dict[str, str] | None = None
) -> list[dict]:
    """Per-asset-account row: inflow (positive deltas), outflow (negative deltas,
    as a magnitude), and net balance -- all all-time, all decimal strings.

    Assets are positive-signed in the ledger (unlike liabilities, which are
    negative), so `balance` is the direct signed sum -- no negation. Investment
    leaves use `inr_amount` (ledger cost basis for commodity lots), matching
    `investments.holdings()` and `networth.monthly_series()`; this is a
    ledger-cost-basis snapshot, never a live market valuation."""
    names = names or {}
    inflow: dict[str, Decimal] = {}
    outflow: dict[str, Decimal] = {}
    net: dict[str, Decimal] = {}
    for r in postings:
        if not r.account.startswith("Assets:"):
            continue
        net[r.account] = net.get(r.account, Decimal(0)) + r.inr_amount
        if r.inr_amount > 0:
            inflow[r.account] = inflow.get(r.account, Decimal(0)) + r.inr_amount
        elif r.inr_amount < 0:
            outflow[r.account] = outflow.get(r.account, Decimal(0)) + (-r.inr_amount)

    return [
        {
            "account": account,
            "account_display": display_account(account, names),
            "inflow": str(inflow.get(account, Decimal(0))),
            "outflow": str(outflow.get(account, Decimal(0))),
            "balance": str(net[account]),
        }
        for account in sorted(net)
    ]
