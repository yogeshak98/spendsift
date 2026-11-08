"""Groww mutual-fund order-history parser (a primitive, sibling of csv_xls.py).

Fixed columns after a metadata preamble:
    Scheme Name | Transaction Type | Units | NAV | Amount | Date
Each data row -> one extended CandidateTxn. Scheme NAME -> commodity symbol is
resolved against a SchemeMaster; an unconfident name sets `unresolved_scheme`
(review-before-import) instead of guessing a commodity.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.templates.fingerprint import is_groww_header
from backend.app.service.ingest.pipeline.normalize import normalize_narration
from backend.app.service.ingest.parsers.csv_xls import detect_delimiter, load_frame
from backend.app.service.ingest.parsers.scheme_master import SchemeMaster
from backend.app.service.ingest.platform_accounts import (
    GROWW,
    platform_clearing_account,
)

_CURRENCY_NOISE = re.compile(r"[₹,\s]")
# Groww exports dates like "18 Mar 2026"; accept a couple of common variants.
_DATE_FORMATS = ("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d")


def _decimal(value: str) -> Decimal | None:
    text = _CURRENCY_NOISE.sub("", str(value))
    if not text or text.lower() == "nan":
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        # Decimal() accepts '+NaN', 'sNaN', 'inf', 'Infinity', '-inf' -- the
        # literal-"nan" check above catches only pandas' own NaN rendering.
        # A non-finite units/NAV is bad input, not imprecise input: it must
        # not reach backend.app.service.ingest.pipeline.charges.charge_tolerance, where a
        # NaN raises InvalidOperation out of max() and an Infinity produces an
        # INFINITE tolerance that silently accepts any discrepancy whatsoever.
        # Returning None routes the row to the review gate.
        return None
    return parsed


def _date(value: str):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _header_row(frame) -> int | None:
    for i in range(len(frame)):
        if is_groww_header([str(c) for c in frame.iloc[i].tolist()]):
            return i
    return None


def parse_groww(path: Path | str, scheme_master: SchemeMaster) -> list[CandidateTxn]:
    path = Path(path)
    clearing = platform_clearing_account(GROWW)
    suffix = path.suffix.lower()
    delimiter = ","
    if suffix not in (".xls", ".xlsx"):
        delimiter = detect_delimiter(path.read_text(encoding="utf-8", errors="replace"))
    # Opt in to display-precision recovery: `charge_tolerance` derives the
    # acceptable charge band from `ulp(units)` and `ulp(nav)`, so the digits
    # Groww printed would be load-bearing arithmetic here, not formatting --
    # IF Groww ever printed them as numbers. As of this writing it does not:
    # every Groww order-history export examined stores Units/NAV/Amount as
    # TEXT cells formatted 'General', so `display_places` returns None and
    # this flag is a no-op on every real Groww file today (see
    # `backend.app.service.ingest.pipeline.charges.charge_tolerance` for the verified
    # scope). It stays on anyway, at no cost, so a future Groww export that
    # starts writing numeric cells with a real number format is handled
    # correctly without a code change.
    frame = load_frame(path, delimiter, widen_display_precision=True)

    header_row = _header_row(frame)
    if header_row is None:
        return []
    header = [str(c).strip() for c in frame.iloc[header_row].tolist()]
    pos = {cell.lower(): i for i, cell in enumerate(header) if cell}

    def cell(row: list, key: str) -> str:
        idx = pos.get(key)
        return str(row[idx]).strip() if idx is not None and idx < len(row) else ""

    out: list[CandidateTxn] = []
    for row_idx in range(header_row + 1, len(frame)):
        row = frame.iloc[row_idx].tolist()
        scheme = cell(row, "scheme name")
        txn_type = cell(row, "transaction type").upper()
        if not scheme or not txn_type:
            continue
        txn_date = _date(cell(row, "date"))
        amount = _decimal(cell(row, "amount"))
        if txn_date is None or amount is None or amount == 0:
            continue
        units = _decimal(cell(row, "units"))
        nav = _decimal(cell(row, "nav"))

        # Exact first; fall back to canonical only when exact misses.
        #
        # Groww writes "X Direct Growth" where AMFI publishes
        # "X Fund - Direct Plan - Growth Option". Exact matching alone found 1
        # of this user's 26 distinct scheme names, so all 294 order rows hit
        # the E1012 review gate and the MF import path was unusable. The
        # canonical pass is still an equality test after dropping boilerplate,
        # and it refuses whenever two real schemes share the cleaned-up form,
        # so an ambiguous name still reaches review rather than being guessed.
        match = scheme_master.resolve(scheme)
        canonical_match = None if match else scheme_master.resolve_canonical(scheme)
        if canonical_match is not None:
            match = canonical_match
        commodity = match.symbol if match else None
        unresolved = None if match else scheme
        # The canonical AMFI name (when resolved) becomes the commodity `name:`
        # metadata; the raw scheme is kept only as the unresolved fallback.
        display = match.name if match else normalize_narration(scheme)

        if txn_type == "PURCHASE":
            direction = Direction.DEBIT  # cash leaves the Groww clearing account
            row_units, row_cost = units, nav
        elif txn_type == "REDEEM":
            direction = Direction.CREDIT  # cash returns to the clearing account
            row_units, row_cost = units, nav
        else:  # DIVIDEND (and any other cash-only payout) — no units
            direction = Direction.CREDIT
            row_units, row_cost = None, None

        out.append(
            CandidateTxn(
                date=txn_date,
                amount=amount,
                raw_narration=f"{txn_type.title()} — {scheme}",
                normalized_narration=display,
                source_account=clearing,
                direction=direction,
                units=row_units,
                commodity=commodity,
                unit_cost=row_cost,
                unresolved_scheme=unresolved,
                # Name-normalized matches are stated, not silent. The row
                # commits without a hold, but review can still see that the
                # statement's wording and AMFI's differ for this scheme.
                flags=(
                    [f"scheme matched by name normalization → {canonical_match.name}"]
                    if canonical_match is not None
                    else []
                ),
            )
        )
    return out
