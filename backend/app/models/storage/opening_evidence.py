"""Opening-balance evidence captured when a CSV/XLS statement is staged.

The first-import prompt is computed from this, server-side, so the chat and
the browser see the same figures and the same arithmetic.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class PrintedOpening(BaseModel):
    """The opening as the statement prints it, outside the data rows.
    `value` is `parse_balance(raw_text)` (a "Dr" suffix or a leading minus is
    negative); `suffix` records a "Cr"/"Dr" suffix separately. `label_text`
    is the matched cell, stripped and cut to 60 characters."""

    label_text: str
    raw_text: str
    value: Decimal
    suffix: Literal["Cr", "Dr"] | None
    row: int
    column: int


class FirstRowOpening(BaseModel):
    """The opening derived from a bank statement's reconciling running
    balance: `derived = balance - signed_amount` on the chronologically first
    row, where a credit is + and a debit is -."""

    date: dt.date
    balance: Decimal
    signed_amount: Decimal
    derived: Decimal


class OpeningEvidence(BaseModel):
    """`account` is the batch's single source account, or None when the batch
    spans several (then no suggestion is made). `printed_error` holds why no
    figure could be taken: a mapped opening cell that could not be read, or a
    running-balance cell that is not a finite number. The prompt shows it as
    the `note`."""

    account: str | None
    printed: PrintedOpening | None
    first_row: FirstRowOpening | None
    printed_error: str | None
