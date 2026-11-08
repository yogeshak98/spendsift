from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from backend.app.models.api.agent import Claims
from backend.app.models.storage.template import Template
from backend.app.service.ingest.parsers.csv_xls import (
    load_frame,
    parse_statement_report,
)
from backend.app.service.ingest.validation.csv_xls import validate
from tests.llm.flows.prove_template.reference_drafts import REFERENCE

TODAY = date(2026, 10, 16)
SAMPLE = Path("tests/fixtures/statements/sample_bank_running_balance.csv")
OPENING = {"label": "^Opening Balance$", "row_offset": 0}
REQUIRED = (
    "claims.opening_balance_printed is required when the draft maps opening_balance"
)


def _template(*, skip: bool = True, **extra) -> Template:
    draft = dict(REFERENCE["sample_bank_running_balance"]["draft"])
    draft["skip_exact_narrations"] = ["Opening Balance"] if skip else []
    draft.update(extra)
    return Template(fingerprint="fp", **draft)


def _claims(printed: str | None = None, **extra) -> Claims:
    return Claims(
        first_data_row=4,
        last_data_row=7,
        opening_balance_printed=None if printed is None else Decimal(printed),
        **extra,
    )


def _validate(template, *, claims=None, reused=False, kind="bank", path=SAMPLE):
    return validate(
        frame=load_frame(path, template.delimiter),
        template=template,
        kind=kind,
        claims=claims,
        report=parse_statement_report(path, template),
        today=TODAY,
        reused=reused,
    )


def _check18(rep):
    (check,) = [c for c in rep.checks if c.id == 18]
    assert check.name == "opening_balance"
    return check


def test_check18_not_applicable_without_field():
    rep = _validate(_template(), claims=_claims())
    assert _check18(rep).status == "not_applicable"
    assert rep.passed


def test_check18_requires_claim_on_proof():
    rep = _validate(_template(opening_balance=OPENING), claims=_claims())
    check = _check18(rep)
    assert (check.status, check.message) == ("fail", REQUIRED)
    assert not rep.passed


def test_check18_requires_claim_without_any_claims():
    rep = _validate(_template(opening_balance=OPENING), claims=None, reused=False)
    assert (_check18(rep).status, _check18(rep).message) == ("fail", REQUIRED)


def test_check18_passes_when_claim_and_derivation_agree():
    rep = _validate(_template(opening_balance=OPENING), claims=_claims("10000.00"))
    assert _check18(rep).status == "pass"
    assert rep.passed


def test_check18_compares_to_the_paisa():
    rep = _validate(_template(opening_balance=OPENING), claims=_claims("10000"))
    assert _check18(rep).status == "pass"


def test_check18_fails_on_claim_mismatch():
    rep = _validate(_template(opening_balance=OPENING), claims=_claims("9000.00"))
    check = _check18(rep)
    assert check.status == "fail"
    assert "9000.00" in check.message and "10000.00" in check.message
    assert not rep.passed


def test_check18_fails_when_derivation_disagrees():
    rep = _validate(
        _template(skip=False, opening_balance=OPENING), claims=_claims("10000.00")
    )
    check = _check18(rep)
    assert check.status == "fail"
    assert check.message == (
        "printed opening 10000.00 disagrees with first-row derivation 0.00"
    )
    assert not rep.passed


def test_check18_derivation_is_bank_only(tmp_path):
    """A card statement gets no derivation, so a printed figure that the
    claim confirms passes on its own."""
    p = tmp_path / "card.csv"
    p.write_text(
        "Opening Bal,5000.00,,\nDate,Description,Amount,Type\n"
        "01/04/2026,SWIGGY,100.00,Dr\n02/04/2026,PAYMENT,500.00,Cr\n",
        encoding="utf-8",
    )
    t = Template(
        fingerprint="fp",
        institution="Card",
        header_row_index=1,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Description",
        amount_column="Amount",
        sign_column="Type",
        default_account="Liabilities:CreditCard:Axis",
        opening_balance={"label": "^Opening Bal$"},
    )
    claims = Claims(
        first_data_row=2, last_data_row=3, opening_balance_printed=Decimal("5000.00")
    )
    rep = _validate(t, claims=claims, kind="card", path=p)
    assert _check18(rep).status == "pass"


