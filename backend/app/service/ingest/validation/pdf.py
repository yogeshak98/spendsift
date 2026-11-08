"""The import gate for PDF card statements.

Every parsed row is reconciled against the statement's own printed summary:
previous dues - payments + purchases + cash advances + other charges +
finance charges must equal the total due, and the parsed debits and credits
must equal what `expected_totals` says the statement printed. A mismatch is
refused; there is no override. The statement must name exactly one card, the
saved layout's own (check 17).

Check ids continue the CSV validator's numbering (`validation/csv_xls.py`) so render
and skill treat both alike.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path

from backend.app.models.api.agent import CheckResult, ValidationReport
from backend.app.service.ingest.validation.csv_xls import _PAISA, _ROW_CAP, _positive
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.normalize import normalize_narration
from backend.app.service.ingest.parsers.pdf_parser import (
    PdfParseReport,
    parse_pdf_report,
)
from backend.app.service.ingest.parsers.pdf_reader import read_lines
from backend.app.models.storage.pdf_template import (
    DATE_ROLES,
    MONEY_ROLES,
    REQUIRED_ROLES,
    PdfTemplate,
)
from backend.app.models.storage.statement_record import StatementSummary

# In-section skips that are not a missed row.
ALLOWED_IN_SECTION = frozenset(
    {"empty", "section_start", "section_end", "continuation", "skip_pattern"}
)
# Lines no row coverage applies to: manual sections have their own check (15).
_NOT_IN_SECTION = frozenset({"outside_section", "manual_section"})
OUTSIDE_PERIOD_FLAG = "dated outside the statement period"
GENERATED_FLAG = "generated from the statement summary"
FINANCE_NARRATION = "Finance charges (statement summary)"
_RUPEE = Decimal("1")
DIFFERENT_CARD = (
    "this statement is for a different card than the saved layout;"
    " run /ingest-stmt to add this card"
)


def _money(value: Decimal) -> str:
    """Indian digit grouping, as statements print it: 1234567.8 -> 12,34,567.80."""
    text = f"{abs(value.quantize(_PAISA)):.2f}"
    whole, frac = text.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{'-' if value < 0 else ''}{whole}.{frac}"


def _lines(rows: list[int]) -> str:
    shown = ", ".join(str(r) for r in rows[:_ROW_CAP])
    return shown + (f" and {len(rows) - _ROW_CAP} more" if len(rows) > _ROW_CAP else "")


def _not_applicable(check_id: int, name: str) -> CheckResult:
    return CheckResult(
        id=check_id,
        name=name,
        status="not_applicable",
        message="the statement summary could not be read (check 12)",
    )


def _decimal(report: PdfParseReport, role: str) -> Decimal | None:
    found = report.summary.get(role)
    return (
        found.value if found is not None and isinstance(found.value, Decimal) else None
    )


def _date(report: PdfParseReport, role: str) -> date | None:
    found = report.summary.get(role)
    if found is None or isinstance(found.value, Decimal):
        return None
    return found.value


def _required_roles(template: PdfTemplate) -> list[str]:
    """REQUIRED_ROLES plus the template's payment form: `payments_credits`,
    or `payments` with `other_credits` when that is mapped too."""
    payments = template.payments_role() or "payments_credits"
    roles = [*REQUIRED_ROLES, payments]
    if payments == "payments" and "other_credits" in template.mapped_roles():
        roles.append("other_credits")
    return roles


def _missing_roles(report: PdfParseReport, template: PdfTemplate) -> list[str]:
    return [role for role in _required_roles(template) if role not in report.summary]


def _mistyped(report: PdfParseReport) -> bool:
    """A value of the wrong type for its role: an amount role holding a date
    or the reverse. There is then no statement, rather than a silent 0."""
    return any(
        (role in MONEY_ROLES) != isinstance(found.value, Decimal)
        for role, found in report.summary.items()
        if role in MONEY_ROLES or role in DATE_ROLES
    )


def statement_of(
    report: PdfParseReport, template: PdfTemplate
) -> StatementSummary | None:
    """The printed summary, or None when any required value is missing or the
    summary could not be read cleanly.

    `payments_credits` is the stored combined figure: on a split template it
    is payments + other_credits, labelled "<payments> + <other credits>"."""
    if report.summary_errors or _missing_roles(report, template) or _mistyped(report):
        return None
    period_start = _date(report, "period_start")
    period_end = _date(report, "period_end")
    previous = _decimal(report, "previous_balance")
    payments_role = template.payments_role() or "payments_credits"
    payments = _decimal(report, payments_role)
    other_credits = (
        _decimal(report, "other_credits") if payments_role == "payments" else None
    )
    purchases = _decimal(report, "purchases_debits")
    total = _decimal(report, "total_due")
    if (
        period_start is None
        or period_end is None
        or previous is None
        or payments is None
        or purchases is None
        or total is None
    ):
        return None
    labels = {role: value.label_text for role, value in report.summary.items()}
    if payments_role == "payments":
        labels["payments_credits"] = (
            f"{labels['payments']} + {labels['other_credits']}"
            if other_credits is not None
            else labels["payments"]
        )
    return StatementSummary(
        account=template.default_account,
        period_start=period_start,
        period_end=period_end,
        previous_balance=previous,
        payments_credits=payments + (other_credits or Decimal("0")),
        purchases_debits=purchases,
        finance_charges=_decimal(report, "finance_charges") or Decimal("0"),
        cash_advances=_decimal(report, "cash_advances") or Decimal("0"),
        other_charges=_decimal(report, "other_charges") or Decimal("0"),
        total_due=total,
        minimum_due=_decimal(report, "minimum_due"),
        due_date=_date(report, "due_date"),
        credit_limit=_decimal(report, "credit_limit"),
        labels=labels,
        finance_charges_listed=template.finance_charges_listed,
    )


def expected_totals(s: StatementSummary) -> tuple[Decimal, Decimal, str]:
    """(expected debits, expected credits, the debit role names joined by
    " + ") that the parsed rows must add up to. The ONE definition check 14
    and the commit re-check both use, so stage and commit cannot drift.

    Debits are purchases + cash advances + other charges, plus finance
    charges when the layout lists them as rows; credits are payments_credits.
    A zero term is left out of the names (it adds nothing)."""
    debits = s.purchases_debits
    roles = ["purchases_debits"]
    for role, value in (
        ("cash_advances", s.cash_advances),
        ("other_charges", s.other_charges),
    ):
        if value != 0:
            debits += value
            roles.append(role)
    if s.finance_charges_listed:
        debits += s.finance_charges
        roles.append("finance_charges")
    return (
        debits.quantize(_PAISA),
        s.payments_credits.quantize(_PAISA),
        " + ".join(roles),
    )


# ---------------------------------------------------------------------------
# Checks


def _coverage(report: PdfParseReport) -> CheckResult:
    missed = [
        s
        for s in report.skipped
        if s.reason not in _NOT_IN_SECTION and s.reason not in ALLOWED_IN_SECTION
    ]
    rows = [s.row_index for s in missed]
    reasons = sorted({s.reason for s in missed})
    return CheckResult(
        id=1,
        name="coverage",
        status="fail" if rows else "pass",
        message=f"lines {_lines(rows)} inside the transaction table did not parse"
        f" ({', '.join(reasons)})"
        if rows
        else "",
        rows=rows,
    )


def _dates(report: PdfParseReport, today: date) -> CheckResult:
    rows = [
        row
        for row, c in zip(report.candidate_rows, report.candidates, strict=True)
        if c.date > today
    ]
    return CheckResult(
        id=6,
        name="dates",
        status="fail" if rows else "pass",
        message=f"lines {_lines(rows)} are dated after today ({today})" if rows else "",
        rows=rows,
    )


def _sanity() -> CheckResult:
    return CheckResult(
        id=8,
        name="sanity",
        status="not_applicable",
        message="direction is verified by the summary totals (check 14)",
    )


def _summary_read(report: PdfParseReport, template: PdfTemplate) -> CheckResult:
    missing = _missing_roles(report, template)
    if report.summary_errors:
        message = "; ".join(report.summary_errors)
    elif missing:
        message = f"missing roles: {', '.join(missing)}"
    else:
        return CheckResult(id=12, name="summary_read", status="pass")
    return CheckResult(id=12, name="summary_read", status="fail", message=message)


def _label(s: StatementSummary, role: str) -> str:
    return s.labels.get(role, role.replace("_", " "))


def _equation(s: StatementSummary, template: PdfTemplate) -> CheckResult:
    computed = (
        s.previous_balance
        - s.payments_credits
        + s.purchases_debits
        + s.cash_advances
        + s.other_charges
        + s.finance_charges
    ).quantize(_PAISA)
    total = s.total_due.quantize(_PAISA)
    # A layout that prints the total due in whole rupees (ruling P9): the
    # terms rounded down or rounded half up are both accepted, and a total
    # due with paise still fails. Only this check rounds (14 and the commit
    # re-check compare rows exactly).
    rupee = template.total_due_rounding == "rupee"
    accepted = (
        sorted(
            {
                computed.quantize(_RUPEE, ROUND_FLOOR),
                computed.quantize(_RUPEE, ROUND_HALF_UP),
            }
        )
        if rupee
        else [computed]
    )
    if total in accepted:
        return CheckResult(id=13, name="summary_equation", status="pass")
    terms = (
        f"{_label(s, 'previous_balance')} {_money(s.previous_balance)}"
        f" − {_label(s, 'payments_credits')} {_money(s.payments_credits)}"
        f" + {_label(s, 'purchases_debits')} {_money(s.purchases_debits)}"
    )
    mode = (
        f", {' or '.join(_money(v) for v in accepted)} in whole rupees, rounded"
        " down or half up (total_due_rounding: rupee)"
        if rupee
        else ", compared to the paisa"
    )
    # Optional terms are named when the layout maps them (an unmapped one is 0).
    mapped = template.mapped_roles()
    for role, value in (
        ("cash_advances", s.cash_advances),
        ("other_charges", s.other_charges),
        ("finance_charges", s.finance_charges),
    ):
        if role in mapped:
            terms += f" + {_label(s, role)} {_money(value)}"
    return CheckResult(
        id=13,
        name="summary_equation",
        status="fail",
        message=f"{terms} ≠ {_label(s, 'total_due')} {_money(total)}"
        f" (the terms give {_money(computed)}{mode}, off by"
        f" {_money(min(abs(total - v) for v in accepted))})",
    )


def _parsed(report: PdfParseReport, direction: Direction) -> Decimal:
    return sum(
        (c.amount for c in report.candidates if c.direction == direction),
        Decimal("0"),
    ).quantize(_PAISA)


def _off(parsed: Decimal, printed: Decimal) -> str:
    return f"off by {_money(abs(parsed - printed))}"


def _totals(report: PdfParseReport, s: StatementSummary) -> CheckResult:
    debits = _parsed(report, Direction.DEBIT)
    credits = _parsed(report, Direction.CREDIT)
    expected_debits, expected_credits, debit_roles = expected_totals(s)
    debit_label = " + ".join(_label(s, role) for role in debit_roles.split(" + "))

    problems = []
    if debits != expected_debits:
        problems.append(
            f"parsed debits {_money(debits)} ≠ {debit_label}"
            f" {_money(expected_debits)} ({_off(debits, expected_debits)})"
        )
    if credits != expected_credits:
        problems.append(
            f"parsed credits {_money(credits)} ≠ {_label(s, 'payments_credits')}"
            f" {_money(expected_credits)} ({_off(credits, expected_credits)})"
        )
    listed_total = _decimal(report, "transactions_total")
    if listed_total is not None:
        listed_total = listed_total.quantize(_PAISA)
        if listed_total not in (debits, debits + credits):
            problems.append(
                f"parsed debits {_money(debits)} (with credits"
                f" {_money(debits + credits)}) ≠"
                f" {_label(s, 'transactions_total')} {_money(listed_total)}"
                f" ({_off(debits, listed_total)})"
            )
    return CheckResult(
        id=14,
        name="summary_totals",
        status="fail" if problems else "pass",
        message="; ".join(problems),
    )


def is_generated(c: CandidateTxn) -> bool:
    """The finance-charge row `finalize_candidates` generates from the summary."""
    return GENERATED_FLAG in c.flags and c.raw_narration == FINANCE_NARRATION


def commit_drift(statement: StatementSummary, rows: list[CandidateTxn]) -> str:
    """Why a staged batch no longer reconciles with its statement ('' when it
    still does). The review patch route sets candidate attributes verbatim and
    rows can be rejected, so commit re-checks check 14 over the batch as it is
    now. `rows` is every row of the batch, duplicates included (they are
    already in the journal); the generated finance-charge row is not a parsed
    row, so it is left out of the totals. Role names, never label text."""
    parsed = [c for c in rows if not is_generated(c)]

    def total(direction: Direction) -> Decimal:
        return sum(
            (c.amount for c in parsed if c.direction == direction), Decimal("0")
        ).quantize(_PAISA)

    debits, credits = total(Direction.DEBIT), total(Direction.CREDIT)
    expected_debits, expected_credits, debit_roles = expected_totals(statement)
    problems = []
    if debits != expected_debits:
        problems.append(
            f"the rows to commit hold debits {_money(debits)} ≠ {debit_roles}"
            f" {_money(expected_debits)} ({_off(debits, expected_debits)})"
        )
    if credits != expected_credits:
        problems.append(
            f"the rows to commit hold credits {_money(credits)} ≠ payments_credits"
            f" {_money(expected_credits)} ({_off(credits, expected_credits)})"
        )
    elsewhere = sorted(
        {c.source_account for c in rows if c.source_account != statement.account}
    )
    if elsewhere:
        problems.append(
            f"rows are booked to {', '.join(elsewhere)}, not the statement's card"
            f" {statement.account}"
        )
    if not problems:
        return ""
    return "summary_totals: " + "; ".join(problems)


def replace_labels(text: str, labels: Iterable[str], view: Callable[[str], str]) -> str:
    """`text` with every summary label replaced by `view(label)`, longest
    first. Check 13/14 messages embed label text, which is document text a
    model-authored regex matched; the agent's views pass it through `view`."""
    unique: set[str] = {x for x in labels if x}
    for label in sorted(unique, key=lambda x: len(x), reverse=True):
        text = text.replace(label, view(label))
    return text


