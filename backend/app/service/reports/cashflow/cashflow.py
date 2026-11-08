from __future__ import annotations

from collections.abc import Callable
from datetime import date as date_type
from decimal import ROUND_HALF_UP, Decimal

from backend.app.models.domain.accounts import INVESTMENT_EXPENSE, is_spending_account
from backend.app.service.reports.cashflow import period
from backend.app.service.reports.cashflow.money import TENTH, allocate, cents, q
from backend.app.service.reports.cashflow.period import DateRange
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    top_category,
)


def _in_fy(rows: list[PostingRow], fy: int) -> list[PostingRow]:
    """India FY: 1 Apr `fy` .. 31 Mar `fy+1`."""
    start = date_type(fy, 4, 1)
    end = date_type(fy + 1, 3, 31)
    return [r for r in rows if start <= r.date <= end]


def available_years(rows: list[PostingRow]) -> dict:
    """Distinct calendar years and Indian FYs present in the postings.

    `calendar_years` = sorted distinct `r.date.year`.
    `fiscal_years` = sorted distinct India FY (April-start year) per the
    repo convention (`_in_fy`): a date's FY = `date.year` if
    `date.month >= 4` else `date.year - 1`. Both ascending, deduplicated,
    plain ints. Empty input -> both empty lists (no error).
    """
    calendar = {r.date.year for r in rows}
    fiscal = {r.date.year if r.date.month >= 4 else r.date.year - 1 for r in rows}
    return {
        "calendar_years": sorted(calendar),
        "fiscal_years": sorted(fiscal),
    }


def _income(rows: list[PostingRow]) -> Decimal:
    # Income accounts carry negative numbers (credits); flip the sign for a positive total.
    return -sum(
        (r.inr_amount for r in rows if r.account.startswith("Income:")), Decimal(0)
    )


def _expenses(rows: list[PostingRow]) -> Decimal:
    # Spending only: `Expenses:Investment:*` is investment cost (`_investment_flow`).
    return sum(
        (r.inr_amount for r in rows if is_spending_account(r.account)), Decimal(0)
    )


# NOTE: intentionally no trailing colon (unlike dashboard._INVESTMENT_PREFIX,
# which uses "Assets:Investment:") so a bare `Assets:Investment` leaf also
# classifies. A sibling like `Assets:InvestmentReserve` would match here; none
# exists today. _LIQUID_PREFIXES mirrors dashboard._LIQUID_PREFIXES verbatim.
_INVESTMENT_PREFIX = "Assets:Investment"
_LIQUID_PREFIXES = ("Assets:Checking:", "Assets:Cash")


def _is_tds(account: str) -> bool:
    # TDS withheld from income: an Income leaf whose path carries a `TDS` segment.
    return account.startswith("Income:") and "TDS" in account.split(":")


def _is_tax_paid(account: str) -> bool:
    # A real tax payment: an Expenses leaf whose path carries a `Tax`/`Taxes` segment.
    if not is_spending_account(account):
        return False
    segs = account.split(":")
    return "Tax" in segs or "Taxes" in segs


def _tds_withheld(rows: list[PostingRow]) -> Decimal:
    # The TDS leg is a POSITIVE contra leg under Income: (gross is the negative
    # leg). Read the withheld magnitude directly off the positive leg.
    return sum((r.inr_amount for r in rows if _is_tds(r.account)), Decimal(0))


def _tax_paid(rows: list[PostingRow]) -> Decimal:
    return sum((r.inr_amount for r in rows if _is_tax_paid(r.account)), Decimal(0))


def _tax(rows: list[PostingRow]) -> Decimal:
    # Combined Tax series = TDS withheld (informational) + tax actually paid.
    return _tds_withheld(rows) + _tax_paid(rows)


def _expenses_ex_tax(rows: list[PostingRow]) -> Decimal:
    # Expenses minus the tax-paid slice, to avoid double-counting it in the Tax bar.
    return sum(
        (
            r.inr_amount
            for r in rows
            if is_spending_account(r.account) and not _is_tax_paid(r.account)
        ),
        Decimal(0),
    )


def _liabilities_flow(rows: list[PostingRow]) -> Decimal:
    # A principal repayment posts a positive amount into the Liabilities account
    # (reducing its negative balance). Report it as-is so a repayment shows as a
    # positive outflow bar, mirroring Paisa.
    return sum(
        (r.amount for r in rows if r.account.startswith("Liabilities:")), Decimal(0)
    )


def _investment_flow(rows: list[PostingRow]) -> Decimal:
    # Sum the INR cost basis, not the raw `amount`: a commodity holding posting's
    # `amount` is the share count (e.g. 100 units), which would leak into the
    # rupee flow. `inr_amount` carries units * lot-cost for commodity postings and
    # mirrors `amount` for plain INR postings (matches investments/networth/dashboard).
    # `Expenses:Investment:*` (the rounding plug, stamp duty) is the cost of
    # investing, so it counts here and never as spending.
    return sum(
        (
            r.inr_amount
            for r in rows
            if r.account.startswith(_INVESTMENT_PREFIX)
            or top_category(r.account) == INVESTMENT_EXPENSE
        ),
        Decimal(0),
    )


