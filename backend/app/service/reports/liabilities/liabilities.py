from __future__ import annotations

from collections.abc import Mapping
from datetime import date as date_type
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow
from backend.app.service.reports.liabilities import card_cycle
from backend.app.service.reports.liabilities import interest as interest_report
from backend.app.service.reports.liabilities.card_flows import (
    CardFlow,
    classify_card_posting,
)
from backend.app.service.reports.liabilities.loan_terms import LoanTerms

_CC_PREFIX = "Liabilities:CreditCard:"


def balances(rows: list[PostingRow]) -> list[dict]:
    totals: dict[str, Decimal] = {}
    for r in rows:
        if r.account.startswith("Liabilities:"):
            totals[r.account] = totals.get(r.account, Decimal(0)) + r.amount
    return [
        {
            "account": acct,
            "outstanding": str(-amt),
        }  # liabilities carry negative numbers
        for acct, amt in sorted(totals.items())
    ]


def _direction(net: Decimal) -> str:
    """Ledger net -> who owes whom. A negative net is money the user owes; a
    positive net (a `Liabilities:People:X` loan to X, an overpaid card) is
    owed to the user."""
    if net < 0:
        return "i_owe"
    if net > 0:
        return "owed_to_me"
    return "settled"


def balance_rows(
    postings: list[PostingRow],
    transactions: list[TransactionRow],
    terms: LoanTerms,
    *,
    closed: frozenset[str] = frozenset(),
    apr_overrides: Mapping[str, tuple[Decimal, str]] | None = None,
) -> list[dict]:
    """Per open liability account: drawn (magnitude of negative deltas),
    repaid (positive deltas), `direction` and `amount` (the magnitude of the
    ledger net), the legacy `balance` (`-net`, may be negative), all-time
    interest, and the APR with its source. All amounts are decimal strings.

    Accounts in `closed` are omitted; settled accounts (net 0) are returned.
    APR is never derived here: `apr_overrides[account]` (rate, source) wins,
    else the loan-terms file entry (`apr_source="file"`), else both None.
    """
    drawn: dict[str, Decimal] = {}
    repaid: dict[str, Decimal] = {}
    net: dict[str, Decimal] = {}
    for r in postings:
        if not r.account.startswith("Liabilities:") or r.account in closed:
            continue
        net[r.account] = net.get(r.account, Decimal(0)) + r.amount
        if r.amount < 0:
            drawn[r.account] = drawn.get(r.account, Decimal(0)) + (-r.amount)
        elif r.amount > 0:
            repaid[r.account] = repaid.get(r.account, Decimal(0)) + r.amount

    interest_by_account: dict[str, Decimal] = {}
    for row in interest_report.by_account_by_month(transactions):
        acct = row["account"]
        interest_by_account[acct] = interest_by_account.get(acct, Decimal(0)) + Decimal(
            row["amount"]
        )

    overrides = apr_overrides or {}
    out: list[dict] = []
    for account in sorted(net):
        apr: str | None = None
        apr_source: str | None = None
        if account in overrides:
            rate, apr_source = overrides[account]
            apr = str(rate)
        else:
            t = terms.get(account)
            if t:
                apr, apr_source = str(t["annual_rate_pct"]), "file"
        account_net = net[account]
        out.append(
            {
                "account": account,
                "drawn": str(drawn.get(account, Decimal(0))),
                "repaid": str(repaid.get(account, Decimal(0))),
                "balance": str(-account_net),  # legacy: negative ledger -> positive
                "interest": str(interest_by_account.get(account, Decimal(0))),
                "apr": apr,
                "apr_source": apr_source,
                "direction": _direction(account_net),
                "amount": str(abs(account_net)),
            }
        )
    return out


_NO_CYCLE: dict = {
    "last4": None,
    "statement_day": None,
    "due_day": None,
    "properties_source": None,
    "last_statement_date": None,
    "next_statement_date": None,
    "current_due_date": None,
    "next_due_date": None,
    "statement_expected": False,
    "expiry": None,
    "expiry_status": None,
}


def _iso(value: date_type | None) -> str | None:
    return None if value is None else value.isoformat()


