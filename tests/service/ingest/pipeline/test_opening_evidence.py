from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.models.storage.template import Template
from backend.app.persistence.templates import TemplateStore
from backend.app.service.ingest.parsers.csv_xls import (
    load_frame,
    parse_statement_report,
)
from backend.app.service.ingest.pipeline.opening_evidence import (
    OpeningCellError,
    csv_evidence,
    first_row_opening,
    printed_opening,
    read_printed_opening,
)
from backend.app.service.ledger.openings.suggest import printed_ledger_amount
from tests.llm.flows.prove_template.reference_drafts import REFERENCE

SAMPLE = Path("tests/fixtures/statements/sample_bank_running_balance.csv")
LEGACY_TEMPLATES = Path("tests/fixtures/persistence/legacy_written/templates")
OPENING = {"label": "^Opening Balance$", "row_offset": 0}


def _sample_template(*, skip: bool = True, **extra) -> Template:
    draft = dict(REFERENCE["sample_bank_running_balance"]["draft"])
    draft["skip_exact_narrations"] = ["Opening Balance"] if skip else []
    draft.update(extra)
    return Template(fingerprint="fp", **draft)


def _read(path: Path, template: Template):
    return load_frame(path, template.delimiter), parse_statement_report(path, template)


def test_reads_header_block_opening():
    t = _sample_template(opening_balance=OPENING)
    frame, report = _read(SAMPLE, t)
    # The in-table row (index 7, narration "Opening Balance") also matches
    # the label; it is a data row, so it is not a second match.
    assert frame.iat[7, 1] == "Opening Balance"
    printed = read_printed_opening(frame, t, report)
    assert printed is not None
    assert printed.value == Decimal("10000.00")
    assert (printed.row, printed.column) == (2, 4)
    assert printed.suffix is None
    assert printed.label_text == "Opening Balance"
    assert printed.raw_text == "10000.00"


CARD_HEADER = "Date,Description,Amount,Type\n"


def _card(tmp_path: Path, text: str) -> tuple[Path, Template]:
    p = tmp_path / "card.csv"
    p.write_text(text, encoding="utf-8")
    header = text.splitlines().index(CARD_HEADER.strip())
    t = Template(
        fingerprint="fp",
        institution="Card",
        header_row_index=header,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Description",
        amount_column="Amount",
        sign_column="Type",
        default_account="Liabilities:CreditCard:Axis",
    )
    return p, t


def _with(t: Template, **update) -> Template:
    return Template.model_validate({**t.model_dump(), **update})


ROWS = "01/04/2026,SWIGGY,100.00,Dr\n02/04/2026,PAYMENT,500.00,Cr\n"


def test_two_matches_outside_rows_is_error(tmp_path):
    text = (
        "Opening Balance,1000.00,,\n"
        + CARD_HEADER
        + ROWS
        + "Opening Balance,900.00,,\n"
    )
    p, t = _card(tmp_path, text)
    t = _with(t, opening_balance=OPENING)
    frame, report = _read(p, t)
    with pytest.raises(OpeningCellError) as err:
        read_printed_opening(frame, t, report)
    message = str(err.value)
    assert "row 0 column 0" in message and "row 4 column 0" in message
    # Rows and columns only: never the statement's cell text.
    assert "1000.00" not in message and "900.00" not in message


def test_no_match_is_error(tmp_path):
    p, t = _card(tmp_path, CARD_HEADER + ROWS)
    t = _with(t, opening_balance=OPENING)
    frame, report = _read(p, t)
    with pytest.raises(OpeningCellError, match="matched no cell"):
        read_printed_opening(frame, t, report)


def test_first_row_derivation_descending():
    t = _sample_template()
    frame, report = _read(SAMPLE, t)
    first = first_row_opening(frame, t, report)
    assert first is not None
    assert first.date == date(2026, 3, 12)
    assert first.balance == Decimal("9500.00")
    assert first.signed_amount == Decimal("-500.00")
    assert first.derived == Decimal("10000.00")


def test_first_row_derivation_counts_in_table_opening_row():
    t = _sample_template(skip=False)
    frame, report = _read(SAMPLE, t)
    first = first_row_opening(frame, t, report)
    assert first is not None
    assert first.date == date(2026, 3, 10)
    assert first.derived == Decimal("0.00")


def test_row_offset_reads_cell_below(tmp_path):
    text = "Opening Bal,,,\n,,,\n5000.00,,,\n" + CARD_HEADER + ROWS
    p, t = _card(tmp_path, text)
    t = _with(t, opening_balance={"label": "^Opening Bal$", "row_offset": 2})
    frame, report = _read(p, t)
    printed = read_printed_opening(frame, t, report)
    assert printed is not None
    assert printed.value == Decimal("5000.00")
    assert (printed.row, printed.column) == (2, 0)


