"""Validation of a proposed (or reused) template against the whole file.

Pure: frame + template + parse report in, ValidationReport out. Runs in the
backend so no agent runtime can skip it.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from backend.app.models.api.agent import CheckResult, Claims, Kind, ValidationReport
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.parsers.csv_xls import ParseReport, parse_balance
from backend.app.models.storage.template import Template
from backend.app.service.ledger.openings.suggest import printed_ledger_amount

# Type-only: pandas is heavy at import and only needed once a file is parsed.
if TYPE_CHECKING:
    import pandas as pd

ALLOWED_IN_RANGE = frozenset(
    {"empty", "header_repeat", "account_marker", "skip_pattern", "skip_exact"}
)
_PAISA = Decimal("0.01")
# Skip reasons that imply the row's date cell parsed (see parser._row_outcome).
_DATED_FAILURES = frozenset({"bad_amount", "no_amount", "no_narration"})
_ROW_CAP = 20
_BALANCE_HEADER = re.compile(r"balance", re.IGNORECASE)


def _range(frame, template, claims, report, reused) -> tuple[int, int, list[str]]:
    warnings: list[str] = []
    if claims is not None:
        return claims.first_data_row, claims.last_data_row, warnings
    lo = template.header_row_index + 1
    hi = report.stop_row - 1 if report.stop_row is not None else len(frame) - 1
    if reused and template.stop_pattern is None and report.candidate_rows:
        last = max(report.candidate_rows)
        footer = [
            s
            for s in report.skipped
            if s.row_index > last and s.reason not in ALLOWED_IN_RANGE
        ]
        if footer:
            warnings.append(
                f"{len(footer)} footer row(s) after the last transaction were ignored"
            )
        hi = last
    return lo, hi, warnings


def _sections(report: ParseReport) -> list[list[int]]:
    """Candidate indices grouped by the account-marker section they sit in."""
    markers = sorted(
        s.row_index for s in report.skipped if s.reason == "account_marker"
    )
    groups: dict[int, list[int]] = {}
    for i, row in enumerate(report.candidate_rows):
        key = sum(1 for m in markers if m < row)
        groups.setdefault(key, []).append(i)
    return list(groups.values())


def _direction(dates: list[date]) -> str:
    if len(set(dates)) <= 1:
        return "flat"
    if all(a <= b for a, b in zip(dates, dates[1:])):
        return "asc"
    if all(a >= b for a, b in zip(dates, dates[1:])):
        return "desc"
    return "mixed"


def _signed(c: CandidateTxn) -> Decimal:
    return c.amount if c.direction == Direction.CREDIT else -c.amount


def _walk(seq: list[tuple[int, CandidateTxn, Decimal]]) -> list[int]:
    bad = []
    for (_, _, prev_bal), (row, cand, bal) in zip(seq, seq[1:]):
        expected = (prev_bal + _signed(cand)).quantize(_PAISA)
        if expected != bal.quantize(_PAISA):
            bad.append(row)
    return bad


def header_position(frame, template: Template, column: str | None) -> int | None:
    """The position of `column` in the template's header row, or None."""
    header = [str(c).strip() for c in frame.iloc[template.header_row_index].tolist()]
    return header.index(column) if column and column in header else None


Walk = list[tuple[int, CandidateTxn, Decimal]]


def _balance_seq(frame, report, section: list[int], pos: int) -> tuple[Walk, list[int]]:
    """(frame row, candidate, balance) in file order for one section, and
    the rows whose balance cell does not parse."""
    seq: Walk = []
    unparsed: list[int] = []
    for i in section:
        row = report.candidate_rows[i]
        balance = parse_balance(str(frame.iat[row, pos]))
        if balance is None:
            unparsed.append(row)
            continue
        seq.append((row, report.candidates[i], balance))
    return seq, unparsed


def _orders(seq: Walk) -> list[Walk]:
    """The walk orders the dates allow: chronological first."""
    return {
        "asc": [seq],
        "desc": [seq[::-1]],
        "flat": [seq, seq[::-1]],
        "mixed": [seq],
    }[_direction([c.date for _, c, _ in seq])]