def _manual_sections(report: PdfParseReport) -> CheckResult:
    by_name: dict[str, list[int]] = defaultdict(list)
    for name, line in report.manual_hits:
        by_name[name].append(line)
    if not by_name:
        return CheckResult(id=15, name="manual_sections", status="pass")
    message = "; ".join(
        f"this statement has a panel called {name} (lines {_lines(lines)});"
        f" {name}"
        " instalments are imported as ordinary rows"
        for name, lines in by_name.items()
    )
    return CheckResult(
        id=15,
        name="manual_sections",
        status="warn",
        message=message,
        rows=[line for _, line in report.manual_hits],
    )


def _outside(c: CandidateTxn, s: StatementSummary) -> bool:
    return not s.period_start <= c.date <= s.period_end


def _statement_period(report: PdfParseReport, s: StatementSummary) -> CheckResult:
    rows = [
        row
        for row, c in zip(report.candidate_rows, report.candidates, strict=True)
        if _outside(c, s)
    ]
    return CheckResult(
        id=16,
        name="statement_period",
        status="warn" if rows else "pass",
        message=f"lines {_lines(rows)} are dated outside the statement period"
        f" {s.period_start}..{s.period_end}"
        if rows
        else "",
        rows=rows,
    )


def _card_number(report: PdfParseReport, template: PdfTemplate) -> CheckResult:
    """Exactly one card number, and the saved layout's own. The message names
    line numbers, never the card's digits."""
    found = report.card_last4s
    if len(found) != 1:
        where = (
            f"card-shaped text on lines {_lines(report.card_lines)}"
            if report.card_lines
            else "no card-shaped text found"
        )
        message = f"expected one card number; {where}"
    elif template.card_last4 and found != {template.card_last4}:
        message = DIFFERENT_CARD
    else:
        return CheckResult(id=17, name="card_number", status="pass")
    return CheckResult(
        id=17,
        name="card_number",
        status="fail",
        message=message,
        rows=list(report.card_lines),
    )