def _card_cycle_fields(
    props: Mapping[str, Any] | None,
    latest_line: date_type | None,
    today: date_type,
    expiry_soon_days: int,
) -> dict:
    """The tile's property and cycle fields from the card's effective
    properties `{statement_day, due_day, last4, expiry, source}`."""
    if not props:
        return dict(_NO_CYCLE)
    statement_day = props.get("statement_day")
    due_day = props.get("due_day")
    expiry = props.get("expiry")
    out = {
        **_NO_CYCLE,
        "last4": props.get("last4"),
        "statement_day": statement_day,
        "due_day": due_day,
        "expiry": expiry,
        "expiry_status": card_cycle.expiry_status(expiry, today, expiry_soon_days),
    }
    if statement_day is None:
        return out
    cycle = card_cycle.cycle_dates(statement_day, due_day, today)
    last = cycle["last_statement_date"]
    return {
        **out,
        "properties_source": props.get("source"),
        "last_statement_date": last.isoformat(),
        "next_statement_date": cycle["next_statement_date"].isoformat(),
        "current_due_date": _iso(cycle["current_due_date"]),
        "next_due_date": _iso(cycle["next_due_date"]),
        "statement_expected": card_cycle.statement_expected(last, latest_line),
    }


def _money_2dp(value: Decimal) -> str:
    """2 dp, ROUND_HALF_UP; a zero is "0.00", never "-0.00"."""
    q = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(q.copy_abs() if q == 0 else q)


def credit_card_summary(
    transactions: list[TransactionRow],
    *,
    properties: Mapping[str, Mapping[str, Any]] | None = None,
    today: date_type | None = None,
    closed: frozenset[str] = frozenset(),
    postings: list[PostingRow] | None = None,
    expiry_soon_days: int,
) -> list[dict]:
    """One tile per card with postings, closed cards omitted.

    `properties` maps a card to its effective `{statement_day, due_day, last4,
    expiry, source}` (stored, else suggested). With it and `today`, each tile
    carries the statement cycle and the expiry with its status (expiring soon
    within `expiry_soon_days`, which the caller takes from the
    `card_expiry_soon_days` setting); without either, those fields are
    null/false.

    `postings` (every ledger posting, including those of transactions without
    a txn-id, such as opening balances) makes `outstanding` and
    `last_activity_date` the card's ledger figures; without it they come from
    `transactions`."""
    totals: dict[str, Decimal] = {}
    last_activity: dict[str, date_type] = {}
    latest_line: dict[str, date_type] = {}
    rows = (
        postings
        if postings is not None
        else [p for txn in transactions for p in txn.postings]
    )
    for p in rows:
        if not p.account.startswith(_CC_PREFIX) or p.account in closed:
            continue
        totals[p.account] = totals.get(p.account, Decimal(0)) + p.amount
        if p.account not in last_activity or p.date > last_activity[p.account]:
            last_activity[p.account] = p.date
    for txn in transactions:
        cards = {
            p.account
            for p in txn.postings
            if p.account.startswith(_CC_PREFIX) and p.account not in closed
        }
        if properties is None or today is None:
            continue
        for card in cards:
            if card not in properties:
                continue
            if card in latest_line and txn.date <= latest_line[card]:
                continue
            kind, _ = classify_card_posting(txn, card)
            if kind != "adjustment":
                latest_line[card] = txn.date
    out: list[dict] = []
    for account, amount in sorted(totals.items()):
        row = {
            "account": account,
            "outstanding": _money_2dp(-amount),
            "last_activity_date": last_activity[account].isoformat(),
        }
        if properties is None or today is None:
            row.update(_NO_CYCLE)
        else:
            row.update(
                _card_cycle_fields(
                    properties.get(account),
                    latest_line.get(account),
                    today,
                    expiry_soon_days,
                )
            )
        out.append(row)
    return out


def credit_card_detail(transactions: list[TransactionRow], account: str) -> list[dict]:
    rows: list[dict] = []
    for txn in transactions:
        if not any(p.account == account for p in txn.postings):
            continue
        kind, amount = classify_card_posting(txn, account)
        rows.append(
            {
                "txn_id": txn.txn_id,
                "date": txn.date.isoformat(),
                "narration": txn.narration,
                "amount": str(amount),
                "kind": kind,
                "is_payment": kind == "payment",
            }
        )
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def _fy_months(fy: int) -> list[tuple[int, int]]:
    """The 12 (year, month) pairs of an Indian FY: Apr `fy` .. Mar `fy+1`."""
    out: list[tuple[int, int]] = []
    y, m = fy, 4
    for _ in range(12):
        out.append((y, m))
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


