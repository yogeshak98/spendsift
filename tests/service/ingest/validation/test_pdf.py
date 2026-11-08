from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.models.api.agent import CheckResult, ValidationReport
from backend.app.service.ingest.validation.pdf import (
    check_pdf,
    commit_drift,
    finalize_candidates,
    statement_of,
    validate_pdf,
)
from backend.app.models.domain.candidate import Direction
from backend.app.service.ingest.parsers.pdf_parser import (
    PdfParseReport,
    parse_pdf_report,
)
from backend.app.service.ingest.parsers.pdf_reader import read_lines
from backend.app.models.storage.pdf_template import PdfTemplate, SummaryField
from tests.service.ingest.pdf_fixtures import Row, make_statement_pdf
from tests.service.ingest.pdf_reference import ROW_PATTERN, hdfc_template

# The exact reading (no rounding, no narration lines): the reference itself
# sets total_due_rounding and description_around, which tests opt into.
TEMPLATE = hdfc_template(total_due_rounding="exact", description_around=False)
TODAY = date(2026, 10, 24)
CARD = "Liabilities:CreditCard:HDFC"

# Previous 8,200.00 - payments 8,200.00 + purchases 15,345.00 = total 15,345.00.
ROWS = [
    Row("21/09/2026", "14:22", "ACME STORE BENGALURU", "10,000.00"),
    Row("22/09/2026", "10:00", "PAYMENT RECEIVED THANK YOU", "8,200.00", credit=True),
    Row("23/09/2026", "11:30", "INITECH FOODS", "5,000.00", bar_apart=True),
    Row("24/09/2026", "11:30", "UMBRELLA PHARMA", "345.00", rewards=3),
]


def _variant(**update) -> PdfTemplate:
    return TEMPLATE.model_copy(update=update)


def _report(tmp_path, template=TEMPLATE, name="s.pdf", **kw) -> PdfParseReport:
    kw.setdefault("domestic", ROWS)
    return parse_pdf_report(
        read_lines(make_statement_pdf(tmp_path / name, **kw)), template
    )


def _validate(report, template=TEMPLATE) -> ValidationReport:
    return validate_pdf(template=template, report=report, today=TODAY)


def _check(result: ValidationReport, check_id: int) -> CheckResult:
    return next(c for c in result.checks if c.id == check_id)


def _statuses(result: ValidationReport) -> dict[int, str]:
    return {c.id: c.status for c in result.checks}


def test_consistent_statement_passes_and_verifies_sign(tmp_path):
    report = _report(tmp_path)
    result = _validate(report)
    assert [c.id for c in result.checks] == [1, 3, 6, 8, 12, 13, 14, 15, 16, 17]
    assert _statuses(result) == {
        1: "pass",
        3: "pass",
        6: "pass",
        8: "not_applicable",
        12: "pass",
        13: "pass",
        14: "pass",
        15: "pass",
        16: "pass",
        17: "pass",
    }
    assert result.passed and result.sign_verified

    statement = statement_of(report, TEMPLATE)
    assert statement is not None
    assert statement.account == CARD
    assert (statement.period_start, statement.period_end) == (
        date(2026, 9, 21),
        date(2026, 10, 20),
    )
    assert statement.previous_balance == Decimal("8200.00")
    assert statement.payments_credits == Decimal("8200.00")
    assert statement.purchases_debits == Decimal("15345.00")
    assert statement.total_due == Decimal("15345.00")
    assert statement.minimum_due == Decimal("767.25")
    assert statement.due_date == date(2026, 11, 10)
    assert statement.labels["total_due"] == "TOTAL AMOUNT DUE"
    assert statement.labels["previous_balance"] == "PREVIOUS STATEMENT DUES"


