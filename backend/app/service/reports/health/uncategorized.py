"""The uncategorized backlog, grouped by merchant.

Committing no longer waits for classification: a row with no category lands in
`Expenses:Uncategorized`, the journal validates, and net worth and cash flow
are correct immediately. Only spend-by-category is affected, so the backlog
has to be *visible* rather than enforced -- this is that surface.

Grouped by the normalized merchant key, not by transaction, because that is
the unit a human can actually answer. One decision about "SWIGGY" settles
every Swiggy row, past and future; answering 250 transactions one at a time is
the chore this whole change exists to remove.
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from typing import Iterable

from beancount.core import data

from backend.app.models.domain.categorize import MerchantBacklog
from backend.app.service.ingest.pipeline.normalize import normalize_narration
from backend.app.persistence.journal import Ledger

UNCATEGORIZED_ACCOUNT = "Expenses:Uncategorized"


def _inr_amount(posting) -> Decimal:
    """Same INR/cost-basis rule as backend/app/service/reports/health/ledger_view.py's private
    helper of the same name, duplicated rather than imported: Uncategorized
    postings are always plain-INR cash legs in practice, but this keeps the
    value identical to what all_postings() would have reported if a
    cost-bearing posting ever did land here."""
    if posting.cost is not None and posting.cost.number is not None:
        return posting.units.number * posting.cost.number
    return posting.units.number


def merchant_backlog_from_entries(
    entries: Iterable[data.Directive],
) -> list[MerchantBacklog]:
    """The grouping body of `merchant_backlog`, factored out to accept
    already-parsed journal entries directly.

    `merchant_backlog(ledger)` triggers a full beancount re-parse (via
    `Ledger.load()`, which is uncached). A caller that has already loaded the
    journal for its own purposes -- `doctor.checks()` is exactly this caller
    -- can pass its own `entries` here instead of going through
    `merchant_backlog(ledger)` and paying for a second parse.
    """
    groups: dict[str, list[tuple[date_type, str, Decimal]]] = {}
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            continue
        narration = entry.narration or ""
        for posting in entry.postings:
            if posting.account != UNCATEGORIZED_ACCOUNT:
                continue
            groups.setdefault(normalize_narration(narration), []).append(
                (entry.date, narration, _inr_amount(posting))
            )

    out = [
        MerchantBacklog(
            merchant=key,
            count=len(rows),
            total=sum((amount for _, _, amount in rows), Decimal(0)),
            first=min(d for d, _, _ in rows),
            last=max(d for d, _, _ in rows),
            sample=max(rows, key=lambda r: r[0])[1],
        )
        for key, rows in groups.items()
        if key
    ]
    # Count desc, then merchant asc so the order is stable across calls.
    out.sort(key=lambda m: (-m.count, m.merchant))
    return out


def merchant_backlog(ledger: Ledger) -> list[MerchantBacklog]:
    """Uncategorized postings grouped by normalized merchant, biggest first.

    Ordered by transaction count rather than rupee value: the point is to
    maximise rows cleared per decision, and a 40-row Swiggy habit is worth
    answering before a single large one-off.
    """
    return merchant_backlog_from_entries(ledger.load().entries)


def backlog_summary(ledger: Ledger) -> dict:
    merchants = merchant_backlog(ledger)
    return {
        "transactions": sum(m.count for m in merchants),
        "merchants": len(merchants),
        "total": str(
            sum((m.total for m in merchants), Decimal(0)).quantize(Decimal("0.01"))
        ),
        "rows": [
            {
                "merchant": m.merchant,
                "count": m.count,
                "total": str(m.total.quantize(Decimal("0.01"))),
                "first": m.first.isoformat(),
                "last": m.last.isoformat(),
                "sample": m.sample,
            }
            for m in merchants
        ],
    }
