from __future__ import annotations

import csv as csvmod
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from backend.app.core.errors.errors import TEMPLATE_SIGN_COLUMN_REQUIRED
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.templates.fingerprint import autoguess_roles
from backend.app.service.ingest.pipeline.normalize import normalize_narration
from backend.app.models.storage.template import Template

# pandas / openpyxl are imported inside the functions that read a file: together
# they cost ~1s+ at import and are only needed when a statement is parsed, so
# keeping them off the module top level keeps app startup fast.
if TYPE_CHECKING:
    import pandas as pd

_CURRENCY_NOISE = re.compile(r"[₹,\s]")
_CANDIDATE_DELIMITERS = ["~|~", "\t", ";", "|", ","]
_FORMAT_LITERAL = re.compile(r'"[^"]*"')
_FORMAT_PLACES = re.compile(r"\.([0#?]+)")


def display_places(number_format: str) -> int | None:
    """Decimal places an Excel number format DISPLAYS, or None if it declares none.

    `pd.read_excel(..., dtype=str)` returns the stored value and discards the
    format, so a cell holding 1000.0 and displaying `1000.000` comes back as
    the string "1000" -- exponent 0, and `charges._ulp` then reports an
    uncertainty of +/-1 on a figure the broker printed to three places. This
    reads back the digits pandas dropped.

    `None` (not 0) for 'General' and for text formats: those declare nothing,
    and a caller must leave such a cell exactly as pandas returned it. A
    format of '0' declares zero places, which is a real claim -- the broker
    printed whole units -- and is therefore 0, not None.

    Only the first format section is read: Excel's sections are
    positive;negative;zero;text, and they are not required to agree on
    precision. The positive section is the one a subscription row displays.
    Quoted literals are stripped first, so a currency prefix or a quoted dot
    inside the format cannot be mistaken for declared digits.
    """
    if not number_format or number_format.strip().lower() == "general":
        return None
    body = _FORMAT_LITERAL.sub("", number_format.split(";")[0])
    if not re.search(r"[0#?]", body):
        # '@' and friends: a text format, which declares no numeric precision.
        return None
    match = _FORMAT_PLACES.search(body)
    return len(match.group(1)) if match else 0


def detect_delimiter(text: str) -> str:
    """Best-effort delimiter guess: the candidate (longest first, to prefer a
    specific multi-char separator over a substring of it) whose split produces
    the most rows agreeing on a field count greater than 1."""
    lines = [line for line in text.splitlines() if line.strip()]
    best = ","
    best_score = (0, 0)
    for delimiter in _CANDIDATE_DELIMITERS:
        counts: dict[int, int] = {}
        for line in lines:
            n = len(line.split(delimiter))
            counts[n] = counts.get(n, 0) + 1
        mode_count, mode_freq = max(counts.items(), key=lambda kv: kv[1])
        if mode_count <= 1:
            continue
        score = (mode_freq, len(delimiter))
        if score > best_score:
            best_score = score
            best = delimiter
    return best