def test_equation_fails_when_total_due_off_by_one_paisa(tmp_path):
    report = _report(tmp_path, summary={"total_due": "C 15,345.01"})
    result = _validate(report)
    check = _check(result, 13)
    assert check.status == "fail"
    assert check.message.startswith(
        "PREVIOUS STATEMENT DUES 8,200.00 − PAYMENTS/CREDITS 8,200.00"
        " + PURCHASES/DEBIT 15,345.00 + FINANCE CHARGES 0.00"
        " ≠ TOTAL AMOUNT DUE 15,345.01"
    )
    assert "0.01" in check.message
    assert not result.passed
    # The rows still add up to the printed purchases and payments.
    assert _check(result, 14).status == "pass"


def test_totals_fail_when_a_row_is_missed(tmp_path):
    no_bar = ROW_PATTERN.replace(r"\s?\|?\s*", r"\s+")
    assert no_bar != ROW_PATTERN
    template = _variant(row_pattern=no_bar)
    report = _report(tmp_path, template)
    assert len(report.candidates) == 3
    result = _validate(report, template)

    coverage = _check(result, 1)
    assert coverage.status == "fail"
    bar_line = next(
        s.row_index for s in report.skipped if s.cells[0].startswith("23/09/2026 |")
    )
    assert coverage.rows == [bar_line]

    totals = _check(result, 14)
    assert totals.status == "fail"
    assert "debits" in totals.message
    assert "5,000.00" in totals.message
    assert "PAYMENTS/CREDITS" not in totals.message  # the credit side adds up
    assert not result.sign_verified and not result.passed


def test_totals_fail_on_sign_flip(tmp_path):
    never = ROW_PATTERN.replace(r"(?P<credit>\+\s)?", r"(?P<credit>(?!x)x)?")
    assert never != ROW_PATTERN
    template = _variant(row_pattern=never)
    report = _report(tmp_path, template)
    assert all(c.direction == Direction.DEBIT for c in report.candidates)
    result = _validate(report, template)
    totals = _check(result, 14)
    assert totals.status == "fail"
    assert "debits" in totals.message and "credits" in totals.message
    assert not result.sign_verified and not result.passed


def test_payment_only_statement_passes(tmp_path):
    rows = [Row("22/09/2026", "10:00", "PAYMENT RECEIVED", "8,200.00", credit=True)]
    report = _report(tmp_path, domestic=rows)
    result = _validate(report)
    assert result.passed and result.sign_verified
    statement = statement_of(report, TEMPLATE)
    assert statement is not None
    assert statement.purchases_debits == 0
    assert statement.total_due == 0


def test_nil_statement_passes(tmp_path):
    zero = "C 0.00"
    report = _report(
        tmp_path,
        domestic=[],
        summary={"previous_balance": zero, "total_due": zero, "minimum_due": zero},
    )
    assert report.candidates == []
    result = _validate(report)
    assert result.passed and result.sign_verified
    statement = statement_of(report, TEMPLATE)
    assert statement is not None
    assert finalize_candidates(report, statement, template=TEMPLATE) == []


def test_out_of_period_row_warns_and_flags(tmp_path):
    rows = [*ROWS, Row("15/09/2026", "09:00", "LATE POSTING", "0.50")]
    report = _report(tmp_path, domestic=rows)
    result = _validate(report)
    check = _check(result, 16)
    assert check.status == "warn"
    late_line = report.candidate_rows[-1]
    assert check.rows == [late_line]
    assert check.message in result.warnings
    assert result.passed  # a warning, not a refusal

    statement = statement_of(report, TEMPLATE)
    assert statement is not None
    final = finalize_candidates(report, statement, template=TEMPLATE)
    assert len(final) == len(report.candidates)
    assert final[-1].flags == ["dated outside the statement period"]
    assert all(c.flags == [] for c in final[:-1])


def test_future_dated_row_fails_dates(tmp_path):
    rows = [*ROWS, Row("25/10/2026", "09:00", "TOMORROW SHOP", "0.50")]
    result = _validate(_report(tmp_path, domestic=rows))
    check = _check(result, 6)
    assert check.status == "fail"
    assert len(check.rows) == 1