def validate_pdf(
    *, template: PdfTemplate, report: PdfParseReport, today: date
) -> ValidationReport:
    summary_read = _summary_read(report, template)
    statement = statement_of(report, template)
    if summary_read.status == "pass" and statement is None:
        # Fail closed: `passed` is the gate, so a summary that cannot become
        # a statement must fail here, not leave 13/14 merely not applicable.
        summary_read = CheckResult(
            id=12,
            name="summary_read",
            status="fail",
            message="summary values could not be read as a statement",
        )
    checks = [
        _coverage(report),
        _positive(report),
        _dates(report, today),
        _sanity(),
        summary_read,
    ]
    if summary_read.status == "fail" or statement is None:
        checks += [
            _not_applicable(13, "summary_equation"),
            _not_applicable(14, "summary_totals"),
        ]
    else:
        checks += [
            _equation(statement, template),
            _totals(report, statement),
        ]
    checks.append(_manual_sections(report))
    checks.append(
        _not_applicable(16, "statement_period")
        if statement is None
        else _statement_period(report, statement)
    )
    checks.append(_card_number(report, template))
    checks.sort(key=lambda c: c.id)
    warnings = [c.message for c in checks if c.status == "warn"]
    sign_verified = any(c.id == 14 and c.status == "pass" for c in checks)
    if not sign_verified:
        warnings.append(
            "sign convention unverified: the parsed rows were not reconciled"
            " against the statement totals"
        )
    return ValidationReport(
        passed=not any(c.status == "fail" for c in checks),
        checks=checks,
        warnings=warnings,
        sign_verified=sign_verified,
    )


