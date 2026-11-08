"""A card statement's journal record: built from its summary, found by its key.

The record is a beancount `custom "card-statement"` entry dated the period
end and keyed on (card account, period end), so a statement is recorded at
most once.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from beancount.core import data
from beancount.parser import parser

from backend.app.models.storage.statement_record import STATEMENT_TYPE, StatementSummary
from backend.app.persistence.journal import Ledger

_PAISA = Decimal("0.01")


def _inr(value: Decimal) -> str:
    return f"{value.quantize(_PAISA)} INR"


def statement_entry(s: StatementSummary) -> data.Custom:
    """Built by parsing text: beancount 3.2 has no public ValueType constructor."""
    meta = [
        f"period-start: {s.period_start}",
        f"previous-balance: {_inr(s.previous_balance)}",
        f"payments: {_inr(s.payments_credits)}",
        f"purchases: {_inr(s.purchases_debits)}",
    ]
    # Only when non-zero, so a record without them (every HDFC one) stays
    # byte-identical to before they existed.
    if s.cash_advances != 0:
        meta.append(f"cash-advances: {_inr(s.cash_advances)}")
    if s.other_charges != 0:
        meta.append(f"other-charges: {_inr(s.other_charges)}")
    meta += [
        f"finance-charges: {_inr(s.finance_charges)}",
        f"total-due: {_inr(s.total_due)}",
    ]
    if s.minimum_due is not None:
        meta.append(f"minimum-due: {_inr(s.minimum_due)}")
    if s.due_date is not None:
        meta.append(f"due-date: {s.due_date}")
    if s.credit_limit is not None:
        meta.append(f"credit-limit: {_inr(s.credit_limit)}")
    meta.append('source: "pdf"')
    text = f'{s.period_end} custom "{STATEMENT_TYPE}" {s.account}\n' + "".join(
        f"  {line}\n" for line in meta
    )
    entries, errors, _ = parser.parse_string(text)
    if errors or len(entries) != 1 or not isinstance(entries[0], data.Custom):
        raise ValueError(f"could not build the {STATEMENT_TYPE} record for {s.account}")
    return entries[0]


def already_recorded(ledger: Ledger, account: str, period_end: date) -> bool:
    return any(
        isinstance(entry, data.Custom)
        and entry.type == STATEMENT_TYPE
        and entry.date == period_end
        and bool(entry.values)
        and entry.values[0].value == account
        for entry in ledger.load().entries
    )