def balance_walk_order(
    frame, template: Template, report: ParseReport
) -> list[int] | None:
    """Candidate indices in chronological, reconciling order: the running
    balance walks from each row to the next. None without a balance column,
    with more than one account section, an unparseable balance, or no order
    that reconciles."""
    pos = header_position(frame, template, template.balance_column)
    sections = _sections(report)
    if pos is None or len(sections) != 1:
        return None
    seq, unparsed = _balance_seq(frame, report, sections[0], pos)
    if unparsed or not seq:
        return None
    index = {row: i for i, row in enumerate(report.candidate_rows)}
    for order in _orders(seq):
        if not _walk(order):
            return [index[row] for row, _, _ in order]
    return None


def _coverage(report, lo, hi) -> CheckResult:
    bad = [
        s.row_index
        for s in report.skipped
        if lo <= s.row_index <= hi and s.reason not in ALLOWED_IN_RANGE
    ]
    reasons = sorted({s.reason for s in report.skipped if s.row_index in set(bad)})
    return CheckResult(
        id=1,
        name="coverage",
        status="fail" if bad else "pass",
        message=f"rows inside {lo}..{hi} did not parse ({', '.join(reasons)})"
        if bad
        else "",
        rows=bad,
    )


def _nothing_outside(report, lo, hi) -> CheckResult:
    bad = [r for r in report.candidate_rows if not lo <= r <= hi]
    return CheckResult(
        id=2,
        name="nothing_outside",
        status="fail" if bad else "pass",
        message=f"transactions parsed outside rows {lo}..{hi}" if bad else "",
        rows=bad,
    )


def _positive(report) -> CheckResult:
    bad = [r for r, c in zip(report.candidate_rows, report.candidates) if c.amount <= 0]
    return CheckResult(
        id=3,
        name="positive_amounts",
        status="fail" if bad else "pass",
        message="amounts must be positive; direction carries the sign" if bad else "",
        rows=bad,
    )


def _running_balance(frame, template, kind, report) -> CheckResult:
    def result(status, message="", rows=()):
        return CheckResult(
            id=4,
            name="running_balance",
            status=status,
            message=message,
            rows=list(rows),
        )

    if not template.balance_column or kind != "bank":
        return result("not_applicable")
    pos = header_position(frame, template, template.balance_column)
    if pos is None:
        return result(
            "fail", f"balance column {template.balance_column!r} is not in the header"
        )
    bad: list[int] = []
    transitions = 0
    for section in _sections(report):
        seq, unparsed = _balance_seq(frame, report, section, pos)
        bad.extend(unparsed)
        transitions += max(len(seq) - 1, 0)
        walks: list[list[int]] = [_walk(o) for o in _orders(seq)]
        bad.extend(min(walks, key=lambda w: len(w)))
    if bad:
        return result(
            "fail",
            "running balance does not reconcile (wrong sign, missing or merged rows)",
            sorted(bad),
        )
    if transitions == 0:
        return result(
            "not_applicable",
            "fewer than two rows with a balance: nothing to reconcile",
        )
    return result("pass")


def _opening_closing(kind, claims, report) -> CheckResult:
    def result(status, message=""):
        return CheckResult(id=5, name="opening_closing", status=status, message=message)

    has_claim = claims is not None and (
        claims.opening_balance is not None or claims.closing_balance is not None
    )
    if kind == "card":
        return (
            result("fail", "balance claims are not accepted for card statements")
            if has_claim
            else result("not_applicable")
        )
    if (
        claims is None
        or claims.opening_balance is None
        or claims.closing_balance is None
    ):
        return result("not_applicable")
    total = sum((_signed(c) for c in report.candidates), Decimal(0))
    if (claims.opening_balance + total).quantize(
        _PAISA
    ) == claims.closing_balance.quantize(_PAISA):
        return result("pass")
    return result(
        "fail",
        f"opening {claims.opening_balance} + net {total} != closing {claims.closing_balance}",
    )


def _dates(claims, report, today, low_confidence) -> CheckResult:
    bad = []
    for row, c in zip(report.candidate_rows, report.candidates):
        outside = claims is not None and (
            (claims.period_start and c.date < claims.period_start)
            or (claims.period_end and c.date > claims.period_end)
        )
        if outside or c.date > today:
            bad.append(row)
    for section in _sections(report):
        dates = [report.candidates[i].date for i in section]
        if _direction(dates) == "mixed":
            rows = [report.candidate_rows[i] for i in section]
            low_confidence.append(
                f"dates are out of order in rows {rows[0]}..{rows[-1]}"
            )
    return CheckResult(
        id=6,
        name="dates",
        status="fail" if bad else "pass",
        message="dates outside the statement period or in the future" if bad else "",
        rows=bad,
    )


