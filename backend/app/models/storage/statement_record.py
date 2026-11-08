"""A card statement's printed summary, and the type name of its journal record.

The record is a beancount `custom "card-statement"` entry dated the period
end and keyed on (card account, period end), so a statement is recorded at
most once.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

STATEMENT_TYPE = "card-statement"


class StatementSummary(BaseModel):
    account: str
    period_start: date
    period_end: date
    previous_balance: Decimal
    payments_credits: Decimal
    purchases_debits: Decimal
    finance_charges: Decimal = Decimal("0")
    # Printed by some issuers (ICICI, Axis); 0 when the layout has no such
    # line. They are debits like purchases, in the equation and the totals.
    cash_advances: Decimal = Decimal("0")
    other_charges: Decimal = Decimal("0")
    total_due: Decimal
    minimum_due: Decimal | None = None
    due_date: date | None = None
    credit_limit: Decimal | None = None
    labels: dict[str, str] = Field(default_factory=dict)  # role -> label text
    # The template's own flag, carried so commit can re-check the totals
    # (finance charges listed as rows count in the debits). Never dumped: it
    # describes the layout, not the printed statement.
    finance_charges_listed: bool = Field(default=False, exclude=True)