def _liquid_flow(rows: list[PostingRow]) -> Decimal:
    return sum(
        (r.amount for r in rows if r.account.startswith(_LIQUID_PREFIXES)),
        Decimal(0),
    )


def _fy_label(fy: int) -> str:
    return f"FY {fy}-{(fy + 1) % 100:02d}"


def income_statement_pnl(rows: list[PostingRow], fys: list[int]) -> dict:
    """Multi-FY income-statement P&L: line items by top-category x FY columns.

    Two sections only (income, expenses) plus a per-column net row. Income
    accounts carry credits (negative) and are sign-flipped positive; expenses
    stay positive. India FY windowing (Apr->Mar) via _in_fy. Decimal throughout;
    str() only at the boundary. Empty cells are "0".
    """
    columns = [{"fy": fy, "label": _fy_label(fy)} for fy in fys]

    # Per-FY windowed rows, computed once.
    windowed = [_in_fy(rows, fy) for fy in fys]

    sections: list[dict] = []
    section_totals: dict[str, list[Decimal]] = {}

    for key, label, keep, flip in (
        ("income", "Revenue", lambda a: a.startswith("Income:"), True),
        ("expenses", "Expenses", is_spending_account, False),
    ):
        # category -> per-column Decimal amounts
        by_cat: dict[str, list[Decimal]] = {}
        for i, fy_rows in enumerate(windowed):
            for r in fy_rows:
                if not keep(r.account):
                    continue
                cat = top_category(r.account)
                amounts = by_cat.setdefault(cat, [Decimal(0) for _ in fys])
                amounts[i] += -r.inr_amount if flip else r.inr_amount

        # Keep only categories with non-zero activity in at least one column.
        kept: list[tuple[str, list[Decimal]]] = [
            (cat, amounts)
            for cat, amounts in by_cat.items()
            if any(a != 0 for a in amounts)
        ]
        # Sort by total across columns descending; tie-break by category ascending.
        kept.sort(key=lambda item: (-sum(item[1], Decimal(0)), item[0]))

        totals = [Decimal(0) for _ in fys]
        for _cat, amounts in kept:
            for i, a in enumerate(amounts):
                totals[i] += a

        section_totals[key] = totals
        sections.append(
            {
                "key": key,
                "label": label,
                "line_items": [
                    {
                        "category": cat,
                        "amounts": [str(a) for a in amounts],
                    }
                    for cat, amounts in kept
                ],
                "totals": [str(t) for t in totals],
            }
        )

    net = [
        str(section_totals["income"][i] - section_totals["expenses"][i])
        for i in range(len(fys))
    ]

    return {
        "fys": list(fys),
        "columns": columns,
        "sections": sections,
        "net": net,
    }


def monthly_series(rows: list[PostingRow], fy: int) -> list[dict]:
    """12 entries in FY order Apr..Mar (months with no activity emit "0").

    Each entry carries five flow series (income, expenses, liabilities,
    investment, tax) plus `balance`, the running cumulative liquid
    (Checking + Cash) position at that month's end.

    - `income` is plain `_income` (= -sum of Income legs). It is already NET of
      TDS because the positive `Income:*:TDS` contra leg lives under `Income:` and
      is already folded in — no extra subtraction.
    - `tax` = TDS withheld (informational, never touched the bank) + tax actually
      paid (`Expenses:*:Tax(es)`, a real liquid outflow).
    - `expenses` excludes the tax-paid slice so the Expenses bar and the Tax bar
      never double-count.
    - `liabilities` reports the amount as-is: liability accounts carry negative
      balances, so a principal repayment posts a positive amount (a positive
      outflow bar) and new borrowing posts a negative amount (see liabilities.py
      for the authoritative repaid/drawn sign convention).
    - `balance` is driven SOLELY by liquid postings, so withheld TDS (no liquid
      leg) does not move the line while tax paid (real liquid debit) does.

    All amounts are decimal strings.
    """
    legacy = ("month", "income", "expenses", "liabilities", "investment", "tax")
    return [
        {**{k: m[k] for k in legacy}, "balance": m["balance"]}
        for m in monthly_series_range(rows, period.fy_range(fy))
    ]


def monthly_series_range(rows: list[PostingRow], r: DateRange) -> list[dict]:
    """`monthly_series` semantics over `period.months_in(r)`, each month clipped
    to `r`, plus `year` and `label` per entry (`label` carries the year when
    the months cover more than one calendar year). `balance` opens from the
    liquid net strictly before `r.start`. Amounts are unquantized decimal
    strings, as `monthly_series`."""
    windowed = period.in_range(rows, r)
    balance = _liquid_flow([row for row in rows if row.date < r.start])
    months = period.months_in(r)
    with_year = period.spans_years(months)
    out: list[dict] = []
    for year, month in months:
        mr = [
            row for row in windowed if row.date.year == year and row.date.month == month
        ]
        balance += _liquid_flow(mr)
        out.append(
            {
                "year": year,
                "month": month,
                "label": period.month_label(year, month, with_year),
                "income": str(_income(mr)),
                "expenses": str(_expenses_ex_tax(mr)),
                "liabilities": str(_liabilities_flow(mr)),
                "investment": str(_investment_flow(mr)),
                "tax": str(_tax(mr)),
                "balance": str(balance),
            }
        )
    return out