def test_check18_reused_passes_without_claim():
    rep = _validate(_template(opening_balance=OPENING), claims=None, reused=True)
    assert _check18(rep).status == "pass"
    assert rep.passed


def test_check18_reused_unreadable_cell_warns_not_fails():
    """A reused template whose cell this file does not print loses only the
    suggestion: check 18 warns with the reason and validation passes."""
    rep = _validate(
        _template(opening_balance={"label": "^Closing Balance$"}),
        claims=None,
        reused=True,
    )
    check = _check18(rep)
    assert check.status == "warn" and "matched no cell" in check.message
    assert rep.passed


def test_check18_proof_unreadable_cell_still_fails():
    rep = _validate(
        _template(opening_balance={"label": "^Closing Balance$"}),
        claims=_claims("10000.00"),
    )
    check = _check18(rep)
    assert check.status == "fail" and "matched no cell" in check.message
    assert not rep.passed


def test_check18_warns_on_a_claim_without_the_field():
    rep = _validate(_template(), claims=_claims("10000.00"))
    check = _check18(rep)
    assert (check.status, check.message) == (
        "warn",
        "claim given but opening_balance not mapped",
    )
    assert rep.passed


def test_check18_two_matches_is_a_failed_check_not_a_crash(tmp_path):
    """OpeningCellError surfaces as a check 18 fail naming both rows."""
    text = SAMPLE.read_text(encoding="utf-8") + "Opening Balance,,,,10000.00\n"
    p = tmp_path / "two.csv"
    p.write_text(text, encoding="utf-8")
    t = _template(opening_balance=OPENING, stop_pattern=None)
    rep = _validate(t, claims=_claims("10000.00"), path=p)
    check = _check18(rep)
    assert check.status == "fail"
    assert "row 2 column 0" in check.message and "row 11 column 0" in check.message


def test_existing_checks_unchanged():
    """Checks 1-10 on a template without the field are exactly what they
    were before check 18 existed (a failing case, so rows and messages are
    pinned too), and check 18 is appended as not_applicable."""
    t = _template(
        skip=False, amount_sign="indicator", sign_column="Ref", stop_pattern=None
    )
    claims = Claims(first_data_row=4, last_data_row=6, period_end=date(2026, 3, 14))
    rep = _validate(t, claims=claims)
    got = [(c.id, c.name, c.status, c.message, c.rows) for c in rep.checks]
    assert got == [
        (1, "coverage", "pass", "", []),
        (2, "nothing_outside", "fail", "transactions parsed outside rows 4..6", [7]),
        (
            3,
            "positive_amounts",
            "fail",
            "amounts must be positive; direction carries the sign",
            [5, 6],
        ),
        (
            4,
            "running_balance",
            "fail",
            "running balance does not reconcile (wrong sign, missing or merged rows)",
            [4, 5, 6],
        ),
        (5, "opening_closing", "not_applicable", "", []),
        (
            6,
            "dates",
            "fail",
            "dates outside the statement period or in the future",
            [4],
        ),
        (7, "day_month_ambiguity", "pass", "", []),
        (
            8,
            "sanity",
            "fail",
            "the sign column has values R2, R3, R4, R5 but every row parsed as "
            "debit: credit_indicators probably wrong",
            [],
        ),
        (9, "stop_skip", "pass", "", []),
        (10, "balance_column_declared", "pass", "", []),
        (18, "opening_balance", "not_applicable", "", []),
    ]
    assert rep.warnings == [
        "sign convention unverified: no running or opening/closing balance was checked"
    ]
    passing = _validate(
        _template(),
        claims=_claims(
            opening_balance=Decimal("10000.00"), closing_balance=Decimal("58000.00")
        ),
    )
    assert [(c.id, c.status) for c in passing.checks] == [
        *[(i, "pass") for i in range(1, 11)],
        (18, "not_applicable"),
    ]
    assert passing.sign_verified
