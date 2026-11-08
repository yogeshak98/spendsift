"""A ledger-cli/hledger journal as parsed, before conversion to beancount."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date as date_type
from decimal import Decimal


@dataclass(frozen=True)
class ParsedPosting:
    account: str
    amount: Decimal | None
    commodity: str | None
    cost: Decimal | None
    cost_currency: str | None
    comment: str | None


@dataclass(frozen=True)
class ParsedTxn:
    date: date_type
    narration: str
    postings: list[ParsedPosting]
    source_line: int
    # Stable random id, assigned once when the transaction is created. Survives
    # edits to date/narration/postings (the staging editor preserves it), so the
    # frontend can track a draft row identity that a content-derived id couldn't.
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class UnparsedLine:
    line_no: int
    text: str
    reason: str


@dataclass(frozen=True)
class ParsedLedger:
    transactions: list[ParsedTxn]
    unparsed: list[UnparsedLine]
    ignored_lines: int
    # The comment / unsupported-directive lines that were skipped, so the review
    # can name them instead of only counting them.
    ignored: list[UnparsedLine] = field(default_factory=list)
