"""Rounding at the serialization boundary of the period reports.

Sums run at full precision; amounts become 2 dp decimal strings
(ROUND_HALF_UP) only when serialized. Where the response states that parts sum
to a total, `allocate` keeps that true of the rounded strings.
"""

from __future__ import annotations

from collections.abc import Hashable
from decimal import ROUND_HALF_UP, Decimal
from typing import TypeVar

CENT = Decimal("0.01")
TENTH = Decimal("0.1")

K = TypeVar("K", bound=Hashable)


def cents(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def q(d: Decimal) -> str:
    """`d` as a 2 dp decimal string."""
    return str(cents(d))


def allocate(parts: dict[K, Decimal], total: Decimal) -> dict[K, str]:
    """Each part at 2 dp, the rounding remainder put on the largest part (by
    magnitude, first on ties), so the strings sum exactly to `total` (already
    at 2 dp). Keeps the order of `parts`."""
    rounded = {k: cents(v) for k, v in parts.items()}
    remainder = total - sum(rounded.values(), Decimal(0))
    if remainder and rounded:
        largest = max(parts, key=lambda k: abs(parts[k]))
        rounded[largest] += remainder
    return {k: str(v) for k, v in rounded.items()}