def _ambiguity(template, report, low_confidence) -> CheckResult:
    fmt = template.date_format
    numeric_day_first = "%d" in fmt and "%m" in fmt and not fmt.startswith("%Y")
    if (
        numeric_day_first
        and report.candidates
        and all(c.date.day <= 12 for c in report.candidates)
    ):
        low_confidence.append(
            "every day value is <= 12: the day/month reading is not proven"
        )
        return CheckResult(
            id=7,
            name="day_month_ambiguity",
            status="warn",
            message=low_confidence[-1],
        )
    return CheckResult(id=7, name="day_month_ambiguity", status="pass")


_CREDIT_TOKENS = {"CR", "CREDIT", "C"}


def _norm_sign(value: object) -> str:
    """Case, whitespace and trailing-punctuation normalised sign value, so
    "Dr", "DR" and "Dr." are one value."""
    return str(value).strip().upper().rstrip(". ").strip()


def _one_direction_despite_signs(frame, template, report) -> str | None:
    """Indicator mode: the sign column disagrees with the parse.

    R17: >= 2 distinct values yet every row parsed one way. R19: a recognised
    credit token (CR, CREDIT, C) is present yet no row parsed as a credit."""
    if (
        not template.amount_column
        or template.amount_sign != "indicator"
        or not template.sign_column
        or not report.candidates
    ):
        return None
    header = [str(c).strip() for c in frame.iloc[template.header_row_index].tolist()]
    if template.sign_column not in header:
        return None
    pos = header.index(template.sign_column)
    values = sorted(
        {v for row in report.candidate_rows if (v := _norm_sign(frame.iat[row, pos]))}
    )
    directions = {c.direction for c in report.candidates}
    if len(values) >= 2 and len(directions) == 1:
        (only,) = directions
        return (
            f"the sign column has values {', '.join(values)} but every row parsed "
            f"as {only.value}: credit_indicators probably wrong"
        )
    if Direction.CREDIT not in directions:
        for v in values:
            if v in _CREDIT_TOKENS:
                return (
                    f"the sign column contains '{v}' but no row parsed as a "
                    "credit: credit_indicators probably wrong"
                )
    return None


def _sanity(kind, report, frame, template) -> CheckResult:
    if not report.candidates:
        return CheckResult(
            id=8, name="sanity", status="fail", message="no transactions parsed"
        )
    if kind in ("bank", "card") and not any(
        c.direction == Direction.DEBIT for c in report.candidates
    ):
        return CheckResult(
            id=8,
            name="sanity",
            status="fail",
            message="no debits parsed: the sign convention is probably wrong",
        )
    message = _one_direction_despite_signs(frame, template, report)
    if message:
        return CheckResult(id=8, name="sanity", status="fail", message=message)
    return CheckResult(id=8, name="sanity", status="pass")


def _stop_skip(claims, report) -> CheckResult:
    if report.parseable_after_stop:
        return CheckResult(
            id=9,
            name="stop_skip",
            status="fail",
            message=f"{report.parseable_after_stop} transaction row(s) lie after the stop row",
        )
    if (
        claims is not None
        and report.stop_row is not None
        and report.stop_row <= claims.last_data_row
    ):
        return CheckResult(
            id=9,
            name="stop_skip",
            status="fail",
            message="stop row is not after the claimed last data row",
            rows=[report.stop_row],
        )
    return CheckResult(id=9, name="stop_skip", status="pass")


def _balance_declared(frame, template) -> CheckResult:
    header = [str(c).strip() for c in frame.iloc[template.header_row_index].tolist()]
    if not template.balance_column and any(_BALANCE_HEADER.search(h) for h in header):
        return CheckResult(
            id=10,
            name="balance_column_declared",
            status="warn",
            message="the header has a balance column but none was declared",
        )
    return CheckResult(id=10, name="balance_column_declared", status="pass")


CLAIM_REQUIRED = (
    "claims.opening_balance_printed is required when the draft maps opening_balance"
)
CLAIM_WITHOUT_FIELD = "claim given but opening_balance not mapped"


