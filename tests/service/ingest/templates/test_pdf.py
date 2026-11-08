from __future__ import annotations

import dataclasses
import logging
import re

import pytest
import yaml
from pydantic import ValidationError

from backend.app.service.ingest.parsers.pdf_reader import read_lines
from backend.app.service.ingest.templates.pdf import match_pdf_template
from backend.app.persistence.templates import PdfTemplateStore
from backend.app.models.storage.pdf_template import (
    REQUIRED_ROLES,
    ManualSection,
    PdfTemplate,
    PdfTemplateFields,
    SummaryField,
    SummaryRow,
    pdf_fingerprint,
)
from backend.app.persistence.templates import TemplateStore
from backend.app.models.storage.template import Template
from tests.service.ingest.pdf_fixtures import Row, make_statement_pdf
from tests.service.ingest.pdf_reference import HDFC_LAST4, HDFC_PDF_DRAFT, hdfc_template

ROW = Row("21/09/2026", "14:22", "ACME STORE BENGALURU", "1,234.00")


def _fields(**update) -> dict:
    data = HDFC_PDF_DRAFT.model_dump()
    data.update(update)
    return data


def test_reference_draft_is_valid():
    assert PdfTemplateFields(**HDFC_PDF_DRAFT.model_dump()) == HDFC_PDF_DRAFT


@pytest.mark.parametrize(
    "name", ["EMI 12", "EMI 2,000.00", "1EMI", "", "x" * 41, "EMI\n"]
)
def test_manual_section_name_must_be_plain_words(name):
    with pytest.raises(ValidationError):
        ManualSection(name=name, start="^EMI", end="^END")


@pytest.mark.parametrize("name", ["EMI", "Personal Loan", "EMI / Loan", "Fees & Taxes"])
def test_manual_section_name_accepts_plain_words(name):
    assert ManualSection(name=name, start="^EMI", end="^END").name == name


def test_row_pattern_must_name_date_description_amount():
    no_amount = r"^(?P<date>\S+) (?P<description>.+) (?P<amt>[\d.]+)$"
    with pytest.raises(ValidationError, match="amount"):
        PdfTemplateFields(**_fields(row_pattern=no_amount))
    with pytest.raises(ValidationError, match="does not compile"):
        PdfTemplateFields(**_fields(row_pattern="(?P<date>"))


@pytest.mark.parametrize(
    "update",
    [
        {"section_start": [".*"]},
        {"section_end": "^$"},
        {"continuation_pattern": "x?"},
        {"skip_patterns": ["^Page", "(Domestic)?"]},
        {"manual_sections": [{"name": "EMI", "start": "^EMI", "end": ".*"}]},
        {"manual_sections": [{"name": "EMI", "start": "", "end": "^Page"}]},
    ],
)
def test_patterns_matching_empty_are_refused(update):
    with pytest.raises(ValidationError, match="matches the empty string"):
        PdfTemplateFields(**_fields(**update))


def test_section_start_needs_at_least_one_pattern():
    with pytest.raises(ValidationError):
        PdfTemplateFields(**_fields(section_start=[]))


@pytest.mark.parametrize("role", REQUIRED_ROLES)
def test_required_roles_present(role):
    summary = HDFC_PDF_DRAFT.model_dump()["summary"]
    del summary[role]
    with pytest.raises(ValidationError, match=f"missing.*{role}"):
        PdfTemplateFields(**_fields(summary=summary))


def test_date_role_needs_date_format_and_money_role_refuses_one():
    summary = HDFC_PDF_DRAFT.model_dump()["summary"]
    summary["period_end"]["date_format"] = None
    with pytest.raises(ValidationError, match="period_end.*needs a date_format"):
        PdfTemplateFields(**_fields(summary=summary))

    summary = HDFC_PDF_DRAFT.model_dump()["summary"]
    summary["total_due"]["date_format"] = "%d %b, %Y"
    with pytest.raises(ValidationError, match="total_due.*must not have a date_format"):
        PdfTemplateFields(**_fields(summary=summary))


