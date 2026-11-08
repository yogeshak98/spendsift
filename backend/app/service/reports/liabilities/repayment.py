from __future__ import annotations

from collections.abc import Mapping
from datetime import date as date_type
from decimal import Decimal

from backend.app.persistence.journal import MetaValue
from backend.app.service.reports.liabilities import amortize
from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow
from backend.app.service.reports.liabilities.meta_keys import (
    EMI_CARD_KEY,
    LABEL_KEY,
    LOAN_PREFIX,
)
from backend.app.service.reports.liabilities.loan_terms import (
    LoanTerms,
    ResolvedTerms,
    resolve_all,
)


def _add_months(year: int, month: int, months: int) -> str:
    m = year * 12 + (month - 1) + months
    return f"{m // 12:04d}-{m % 12 + 1:02d}"


def _payoff_month(today: date_type, term_months: int) -> str:
    return _add_months(today.year, today.month, term_months)


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _amount(value: Decimal) -> str:
    return "0" if value == 0 else str(value)


def _emi_pool(
    rows: list[PostingRow],
    pool_account: str | None,
    account_meta: Mapping[str, Mapping[str, MetaValue]] | None,
    transactions: list[TransactionRow] | None,
) -> dict | None:
    """Postings still booked on the lumped card EMI account itself. None when
    that account is neither open nor posted to.

    With `transactions` (rows carrying a txn-id, as `all_transactions`
    gives), only their postings are counted: those are the rows the split
    review can show and move. Without it every posting in `rows` counts."""
    if pool_account is None:
        return None
    posted = any(r.account == pool_account for r in rows)
    if not posted and pool_account not in (account_meta or {}):
        return None
    if transactions is None:
        on_pool = [r.amount for r in rows if r.account == pool_account]
    else:
        on_pool = [
            p.amount
            for txn in transactions
            for p in txn.postings
            if p.account == pool_account
        ]
    return {
        "account": pool_account,
        "unassigned_count": len(on_pool),
        "unassigned_amount": _amount(abs(sum(on_pool, Decimal(0)))),
    }


def _loan(
    account: str,
    outstanding: Decimal,
    drawn: Decimal,
    meta: Mapping[str, MetaValue],
    terms: ResolvedTerms | None,
    today: date_type,
) -> dict:
    base = {
        "account": account,
        "label": _text(meta.get(LABEL_KEY)),
        "card": _text(meta.get(EMI_CARD_KEY)),
        "outstanding": str(outstanding),
    }
    if terms is None:
        return {
            **base,
            "terms_set": False,
            "terms_source": None,
            "original_principal": str(drawn) if drawn else None,
            "annual_rate_pct": None,
            "term_months": None,
            "paid_instalments": None,
            "start_month": None,
            "emi": None,
            "total_interest": None,
            "payoff_month": None,
            "schedule": [],
        }
    principal = terms["original_principal"] or outstanding
    rate = terms["annual_rate_pct"]
    months = terms["term_months"]
    rows_sched = amortize.schedule(principal, rate, months)
    schedule = [
        {
            "month": s["month"],
            "emi": str(s["emi"]),
            "principal": str(s["principal"]),
            "interest": str(s["interest"]),
            "balance": str(s["balance"]),
        }
        for s in rows_sched
    ]
    total_interest = sum((s["interest"] for s in rows_sched), Decimal(0))
    start = terms["start_month"]
    if terms["source"] == "inferred" and start is not None:
        year, month = (int(part) for part in start.split("-"))
        payoff = _add_months(year, month, months - 1)
    else:
        payoff = _payoff_month(today, months)
    return {
        **base,
        "terms_set": True,
        "terms_source": terms["source"],
        "original_principal": str(principal),
        "annual_rate_pct": str(rate),
        "term_months": months,
        "paid_instalments": terms["paid_instalments"],
        "start_month": start,
        "emi": str(amortize.emi(principal, rate, months)),
        "total_interest": str(total_interest),
        "payoff_month": payoff,
        "schedule": schedule,
    }


def schedules(
    rows: list[PostingRow],
    terms: LoanTerms,
    today: date_type,
    *,
    account_meta: Mapping[str, Mapping[str, MetaValue]] | None = None,
    pool_account: str | None = None,
    closed: frozenset[str] = frozenset(),
    transactions: list[TransactionRow] | None = None,
) -> dict:
    """`{"loans": [...], "emi_pool": {...} | None}` for every
    `Liabilities:Loan:*` account with a nonzero balance.

    Terms come from the account's `open` metadata, then the legacy file, then
    inference from the account's own postings; `pool_account` (the lumped
    card EMI account) is never inferred and is reported as `emi_pool`.
    Accounts in `closed` are omitted, as on the Balance screen.
    `transactions` scopes the pool's counts (see `_emi_pool`)."""
    totals: dict[str, Decimal] = {}
    drawn: dict[str, Decimal] = {}
    for r in rows:
        if r.account.startswith(LOAN_PREFIX) and r.account not in closed:
            totals[r.account] = totals.get(r.account, Decimal(0)) + r.amount
            if r.amount < 0:
                drawn[r.account] = drawn.get(r.account, Decimal(0)) - r.amount

    resolved = resolve_all(rows, terms, account_meta, pool_account)
    meta = account_meta or {}
    loans = [
        _loan(
            account,
            -total,  # liabilities stored negative
            drawn.get(account, Decimal(0)),
            meta.get(account, {}),
            resolved.get(account),
            today,
        )
        for account, total in sorted(totals.items())
        if total != 0
    ]
    return {
        "loans": loans,
        "emi_pool": _emi_pool(rows, pool_account, meta, transactions),
    }