def test_summary_only_finance_charges_generate_one_flagged_row(tmp_path):
    summary = {"finance_charges": "C 120.00", "total_due": "C 15,465.00"}
    report = _report(tmp_path, summary=summary)
    result = _validate(report)
    assert result.passed and result.sign_verified

    statement = statement_of(report, TEMPLATE)
    assert statement is not None and statement.finance_charges == Decimal("120.00")
    final = finalize_candidates(report, statement, template=TEMPLATE)
    assert len(final) == len(report.candidates) + 1
    generated = final[-1]
    assert generated.direction == Direction.DEBIT
    assert generated.amount == Decimal("120.00")
    assert generated.date == date(2026, 10, 20)
    assert generated.raw_narration == "Finance charges (statement summary)"
    assert generated.source_account == CARD
    assert generated.suggested_category is None
    assert generated.flags == ["generated from the statement summary"]
    # The report itself is untouched: the totals check never sees the row.
    assert len(report.candidates) == len(ROWS)


def test_finance_charges_listed_counts_them_in_debits(tmp_path):
    rows = [*ROWS, Row("20/10/2026", "00:00", "INTEREST CHARGE", "120.00")]
    summary = {
        "purchases_debits": "C 15,345.00",
        "finance_charges": "C 120.00",
        "total_due": "C 15,465.00",
    }
    listed = _variant(finance_charges_listed=True)
    report = _report(tmp_path, listed, domestic=rows, summary=summary)
    result = _validate(report, listed)
    assert _check(result, 14).status == "pass"
    assert result.passed and result.sign_verified
    statement = statement_of(report, listed)
    assert statement is not None
    # Listed in the table already: nothing generated.
    assert len(finalize_candidates(report, statement, template=listed)) == len(rows)

    # Read as "not listed", the parsed debits are 120.00 over the printed
    # purchases: the flag is what lets the charge row count.
    unlisted = _validate(report, TEMPLATE)
    assert _check(unlisted, 14).status == "fail"
    assert "120.00" in _check(unlisted, 14).message


def test_emi_lines_warn_in_manual_sections_and_still_pass(tmp_path):
    report = _report(
        tmp_path, emi_lines=["EMI 1/12 C 2,000.00", "EMI INTEREST C 150.00"]
    )
    result = _validate(report)
    check = _check(result, 15)
    lines = [line for _, line in report.manual_hits]
    assert len(lines) == 2
    assert check.status == "warn"
    assert check.rows == lines
    assert check.message == (
        f"this statement has a panel called EMI (lines {lines[0]}, {lines[1]});"
        " EMI instalments are imported as ordinary rows"
    )
    # The user's decision (2026-10-25): an EMI panel warns, it does not refuse.
    assert result.passed
    assert check.message in result.warnings
    assert [c.id for c in result.checks if c.status == "fail"] == []


def test_summary_read_failure_makes_dependent_checks_not_applicable(tmp_path):
    broken = _variant(
        summary={**TEMPLATE.summary, "total_due": SummaryField(label="NO SUCH LABEL")}
    )
    report = _report(tmp_path, broken)
    result = _validate(report, broken)
    check = _check(result, 12)
    assert check.status == "fail"
    assert check.message == "; ".join(report.summary_errors)
    assert "total_due" in check.message
    for dependent in (13, 14, 16):
        assert _check(result, dependent).status == "not_applicable"
    assert not result.passed and not result.sign_verified
    assert statement_of(report, broken) is None


def test_colliding_summary_roles_fail_summary_read(tmp_path):
    # Both roles read the same printed value, so the parser drops both.
    colliding = _variant(
        summary={
            **TEMPLATE.summary,
            "payments_credits": SummaryField(
                label="PREVIOUS STATEMENT DUES", line_offset=1
            ),
        }
    )
    report = _report(tmp_path, colliding)
    result = _validate(report, colliding)
    assert _check(result, 12).status == "fail"
    assert statement_of(report, colliding) is None