@pytest.mark.parametrize("offset", [-1, 6])
def test_line_offset_is_bounded(offset):
    with pytest.raises(ValidationError):
        SummaryField(label="TOTAL", line_offset=offset)


def test_line_offset_reaches_five_lines_below():
    # A label block can sit 4 lines above its values (measured on HDFC).
    assert SummaryField(label="TOTAL", line_offset=5).line_offset == 5
    assert SummaryField(label="TOTAL", line_offset=4).line_offset == 4


def test_unknown_summary_role_is_refused():
    summary = HDFC_PDF_DRAFT.model_dump()["summary"]
    summary["balance"] = {"label": "BALANCE"}
    with pytest.raises(ValidationError):
        PdfTemplateFields(**_fields(summary=summary))


@pytest.mark.parametrize(
    "account", ["Assets:Bank:HDFC", "Liabilities:Loan:HDFC", "Liabilities:CreditCard:"]
)
def test_default_account_must_be_a_card(account):
    with pytest.raises(ValidationError, match="Liabilities:CreditCard:"):
        PdfTemplateFields(**_fields(default_account=account))


def test_fingerprint_is_16_hex_and_order_independent():
    fp = pdf_fingerprint(HDFC_PDF_DRAFT, HDFC_LAST4)
    assert re.fullmatch(r"[0-9a-f]{16}", fp)
    summary = dict(reversed(list(HDFC_PDF_DRAFT.summary.items())))
    reordered = HDFC_PDF_DRAFT.model_copy(
        update={
            "summary": summary,
            "section_start": [*HDFC_PDF_DRAFT.section_start, "^Card Summary$"],
        }
    )
    swapped = HDFC_PDF_DRAFT.model_copy(
        update={
            "summary": HDFC_PDF_DRAFT.summary,
            "section_start": ["^Card Summary$", *HDFC_PDF_DRAFT.section_start],
        }
    )
    assert pdf_fingerprint(reordered, HDFC_LAST4) == pdf_fingerprint(
        swapped, HDFC_LAST4
    )
    assert pdf_fingerprint(reordered, HDFC_LAST4) != fp
    assert pdf_fingerprint(HDFC_PDF_DRAFT, HDFC_LAST4) == fp
    assert hdfc_template().fingerprint == fp


def test_template_fingerprint_must_be_16_hex():
    data = hdfc_template().model_dump()
    data["fingerprint"] = "../escape"
    with pytest.raises(ValidationError, match="16 lowercase hex"):
        PdfTemplate(**data)


def test_store_roundtrip_and_match(tmp_path):
    store = PdfTemplateStore(tmp_path / "pdf")
    template = hdfc_template()
    store.save(template)
    assert store.get_by_fingerprint(template.fingerprint) == template
    assert store.get_by_fingerprint("0" * 16) is None
    assert store.get_by_fingerprint("../../etc") is None
    assert store.list() == [template]

    lines = read_lines(make_statement_pdf(tmp_path / "s.pdf", domestic=[ROW]))
    assert match_pdf_template(store, lines) == template

    renamed = [
        dataclasses.replace(ln, text=ln.text.replace("REWARDS", "POINTS"))
        for ln in lines
    ]
    assert match_pdf_template(store, renamed) is None


def test_store_lives_apart_from_csv_templates(tmp_path):
    csv_store = TemplateStore(tmp_path)
    csv_store.save(
        Template(
            fingerprint="csv1",
            institution="HDFC",
            header_row_index=0,
            date_column="Date",
            date_format="%d/%m/%Y",
            narration_column="Narration",
            default_account="Assets:Checking:HDFC",
        )
    )
    pdf_store = PdfTemplateStore(tmp_path / "pdf")
    pdf_store.save(hdfc_template())

    assert [t.fingerprint for t in csv_store.list()] == ["csv1"]
    assert [t.fingerprint for t in pdf_store.list()] == [hdfc_template().fingerprint]


OLD_WARNING = (
    "PDF template {name} is from an older version and was skipped"
    " — run /ingest-stmt on a statement for that card again"
)


