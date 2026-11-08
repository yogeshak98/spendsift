"""The group cards of Net Worth · Overview: one builder per group.

Each builder takes the shared `Ctx` (the postings, the reporting date and the
liability read-model inputs) and returns a `Group`, or None when the group has
no accounts with a balance. Balances are ledger cost basis, the same figures
Balance sums.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from backend.app.models.domain.accounts import BANK_PREFIXES

from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    TransactionRow,
    display_account,
    open_meta_from_entries,
)
from backend.app.service.reports.liabilities import in_transit, liabilities, repayment
from backend.app.service.reports.liabilities.loan_terms import LoanTerms
from backend.app.service.reports.networth.account_groups import group_of, vehicle_of
from backend.app.service.reports.networth.liquidity import (
    average_monthly_spend,
    months_of_cover,
)

STALE_IMPORT_DAYS = 14
TOP_ACCOUNTS = 3
IN_TRANSIT_LABEL = "Card payments in transit"
_CENT = Decimal("0.01")


def money(value: Decimal) -> str:
    q = value.quantize(_CENT, rounding=ROUND_HALF_UP)
    return str(q.copy_abs() if q == 0 else q)


@dataclass
class Group:
    id: str
    total: Decimal
    accounts: list[dict]
    count: int
    facts: dict


@dataclass
class Ctx:
    """Everything a group builder reads. `balances` maps each counted account
    to its signed ledger balance (liabilities negative); `owed` is the same for
    liability groups with the sign flipped (positive = you owe)."""

    rows: list[PostingRow]
    names: dict[str, str]
    report_date: date
    balances: dict[str, Decimal]
    transactions: list[TransactionRow]
    closed: frozenset[str]
    entries: list
    ledger_storage: Path
    card_properties: dict
    card_clearing_account: str
    card_emi_account: str | None
    card_expiry_soon_days: int
    payments: list = field(default_factory=list)
    vehicles: dict = field(default_factory=dict)

    def accounts_in(self, gid: str) -> list[tuple[str, Decimal]]:
        return [
            (a, v)
            for a, v in sorted(self.balances.items())
            if group_of(a) == gid and money(v) != "0.00"
        ]

    def owed_in(self, gid: str) -> list[tuple[str, Decimal]]:
        return [(a, -v) for a, v in self.accounts_in(gid)]


def _tail(account: str) -> str:
    """`Assets:Checking:HDFC` -> `HDFC`: everything below the second segment."""
    parts = account.split(":")
    return " · ".join(parts[2:]) if len(parts) > 2 else parts[-1]


def _short(d: date) -> str:
    return f"{d.day} {d:%b}"


def _total(items: list[tuple[str, Decimal]]) -> Decimal:
    return sum((v for _, v in items), Decimal(0))


def account_row(
    account: str,
    name: str,
    balance: Decimal,
    meta: str = "",
    *,
    direction: str | None = None,
    stale: bool | None = None,
) -> dict:
    return {
        "account": account,
        "name": name,
        "balance": money(balance),
        "direction": direction,
        "meta": meta,
        "stale": stale,
    }


def empty_facts() -> dict:
    return {
        "xirr_pct": None,
        "gain": None,
        "invested": None,
        "valuation": None,
        "months_of_cover": None,
        "change_this_month": None,
        "stale_count": None,
        "includes_in_transit": None,
        "emi_per_month": None,
        "last_ends": None,
        "owed_net_of_in_transit": None,
        "paid_awaiting_statement": None,
        "next_due": None,
        "you_owe": None,
        "owed_to_you": None,
    }


def _last_activity(rows: list[PostingRow]) -> dict[str, date]:
    out: dict[str, date] = {}
    for r in rows:
        if r.account not in out or r.date > out[r.account]:
            out[r.account] = r.date
    return out


def stale_bank_accounts(
    rows: list[PostingRow], report_date: date, closed: frozenset[str]
) -> dict[str, date]:
    """Each stale bank account and its last posting date: open, a balance that
    shows as non-zero (2 dp, half up), and no posting in the `STALE_IMPORT_DAYS`
    before `report_date`. Cash is kept by hand, so it has no import to go stale.
    The one rule for the Net Worth Bank & cash card and the Dashboard."""
    balance: dict[str, Decimal] = {}
    bank = [r for r in rows if r.account.startswith(BANK_PREFIXES)]
    for r in bank:
        balance[r.account] = balance.get(r.account, Decimal(0)) + r.inr_amount
    cutoff = report_date - timedelta(days=STALE_IMPORT_DAYS)
    return {
        a: seen
        for a, seen in _last_activity(bank).items()
        if seen < cutoff and a not in closed and money(balance[a]) != "0.00"
    }


def investments_group(ctx: Ctx) -> Group | None:
    """Rows are vehicles (ledger cost by vehicle), not raw accounts; the facts
    are the pooled XIRR of what is priced, the gain on it, and the invested
    total across every vehicle."""
    items = ctx.accounts_in("investments")
    if not items:
        return None
    by_vehicle: dict[str, Decimal] = {}
    labels: dict[str, str] = {}
    for account, amount in items:
        vehicle = vehicle_of(account)
        if vehicle is not None:
            by_vehicle[vehicle[0]] = by_vehicle.get(vehicle[0], Decimal(0)) + amount
            labels[vehicle[0]] = vehicle[1]
    info = {v["key"]: v for v in ctx.vehicles["vehicles"]}
    accounts = []
    for key, amount in sorted(by_vehicle.items(), key=lambda kv: kv[1], reverse=True)[
        :TOP_ACCOUNTS
    ]:
        v = info.get(key)
        if v is None:
            meta = "at cost"
        else:
            n = v["holdings"]
            meta = f"{n} holding" + ("" if n == 1 else "s")
            if v["xirr_pct"] is not None:
                meta += f" · XIRR {v['xirr_pct']}%"
            elif v["at_cost"]:
                meta += " · at cost"
        accounts.append(account_row(key, labels[key], amount, meta))
    facts = empty_facts()
    facts.update(
        xirr_pct=ctx.vehicles["xirr_pct"],
        gain=money(ctx.vehicles["gain"]),
        invested=money(ctx.vehicles["invested"]),
    )
    return Group("investments", _total(items), accounts, len(by_vehicle), facts)


def property_group(ctx: Ctx) -> Group | None:
    items = ctx.accounts_in("property")
    if not items:
        return None
    since: dict[str, int] = {}
    for r in ctx.rows:
        if group_of(r.account) == "property" and r.inr_amount > 0:
            since[r.account] = min(since.get(r.account, r.date.year), r.date.year)
    accounts = [
        account_row(
            a,
            display_account(_tail(a), ctx.names),
            v,
            f"at cost · since {since[a]}" if a in since else "at cost",
        )
        for a, v in sorted(items, key=lambda kv: kv[1], reverse=True)[:TOP_ACCOUNTS]
    ]
    facts = empty_facts()
    facts.update(valuation="at_cost")
    return Group("property", _total(items), accounts, len(items), facts)


def bank_group(ctx: Ctx) -> Group | None:
    items = ctx.accounts_in("bank")
    if not items:
        return None
    activity = _last_activity(ctx.rows)
    stale_accounts = stale_bank_accounts(ctx.rows, ctx.report_date, ctx.closed)
    accounts = []
    for a, v in sorted(items, key=lambda kv: kv[1], reverse=True):
        seen = activity.get(a)
        stale = a in stale_accounts
        accounts.append(
            account_row(
                a,
                _tail(a),
                v,
                f"last activity {_short(seen)}" if seen else "no activity",
                stale=stale,
            )
        )
    liquid = _total(items)
    month_change = sum(
        (
            r.inr_amount
            for r in ctx.rows
            if group_of(r.account) == "bank"
            and (r.date.year, r.date.month)
            == (ctx.report_date.year, ctx.report_date.month)
        ),
        Decimal(0),
    )
    facts = empty_facts()
    facts.update(
        months_of_cover=months_of_cover(
            liquid, average_monthly_spend(ctx.rows, ctx.report_date)
        ),
        change_this_month=money(month_change),
        stale_count=len(stale_accounts),
    )
    return Group("bank", liquid, accounts[:TOP_ACCOUNTS], len(items), facts)


def other_group(ctx: Ctx) -> Group | None:
    items = ctx.accounts_in("other")
    if not items:
        return None
    accounts = []
    for a, v in sorted(items, key=lambda kv: abs(kv[1]), reverse=True)[:TOP_ACCOUNTS]:
        transit = a == ctx.card_clearing_account
        accounts.append(
            account_row(
                a,
                IN_TRANSIT_LABEL if transit else display_account(_tail(a), ctx.names),
                v,
                "paid to a card, not on its statement yet" if transit else "",
            )
        )
    facts = empty_facts()
    facts.update(includes_in_transit=in_transit.summary(ctx.payments)["amount"])
    return Group("other", _total(items), accounts, len(items), facts)


def loans_group(ctx: Ctx) -> Group | None:
    items = ctx.owed_in("loans")
    if not items:
        return None
    terms = LoanTerms.from_file(ctx.ledger_storage / "rules" / "loan-terms.yaml")
    schedule = repayment.schedules(
        ctx.rows,
        terms,
        ctx.report_date,
        account_meta=open_meta_from_entries(ctx.entries),
        pool_account=ctx.card_emi_account,
        closed=ctx.closed,
        transactions=ctx.transactions,
    )
    plans = {loan["account"]: loan for loan in schedule["loans"]}
    accounts = []
    emi_total = Decimal(0)
    last_end: str | None = None
    for a, v in sorted(items, key=lambda kv: kv[1], reverse=True):
        loan = plans.get(a)
        label = (loan or {}).get("label") or _tail(a)
        if loan is None or not loan["terms_set"]:
            meta = "terms not set"
        else:
            emi_total += Decimal(loan["emi"])
            end = loan["payoff_month"]
            last_end = end if last_end is None else max(last_end, end)
            paid, term = loan["paid_instalments"], loan["term_months"]
            meta = (
                f"{term - paid} of {term} left"
                if paid is not None
                else f"{loan['emi']}/month · ends {end}"
            )
        accounts.append(account_row(a, label, v, meta, direction="i_owe"))
    facts = empty_facts()
    facts.update(
        emi_per_month=money(emi_total) if emi_total else None, last_ends=last_end
    )
    return Group("loans", _total(items), accounts[:TOP_ACCOUNTS], len(items), facts)


def cards_group(ctx: Ctx) -> Group | None:
    items = ctx.owed_in("cards")
    if not items:
        return None
    summary = liabilities.credit_card_summary(
        ctx.transactions,
        properties=ctx.card_properties,
        today=ctx.report_date,
        closed=ctx.closed,
        postings=ctx.rows,
        expiry_soon_days=ctx.card_expiry_soon_days,
    )
    info = {c["account"]: c for c in summary}
    per_card = in_transit.per_card(ctx.payments)
    dues: list[date] = []
    accounts = []
    for a, v in sorted(items, key=lambda kv: kv[1], reverse=True):
        card = info.get(a, {})
        in_flight = Decimal(per_card[a]["amount"]) if a in per_card else Decimal(0)
        due = next(
            (
                date.fromisoformat(card[k])
                for k in ("current_due_date", "next_due_date")
                if card.get(k) and date.fromisoformat(card[k]) >= ctx.report_date
            ),
            None,
        )
        if due is not None:
            dues.append(due)
        last4 = card.get("last4")
        name = _tail(a)
        if last4 and name.endswith(last4):
            name = name[: -len(last4)]
        meta = [f"••{last4}"] if last4 else []
        if due is not None:
            meta.append(f"due {_short(due)}")
        if in_flight:
            meta.append(f"{money(in_flight)} paid, awaiting statement")
        # The row shows what is still owed: net of money already paid in.
        accounts.append(
            account_row(a, name, v - in_flight, " · ".join(meta), direction="i_owe")
        )
    outstanding = _total(items)
    transit = in_transit.summary(ctx.payments)["amount"]
    facts = empty_facts()
    facts.update(
        owed_net_of_in_transit=money(outstanding - Decimal(transit)),
        paid_awaiting_statement=transit,
        next_due=min(dues).isoformat() if dues else None,
    )
    return Group("cards", outstanding, accounts[:TOP_ACCOUNTS], len(items), facts)


def _direction(owed: Decimal) -> str:
    return "i_owe" if owed > 0 else "owed_to_me"


def people_group(ctx: Ctx) -> Group | None:
    items = ctx.owed_in("people")
    if not items:
        return None
    accounts = [
        account_row(a, _tail(a), abs(v), direction=_direction(v))
        for a, v in sorted(items, key=lambda kv: abs(kv[1]), reverse=True)[
            :TOP_ACCOUNTS
        ]
    ]
    facts = empty_facts()
    facts.update(
        you_owe=money(sum((v for _, v in items if v > 0), Decimal(0))),
        owed_to_you=money(sum((-v for _, v in items if v < 0), Decimal(0))),
    )
    return Group("people", _total(items), accounts, len(items), facts)


def other_liabilities_group(ctx: Ctx) -> Group | None:
    items = ctx.owed_in("other_liabilities")
    if not items:
        return None
    accounts = [
        account_row(a, _tail(a), abs(v), direction=_direction(v))
        for a, v in sorted(items, key=lambda kv: abs(kv[1]), reverse=True)[
            :TOP_ACCOUNTS
        ]
    ]
    return Group(
        "other_liabilities", _total(items), accounts, len(items), empty_facts()
    )
