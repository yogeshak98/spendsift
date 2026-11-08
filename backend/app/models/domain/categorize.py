"""Shapes of the categorization backlog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal


@dataclass(frozen=True)
class MerchantBacklog:
    merchant: str  # normalized key -- what a learned rule would match on
    count: int
    total: Decimal
    first: date_type
    last: date_type
    sample: str  # one raw narration, so the key is recognisable