def test_negative_row_amount_fails_positive_amounts(tmp_path):
    signed = ROW_PATTERN.replace(
        r"(?P<amount>[\d,]+\.\d{2})", r"(?P<amount>-?[\d,]+\.\d{2})"
    )
    assert signed != ROW_PATTERN
    template = _variant(row_pattern=signed)
    rows = [*ROWS, Row("24/09/2026", "12:00", "REVERSAL", "-50.00")]
    report = _report(tmp_path, template, domestic=rows)
    assert report.candidates[-1].amount == Decimal("-50.00")
    result = _validate(report, template)
    check = _check(result, 3)
    assert check.status == "fail"
    assert check.rows == [report.candidate_rows[-1]]
    assert not result.passed


def test_check_8_rule_not_applied_to_pdf(tmp_path):
    rows = [Row("22/09/2026", "10:00", "PAYMENT RECEIVED", "8,200.00", credit=True)]
    result = _validate(_report(tmp_path, domestic=rows))
    check = _check(result, 8)
    assert check.status == "not_applicable"
    assert check.message == "direction is verified by the summary totals (check 14)"


def test_transactions_total_accepts_debits_plus_credits(tmp_path):
    report = _report(tmp_path, summary={"transactions_total": "C 23,545.00"})
    assert _check(_validate(report), 14).status == "pass"


def test_transactions_total_mismatch_fails(tmp_path):
    report = _report(tmp_path, summary={"transactions_total": "C 15,000.00"})
    totals = _check(_validate(report), 14)
    assert totals.status == "fail"
    assert "TRANSACTIONS TOTAL AMOUNT" in totals.message


def test_check_pdf_reads_parses_validates_and_summarises(tmp_path):
    path = make_statement_pdf(tmp_path / "s.pdf", domestic=ROWS)
    report, result, statement = check_pdf(path, TEMPLATE, TODAY)
    assert len(report.candidates) == len(ROWS)
    assert result.passed
    assert statement is not None and statement.total_due == Decimal("15345.00")


def test_negative_summary_finance_charge_generates_a_credit(tmp_path):
    summary = {"finance_charges": "C 120.00 Cr", "total_due": "C 15,225.00"}
    report = _report(tmp_path, summary=summary)
    assert _validate(report).passed
    statement = statement_of(report, TEMPLATE)
    assert statement is not None and statement.finance_charges == Decimal("-120.00")
    generated = finalize_candidates(report, statement, template=TEMPLATE)[-1]
    assert generated.amount == Decimal("120.00")
    assert generated.direction == Direction.CREDIT


def test_summary_values_that_are_not_a_statement_fail_check_12(tmp_path):
    """Fail closed: every role is present and check 12's own rule would pass,
    but a value of the wrong type means there is no statement to gate on."""
    import dataclasses

    report = _report(tmp_path)
    wrong = dataclasses.replace(report.summary["previous_balance"], value=TODAY)
    report.summary["previous_balance"] = wrong
    assert not report.summary_errors and statement_of(report, TEMPLATE) is None

    result = _validate(report)
    check = _check(result, 12)
    assert check.status == "fail"
    assert check.message == "summary values could not be read as a statement"
    assert _statuses(result)[13] == _statuses(result)[14] == "not_applicable"
    assert result.passed is False


def test_statement_carries_the_templates_finance_charges_listed(tmp_path):
    report = _report(tmp_path)
    for listed in (True, False):
        template = _variant(finance_charges_listed=listed)
        statement = statement_of(report, template)
        assert statement is not None
        assert statement.finance_charges_listed is listed


# ---------------------------------------------------------------------------
# Cash advances, other charges and split payments

# Extra summary lines sit below the domestic table on page 1; the template
# skips them inside the section and reads each value from its own line.
CASH = Row("25/09/2026", "09:15", "ATM CASH WITHDRAWAL", "500.00")
FEE = Row("26/09/2026", "09:15", "OVERLIMIT FEE", "99.00")