def _opening_cell_check(frame, template, kind, claims, report, reused) -> CheckResult:
    """Check 18: the printed opening cell reads, matches the prover's own
    reading, and (bank) agrees with the running balance's first row."""
    # Deferred: opening_evidence imports balance_walk_order from this module.
    from backend.app.service.ingest.pipeline.opening_evidence import (
        OpeningCellError,
        first_row_opening,
        read_printed_opening,
    )

    def result(status, message=""):
        return CheckResult(
            id=18, name="opening_balance", status=status, message=message
        )

    claim = None if claims is None else claims.opening_balance_printed
    if template.opening_balance is None:
        if claim is not None:
            return result("warn", CLAIM_WITHOUT_FIELD)
        return result("not_applicable")
    try:
        printed = read_printed_opening(frame, template, report)
    except OpeningCellError as exc:
        # A reused template was proven when saved: this month's file not
        # printing the cell only loses the suggestion (staging records the
        # reason), so it warns. A proof must read it.
        return result("warn" if reused else "fail", str(exc))
    if printed is None:  # unreachable while the template maps a cell
        return result("not_applicable")
    if reused:
        return result("pass")
    if claim is None:
        return result("fail", CLAIM_REQUIRED)
    value = printed.value.quantize(_PAISA)
    if value != claim.quantize(_PAISA):
        return result(
            "fail",
            f"printed opening read as {value} but "
            f"claims.opening_balance_printed is {claim.quantize(_PAISA)}",
        )
    if kind == "bank":
        first = first_row_opening(frame, template, report)
        ledger = printed_ledger_amount("bank", printed).quantize(_PAISA)
        if first is not None and first.derived.quantize(_PAISA) != ledger:
            return result(
                "fail",
                f"printed opening {ledger} disagrees with first-row derivation "
                f"{first.derived.quantize(_PAISA)}",
            )
    return result("pass")


def _hidden_by_skip_pattern(frame, template, report, lo, hi) -> list[int]:
    """In-range rows a skip pattern dropped although they carry a date cell."""
    header = [str(c).strip() for c in frame.iloc[template.header_row_index].tolist()]
    if template.date_column not in header:
        return []
    pos = header.index(template.date_column)
    return [
        s.row_index
        for s in report.skipped
        if s.reason == "skip_pattern"
        and lo <= s.row_index <= hi
        and str(frame.iat[s.row_index, pos]).strip()
    ]


def validate(
    *,
    frame: pd.DataFrame,
    template: Template,
    kind: Kind,
    claims: Claims | None,
    report: ParseReport,
    today: date,
    reused: bool,
) -> ValidationReport:
    lo, hi, warnings = _range(frame, template, claims, report, reused)
    low_confidence: list[str] = []
    checks = [
        _coverage(report, lo, hi),
        _nothing_outside(report, lo, hi),
        _positive(report),
        _running_balance(frame, template, kind, report),
        _opening_closing(kind, claims, report),
        _dates(claims, report, today, low_confidence),
        _ambiguity(template, report, low_confidence),
        _sanity(kind, report, frame, template),
        _stop_skip(claims, report),
        _balance_declared(frame, template),
        _opening_cell_check(frame, template, kind, claims, report, reused),
    ]
    warnings += [c.message for c in checks if c.status == "warn" and c.id == 10]
    if claims is not None:
        outside = [
            s.row_index
            for s in report.skipped
            if not lo <= s.row_index <= hi and s.reason in _DATED_FAILURES
        ]
        if outside:
            warnings.append(
                f"{len(outside)} dated row(s) outside the claimed rows {lo}..{hi} "
                f"did not parse: rows {outside[:_ROW_CAP]}"
            )
    hidden = _hidden_by_skip_pattern(frame, template, report, lo, hi)
    if hidden:
        low_confidence.append(
            f"{len(hidden)} row(s) with a date were skipped by skip_row_patterns: "
            f"rows {hidden[:_ROW_CAP]}"
        )
    passed = not any(c.status == "fail" for c in checks)
    sign_verified = any(c.id in (4, 5) and c.status == "pass" for c in checks)
    if not sign_verified:
        warnings.append(
            "sign convention unverified: no running or opening/closing balance was checked"
        )
    return ValidationReport(
        passed=passed,
        checks=checks,
        low_confidence=low_confidence,
        warnings=warnings,
        sign_verified=sign_verified,
    )
