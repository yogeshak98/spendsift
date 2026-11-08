"""The opening balance a CSV/XLS statement carries: the figure it prints
outside its data rows, and the one its running balance implies.

Pure: frame + template + parse report in, evidence out. Validation check 18
proves the printed cell when a template maps it; staging records both
readings so the first-import prompt can suggest an opening.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.models.storage.opening_evidence import (
    FirstRowOpening,
    OpeningEvidence,
    PrintedOpening,
)
from backend.app.models.storage.template import Template
from backend.app.service.ingest.parsers.csv_xls import (
    ParseReport,
    balance_suffix,
    parse_balance,
)
from backend.app.service.ingest.validation.csv_xls import (
    balance_walk_order,
    header_position,
)

# Type-only: pandas is heavy at import and only needed once a file is parsed.
if TYPE_CHECKING:
    import pandas as pd

LABEL_CHARS = 60
NON_FINITE_BALANCE = (
    "the running balance column holds a value that is not a finite number, "
    "so no opening is derived from it"
)


class OpeningCellError(ValueError):
    """A mapped opening cell could not be read. The message names rows and
    columns only, never the statement's cell text."""


def _cell(frame, row: int, column: int) -> str:
    return str(frame.iat[row, column]).strip()


def _data_rows(report: ParseReport) -> tuple[int, int] | None:
    """The first and last row that parsed as a transaction, counting a row
    `skip_exact_narrations` dropped (an in-table "Opening Balance" row is a
    data row, never the printed cell)."""
    rows = list(report.candidate_rows) + [
        s.row_index for s in report.skipped if s.reason == "skip_exact"
    ]
    return (min(rows), max(rows)) if rows else None


def _double_signed(text: str) -> bool:
    """A minus together with a "Cr"/"Dr" suffix: two signs that may cancel or
    agree, so the figure is refused rather than guessed."""
    return balance_suffix(text) is not None and "-" in text


def _finite_balance(text: str) -> Decimal | None:
    """`parse_balance`, but None for a figure that is not a finite number:
    Decimal reads "Infinity", "-NaN" and "sNaN" (which raises on use), and
    none of them is a balance."""
    try:
        value = parse_balance(text)
    except InvalidOperation:
        return None
    return value if value is not None and value.is_finite() else None


def printed_opening(
    label_text: str, raw_text: str, *, row: int, column: int
) -> PrintedOpening | None:
    """The printed opening from the matched label text and the value cell;
    None when the value does not parse as a finite balance or carries two
    signs."""
    raw = raw_text.strip()
    if _double_signed(raw):
        return None
    value = _finite_balance(raw)
    if value is None:
        return None
    return PrintedOpening(
        label_text=label_text.strip()[:LABEL_CHARS],
        raw_text=raw,
        value=value,
        suffix=balance_suffix(raw),
        row=row,
        column=column,
    )


def read_printed_opening(
    frame: pd.DataFrame, template: Template, report: ParseReport
) -> PrintedOpening | None:
    """The opening the statement prints outside its data rows, read by the
    template's `opening_balance` cell. None when the template maps none.

    Raises OpeningCellError when the label matches no cell or several, or
    the value cell is missing or does not parse."""
    cell = template.opening_balance
    if cell is None:
        return None
    span = _data_rows(report)
    if span is None:
        raise OpeningCellError("no data rows: the opening cell cannot be placed")
    lo, hi = span
    outside = [r for r in range(len(frame)) if r < lo or r > hi]
    pattern = re.compile(cell.label)
    matches = [
        (r, c, found.group(0))
        for r in outside
        for c in range(frame.shape[1])
        if (found := pattern.search(_cell(frame, r, c)))
    ]
    if not matches:
        raise OpeningCellError(
            f"opening_balance label matched no cell outside the data rows {lo}..{hi}"
        )
    if len(matches) > 1:
        where = ", ".join(f"row {r} column {c}" for r, c, _ in matches)
        raise OpeningCellError(
            f"opening_balance label matched {len(matches)} cells outside the "
            f"data rows ({where}); it must match exactly one"
        )
    # Only the text the label regex matched is kept: the rest of the cell
    # may carry an account number.
    (row, column, label) = matches[0]
    if cell.row_offset == 0:
        for c in range(column + 1, frame.shape[1]):
            text = _cell(frame, row, c)
            if _double_signed(text):
                raise OpeningCellError(
                    f"row {row} column {c} has both a minus and a Cr/Dr suffix"
                )
            if text:
                printed = printed_opening(label, text, row=row, column=c)
                if printed is not None:
                    return printed
        raise OpeningCellError(
            f"no balance to the right of the opening_balance label (row {row} "
            f"column {column})"
        )
    target = row + cell.row_offset
    if target >= len(frame) or lo <= target <= hi:
        raise OpeningCellError(
            f"row {target} ({cell.row_offset} below the opening_balance label at "
            f"row {row}) is not a row outside the data rows"
        )
    value_text = _cell(frame, target, column)
    printed = printed_opening(label, value_text, row=target, column=column)
    if printed is None:
        reason = (
            "has both a minus and a Cr/Dr suffix"
            if _double_signed(value_text)
            else "is not a balance"
        )
        raise OpeningCellError(
            f"row {target} column {column} (below the opening_balance label) {reason}"
        )
    return printed


def _signed(c: CandidateTxn) -> Decimal:
    return c.amount if c.direction == Direction.CREDIT else -c.amount


def first_row_opening(
    frame: pd.DataFrame, template: Template, report: ParseReport
) -> FirstRowOpening | None:
    """The opening the running balance implies: on the chronologically first
    row, its balance minus its signed amount. None without a balance column
    or a reconciling walk (card CSVs carry none)."""
    order = balance_walk_order(frame, template, report)
    if not order:
        return None
    pos = header_position(frame, template, template.balance_column)
    if pos is None:
        return None
    first = order[0]
    balance = _finite_balance(_cell(frame, report.candidate_rows[first], pos))
    if balance is None:
        return None
    candidate = report.candidates[first]
    signed = _signed(candidate)
    return FirstRowOpening(
        date=candidate.date,
        balance=balance,
        signed_amount=signed,
        derived=balance - signed,
    )


def _single_account(report: ParseReport) -> str | None:
    accounts = {c.source_account for c in report.candidates if not c.needs_account}
    return next(iter(accounts)) if len(accounts) == 1 else None


def csv_evidence(
    frame: pd.DataFrame, template: Template, report: ParseReport
) -> OpeningEvidence:
    """Both readings for the review batch. Never raises on an unreadable
    opening cell or a running balance that is not a finite number (the
    balance walk cannot add "Infinity" or "sNaN"): the reason goes to
    `printed_error`, the evidence's one note, and the other reading is
    kept."""
    printed: PrintedOpening | None = None
    reasons: list[str] = []
    try:
        printed = read_printed_opening(frame, template, report)
    except OpeningCellError as exc:
        reasons.append(str(exc))
    first_row: FirstRowOpening | None = None
    try:
        first_row = first_row_opening(frame, template, report)
    except InvalidOperation:
        reasons.append(NON_FINITE_BALANCE)
    return OpeningEvidence(
        account=_single_account(report),
        printed=printed,
        first_row=first_row,
        printed_error="; ".join(reasons) or None,
    )