def _summary(**changes) -> dict:
    """The reference summary with roles added ({role: spec}) or dropped (None)."""
    summary = HDFC_PDF_DRAFT.model_dump()["summary"]
    for role, spec in changes.items():
        if spec is None:
            summary.pop(role, None)
        else:
            summary[role] = spec
    return summary


def _row(label="^PREVIOUS STATEMENT DUES", offset=1, roles=None, labels=None):
    roles = roles or ["previous_balance"]
    labels = labels or ["PREVIOUS STATEMENT DUES"]
    return {"label": label, "line_offset": offset, "roles": roles, "labels": labels}


def test_payments_rule_refuses_both_and_neither():
    both = _summary(payments={"label": "PAYMENTS", "line_offset": 1})
    with pytest.raises(ValidationError, match="not both"):
        PdfTemplateFields(**_fields(summary=both))

    neither = _summary(payments_credits=None)
    with pytest.raises(ValidationError, match="neither is mapped"):
        PdfTemplateFields(**_fields(summary=neither))

    # other_credits belongs to the split form only.
    combined_plus = _summary(other_credits={"label": "CREDITS", "line_offset": 1})
    with pytest.raises(ValidationError, match="other_credits"):
        PdfTemplateFields(**_fields(summary=combined_plus))

    # The rule counts summary_rows too.
    row_too = _row(label="^PAYMENTS", roles=["payments"], labels=["PAYMENTS"])
    with pytest.raises(ValidationError, match="not both"):
        PdfTemplateFields(**_fields(summary_rows=[row_too]))


def test_split_payments_with_other_credits_is_accepted():
    split = _summary(
        payments_credits=None,
        payments={"label": "PAYMENTS", "line_offset": 1},
        other_credits={"label": "OTHER CREDITS", "line_offset": 1},
    )
    fields = PdfTemplateFields(**_fields(summary=split))
    assert fields.mapped_roles()["payments"] == "summary"
    assert fields.mapped_roles()["other_credits"] == "summary"
    assert "payments_credits" not in fields.mapped_roles()

    # payments alone, from a summary row, is the split form too.
    in_row = _row(
        label="^PAYMENTS", roles=["payments", "cash_advances"], labels=["P", "C"]
    )
    fields = PdfTemplateFields(
        **_fields(summary=_summary(payments_credits=None), summary_rows=[in_row])
    )
    assert fields.mapped_roles()["payments"] == "row:0"
    assert fields.mapped_roles()["cash_advances"] == "row:0"


def test_role_mapped_twice_is_refused():
    with pytest.raises(ValidationError, match="previous_balance is mapped twice"):
        PdfTemplateFields(**_fields(summary_rows=[_row()]))

    rows = [
        _row(roles=["cash_advances"], labels=["CASH"]),
        _row(offset=2, roles=["cash_advances"], labels=["CASH"]),
    ]
    with pytest.raises(ValidationError, match="cash_advances is mapped twice"):
        PdfTemplateFields(**_fields(summary_rows=rows))

    within = _row(roles=["cash_advances", "cash_advances"], labels=["A", "B"])
    with pytest.raises(ValidationError, match="cash_advances is mapped twice"):
        PdfTemplateFields(**_fields(summary_rows=[within]))


def test_summary_row_lengths_and_no_date_roles():
    with pytest.raises(ValidationError, match="same length"):
        SummaryRow(**_row(roles=["cash_advances", "other_charges"], labels=["CASH"]))
    with pytest.raises(ValidationError, match="period_end is a date"):
        SummaryRow(**_row(roles=["cash_advances", "period_end"], labels=["A", "B"]))
    with pytest.raises(ValidationError, match="blank"):
        SummaryRow(**_row(roles=["cash_advances"], labels=["  "]))
    with pytest.raises(ValidationError):
        SummaryRow(label="^X", line_offset=1, roles=[], labels=[])
    with pytest.raises(ValidationError, match="matches the empty string"):
        SummaryRow(**_row(label="(TOTAL)?", roles=["cash_advances"], labels=["T"]))
    with pytest.raises(ValidationError, match="does not compile"):
        SummaryRow(**_row(label="(TOTAL", roles=["cash_advances"], labels=["T"]))
    for offset in (0, 6):
        with pytest.raises(ValidationError):
            SummaryRow(**_row(offset=offset, roles=["cash_advances"], labels=["C"]))
    assert SummaryRow(**_row(offset=5, roles=["cash_advances"], labels=["C"]))
    with pytest.raises(ValidationError):
        SummaryRow(**_row(roles=["balance"], labels=["B"]))
    row = SummaryRow(
        **_row(roles=["cash_advances", "other_charges"], labels=["C", "O"])
    )
    assert row.roles == ["cash_advances", "other_charges"]