def finalize_candidates(
    report: PdfParseReport, statement: StatementSummary, *, template: PdfTemplate
) -> list[CandidateTxn]:
    """The candidates to review, after a passing validation.

    Rows dated outside the period are flagged. Finance charges printed only in
    the summary become one generated, flagged row; the totals check never sees
    it, since it runs on the report before this does.

    `template` is required: whether the charges are already listed as rows is
    the template's `finance_charges_listed`, which the summary does not carry.
    """
    final = [
        c.model_copy(update={"flags": [*c.flags, OUTSIDE_PERIOD_FLAG]})
        if _outside(c, statement)
        else c
        for c in report.candidates
    ]
    charges = statement.finance_charges
    if charges != 0 and not template.finance_charges_listed:
        # Amounts stay positive; a negative printed charge (a reversal) is a
        # credit to the card.
        final.append(
            CandidateTxn(
                date=statement.period_end,
                amount=abs(charges),
                raw_narration=FINANCE_NARRATION,
                normalized_narration=normalize_narration(FINANCE_NARRATION),
                source_account=template.default_account,
                direction=Direction.DEBIT if charges > 0 else Direction.CREDIT,
                suggested_category=None,
                flags=[GENERATED_FLAG],
            )
        )
    return final


def check_pdf(
    path: Path, template: PdfTemplate, today: date
) -> tuple[PdfParseReport, ValidationReport, StatementSummary | None]:
    report = parse_pdf_report(read_lines(path), template)
    validation = validate_pdf(template=template, report=report, today=today)
    return report, validation, statement_of(report, template)