def _with_lines(template: PdfTemplate, **labels: str) -> PdfTemplate:
    """`template` reading each role in `labels` from a same-line value, its
    lines skipped in the table."""
    summary = {
        **template.summary,
        **{role: SummaryField(label=f"^{text}") for role, text in labels.items()},
    }
    skips = [*template.skip_patterns, *(f"^{text}" for text in labels.values())]
    return template.model_copy(update={"summary": summary, "skip_patterns": skips})


ISSUER = _with_lines(
    TEMPLATE, cash_advances="CASH ADVANCES", other_charges="OTHER CHARGES"
)


def _issuer_report(tmp_path, *, cash="500.00", other="99.00", **summary):
    # Default printed total = 8,200 − 8,200 + every row (15,944.00).
    summary = {"purchases_debits": "C 15,345.00", **summary}
    return _report(
        tmp_path,
        ISSUER,
        domestic=[*ROWS, CASH, FEE],
        summary=summary,
        extra_lines={0: [f"CASH ADVANCES C {cash}", f"OTHER CHARGES C {other}"]},
    )


def test_cash_advances_and_other_charges_reconcile(tmp_path):
    report = _issuer_report(tmp_path)
    result = _validate(report, ISSUER)
    assert result.passed and result.sign_verified, result.checks
    statement = statement_of(report, ISSUER)
    assert statement is not None
    assert statement.cash_advances == Decimal("500.00")
    assert statement.other_charges == Decimal("99.00")
    assert statement.purchases_debits == Decimal("15345.00")


def test_equation_counts_cash_advances_and_other_charges_to_the_paisa(tmp_path):
    report = _issuer_report(tmp_path, total_due="C 15,944.01")
    result = _validate(report, ISSUER)
    equation = _check(result, 13)
    assert equation.status == "fail"
    assert equation.message.startswith(
        "PREVIOUS STATEMENT DUES 8,200.00 − PAYMENTS/CREDITS 8,200.00"
        " + PURCHASES/DEBIT 15,345.00 + CASH ADVANCES 500.00"
        " + OTHER CHARGES 99.00 + FINANCE CHARGES 0.00"
        " ≠ TOTAL AMOUNT DUE 15,944.01"
    )
    assert "off by 0.01" in equation.message
    assert _check(result, 14).status == "pass"
    assert not result.passed


def test_totals_count_cash_advances_and_other_charges_to_the_paisa(tmp_path):
    # The printed cash advances are a paisa more than the row; the equation
    # still balances because the printed total carries the same paisa.
    report = _issuer_report(tmp_path, cash="500.01", total_due="C 15,944.01")
    result = _validate(report, ISSUER)
    assert _check(result, 13).status == "pass"
    totals = _check(result, 14)
    assert totals.status == "fail"
    assert totals.message == (
        "parsed debits 15,944.00 ≠ PURCHASES/DEBIT + CASH ADVANCES + OTHER CHARGES"
        " 15,944.01 (off by 0.01)"
    )
    assert not result.passed and not result.sign_verified


def _split(tmp_path, *, other_credits: bool):
    summary = {k: v for k, v in TEMPLATE.summary.items() if k != "payments_credits"}
    summary["payments"] = SummaryField(label="PAYMENTS/CREDITS", line_offset=4)
    template = TEMPLATE.model_copy(update={"summary": summary})
    if not other_credits:
        return template, _report(tmp_path, template)
    template = _with_lines(template, other_credits="OTHER CREDITS")
    # The fixture prints its payments_credits value under PAYMENTS/CREDITS.
    report = _report(
        tmp_path,
        template,
        summary={"payments_credits": "C 8,000.00"},
        extra_lines={0: ["OTHER CREDITS C 200.00"]},
    )
    return template, report


def test_split_payments_combine_into_payments_credits(tmp_path):
    template, report = _split(tmp_path, other_credits=True)
    assert template.payments_role() == "payments"
    result = _validate(report, template)
    assert result.passed and result.sign_verified, result.checks
    statement = statement_of(report, template)
    assert statement is not None
    assert statement.payments_credits == Decimal("8200.00")
    assert statement.labels["payments_credits"] == "PAYMENTS/CREDITS + OTHER CREDITS"