def test_row_offset_into_the_data_rows_is_error(tmp_path):
    text = "Opening Bal,,,\n" + CARD_HEADER + ROWS
    p, t = _card(tmp_path, text)
    t = _with(t, opening_balance={"label": "^Opening Bal$", "row_offset": 2})
    frame, report = _read(p, t)
    with pytest.raises(OpeningCellError, match="not a row outside the data rows"):
        read_printed_opening(frame, t, report)


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        ("bank", "10,000.00", Decimal("10000.00")),
        ("bank", "500.00 Dr", Decimal("-500.00")),
        ("bank", "-75.10", Decimal("-75.10")),
        ("card", "5000.00", Decimal("-5000.00")),
        ("card", "5000.00 Cr", Decimal("5000.00")),
        ("card", "5000.00 Dr", Decimal("-5000.00")),
        ("card", "-200.00", Decimal("200.00")),
    ],
)
def test_printed_ledger_amount_signs(kind, raw, expected):
    printed = printed_opening("Opening Balance", raw, row=0, column=1)
    assert printed is not None
    assert printed_ledger_amount(kind, printed) == expected


@pytest.mark.parametrize("raw", ["-500.00 Dr", "-200.00 Cr", "- 5,000.00 cr"])
def test_minus_with_a_suffix_is_refused(raw):
    """Two signs may cancel or agree: refused, never guessed."""
    assert printed_opening("Opening Balance", raw, row=0, column=1) is None


def test_minus_with_a_suffix_makes_the_cell_unreadable(tmp_path):
    for text, offset in (
        ("Opening Balance,-500.00 Dr,1000.00,\n", 0),
        ("Opening Balance,,,\n-500.00 Dr,,,\n", 1),
    ):
        p, t = _card(tmp_path, text + CARD_HEADER + ROWS)
        t = _with(
            t, opening_balance={"label": "^Opening Balance$", "row_offset": offset}
        )
        frame, report = _read(p, t)
        with pytest.raises(OpeningCellError, match="both a minus and a Cr/Dr suffix"):
            read_printed_opening(frame, t, report)


def test_label_text_is_the_match_not_the_cell(tmp_path):
    text = "Opening Balance A/c 50100012345678,10000.00,,\n" + CARD_HEADER + ROWS
    p, t = _card(tmp_path, text)
    t = _with(t, opening_balance={"label": "Opening Balance"})
    frame, report = _read(p, t)
    printed = read_printed_opening(frame, t, report)
    assert printed is not None and printed.label_text == "Opening Balance"


def test_suffix_is_recorded():
    assert printed_opening("x", "5,000.00 cr", row=0, column=1).suffix == "Cr"
    assert printed_opening("x", "5,000.00 DR.", row=0, column=1).suffix == "Dr"
    assert printed_opening("x", "5,000.00", row=0, column=1).suffix is None


def test_no_balance_column_no_derivation(tmp_path):
    p, t = _card(tmp_path, CARD_HEADER + ROWS)
    frame, report = _read(p, t)
    assert first_row_opening(frame, t, report) is None


def test_csv_evidence_never_raises_on_a_bad_cell(tmp_path):
    p, t = _card(tmp_path, CARD_HEADER + ROWS)
    t = _with(t, opening_balance=OPENING)
    frame, report = _read(p, t)
    ev = csv_evidence(frame, t, report)
    assert ev.printed is None and "matched no cell" in (ev.printed_error or "")
    assert ev.account == "Liabilities:CreditCard:Axis"


def test_csv_evidence_on_the_sample():
    t = _sample_template(opening_balance=OPENING)
    frame, report = _read(SAMPLE, t)
    ev = csv_evidence(frame, t, report)
    assert ev.account == "Assets:Checking:HDFC"
    assert ev.printed is not None and ev.printed.value == Decimal("10000.00")
    assert ev.first_row is not None and ev.first_row.derived == Decimal("10000.00")
    assert ev.printed_error is None


def test_template_saved_before_the_field_reads_no_opening(tmp_path):
    """A template YAML written before `opening_balance` existed loads with
    it unset, and its evidence carries no printed figure and no error."""
    (legacy,) = TemplateStore(LEGACY_TEMPLATES).list()
    assert legacy.opening_balance is None
    t = _sample_template()
    assert t.opening_balance is None
    frame, report = _read(SAMPLE, t)
    ev = csv_evidence(frame, t, report)
    assert ev.printed is None and ev.printed_error is None