def test_card_pattern_needs_last4_group_and_not_empty():
    with pytest.raises(ValidationError, match="last4"):
        PdfTemplateFields(**_fields(card_pattern=r"Card No: [\dX ]+(\d{4})"))
    with pytest.raises(ValidationError, match="matches the empty string"):
        PdfTemplateFields(**_fields(card_pattern=r"(?P<last4>\d{4})?"))
    with pytest.raises(ValidationError, match="does not compile"):
        PdfTemplateFields(**_fields(card_pattern=r"(?P<last4>\d{4}"))
    data = _fields()
    del data["card_pattern"]
    with pytest.raises(ValidationError, match="card_pattern"):
        PdfTemplateFields(**data)

    # The saved template's card_last4 is exactly four digits.
    for bad in ("123", "12345", "12a4", "XXXX"):
        with pytest.raises(ValidationError, match="card_last4"):
            HDFC_PDF_DRAFT.to_template(card_last4=bad)


def test_reference_card_pattern_finds_the_fixture_card(tmp_path):
    from backend.app.service.ingest.parsers.pdf_words import find_card_last4

    lines = read_lines(make_statement_pdf(tmp_path / "s.pdf", domestic=[ROW]))
    found, card_lines = find_card_last4(lines, HDFC_PDF_DRAFT.card_pattern)
    assert found == {HDFC_LAST4}
    # The card and the unmasked 16-digit customer number are both
    # card-shaped; the pattern, anchored on "Card No:", takes only the card.
    assert len(card_lines) == 2


def test_the_fixture_customer_number_is_not_taken_as_the_card(tmp_path):
    # Final review M1: the HDFC fixture prints a second, unmasked 16-digit
    # number. A card_pattern that is not anchored on the card's label finds
    # both, so check 17 refuses it; the reference finds only the card.
    from datetime import date

    from backend.app.service.ingest.validation.pdf import check_pdf
    from backend.app.service.ingest.parsers.pdf_words import find_card_last4
    from tests.service.ingest.pdf_fixtures import CUSTOMER_NO

    path = make_statement_pdf(tmp_path / "s.pdf", domestic=[ROW])
    lines = read_lines(path)
    unanchored = r"(?:[\dX*][ -]?){12}(?P<last4>\d{4})"
    found, _ = find_card_last4(lines, unanchored)
    assert found == {HDFC_LAST4, CUSTOMER_NO[-4:]}
    loose = hdfc_template().model_copy(update={"card_pattern": unanchored})
    _, validation, _ = check_pdf(path, loose, date(2026, 10, 25))
    assert next(c for c in validation.checks if c.id == 17).status == "fail"
    _, validation, _ = check_pdf(path, hdfc_template(), date(2026, 10, 25))
    assert next(c for c in validation.checks if c.id == 17).status == "pass"


def test_fingerprint_includes_rows_and_last4():
    assert pdf_fingerprint(HDFC_PDF_DRAFT, "1234") != pdf_fingerprint(
        HDFC_PDF_DRAFT, "5678"
    )
    assert HDFC_PDF_DRAFT.to_template(card_last4="5678").fingerprint == pdf_fingerprint(
        HDFC_PDF_DRAFT, "5678"
    )
    with_row = PdfTemplateFields(
        **_fields(summary_rows=[_row(roles=["cash_advances"], labels=["CASH"])])
    )
    moved_row = PdfTemplateFields(
        **_fields(
            summary_rows=[_row(offset=2, roles=["cash_advances"], labels=["CASH"])]
        )
    )
    base = pdf_fingerprint(HDFC_PDF_DRAFT, HDFC_LAST4)
    assert pdf_fingerprint(with_row, HDFC_LAST4) != base
    assert pdf_fingerprint(moved_row, HDFC_LAST4) != pdf_fingerprint(
        with_row, HDFC_LAST4
    )


