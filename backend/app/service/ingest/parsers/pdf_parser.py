"""Turn numbered PDF lines into review candidates and summary values.

Deterministic: the template says where to look, this module reads every date
and amount itself. Every line ends up either a candidate or a skipped row with
a reason, so coverage can be checked line by line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.normalize import normalize_narration
from backend.app.service.ingest.parsers.csv_xls import (
    ParseReport,
    SkippedRow,
    _parse_date,
)
from backend.app.service.ingest.parsers.pdf_reader import PdfLine
from backend.app.models.storage.pdf_template import (
    DATE_ROLES,
    PdfTemplate,
    SummaryField,
    SummaryRow,
)
from backend.app.service.ingest.parsers.pdf_words import (
    DATE_TEXT,
    find_card_last4,
    parse_amount_word,
    parse_money,
    starts_with_date,
)

AMOUNT_TOKEN = re.compile(r"[\d,]+\.\d{2}")
# A description that is only the row's time (HH:MM or HH:MM:SS).
TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
# A row and at most this many continuation lines (three visual lines), so a
# panel printed under the table can never be glued on (ruling R7, amended).
MAX_CONTINUATION_LINES = 2


@dataclass(frozen=True)
class SummaryValue:
    role: str
    label_text: str  # the matched label text from the line
    line: int  # index of the line the value was read from
    value: Decimal | date


@dataclass
class PdfParseReport(ParseReport):
    summary: dict[str, SummaryValue] = field(default_factory=dict)
    summary_errors: list[str] = field(default_factory=list)
    manual_hits: list[tuple[str, int]] = field(default_factory=list)  # (name, line)
    total_lines: int = 0
    # From the template's card_pattern; judged by the validator, not here.
    card_last4s: set[str] = field(default_factory=set)
    card_lines: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rows


def _row_candidate(
    match: re.Match[str], template: PdfTemplate, narration: str | None = None
) -> CandidateTxn | str:
    """A candidate, or the skip reason. A template may make a group optional,
    so a group that did not take part (None) or is blank is a skip, never a
    crash. `narration`, when given, replaces the description group (the
    lines around the row, `description_around`)."""
    date_text = match.group("date")
    txn_date = _parse_date(date_text, template.date_format) if date_text else None
    if txn_date is None:
        return "bad_date"
    raw_amount = match.group("amount")
    amount = parse_money(raw_amount) if raw_amount else None
    if amount is None:
        return "bad_amount"
    raw = narration or (match.group("description") or "").strip()
    if not raw:
        return "no_match"
    groups = match.groupdict()
    fx_amount, fx_currency = groups.get("fx_amount"), groups.get("fx_currency")
    return CandidateTxn(
        date=txn_date,
        amount=amount,
        raw_narration=raw,
        normalized_narration=normalize_narration(raw),
        source_account=template.default_account,
        direction=Direction.CREDIT if groups.get("credit") else Direction.DEBIT,
        fx=f"{fx_currency} {fx_amount}" if fx_amount and fx_currency else None,
    )


def _with_continuation(candidate: CandidateTxn, text: str) -> CandidateTxn:
    raw = f"{candidate.raw_narration} {text}"
    updated = candidate.model_copy(
        update={
            "id": "",
            "raw_narration": raw,
            "normalized_narration": normalize_narration(raw),
        }
    )
    # model_copy skips validation; re-validate so the content-derived id is
    # recomputed from the joined narration.
    return CandidateTxn.model_validate(updated.model_dump())


def _parse_rows(lines: list[PdfLine], template: PdfTemplate, report: PdfParseReport):
    row = re.compile(template.row_pattern)
    starts = [re.compile(p) for p in template.section_start]
    end = re.compile(template.section_end) if template.section_end else None
    cont = (
        re.compile(template.continuation_pattern)
        if template.continuation_pattern
        else None
    )
    skips = [re.compile(p) for p in template.skip_patterns]
    manual = [
        (m.name, re.compile(m.start), re.compile(m.end))
        for m in template.manual_sections
    ]

    section_page: int | None = None  # page the open section started on
    manual_open: tuple[str, re.Pattern[str]] | None = None
    # position in `lines` of the last candidate or of its last continuation
    last_candidate: int | None = None
    chained = 0  # continuation lines joined onto the last candidate
    # description_around: positions of in-section lines that were nothing
    # (no row, skip, continuation, header), and the line below a row that
    # was taken as its narration.
    # position -> its entry in report.skipped
    plain: dict[int, int] = {}
    taken_below: int | None = None

    def skip(line: PdfLine, reason: str) -> None:
        report.skipped.append(SkippedRow(line.index, reason, [line.text]))

    def narration_line(text: str) -> bool:
        """Text that could only be part of a narration: not blank, not
        date-led, and not a row, skip, section boundary or manual start."""
        return bool(
            text.strip()
            and not starts_with_date(text)
            and not row.search(text)
            and not any(s.search(text) for s in skips)
            and not any(s.search(text) for s in starts)
            and not (end is not None and end.search(text))
            and not any(start.search(text) for _, start, _ in manual)
        )

    def around(pos: int, match: re.Match[str]) -> tuple[bool, bool]:
        """(take the line above, take the line below) as the narration of the
        row at `pos`: only for a blank or time-only description, only plain
        lines on the same page, never across a section boundary."""
        description = (match.group("description") or "").strip()
        if not template.description_around or (
            description and not TIME_ONLY.match(description)
        ):
            return False, False
        page = lines[pos].page
        above = (
            pos - 1 in plain
            and lines[pos - 1].page == page
            and narration_line(lines[pos - 1].text)
        )
        below = (
            pos + 1 < len(lines)
            and lines[pos + 1].page == page
            and narration_line(lines[pos + 1].text)
        )
        return above, below

    def narrates_next_row(pos: int) -> bool:
        """With description_around, the line at `pos` is the line above a row
        that has no description of its own, so it is that row's narration and
        never the continuation of the row before it."""
        if not template.description_around or pos + 1 >= len(lines):
            return False
        below = lines[pos + 1]
        if below.page != lines[pos].page:
            return False
        # The main loop's order: a manual start, a section start and the
        # section end take the line before the row pattern is tried.
        if (
            any(start.search(below.text) for _, start, _ in manual)
            or any(s.search(below.text) for s in starts)
            or (end is not None and end.search(below.text))
        ):
            return False
        match = row.search(below.text)
        if match is None:
            return False
        description = (match.group("description") or "").strip()
        return not description or bool(TIME_ONLY.match(description))

    for pos, line in enumerate(lines):
        text = line.text
        if section_page is not None and line.page != section_page:
            section_page = None
        if pos == taken_below:
            # Already the narration of the row above (description_around).
            taken_below = None
            skip(line, "continuation")
            continue

        opened = False
        if manual_open is None:
            manual_open = next(
                ((name, stop) for name, start, stop in manual if start.search(text)),
                None,
            )
            opened = manual_open is not None
        if manual_open is not None:
            name, stop = manual_open
            if AMOUNT_TOKEN.search(text):
                report.manual_hits.append((name, line.index))
            if not opened and stop.search(text):
                manual_open = None
            skip(line, "manual_section")
            continue

        if any(s.search(text) for s in starts):
            section_page = line.page
            skip(line, "section_start")
            continue
        if section_page is None:
            skip(line, "outside_section")
            continue
        if end is not None and end.search(text):
            section_page = None
            skip(line, "section_end")
            continue

        match = row.search(text)
        if match:
            above, below = around(pos, match)
            parts = [lines[pos - 1].text] if above else []
            parts += [lines[pos + 1].text] if below else []
            narration = " ".join(p.strip() for p in parts) or None
            outcome = _row_candidate(match, template, narration)
            if isinstance(outcome, str):
                skip(line, outcome)
            else:
                report.candidates.append(outcome)
                report.candidate_rows.append(line.index)
                last_candidate = pos
                chained = 0
                if above:
                    # The line above was skipped as no_match; it is narration.
                    above_line = lines[pos - 1]
                    report.skipped[plain[pos - 1]] = SkippedRow(
                        above_line.index, "continuation", [above_line.text]
                    )
                if below:
                    # Taken greedily: one plain line shared by two blank rows
                    # goes to the first row as its line below (narration only;
                    # amounts and coverage are unaffected).
                    taken_below = pos + 1
                    # The narration is complete: nothing more joins this row.
                    last_candidate = None
            continue
        # Skip patterns first (ruling R6): a heading or footer directly under a
        # row must never be glued onto its narration.
        if any(s.search(text) for s in skips):
            skip(line, "skip_pattern")
        elif (
            cont is not None
            and last_candidate is not None
            and last_candidate == pos - 1
            and chained < MAX_CONTINUATION_LINES
            and lines[last_candidate].page == line.page
            and not starts_with_date(text)
            and not narrates_next_row(pos)
            and cont.search(text)
        ):
            report.candidates[-1] = _with_continuation(report.candidates[-1], text)
            # Consecutive continuation lines keep joining the same row (R7),
            # up to MAX_CONTINUATION_LINES; a section end ends the chain above.
            last_candidate = pos
            chained += 1
            skip(line, "continuation")
        elif not text.strip():
            skip(line, "empty")
        else:
            plain[pos] = len(report.skipped)
            skip(line, "no_match")


# ---------------------------------------------------------------------------
# Summary


@dataclass(frozen=True)
class _Read:
    line: int  # index in the document
    span: tuple[int, int]
    value: Decimal | date


def _extent(line: PdfLine, start: int, end: int) -> float | None:
    """Centre x of the words overlapping chars [start, end) of the line."""
    xs = [
        (w.x0, w.x1)
        for w, (s, e) in zip(line.words, line.spans, strict=True)
        if s < end and e > start
    ]
    if not xs:
        return None
    return (min(x0 for x0, _ in xs) + max(x1 for _, x1 in xs)) / 2


def _amount_words(line: PdfLine) -> list[tuple[int, Decimal, tuple[int, int]]]:
    """(word index, signed value, char span) of every amount word on the line.

    A preceding glyph word (C, ₹, `, Rs., INR) is simply not an amount word; a
    following `Cr` negates, `Dr` keeps the value positive.
    """
    found = []
    for i, word in enumerate(line.words):
        value = parse_amount_word(word.text)
        if value is None:
            continue
        nxt = line.words[i + 1].text.lower() if i + 1 < len(line.words) else ""
        if nxt == "cr":
            value = -value
        found.append((i, value, line.spans[i]))
    return found


def _read_money(
    lines: list[PdfLine], pos: int, m: re.Match[str], spec: SummaryField
) -> _Read | str:
    label_line = lines[pos]
    if spec.line_offset == 0:
        for _, value, span in _amount_words(label_line):
            if span[0] >= m.end():
                return _Read(label_line.index, span, value)
        return f"no amount after the label on line {label_line.index}"
    target = _offset_line(lines, pos, spec.line_offset)
    if isinstance(target, str):
        return target
    centre = _extent(label_line, m.start(), m.end())
    words = _amount_words(target)
    if centre is None or not words:
        return f"no amount on line {target.index}"
    i, value, span = min(
        words,
        key=lambda w: abs((target.words[w[0]].x0 + target.words[w[0]].x1) / 2 - centre),
    )
    return _Read(target.index, span, value)


def _read_date(
    lines: list[PdfLine],
    pos: int,
    m: re.Match[str],
    spec: SummaryField,
    nth: int | None,
) -> _Read | str:
    """`nth` set: take the nth date in reading order (a shared period label);
    otherwise the first date after the label, or the one nearest it below."""
    label_line = lines[pos]
    if spec.line_offset == 0:
        target = label_line
        dates = [d for d in DATE_TEXT.finditer(target.text) if d.start() >= m.end()]
    else:
        found = _offset_line(lines, pos, spec.line_offset)
        if isinstance(found, str):
            return found
        target = found
        dates = list(DATE_TEXT.finditer(target.text))
    if nth is not None:
        if len(dates) <= nth:
            return f"date {nth + 1} not found on line {target.index}"
        hit = dates[nth]
    elif not dates:
        return f"no date on line {target.index}"
    elif spec.line_offset == 0:
        hit = dates[0]
    else:
        centre = _extent(label_line, m.start(), m.end())
        if centre is None:
            return f"no date on line {target.index}"

        def distance(d: re.Match[str]) -> float:
            x = _extent(target, d.start(), d.end())
            return float("inf") if x is None else abs(x - centre)

        hit = min(dates, key=distance)
    assert spec.date_format is not None  # validated on the template
    value = _parse_date(hit.group(0), spec.date_format)
    if value is None:
        return (
            f"date {hit.group(0)!r} on line {target.index} does not match"
            f" {spec.date_format}"
        )
    return _Read(target.index, hit.span(), value)


def _offset_line(lines: list[PdfLine], pos: int, offset: int) -> PdfLine | str:
    if pos + offset >= len(lines):
        return f"no line {offset} below the label on line {lines[pos].index}"
    return lines[pos + offset]


def _read_row(
    lines: list[PdfLine], row: SummaryRow
) -> list[tuple[str, str, _Read]] | str:
    """(role, label text, read) for each role of an ordered summary row, or
    the error. The label line must be unique and print every label in order;
    the value line must hold exactly one amount word per role."""
    label = re.compile(row.label)
    hits = [pos for pos, ln in enumerate(lines) if label.search(ln.text)]
    if not hits:
        return "label not found"
    if len(hits) > 1:
        numbers = ", ".join(str(lines[pos].index) for pos in hits)
        return f"label matches lines {numbers} — make it specific"
    pos = hits[0]
    text, start = lines[pos].text, 0
    for printed in row.labels:
        found = text.find(printed, start)
        if found < 0:
            return (
                f"label {printed!r} is missing or out of order on line"
                f" {lines[pos].index}"
            )
        start = found + len(printed)
    target = _offset_line(lines, pos, row.line_offset)
    if isinstance(target, str):
        return target
    words = _amount_words(target)
    if len(words) != len(row.roles):
        return (
            f"line {target.index} has {len(words)} amounts, expected {len(row.roles)}"
        )
    return [
        (role, printed, _Read(target.index, span, value))
        for role, printed, (_, value, span) in zip(
            row.roles, row.labels, words, strict=True
        )
    ]


def _read_summary(lines: list[PdfLine], template: PdfTemplate, report: PdfParseReport):
    reads: dict[str, tuple[str, _Read]] = {}
    for role, spec in template.summary.items():
        label = re.compile(spec.label)
        hits = [
            (pos, m) for pos, ln in enumerate(lines) if (m := label.search(ln.text))
        ]
        if not hits:
            report.summary_errors.append(f"{role}: label {spec.label} not found")
            continue
        if len(hits) > 1:
            numbers = ", ".join(str(lines[pos].index) for pos, _ in hits)
            report.summary_errors.append(
                f"{role}: label {spec.label} matches lines {numbers} — make it specific"
            )
            continue
        pos, m = hits[0]
        if role in DATE_ROLES:
            start = template.summary.get("period_start")
            shared = (
                role in ("period_start", "period_end")
                and start is not None
                and (end := template.summary.get("period_end")) is not None
                and start.label == end.label
                and start.line_offset == end.line_offset
            )
            nth = (0 if role == "period_start" else 1) if shared else None
            outcome = _read_date(lines, pos, m, spec, nth)
        else:
            outcome = _read_money(lines, pos, m, spec)
        if isinstance(outcome, str):
            report.summary_errors.append(f"{role}: {outcome}")
            continue
        reads[role] = (m.group(0), outcome)

    # Rows after single labels, into the same reads, so the collision rule
    # below covers row-read values too.
    for row in template.summary_rows:
        outcome = _read_row(lines, row)
        if isinstance(outcome, str):
            report.summary_errors.append(f"summary row {row.label}: {outcome}")
            continue
        for role, printed, read in outcome:
            reads[role] = (printed, read)

    colliding: set[str] = set()
    roles = list(reads)
    for i, a in enumerate(roles):
        for b in roles[i + 1 :]:
            ra, rb = reads[a][1], reads[b][1]
            if (ra.line, ra.span) == (rb.line, rb.span):
                report.summary_errors.append(
                    f"{a} and {b} read the same value on line {ra.line}"
                )
                colliding.update((a, b))
    for role, (label_text, read) in reads.items():
        if role not in colliding:
            report.summary[role] = SummaryValue(role, label_text, read.line, read.value)


def parse_pdf_report(lines: list[PdfLine], template: PdfTemplate) -> PdfParseReport:
    report = PdfParseReport(total_lines=len(lines))
    _parse_rows(lines, template, report)
    _read_summary(lines, template, report)
    report.card_last4s, report.card_lines = find_card_last4(
        lines, template.card_pattern
    )
    return report