def _read_rows_csv_like(path: Path, delimiter: str) -> list[list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(delimiter) == 1:
        return list(csvmod.reader(io.StringIO(text), delimiter=delimiter))
    return [line.split(delimiter) for line in text.splitlines()]


def _widen_to_display(path: Path, frame: pd.DataFrame) -> pd.DataFrame:
    """Re-quantize each numeric cell of `frame` to the places its xlsx cell
    DISPLAYS, reading the format pandas discarded.

    Value-preserving by construction: the target exponent is
    `min(stored_exponent, -display_places)`, so a value finer than its own
    format (100.0004 shown as `100.000`) is left alone rather than rounded.
    Only the exponent moves, never the number.

    A second read of the file via openpyxl, not a replacement for pandas:
    pandas owns the grid shape, the merged-cell and type handling, and the
    `.xls` path. This only annotates precision, so a cell the two readers
    disagree about -- a different value at the same position, a
    non-numeric cell, no declared places -- simply keeps pandas' string.

    The two readers agree this reliably partly because pandas' own default
    engine for `.xlsx` IS openpyxl (`.xls` defaults to `xlrd` instead, which
    is why that suffix is excluded above) -- so most of the time this is one
    reader's view checked against itself read twice, not two independent
    implementations. The value-agreement guard below is what keeps this
    function correct if pandas ever changes that default engine; it is not
    redundant belt-and-braces.

    The caller gates this on SUFFIX (`.xlsx`), but pandas picks its engine on
    CONTENT: a file misnamed `.xlsx` whose bytes are actually the legacy
    binary `.xls` format reads fine through `pd.read_excel` (it sniffs and
    falls back to `xlrd`), while `openpyxl.load_workbook` below only ever
    understands the zip-based format and raises `zipfile.BadZipFile` on
    exactly that input. This function only ever ADDS precision on top of
    `frame`, which pandas has already produced successfully by the time this
    is called -- so failing to widen can never be worse than not having this
    feature, whereas letting an exception escape here turns an otherwise
    readable import into an unhandled 500. Catch broadly and degrade to the
    unwidened `frame` rather than assume which exception a second file format
    reader might raise.
    """
    import openpyxl

    try:
        book = openpyxl.load_workbook(path, data_only=True)
    except Exception:
        return frame
    sheet = book[book.sheetnames[0]]
    widened = frame.copy()
    for row_idx, row in enumerate(sheet.iter_rows()):
        if row_idx >= len(widened.index):
            break
        for col_idx, cell in enumerate(row):
            if col_idx >= len(widened.columns):
                break
            if not isinstance(cell.value, (int, float)) or isinstance(cell.value, bool):
                continue
            places = display_places(cell.number_format or "")
            if places is None:
                continue
            text = str(widened.iat[row_idx, col_idx])
            try:
                value = Decimal(text)
            except InvalidOperation:
                continue
            if not value.is_finite():
                continue
            # The alignment guard, and what makes value-preservation provable
            # per cell rather than assumed globally: this walks two
            # independent readers of the same file by POSITION, so a sheet
            # where pandas and openpyxl disagree on the grid (a dropped blank
            # row, a merged region) would otherwise widen one cell using
            # another cell's declared precision. Requiring the two readers to
            # agree on the NUMBER makes that disagreement self-detecting --
            # a mismatched pair is skipped, keeping pandas' string.
            try:
                if value != Decimal(str(cell.value)):
                    continue
            except InvalidOperation:
                continue
            exponent = value.as_tuple().exponent
            if not isinstance(exponent, int):
                continue
            target = min(exponent, -places)
            # quantize ADDS significant digits (it's the only Decimal call in
            # this function that does), so a wide value paired with a
            # many-place format can exceed the default 28-digit context and
            # raise where the parse above it did not. Degrade to pandas'
            # string like every other disagreement this function handles.
            try:
                widened.iat[row_idx, col_idx] = str(
                    value.quantize(Decimal(1).scaleb(target))
                )
            except InvalidOperation:
                continue
    return widened


def load_frame(
    path: Path,
    delimiter: str = ",",
    *,
    widen_display_precision: bool = False,
) -> pd.DataFrame:
    """Raw grid: every row is data, columns are integer positions, all cells
    are strings with blanks normalized to ''. Header location is a property
    of the Template, not of pandas.

    `widen_display_precision` re-attaches each `.xlsx` cell's displayed
    precision to the string pandas returned (see `_widen_to_display`). It is
    OFF by default and only `backend/app/service/ingest/parsers/groww_mf.py` turns it on: the change
    rewrites amount strings, and amount-string formatting has already caused
    one double-booking incident in this journal. Narrow it stays until a
    second caller has a reason.
    """
    import pandas as pd

    suffix = path.suffix.lower()
    if suffix in (".xls", ".xlsx"):
        frame = pd.read_excel(path, header=None, dtype=str).fillna("")
        if widen_display_precision and suffix == ".xlsx":
            frame = _widen_to_display(path, frame)
        return frame
    rows = _read_rows_csv_like(path, delimiter)
    width = max((len(r) for r in rows), default=0)
    padded = [r + [""] * (width - len(r)) for r in rows]
    return pd.DataFrame(padded, dtype=str).fillna("")


def detect_header_row(frame: pd.DataFrame) -> int | None:
    """First row whose cells autoguess both a date and a narration column."""
    for i in range(len(frame)):
        cells = [str(c) for c in frame.iloc[i].tolist()]
        roles = autoguess_roles(cells)
        if roles["date"] and roles["narration"]:
            return i
    return None


def _money(value) -> Decimal | None:
    if value is None:
        return None
    text = _CURRENCY_NOISE.sub("", str(value))
    if not text or text.lower() == "nan":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_date(date_text: str, date_format: str):
    """Parse against the template's configured format, falling back to a
    2-digit/4-digit year swap of that same format — the same bank/header
    can export either across different statement periods, and the day and
    month positions stay unambiguous either way."""
    for fmt in (
        date_format,
        date_format.replace("%Y", "%y")
        if "%Y" in date_format
        else date_format.replace("%y", "%Y")
        if "%y" in date_format
        else None,
    ):
        if fmt is None:
            continue
        try:
            return datetime.strptime(date_text, fmt).date()
        except ValueError:
            continue
    return None


_BALANCE_SUFFIX = re.compile(r"\s*(cr|dr)\.?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SkippedRow:
    row_index: int
    reason: str
    cells: list[str]


@dataclass
class ParseReport:
    candidates: list[CandidateTxn] = field(default_factory=list)
    # Frame row index per candidate, parallel to `candidates`. Kept out of
    # CandidateTxn so content-derived candidate ids do not change.
    candidate_rows: list[int] = field(default_factory=list)
    skipped: list[SkippedRow] = field(default_factory=list)
    stop_row: int | None = None
    # Rows after `stop_row` that WOULD have parsed into a transaction. Must
    # be 0 for a trustworthy parse (validation check 9).
    parseable_after_stop: int = 0


def parse_balance(text: str) -> Decimal | None:
    """A printed running balance: '12,345.00 Cr', '500.00 Dr' (negative), '-75.10'."""
    text = str(text).strip()
    if not text:
        return None
    sign = 1
    suffix = _BALANCE_SUFFIX.search(text)
    if suffix:
        if suffix.group(1).lower() == "dr":
            sign = -1
        text = text[: suffix.start()]
    value = _money(text)
    return None if value is None else value * sign


def balance_suffix(text: str) -> Literal["Cr", "Dr"] | None:
    """The "Cr"/"Dr" suffix `parse_balance` reads off a printed balance."""
    match = _BALANCE_SUFFIX.search(str(text).strip())
    if match is None:
        return None
    return "Cr" if match.group(1).lower() == "cr" else "Dr"


def _row_outcome(
    cells: list[str],
    col_pos: dict[str, int],
    template: Template,
    marker_key: str | None,
) -> CandidateTxn | str:
    """A candidate, or the reason this row is not one."""

    def cell(col_name: str | None) -> str:
        if col_name is None or col_name not in col_pos:
            return ""
        return str(cells[col_pos[col_name]]).strip()

    date_text = cell(template.date_column)
    if not date_text:
        return "no_date"
    txn_date = _parse_date(date_text, template.date_format)
    if txn_date is None:
        return "bad_date"

    raw = cell(template.narration_column)
    if not raw:
        return "no_narration"

    if template.amount_column:
        amount_text = cell(template.amount_column)
        if not amount_text:
            return "no_amount"
        amount = _money(amount_text)
        if amount is None:
            return "bad_amount"
        if template.amount_sign == "signed":
            if amount == 0:
                return "no_amount"
            direction = Direction.DEBIT if amount < 0 else Direction.CREDIT
            amount = abs(amount)
        else:
            sign_text = cell(template.sign_column).strip().upper()
            indicators = {i.strip().upper() for i in template.credit_indicators}
            direction = Direction.CREDIT if sign_text in indicators else Direction.DEBIT
    else:
        debit_text = cell(template.debit_column)
        credit_text = cell(template.credit_column)
        debit = _money(debit_text)
        credit = _money(credit_text)
        if debit:
            amount, direction = debit, Direction.DEBIT
        elif credit:
            amount, direction = credit, Direction.CREDIT
        elif (debit_text and debit is None) or (credit_text and credit is None):
            return "bad_amount"
        else:
            return "no_amount"

    account = template.account_marker_map.get(marker_key) if marker_key else None
    if account is None:
        account = template.default_account
    needs_account = account is None

    return CandidateTxn(
        date=txn_date,
        amount=amount,
        raw_narration=raw,
        normalized_narration=normalize_narration(raw),
        source_account=account or "",
        direction=direction,
        needs_account=needs_account,
        unresolved_marker_key=marker_key if needs_account else None,
    )


def _row_to_candidate(
    cells: list[str],
    col_pos: dict[str, int],
    template: Template,
    marker_key: str | None,
) -> CandidateTxn | None:
    outcome = _row_outcome(cells, col_pos, template, marker_key)
    return outcome if isinstance(outcome, CandidateTxn) else None


def _assert_direction_columns_resolve(
    template: Template, col_pos: dict[str, int]
) -> None:
    """Refuse a template whose direction columns are not in THIS file's header.

    `Template`'s own validator cannot do this: whether a column name resolves
    is a property of the template AND a header row, and the model never sees a
    header row. `_row_to_candidate`'s `cell()` returns "" for an unresolved
    name, and "" is never a credit indicator -- so a template saying
    `Debit/Credit` against a file whose header reads `Dr/Cr` books every row,
    payments included, as money going out and says nothing.

    Checked once per file, before the row loop: the answer is the same for
    every row, and a per-row check would raise on the first data row anyway
    while re-doing the work len(frame) times.
    """
    unresolved = [
        name
        for name in (
            # A signed amount column never reads sign_column, so it is not a
            # direction column there.
            template.sign_column
            if template.amount_column and template.amount_sign != "signed"
            else None,
            template.debit_column,
            template.credit_column,
        )
        if name is not None and name not in col_pos
    ]
    if unresolved:
        raise TEMPLATE_SIGN_COLUMN_REQUIRED.exc({"template": template.fingerprint})


def parse_statement_report(path: Path, template: Template) -> ParseReport:
    """Parse every row after the header and account for each one.

    `stop_pattern` and `skip_row_patterns` only act on rows that would NOT
    parse into a transaction, so a narration that happens to contain the stop
    word stays a transaction. `skip_exact_narrations` is the one way to drop a
    row that does parse, and only on an exact (trimmed, case-folded) match.
    """
    frame = load_frame(path, template.delimiter)
    header_cells = [
        str(c).strip() for c in frame.iloc[template.header_row_index].tolist()
    ]
    col_pos = {cell: i for i, cell in enumerate(header_cells) if cell}
    _assert_direction_columns_resolve(template, col_pos)
    marker_re = (
        re.compile(template.account_marker_pattern)
        if template.account_marker_pattern
        else None
    )
    stop_re = re.compile(template.stop_pattern) if template.stop_pattern else None
    skip_res = [re.compile(p) for p in template.skip_row_patterns]
    exact = {n.strip().casefold() for n in template.skip_exact_narrations}

    report = ParseReport()
    current_marker_key: str | None = None
    for row_idx in range(len(frame)):
        cells = [str(c) for c in frame.iloc[row_idx].tolist()]
        joined = " ".join(cells)
        trimmed = joined.strip()
        is_marker = False
        if marker_re:
            match = marker_re.search(joined)
            if match:
                current_marker_key = match.group(1)
                is_marker = True
        if row_idx <= template.header_row_index:
            continue

        stripped = [c.strip() for c in cells]
        outcome = _row_outcome(cells, col_pos, template, current_marker_key)

        def skip(reason: str) -> None:
            report.skipped.append(SkippedRow(row_idx, reason, stripped))

        if report.stop_row is not None:
            if isinstance(outcome, CandidateTxn):
                report.parseable_after_stop += 1
            skip("after_stop")
        elif isinstance(outcome, CandidateTxn):
            if outcome.raw_narration.strip().casefold() in exact:
                skip("skip_exact")
            else:
                report.candidates.append(outcome)
                report.candidate_rows.append(row_idx)
        elif not any(stripped):
            skip("empty")
        elif stripped == header_cells:
            skip("header_repeat")
        elif is_marker:
            skip("account_marker")
        elif stop_re and stop_re.search(trimmed):
            report.stop_row = row_idx
            skip("after_stop")
        elif any(r.search(trimmed) for r in skip_res):
            skip("skip_pattern")
        else:
            skip(outcome)
    return report


def parse_statement(path: Path, template: Template) -> list[CandidateTxn]:
    return parse_statement_report(path, template).candidates
