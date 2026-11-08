from __future__ import annotations

from datetime import date
from decimal import Decimal

from beancount.core import data

from backend.app.models.domain.accounts import HOLDING_PREFIX, Vehicle
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.platform_accounts import (
    GROWW,
    platform_clearing_account,
)
from backend.app.service.ledger.openings import read as openings_read
from backend.app.service.reports.health.ledger_view import (
    all_postings,
    commodity_names_from_entries,
)
from backend.app.service.reports.health.uncategorized import (
    merchant_backlog_from_entries,
)


def _vehicle_total(
    ledger: Ledger, prefix: str, *, exclude_account: str | None = None
) -> Decimal:
    """Sum `inr_amount` for postings whose account starts with `prefix` and
    (if given) is not `exclude_account`."""
    total = Decimal(0)
    for row in all_postings(ledger):
        if not row.account.startswith(prefix):
            continue
        if row.account == exclude_account:
            continue
        total += row.inr_amount
    return total


def _has_nonzero_activity(
    ledger: Ledger, prefix: str, *, exclude_account: str | None = None
) -> bool:
    """True if any posting under `prefix` (excluding `exclude_account`) is nonzero."""
    for row in all_postings(ledger):
        if not row.account.startswith(prefix):
            continue
        if row.account == exclude_account:
            continue
        if row.inr_amount != 0:
            return True
    return False


_MONEY = Decimal("0.01")


def _negative_assets(ledger: Ledger) -> list[dict]:
    """Asset accounts whose balance is below zero, worst first.

    A negative asset balance is impossible in the world the journal models:
    you cannot hold minus ninety thousand rupees of cash, and a broker wallet
    cannot owe you money it never held. Every occurrence is a modelling defect
    -- a funding leg that was never imported, an inverted sign, or an account
    that is really a liability.

    Deliberately scoped to `Assets:` only. A negative balance on a liability,
    income or equity account is ordinary double-entry; flagging those would
    bury the real signal and train the reader to skip this check.
    """
    totals: dict[str, Decimal] = {}
    for row in all_postings(ledger):
        if not row.account.startswith("Assets:"):
            continue
        totals[row.account] = totals.get(row.account, Decimal(0)) + row.inr_amount

    negatives = [
        {"account": account, "balance": str(balance.quantize(_MONEY))}
        for account, balance in totals.items()
        if balance < 0
    ]
    # Worst (most negative) first: the ordering is the triage. Ties fall back
    # to the account name so the output is stable across runs.
    negatives.sort(key=lambda n: (Decimal(n["balance"]), n["account"]))
    return negatives


def checks(
    ledger: Ledger,
    investment_ledger: Ledger | None = None,
    *,
    today: date | None = None,
) -> dict:
    """Journal health checks.

    `openings` lists the balance-sheet accounts that have activity but no
    opening balance, or whose opening is dated after their first activity
    (see `service.ledger.openings`). `today` (default: the
    wall clock) only feeds that read model; the block itself does not
    depend on it.

    Note: `mf_reconciliation_gap` reports the raw drift between the main
    ledger's coarse MutualFund total and the investment sub-ledger's
    per-scheme total. A nonzero gap is *expected* (not a bug) while the
    historical per-scheme migration hasn't run yet — see
    `mf_migration_pending`, which flags exactly that transitional state so
    callers don't mistake pending migration for real desync/corruption.
    """
    result = ledger.load()
    txns = [e for e in result.entries if isinstance(e, data.Transaction)]
    uncategorized_count = sum(
        1 for e in txns for p in e.postings if p.account == "Expenses:Uncategorized"
    )
    # Reuses `result.entries` (already parsed above) via
    # merchant_backlog_from_entries rather than calling merchant_backlog(ledger),
    # which would trigger a second full beancount parse of the same journal.
    backlog = merchant_backlog_from_entries(result.entries)
    out: dict = {
        "journal_valid": not result.errors,
        "errors": result.errors,
        "transaction_count": len(txns),
        "uncategorized_count": uncategorized_count,
        # NOTE: uncategorized_count (above) and uncategorized["transactions"]
        # (below) can legitimately disagree. uncategorized_count counts every
        # raw posting against Expenses:Uncategorized; uncategorized["transactions"]
        # is derived from merchant_backlog_from_entries, which drops any posting
        # whose normalized narration is empty (see `if key` in
        # merchant_backlog_from_entries) since an empty merchant key cannot be
        # grouped into an answerable decision. A posting with a narration that
        # normalizes to "" is the only case that makes the two counts differ.
        "uncategorized": {
            "transactions": sum(m.count for m in backlog),
            "merchants": len(backlog),
        },
        "negative_assets": _negative_assets(ledger),
        "openings": openings_read.doctor_block(
            result.entries,
            today=today if today is not None else date.today(),
            names=commodity_names_from_entries(result.entries),
        ),
    }
    if investment_ledger is not None:
        inv_result = investment_ledger.load()
        # The MF clearing account nets the subsystem's cash legs; it is not a
        # holding, so neither side of the reconciliation counts it.
        clearing = platform_clearing_account(GROWW)
        main_total = _vehicle_total(ledger, "Assets:Investment:MutualFund")
        sub_total = _vehicle_total(
            investment_ledger,
            HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value],
            exclude_account=clearing,
        )
        main_has_legacy_activity = _has_nonzero_activity(
            ledger,
            HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value],
            exclude_account=clearing,
        )
        sub_has_holdings = _has_nonzero_activity(
            investment_ledger,
            HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value],
            exclude_account=clearing,
        )
        out["investment_ledger_valid"] = not inv_result.errors
        out["investment_ledger_errors"] = inv_result.errors
        out["mf_reconciliation_gap"] = str(main_total - sub_total)
        out["mf_migration_pending"] = (
            not sub_has_holdings
        ) and main_has_legacy_activity
    return out
