from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from backend.app.service.reports.health.ledger_view import (
    PREPAID_TAX_PREFIXES,
    PostingRow,
    display_account,
)

_MONEY_Q = Decimal("0.01")


def _round(d: Decimal) -> Decimal:
    return d.quantize(_MONEY_Q, rounding=ROUND_HALF_UP)


def _money(d: Decimal) -> str:
    """2-dp decimal string, ROUND_HALF_UP (mirrors valuation._money_str).

    A sub-paisa negative rounds to "0.00", never "-0.00".
    """
    q = _round(d)
    return str(q.copy_abs() if q == 0 else q)


def monthly_series(rows: list[PostingRow]) -> list[dict]:
    """Cumulative assets / liabilities / net worth at each month end (YYYY-MM).

    Net worth is assets + liabilities (liability balances are negative), accumulated
    in date order so each month's value is the running position at that month's end.
    """
    by_month: dict[str, list[PostingRow]] = {}
    for r in rows:
        by_month.setdefault(f"{r.date.year:04d}-{r.date.month:02d}", []).append(r)

    assets = Decimal(0)
    liabilities = Decimal(0)
    out: list[dict] = []
    for month in sorted(by_month):
        for r in by_month[month]:
            if r.account.startswith("Assets:") and not r.account.startswith(
                PREPAID_TAX_PREFIXES
            ):
                assets += r.inr_amount
            elif r.account.startswith("Liabilities:"):
                liabilities += r.inr_amount
        out.append(
            {
                "month": month,
                "assets": str(assets),
                "liabilities": str(liabilities),
                "net": str(assets + liabilities),
            }
        )
    return out


def current_position(
    rows: list[PostingRow], names: dict[str, str] | None = None
) -> dict:
    """Net-worth position today, from the same rows and prefix rules as the series.

    Assets exclude prepaid tax (reported separately in `excluded`); liabilities stay
    signed (negative = owed). `by_group` splits counted assets by the second account
    segment (`Assets:Checking:HDFC` -> `Checking`) so bank balances are visible.

    Rounding: each group is rounded to 2 dp and `assets` is the sum of those
    rounded groups, so the chips always add up to Assets exactly. `net` is the raw
    assets + liabilities rounded once, so it equals the Dashboard's
    `cash_net_worth` after rounding to 2 dp; when several groups carry sub-paisa
    remainders, `assets + liabilities` can differ from `net` by a few paise.
    `excluded_total` is likewise the sum of the listed (rounded) exclusions.
    """
    names = names or {}
    raw_assets = Decimal(0)
    raw_liabilities = Decimal(0)
    groups: dict[str, Decimal] = {}
    excluded_raw: dict[str, Decimal] = {}
    as_of: date | None = None
    for r in rows:
        if r.account.startswith("Assets:"):
            if r.account.startswith(PREPAID_TAX_PREFIXES):
                excluded_raw[r.account] = (
                    excluded_raw.get(r.account, Decimal(0)) + r.inr_amount
                )
            else:
                raw_assets += r.inr_amount
                group = r.account.split(":")[1]
                groups[group] = groups.get(group, Decimal(0)) + r.inr_amount
        elif r.account.startswith("Liabilities:"):
            raw_liabilities += r.inr_amount
        else:
            continue
        if as_of is None or r.date > as_of:
            as_of = r.date

    rounded_groups = {g: _round(v) for g, v in groups.items()}
    by_group = [
        {"group": g, "amount": _money(v)}
        for g, v in sorted(rounded_groups.items(), key=lambda kv: (-kv[1], kv[0]))
        if v != 0
    ]
    rounded_excluded = {a: _round(v) for a, v in excluded_raw.items()}
    excluded = [
        {
            "account": a,
            "account_display": display_account(a, names),
            "amount": _money(v),
        }
        for a, v in sorted(rounded_excluded.items())
        if v != 0
    ]
    return {
        "as_of": as_of.isoformat() if as_of is not None else None,
        "assets": _money(sum(rounded_groups.values(), Decimal(0))),
        "liabilities": _money(raw_liabilities),
        "net": _money(raw_assets + raw_liabilities),
        "by_group": by_group,
        "excluded": excluded,
        "excluded_total": _money(sum(rounded_excluded.values(), Decimal(0))),
    }
