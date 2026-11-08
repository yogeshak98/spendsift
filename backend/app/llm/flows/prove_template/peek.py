"""Masked head/tail of a stored statement, in the parser's own row indices."""

from __future__ import annotations

from pathlib import Path

from backend.app.llm.flows.prove_template.mask import mask
from backend.app.llm.flows.prove_template.redact import (
    is_transaction_shaped,
    redact_free_text,
)
from backend.app.service.ingest.parsers.csv_xls import detect_header_row, load_frame
from backend.app.service.ingest.parsers.pdf_reader import read_lines

PEEK_HEAD = 100
PEEK_TAIL = 100


def _row(frame, i: int, header: int | None = None) -> dict:
    """Rows above `header` (the preamble, where holder name and address sit)
    are structurally redacted; the header row and below are only masked."""
    redact = header is not None and i < header
    cells = {
        str(j): redact_free_text(mask(str(v).strip()))
        if redact
        else mask(str(v).strip())
        for j, v in enumerate(frame.iloc[i].tolist())
        if str(v).strip()
    }
    return {"row": i, "cells": cells}


def peek(path: Path, delimiter: str, rows: tuple[int, int] | None = None) -> dict:
    """First PEEK_HEAD and last PEEK_TAIL non-empty rows (or the window
    `rows`, inclusive). Cells are keyed by their ORIGINAL column index so
    positions line up with the header even though empty cells are dropped.
    Uses `load_frame`, so row numbers are exactly the parser's."""
    frame = load_frame(path, delimiter)
    non_empty = [
        i
        for i in range(len(frame))
        if any(str(v).strip() for v in frame.iloc[i].tolist())
    ]
    if rows is not None:
        lo, hi = rows
        head = [i for i in non_empty if lo <= i <= hi]
        tail: list[int] = []
    else:
        head = non_empty[:PEEK_HEAD]
        seen = set(head)
        tail = [i for i in non_empty[-PEEK_TAIL:] if i not in seen]
    header = detect_header_row(frame)
    return {
        "total_rows": len(frame),
        "non_empty_rows": len(non_empty),
        "head": [_row(frame, i, header) for i in head],
        "tail": [_row(frame, i, header) for i in tail],
        "window": rows is not None,
    }


def pdf_line_text(text: str) -> str:
    """A PDF line as the model may see it: masked, and structurally redacted
    unless it is transaction-shaped (name, address, PIN never show). Peek and
    the PDF dry-run payload both use this one rule."""
    masked = mask(text)
    return masked if is_transaction_shaped(text) else redact_free_text(masked)


def peek_pdf(path: Path, rows: tuple[int, int] | None = None) -> dict:
    """Masked head/tail of a PDF statement by line index. Lines that are not
    transaction rows are structurally redacted (name, address, PIN never show)."""
    lines = read_lines(path)
    if rows is not None:
        lo, hi = rows
        head = [ln for ln in lines if lo <= ln.index <= hi]
        tail: list = []
    else:
        head = lines[:PEEK_HEAD]
        seen = {ln.index for ln in head}
        tail = [ln for ln in lines[-PEEK_TAIL:] if ln.index not in seen]

    def shape(ln) -> dict:
        return {"row": ln.index, "page": ln.page, "text": pdf_line_text(ln.text)}

    return {
        "format": "pdf",
        "total_rows": len(lines),
        "pages": max((ln.page for ln in lines), default=-1) + 1,
        "non_empty_rows": len(lines),
        "head": [shape(ln) for ln in head],
        "tail": [shape(ln) for ln in tail],
        "window": rows is not None,
    }
