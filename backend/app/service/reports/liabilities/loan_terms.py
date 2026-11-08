from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, TypedDict

import yaml

from backend.app.persistence.journal import MetaValue
from backend.app.service.reports.health.ledger_view import PostingRow
from backend.app.service.reports.liabilities.emi_terms import InferredTerms, infer_terms
from backend.app.service.reports.liabilities.meta_keys import (
    LOAN_PREFIX,
    PRINCIPAL_KEY,
    RATE_KEY,
    TERM_KEY,
)


class LoanTerms:
    """User-owned per-loan amortization terms, persisted as YAML keyed by full
    account name. Missing file or invalid entry -> no terms (never fabricated)."""

    def __init__(self, raw: dict) -> None:
        self._raw = raw or {}

    @classmethod
    def from_file(cls, path: Path) -> "LoanTerms":
        if not path.exists():
            return cls({})
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            return cls({})
        return cls(raw if isinstance(raw, dict) else {})

    def get(self, account: str) -> dict | None:
        entry = self._raw.get(account)
        if not isinstance(entry, dict):
            return None
        try:
            rate = Decimal(str(entry["annual_rate_pct"]))
            months = int(entry["term_months"])
        except (KeyError, ValueError, TypeError, InvalidOperation):
            return None
        if months <= 0:
            return None
        original = entry.get("original_principal")
        try:
            original_principal = (
                Decimal(str(original)) if original is not None else None
            )
        except (ValueError, InvalidOperation):
            original_principal = None
        return {
            "annual_rate_pct": rate,
            "term_months": months,
            "original_principal": original_principal,
        }


# ── Terms resolution: the account, the legacy file, then inference ──────────
MAX_RATE_PCT = Decimal(60)
MAX_TERM_MONTHS = 600

TermsSource = Literal["account", "file", "inferred"]


class ResolvedTerms(TypedDict):
    annual_rate_pct: Decimal
    term_months: int
    original_principal: Decimal | None
    source: TermsSource
    paid_instalments: int | None
    start_month: str | None


def meta_decimal(value: object) -> Decimal | None:
    """A metadata value as a finite Decimal, or None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _account_terms(meta: Mapping[str, MetaValue]) -> ResolvedTerms | None:
    """Terms from the account's `open` metadata: both `rate-pct` (0..60) and
    `term-months` (a whole 1..600), else None. `principal` is optional and
    ignored unless positive."""
    rate = meta_decimal(meta.get(RATE_KEY))
    months = meta_decimal(meta.get(TERM_KEY))
    if rate is None or months is None:
        return None
    if not (0 <= rate <= MAX_RATE_PCT):
        return None
    if months != months.to_integral_value() or not (1 <= months <= MAX_TERM_MONTHS):
        return None
    principal = meta_decimal(meta.get(PRINCIPAL_KEY))
    return ResolvedTerms(
        annual_rate_pct=rate,
        term_months=int(months),
        original_principal=principal if principal and principal > 0 else None,
        source="account",
        paid_instalments=None,
        start_month=None,
    )


def resolve_terms(
    account: str,
    meta: Mapping[str, MetaValue],
    file_terms: LoanTerms,
    inferred: InferredTerms | None,
) -> ResolvedTerms | None:
    """The first of: the account's own terms, the legacy `loan-terms.yaml`
    entry, the terms inferred from its postings. None when none applies."""
    own = _account_terms(meta)
    if own is not None:
        return own
    entry = file_terms.get(account)
    if entry is not None:
        return ResolvedTerms(
            annual_rate_pct=entry["annual_rate_pct"],
            term_months=entry["term_months"],
            original_principal=entry["original_principal"],
            source="file",
            paid_instalments=None,
            start_month=None,
        )
    if inferred is not None:
        return ResolvedTerms(
            annual_rate_pct=inferred["annual_rate_pct"],
            term_months=inferred["term_months"],
            original_principal=inferred["principal"],
            source="inferred",
            paid_instalments=inferred["paid_instalments"],
            start_month=inferred["start_month"],
        )
    return None


def is_plan_account(account: str, pool_account: str | None) -> bool:
    """`account` is a card EMI plan: a direct child of the pool account."""
    if not pool_account:
        return False
    prefix = pool_account + ":"
    return account.startswith(prefix) and ":" not in account[len(prefix) :]


def resolve_all(
    rows: list[PostingRow],
    file_terms: LoanTerms,
    account_meta: Mapping[str, Mapping[str, MetaValue]] | None,
    pool_account: str | None,
) -> dict[str, ResolvedTerms]:
    """Resolved terms of every `Liabilities:Loan:*` account with postings.

    Terms are inferred only for card EMI plan accounts: direct children of
    `pool_account` (the lumped card EMI account), never the pool itself. Any
    other loan (a home or car loan whose full EMI is booked to it in equal
    amounts would otherwise read as a no-cost 0% plan) takes its terms from
    its metadata or the file, or has none."""
    by_account: dict[str, list[PostingRow]] = {}
    for r in rows:
        if r.account.startswith(LOAN_PREFIX):
            by_account.setdefault(r.account, []).append(r)
    meta = account_meta or {}
    out: dict[str, ResolvedTerms] = {}
    for account, postings in by_account.items():
        inferred = (
            infer_terms(postings) if is_plan_account(account, pool_account) else None
        )
        resolved = resolve_terms(account, meta.get(account, {}), file_terms, inferred)
        if resolved is not None:
            out[account] = resolved
    return out