_MONTH_AMOUNTS = (
    "income",
    "expenses",
    "liabilities",
    "investment",
    "tax",
    "balance",
)


def _by_top_category(
    rows: list[PostingRow], keep: Callable[[str], bool], sign: int
) -> list[dict]:
    """Nonzero top-category totals, desc then category asc, rounded so the
    lines sum exactly to the rounded section total."""
    totals: dict[str, Decimal] = {}
    for row in rows:
        if keep(row.account):
            cat = top_category(row.account)
            totals[cat] = totals.get(cat, Decimal(0)) + sign * row.inr_amount
    ordered = dict(
        sorted(
            ((cat, amt) for cat, amt in totals.items() if amt != 0),
            key=lambda kv: (-kv[1], kv[0]),
        )
    )
    rounded = allocate(ordered, cents(sum(ordered.values(), Decimal(0))))
    return [{"category": cat, "amount": amt} for cat, amt in rounded.items()]


def _is_income(account: str) -> bool:
    return account.startswith("Income:")


def statement(rows: list[PostingRow], r: DateRange) -> dict:
    """`CashflowStatement`: top-category income (net of TDS) and spending
    totals (tax paid included) in `r`, desc, then category asc; zero
    categories are dropped. Each section's lines sum exactly to its rounded
    total, and `net` is the rounded income minus the rounded expenses, so it
    equals `overview` `totals.saved`."""
    windowed = period.in_range(rows, r)
    return {
        "income": _by_top_category(windowed, _is_income, -1),
        "expenses": _by_top_category(windowed, is_spending_account, 1),
        "net": str(cents(_income(windowed)) - cents(_expenses(windowed))),
    }


def _flow_point(key: str, label: str, rows: list[PostingRow]) -> dict:
    income = _income(rows)
    expenses = _expenses(rows)
    return {
        "key": key,
        "label": label,
        "income": q(income),
        "expenses": q(expenses),
        "invested": q(_investment_flow(rows)),
        "net": str(cents(income) - cents(expenses)),
    }


def flow_series(rows: list[PostingRow], r: DateRange) -> list[dict]:
    """One point per day (`period.granularity(r) == "day"`) or per month of
    `r`, zero-filled, chronological, with the `totals` semantics per bucket:
    `income` net of TDS, `expenses` all spending (tax paid included),
    `invested`, and `net` = rounded income - rounded expenses. Keys and labels
    as the expense overview's `series`. `rows` must already be windowed to `r`."""
    if period.granularity(r) == "day":
        by_day: dict[date_type, list[PostingRow]] = {}
        for row in rows:
            by_day.setdefault(row.date, []).append(row)
        return [
            _flow_point(d.isoformat(), period.day_label(d), by_day.get(d, []))
            for d in period.days_in(r)
        ]
    by_month: dict[tuple[int, int], list[PostingRow]] = {}
    for row in rows:
        by_month.setdefault((row.date.year, row.date.month), []).append(row)
    months = period.months_in(r)
    with_year = period.spans_years(months)
    return [
        _flow_point(
            f"{y:04d}-{m:02d}",
            period.month_label(y, m, with_year),
            by_month.get((y, m), []),
        )
        for y, m in months
    ]


def overview(rows: list[PostingRow], r: DateRange) -> dict:
    """`CashflowOverviewResponse`: totals, series, by_month and statement for `r`.

    `expenses` is all spending, tax paid included (it equals the expense
    overview's `headline.net` for `r` with `funding=all`); `invested` is the
    `Assets:Investment*` flow plus the `Expenses:Investment*` cost; `saved` is
    the rounded `income - expenses`; `savings_rate` is `saved / income * 100`
    at 1 dp (full precision), or None when income <= 0."""
    windowed = period.in_range(rows, r)
    income = _income(windowed)
    expenses = _expenses(windowed)
    saved = income - expenses
    rate = (
        str((saved / income * 100).quantize(TENTH, rounding=ROUND_HALF_UP))
        if income > 0
        else None
    )
    return {
        "start": r.start.isoformat(),
        "end": r.end.isoformat(),
        "totals": {
            "income": q(income),
            "expenses": q(expenses),
            "invested": q(_investment_flow(windowed)),
            "saved": str(cents(income) - cents(expenses)),
            "savings_rate": rate,
        },
        "granularity": period.granularity(r),
        "series": flow_series(windowed, r),
        "by_month": [
            {**m, **{k: q(Decimal(m[k])) for k in _MONTH_AMOUNTS}}
            for m in monthly_series_range(rows, r)
        ],
        "statement": statement(rows, r),
    }