def test_match_needs_same_card(tmp_path):
    store = PdfTemplateStore(tmp_path / "pdf")
    template = hdfc_template()
    store.save(template)

    other = make_statement_pdf(
        tmp_path / "other.pdf", domestic=[ROW], card="XXXX XXXX XXXX 5678"
    )
    assert match_pdf_template(store, read_lines(other)) is None

    same = make_statement_pdf(tmp_path / "same.pdf", domestic=[ROW])
    assert match_pdf_template(store, read_lines(same)) == template


def test_old_template_file_is_skipped_not_fatal(tmp_path, caplog):
    store = PdfTemplateStore(tmp_path / "pdf")
    template = hdfc_template()
    store.save(template)
    old = template.model_dump()
    del old["card_pattern"], old["card_last4"]
    old["fingerprint"] = "0000000000000000"
    old_path = store.dir / "0000000000000000.yaml"
    old_path.write_text(yaml.safe_dump(old, sort_keys=False), encoding="utf-8")
    (store.dir / "1111111111111111.yaml").write_text("- not a template\n")
    (store.dir / "2222222222222222.yaml").write_bytes(b"\xff\xfe\x00bad")

    lines = read_lines(make_statement_pdf(tmp_path / "s.pdf", domestic=[ROW]))
    with caplog.at_level(logging.WARNING, logger="backend.app.persistence.templates"):
        assert match_pdf_template(store, lines) == template
        assert store.list() == [template]
        assert store.get_by_fingerprint("0000000000000000") is None
        assert match_pdf_template(store, lines) == template

    messages = [r.getMessage() for r in caplog.records]
    assert messages.count(OLD_WARNING.format(name="0000000000000000.yaml")) == 1
    assert messages.count(OLD_WARNING.format(name="1111111111111111.yaml")) == 1
    assert messages.count(OLD_WARNING.format(name="2222222222222222.yaml")) == 1
    assert old_path.exists()  # skipped, never deleted


def test_match_uses_row_labels_for_required_roles(tmp_path):
    fields = PdfTemplateFields(
        **_fields(
            summary=_summary(previous_balance=None),
            summary_rows=[_row(label="^PREVIOUS STATEMENT DUES")],
        )
    )
    assert fields.mapped_roles()["previous_balance"] == "row:0"
    store = PdfTemplateStore(tmp_path / "pdf")
    template = fields.to_template(card_last4=HDFC_LAST4)
    store.save(template)

    lines = read_lines(make_statement_pdf(tmp_path / "s.pdf", domestic=[ROW]))
    assert match_pdf_template(store, lines) == template

    renamed = [
        dataclasses.replace(
            ln, text=ln.text.replace("PREVIOUS STATEMENT DUES", "OPENING BALANCE")
        )
        for ln in lines
    ]
    assert match_pdf_template(store, renamed) is None


def test_match_never_raises_on_unusual_roles(tmp_path):
    store = PdfTemplateStore(tmp_path / "pdf")
    template = hdfc_template()
    # Built without validation: no payments role at all, a bad card pattern.
    odd = PdfTemplate.model_construct(
        **{
            **template.model_dump(),
            "summary": {
                k: v
                for k, v in template.summary.items()
                if k not in ("payments_credits", "total_due")
            },
            "card_pattern": "(?P<last4>",
        }
    )
    store.list = lambda: [odd]  # type: ignore[method-assign]
    lines = read_lines(make_statement_pdf(tmp_path / "s.pdf", domestic=[ROW]))
    assert match_pdf_template(store, lines) is None