def test_split_payments_without_other_credits(tmp_path):
    template, report = _split(tmp_path, other_credits=False)
    result = _validate(report, template)
    assert result.passed, result.checks
    statement = statement_of(report, template)
    assert statement is not None
    assert statement.payments_credits == Decimal("8200.00")
    assert statement.labels["payments_credits"] == "PAYMENTS/CREDITS"


def test_split_payments_missing_is_a_missing_role(tmp_path):
    template, _ = _split(tmp_path, other_credits=False)
    report = _report(tmp_path, template)
    del report.summary["payments"]
    check = _check(_validate(report, template), 12)
    assert check.status == "fail"
    assert check.message == "missing roles: payments"


def test_stage_and_commit_use_one_expected_totals(tmp_path, monkeypatch):
    """Check 14 and the commit re-check read ONE expected-totals function, so
    a batch that passes stage cannot be refused at commit, or the reverse."""
    import backend.app.service.ingest.validation.pdf as vp

    report = _issuer_report(tmp_path)
    statement = statement_of(report, ISSUER)
    assert statement is not None
    rows = finalize_candidates(report, statement, template=ISSUER)
    assert _check(_validate(report, ISSUER), 14).status == "pass"
    assert vp.commit_drift(statement, rows) == ""

    debits, credits, names = vp.expected_totals(statement)
    assert (debits, credits) == (Decimal("15944.00"), Decimal("8200.00"))
    assert names == "purchases_debits + cash_advances + other_charges"

    monkeypatch.setattr(vp, "expected_totals", lambda s: (debits + 1, credits, names))
    assert _check(_validate(report, ISSUER), 14).status == "fail"
    assert "15,945.00" in vp.commit_drift(statement, rows)


# ---------------------------------------------------------------------------
# Check 17: one card number per statement


def test_card_number_passes_on_the_saved_card(tmp_path):
    result = _validate(_report(tmp_path))
    check = _check(result, 17)
    assert (check.name, check.status) == ("card_number", "pass")


def test_card_number_fails_when_none_is_printed(tmp_path):
    report = _report(tmp_path, card="ON FILE", customer_no=None)
    assert report.card_last4s == set() and report.card_lines == []
    result = _validate(report)
    check = _check(result, 17)
    assert check.status == "fail"
    assert check.message == "expected one card number; no card-shaped text found"
    assert not result.passed


def test_card_number_fails_on_two_cards_naming_lines_only(tmp_path):
    report = _report(tmp_path, extra_lines={0: ["Card No: XXXX XXXX XXXX 5678"]})
    assert report.card_last4s == {"1234", "5678"}
    check = _check(_validate(report), 17)
    assert check.status == "fail"
    lines = ", ".join(str(i) for i in report.card_lines)
    assert (
        check.message == f"expected one card number; card-shaped text on lines {lines}"
    )
    assert check.rows == report.card_lines
    assert "1234" not in check.message and "5678" not in check.message


def test_card_number_fails_on_a_different_card_than_the_saved_layout(tmp_path):
    other = hdfc_template(card_last4="9876")
    report = _report(tmp_path, other)
    assert report.card_last4s == {"1234"}
    result = _validate(report, other)
    check = _check(result, 17)
    assert check.status == "fail"
    assert check.message == (
        "this statement is for a different card than the saved layout;"
        " run /ingest-stmt to add this card"
    )
    assert "1234" not in check.message and "9876" not in check.message
    assert not result.passed
    # Every other check still passes: the card alone refuses it.
    assert [c.id for c in result.checks if c.status == "fail"] == [17]


def test_card_number_runs_when_the_summary_cannot_be_read(tmp_path):
    broken = _variant(
        summary={**TEMPLATE.summary, "total_due": SummaryField(label="NO SUCH LABEL")}
    )
    result = _validate(_report(tmp_path, broken), broken)
    assert _check(result, 12).status == "fail"
    assert _check(result, 17).status == "pass"


