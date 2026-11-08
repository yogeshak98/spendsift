"""Fee rows (IGST, FX markup, ...) take their category from the charge beside them.

Pure functions over review candidates. A fee row's category is derived from its
charge on every refresh, never copied once; when the charge is gone the row is
unlinked and left uncategorized and flagged.

Candidate ids are content-derived, so twin rows share an id. A `follows`
reference resolves to the FIRST row with that id; linking twins is unsupported.
"""

from __future__ import annotations

from collections.abc import Callable

from backend.app.service.categorize.patterns import is_fee_narration
from backend.app.service.categorize.review_flags import (
    FEE_SPLIT_FLAG,
    FEE_UNLINKED_FLAG,
    UNCATEGORIZED,
    UNCATEGORIZED_FLAG,
)
from backend.app.models.domain.candidate import CandidateTxn

__all__ = [
    "FEE_SPLIT_FLAG",
    "FEE_UNLINKED_FLAG",
    "link_fee_rows",
    "refresh_fee_categories",
    "unlink_fee_row",
]


def _has_category(c: str | None) -> bool:
    return c is not None and c != UNCATEGORIZED


def _set_flag(row: CandidateTxn, flag: str, present: bool) -> None:
    if present and flag not in row.flags:
        row.flags = [*row.flags, flag]
    elif not present and flag in row.flags:
        row.flags = [f for f in row.flags if f != flag]


def unlink_fee_row(row: CandidateTxn) -> None:
    row.follows = None
    row.suggested_category = None
    row.splits = []
    row.category_source = "none"
    _set_flag(row, FEE_UNLINKED_FLAG, True)
    _set_flag(row, FEE_SPLIT_FLAG, False)
    _set_flag(row, UNCATEGORIZED_FLAG, False)


def _anchor(part: list[CandidateTxn], i: int, step: int) -> CandidateTxn | None:
    row = part[i]
    j = i + step
    while 0 <= j < len(part) and is_fee_narration(part[j].raw_narration):
        j += step
    if not 0 <= j < len(part):
        return None
    cand = part[j]
    if (
        cand.date == row.date
        and cand.direction == row.direction
        and cand.suggested_other_leg is None
    ):
        return cand
    return None


def link_fee_rows(
    candidates: list[CandidateTxn], *, skip: Callable[[CandidateTxn], bool]
) -> None:
    part = [c for c in candidates if c.commodity is None and not skip(c)]
    for i, row in enumerate(part):
        if (
            not is_fee_narration(row.raw_narration)
            or row.suggested_other_leg is not None
            or row.category_source == "user"
        ):
            continue
        anchor = _anchor(part, i, -1) or _anchor(part, i, +1)
        if anchor is None:
            unlink_fee_row(row)
            continue
        row.follows = anchor.id
        row.category_source = "fee_follows"
        _set_flag(row, FEE_UNLINKED_FLAG, False)


def refresh_fee_categories(batch: list[CandidateTxn]) -> None:
    for row in batch:
        if row.follows is None:
            continue
        anchor = next((a for a in batch if a.id == row.follows and a is not row), None)
        if anchor is None:
            unlink_fee_row(row)
            continue
        cat = anchor.suggested_category
        row.suggested_category = cat if _has_category(cat) else None
        row.splits = []
        row.category_source = "fee_follows"
        _set_flag(row, FEE_SPLIT_FLAG, len(anchor.splits) > 1)
        _set_flag(row, FEE_UNLINKED_FLAG, False)
        _set_flag(row, UNCATEGORIZED_FLAG, row.suggested_category is None)