_FLOW_FIELDS: tuple[CardFlow, ...] = (
    "purchase",
    "charge",
    "payment",
    "refund",
    "emi",
    "adjustment",
)


def _flow_value(kind: CardFlow, amount: Decimal) -> Decimal:
    """A card posting's contribution to its month field.

    purchases/charges are positive spend magnitudes (a merchant debit is
    negative in the ledger). payments/refunds keep the ledger sign, so money
    sent back to the bank reduces payments. emi/adjustments are the debt added
    (negated ledger sign), so an EMI conversion credit shows negative.
    """
    if kind in ("payment", "refund"):
        return amount
    return -amount


def credit_card_monthly(
    transactions: list[TransactionRow],
    fy: int,
    account: str | None = None,
) -> dict:
    """FY-scoped (Apr–Mar) monthly credit-card activity.

    Every card posting is classified by `classify_card_posting` (sign plus
    dominant counter-leg) and lands in exactly one month field, all decimal
    strings:

    - `purchases`: spend on merchants (debit vs `Expenses:*` other than fees
      and interest, vs an asset other than a bank, checking or the clearing
      account -- a cash advance, a wallet load -- or vs a root not listed
      here such as `Liabilities:Cash:*`).
    - `charges`: fees and interest (debit vs `Expenses:Fees:*` /
      `Expenses:Interest:*`).
    - `payments`: bill payments, signed: credits vs `Assets:*`,
      `Liabilities:Cash:*` or `Equity:*`, less any card debit vs
      `Assets:Bank:*`, `Assets:Checking:*` or the card clearing account (an
      excess payment returned to the bank).
    - `refunds`: merchant reversals and cashback (credit vs `Income:*`,
      `Expenses:*` or anything unclassified).
    - `emi`: net debt added by `Liabilities:Loan:*` legs (principal debits
      minus conversion credits); may be negative.
    - `adjustments`: net debt added by `Equity:*` debits (opening entries).

    So `Δoutstanding = purchases + charges − payments − refunds + emi +
    adjustments`. `outstanding` starts from the net debt carried into 1 Apr
    of `fy` (the opening balance), not zero, and reconciles with the ledger
    balance. `account=None` aggregates all `Liabilities:CreditCard:*` cards;
    otherwise restricts to that one account. `cards` is always the full
    distinct set so a UI selector stays stable when one card is focused.
    """
    apr1 = date_type(fy, 4, 1)
    next_apr1 = date_type(fy + 1, 4, 1)

    cards: set[str] = set()
    net_before = Decimal(0)  # signed net of card postings strictly before 1 Apr
    flows: dict[tuple[int, int], dict[CardFlow, Decimal]] = {}
    month_net: dict[tuple[int, int], Decimal] = {}

    for txn in transactions:
        txn_cards = {
            p.account for p in txn.postings if p.account.startswith(_CC_PREFIX)
        }
        cards |= txn_cards
        for card in sorted(txn_cards):
            if account is not None and card != account:
                continue
            kind, amt = classify_card_posting(txn, card)
            if txn.date < apr1:
                net_before += amt
                continue
            if txn.date >= next_apr1:
                continue
            key = (txn.date.year, txn.date.month)
            month_net[key] = month_net.get(key, Decimal(0)) + amt
            bucket = flows.setdefault(key, {})
            bucket[kind] = bucket.get(kind, Decimal(0)) + _flow_value(kind, amt)

    months_out: list[dict] = []
    running = net_before  # signed; debt is negative in the ledger
    for y, m in _fy_months(fy):
        key = (y, m)
        running += month_net.get(key, Decimal(0))
        bucket = flows.get(key, {})
        values = {k: str(bucket.get(k, Decimal(0))) for k in _FLOW_FIELDS}
        months_out.append(
            {
                "month": f"{y:04d}-{m:02d}",
                "purchases": values["purchase"],
                "charges": values["charge"],
                "payments": values["payment"],
                "refunds": values["refund"],
                "emi": values["emi"],
                "adjustments": values["adjustment"],
                "outstanding": str(-running),  # negative ledger -> positive magnitude
            }
        )

    return {
        "fy": fy,
        "account": account,
        "cards": sorted(cards),
        "opening": str(-net_before),
        "months": months_out,
    }