def test_an_optional_amount_holding_a_date_is_no_statement(tmp_path):
    """Fail closed: a mistyped cash advance is not read as 0."""
    import dataclasses

    report = _issuer_report(tmp_path)
    wrong = dataclasses.replace(report.summary["cash_advances"], value=TODAY)
    report.summary["cash_advances"] = wrong
    assert statement_of(report, ISSUER) is None
    assert _check(_validate(report, ISSUER), 12).status == "fail"


# --- Task 6b: a total due printed rounded to whole rupees --------------------
# Previous X − payments 8,200.00 + purchases 15,345.00 = the terms; the rows
# match purchases and payments, so check 14 passes throughout.

RUPEE = TEMPLATE.model_copy(update={"total_due_rounding": "rupee"})


@pytest.mark.parametrize(
    ("template", "previous", "total", "status"),
    [
        # Ruling P9: the printed whole-rupee total may be the terms rounded
        # down or rounded half up.
        (RUPEE, "C 8,200.33", "C 15,345.00", "pass"),  # x.33: floor
        (RUPEE, "C 8,200.60", "C 15,345.00", "pass"),  # x.60: floor only
        (RUPEE, "C 8,200.60", "C 15,346.00", "pass"),  # x.60: half up only
        (RUPEE, "C 8,199.50", "C 15,345.00", "pass"),  # 15,344.50: half up, not even
        (RUPEE, "C 8,200.49", "C 15,345.00", "pass"),
        (RUPEE, "C 8,200.60", "C 15,347.00", "fail"),  # floor + 2
        (RUPEE, "C 8,200.60", "C 15,344.00", "fail"),  # floor − 1
        (RUPEE, "C 8,201.00", "C 15,345.00", "fail"),  # a whole rupee is a gap
        (RUPEE, "C 8,200.40", "C 15,345.40", "fail"),  # a paise total due
        (TEMPLATE, "C 8,200.40", "C 15,345.00", "fail"),  # exact is to the paisa
        (TEMPLATE, "C 8,200.00", "C 15,345.01", "fail"),
        (TEMPLATE, "C 8,200.00", "C 15,345.00", "pass"),
    ],
    ids=[
        "rupee-x.33-floor", "rupee-x.60-floor", "rupee-x.60-half-up",
        "rupee-x.50-even-half-up", "rupee-x.49", "rupee-floor+2", "rupee-floor-1",
        "rupee-1-rupee-gap", "rupee-paise-total", "exact-x.40", "exact-0.01",
        "exact-equal",
    ],
)  # fmt: skip
def test_rupee_rounding_applies_to_the_equation_only(
    tmp_path, template, previous, total, status
):
    report = _report(
        tmp_path, template, summary={"previous_balance": previous, "total_due": total}
    )
    result = _validate(report, template)
    equation = _check(result, 13)
    assert equation.status == status, equation.message
    # Check 14 compares the rows with the printed purchases and payments,
    # which are never rounded.
    assert _check(result, 14).status == "pass"
    if status == "fail":
        mode = "rounded down or half up" if template is RUPEE else "to the paisa"
        assert mode in equation.message, equation.message


def test_rupee_rounding_never_loosens_the_totals_or_commit(tmp_path):
    # A paisa short on purchases fails check 14 and the commit re-check even
    # when the printed total due is rounded to whole rupees.
    report = _report(
        tmp_path,
        RUPEE,
        summary={"purchases_debits": "C 15,345.01", "total_due": "C 15,345.00"},
    )
    result = _validate(report, RUPEE)
    assert _check(result, 13).status == "pass"
    assert _check(result, 14).status == "fail"
    statement = statement_of(report, RUPEE)
    assert statement is not None
    assert commit_drift(statement, report.candidates) != ""
    assert not result.passed


def test_rupee_rounding_is_not_in_the_fingerprint():
    from backend.app.models.storage.pdf_template import pdf_fingerprint

    assert pdf_fingerprint(RUPEE, "1234") == pdf_fingerprint(TEMPLATE, "1234")
    assert TEMPLATE.total_due_rounding == "exact"
