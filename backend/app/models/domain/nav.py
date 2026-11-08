"""One NAV observation for a scheme on a date."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class NavRow:
    scheme_code: str
    nav: Decimal
    date: date
