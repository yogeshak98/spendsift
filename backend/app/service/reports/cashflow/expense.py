from __future__ import annotations

from decimal import Decimal
from typing import Literal

from backend.app.models.domain.accounts import is_spending_account
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    in_month,
    top_category,
)

# Calendar-order 3-letter month abbreviations, indexed by (month - 1).
_MONTH_LABELS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def _expenses(rows: list[PostingRow]) -> list[PostingRow]:
    """Spending postings only: `Expenses:Investment:*` is investment cost."""
    return [r for r in rows if is_spending_account(r.account)]


def _pct_rows(totals: dict[str, Decimal]) -> list[dict]:
    """Category rows by amount desc, `pct` of the positive total.

    A category whose total is <= 0 (refunds only) has `pct` 0.0, and so does
    every category when none is positive -- never a negative share."""
    positive = sum((amt for amt in totals.values() if amt > 0), Decimal(0))
    ordered = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [
        {
            "category": cat,
            "amount": str(amt),
            "pct": round(float(amt / positive * 100), 1)
            if amt > 0 and positive > 0
            else 0.0,
        }
        for cat, amt in ordered
    ]


def by_category(rows: list[PostingRow], year: int, month: int) -> list[dict]:
    totals: dict[str, Decimal] = {}
    for r in _expenses(in_month(rows, year, month)):
        cat = top_category(r.account)
        totals[cat] = totals.get(cat, Decimal(0)) + r.inr_amount
    return _pct_rows(totals)


def by_day(rows: list[PostingRow], year: int, month: int) -> dict[int, str]:
    totals: dict[int, Decimal] = {}
    for r in _expenses(in_month(rows, year, month)):
        totals[r.date.day] = totals.get(r.date.day, Decimal(0)) + r.inr_amount
    return {day: str(amt) for day, amt in sorted(totals.items())}


def postings(rows: list[PostingRow], year: int, month: int) -> list[dict]:
    """The month's individual expense postings, most-recent first.

    One entry per Expenses: posting. `category` is the top-2-level account path
    (e.g. ``Expenses:Rent``); `account` is the full account path; `merchant` is
    the transaction narration; amounts are decimal strings. Sorted most-recent
    first, then by amount descending.
    """
    items = [
        {
            "date": r.date.isoformat(),
            "account": r.account,
            "category": top_category(r.account),
            "merchant": r.narration,
            "amount": str(r.inr_amount),
        }
        for r in _expenses(in_month(rows, year, month))
    ]
    # Secondary key must compare numerically — `amount` is a decimal string, so a
    # raw string sort would order "888" ahead of "35000" within the same date.
    return sorted(items, key=lambda i: (i["date"], Decimal(i["amount"])), reverse=True)


def trailing_months(
    rows: list[PostingRow], year: int, month: int, count: int = 6
) -> list[dict]:
    """`count` consecutive months ending at (year, month), oldest first.

    Each entry: {month, year, label, amount}. `amount` is "0" for months with
    no expenses. `label` is the 3-letter month abbreviation.
    """
    out: list[dict] = []
    for offset in range(count - 1, -1, -1):
        # step back `offset` months from (year, month)
        idx = (year * 12 + (month - 1)) - offset
        y, m = divmod(idx, 12)
        m += 1
        total = sum(
            (r.inr_amount for r in _expenses(in_month(rows, y, m))),
            start=Decimal(0),
        )
        out.append(
            {"month": m, "year": y, "label": _MONTH_LABELS[m - 1], "amount": str(total)}
        )
    return out


TagKind = Literal["expense", "income"]


def _tagged(rows: list[PostingRow], kind: TagKind) -> list[tuple[PostingRow, Decimal]]:
    """(row, signed amount) for the side's postings: spending at `inr_amount`,
    or `Income:` at `-inr_amount` (received is positive, a TDS leg reduces it)."""
    if kind == "income":
        return [(r, -r.inr_amount) for r in rows if r.account.startswith("Income:")]
    return [(r, r.inr_amount) for r in _expenses(rows)]


def by_tag(rows: list[PostingRow], kind: TagKind = "expense") -> list[dict]:
    """Totals per tag. `kind="income"` sums `Income:` postings as `-inr_amount`."""
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for r, amount in _tagged(rows, kind):
        for tag in r.tags:
            totals[tag] = totals.get(tag, Decimal(0)) + amount
            counts[tag] = counts.get(tag, 0) + 1
    ordered = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [
        {"tag": tag, "amount": str(amt), "count": counts[tag]} for tag, amt in ordered
    ]


def by_tag_groups(rows: list[PostingRow], kind: TagKind = "expense") -> list[dict]:
    """Roll `by_tag` up by the part of each tag before the first "/".

    A posting counts once per prefix, even when it carries several tags under it.
    Groups are ordered by amount (largest first), ties by prefix ascending.
    `kind` as in `by_tag`.
    """
    tags_by_prefix: dict[str, list[dict]] = {}
    for entry in by_tag(rows, kind):
        tags_by_prefix.setdefault(entry["tag"].split("/", 1)[0], []).append(entry)
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for r, amount in _tagged(rows, kind):
        for prefix in {tag.split("/", 1)[0] for tag in r.tags}:
            totals[prefix] = totals.get(prefix, Decimal(0)) + amount
            counts[prefix] = counts.get(prefix, 0) + 1
    ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {
            "prefix": prefix,
            "amount": str(amt),
            "count": counts[prefix],
            "tags": tags_by_prefix[prefix],
        }
        for prefix, amt in ordered
    ]
