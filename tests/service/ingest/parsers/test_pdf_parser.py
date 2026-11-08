from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.parsers.pdf_parser import parse_pdf_report
from backend.app.service.ingest.parsers.pdf_reader import PdfLine, PdfWord, read_lines
from backend.app.models.storage.pdf_template import (
    PdfTemplate,
    PdfTemplateFields,
    SummaryField,
    SummaryRow,
)
from tests.service.ingest.pdf_fixtures import Row, make_statement_pdf
from tests.service.ingest.pdf_reference import ROW_PATTERN, hdfc_template

# The exact reading (no rounding, no narration lines): the reference itself
# sets total_due_rounding and description_around, which tests opt into.
TEMPLATE = hdfc_template(total_due_rounding="exact", description_around=False)

DOMESTIC = [
    Row("21/09/2026", "14:22", "ACME STORE BENGALURU", "1,234.00"),
    Row("22/09/2026", "10:00", "INITECH FOODS", "250.50"),
    Row("23/09/2026", "11:30", "UMBRELLA PHARMA", "75.00"),
]
INTERNATIONAL = [
    Row("25/09/2026", "09:05", "GLOBEX LONDON", "99.00"),
    Row("26/09/2026", "18:40", "HOOLI CLOUD", "1,20,000.00"),
    Row("27/09/2026", "07:15", "SOYLENT", "12.34"),
]


def _lines(tmp_path, name="s.pdf", **kw) -> list[PdfLine]:
    kw.setdefault("domestic", DOMESTIC)
    return read_lines(make_statement_pdf(tmp_path / name, **kw))


def _variant(**update) -> PdfTemplate:
    return TEMPLATE.model_copy(update=update)


def _with_summary(**roles: SummaryField) -> PdfTemplate:
    return _variant(summary={**TEMPLATE.summary, **roles})


def _built(*rows: str | list[tuple[str, float]]) -> list[PdfLine]:
    """PdfLines built by hand for layouts the generated fixture does not print.

    A row is plain text starting at x=0, or a list of (text, x) segments. Each
    word is 5pt per character with a 5pt gap.
    """
    out: list[PdfLine] = []
    for row in rows:
        segments = [(row, 0.0)] if isinstance(row, str) else row
        words: list[PdfWord] = []
        for text, x in segments:
            for token in text.split():
                words.append(PdfWord(token, x, x + 5 * len(token)))
                x += 5 * len(token) + 5
        spans, pos = [], 0
        for word in words:
            spans.append((pos, pos + len(word.text)))
            pos += len(word.text) + 1
        out.append(
            PdfLine(
                index=len(out),
                page=0,
                text=" ".join(w.text for w in words),
                words=tuple(words),
                spans=tuple(spans),
            )
        )
    return out


def _reasons(report) -> dict[str, str]:
    return {s.cells[0]: s.reason for s in report.skipped}


def test_rows_parse_across_pages_with_different_x_offsets(tmp_path):
    lines = _lines(tmp_path, international=INTERNATIONAL)
    report = parse_pdf_report(lines, TEMPLATE)

    assert [c.amount for c in report.candidates] == [
        Decimal("1234.00"),
        Decimal("250.50"),
        Decimal("75.00"),
        Decimal("99.00"),
        Decimal("120000.00"),
        Decimal("12.34"),
    ]
    assert [lines[i].page for i in report.candidate_rows] == [0, 0, 0, 1, 1, 1]
    assert report.candidates[0].raw_narration == "ACME STORE BENGALURU"
    assert report.candidates[0].date == date(2026, 9, 21)
    assert {c.source_account for c in report.candidates} == {
        "Liabilities:CreditCard:HDFC"
    }
    assert report.total_lines == len(lines)
    assert len(report.skipped) + len(report.candidates) == len(lines)


def test_plus_credit_is_credit_and_rewards_never_amount(tmp_path):
    rows = [
        Row("21/09/2026", "14:22", "ACME STORE", "1,234.00", rewards=12),
        Row("22/09/2026", "10:00", "ACME REFUND", "500.00", credit=True),
    ]
    report = parse_pdf_report(_lines(tmp_path, domestic=rows), TEMPLATE)
    debit, credit = report.candidates
    assert debit.amount == Decimal("1234.00")
    assert debit.direction is Direction.DEBIT
    assert debit.raw_narration == "ACME STORE"
    assert credit.amount == Decimal("500.00")
    assert credit.direction is Direction.CREDIT


def test_wrapped_description_joins_and_changes_id(tmp_path):
    plain = Row("21/09/2026", "14:22", "ACME STORE", "1,234.00")
    wrapped = dataclasses.replace(plain, wrap="BENGALURU KA")
    (twin,) = parse_pdf_report(
        _lines(tmp_path, "a.pdf", domestic=[plain]), TEMPLATE
    ).candidates
    report = parse_pdf_report(_lines(tmp_path, "b.pdf", domestic=[wrapped]), TEMPLATE)
    (joined,) = report.candidates

    assert joined.raw_narration == "ACME STORE BENGALURU KA"
    assert joined.normalized_narration != twin.normalized_narration
    assert joined.id != twin.id
    # The id is the content-derived one for the joined narration, not a stale
    # or blank carry-over.
    assert joined.id == CandidateTxn(**{**joined.model_dump(), "id": ""}).id
    assert _reasons(report)["BENGALURU KA"] == "continuation"


def test_bar_apart_date_variant_parses(tmp_path):
    row = Row("23/09/2026", "11:30", "BAR SHOP", "10.00", bar_apart=True)
    (cand,) = parse_pdf_report(_lines(tmp_path, domestic=[row]), TEMPLATE).candidates
    assert cand.date == date(2026, 9, 23)
    assert cand.raw_narration == "BAR SHOP"
    assert cand.amount == Decimal("10.00")


def test_unknown_line_inside_section_is_no_match(tmp_path):
    lines = _lines(tmp_path, extra_lines={0: ["01/10/2026 SOMETHING ODD"]})
    report = parse_pdf_report(lines, TEMPLATE)
    assert _reasons(report)["01/10/2026 SOMETHING ODD"] == "no_match"
    assert len(report.candidates) == 3

    # A continuation chain ends at the first line that is not one: a stray
    # line after a skipped heading is not glued onto the row above.
    rows = [dataclasses.replace(DOMESTIC[0], wrap="BENGALURU KA")]
    lines = _lines(
        tmp_path,
        "w.pdf",
        domestic=rows,
        extra_lines={0: ["Domestic Transactions", "STRAY NOTE"]},
    )
    report = parse_pdf_report(lines, TEMPLATE)
    assert _reasons(report)["STRAY NOTE"] == "no_match"
    assert report.candidates[0].raw_narration == "ACME STORE BENGALURU BENGALURU KA"


def test_description_wrapped_onto_three_lines_chains(tmp_path):
    rows = [dataclasses.replace(DOMESTIC[0], wrap="MG ROAD")]
    lines = _lines(tmp_path, domestic=rows, extra_lines={0: ["KARNATAKA"]})
    report = parse_pdf_report(lines, TEMPLATE)
    (cand,) = report.candidates
    assert cand.raw_narration == "ACME STORE BENGALURU MG ROAD KARNATAKA"
    assert cand.id == CandidateTxn(**{**cand.model_dump(), "id": ""}).id
    reasons = _reasons(report)
    assert reasons["MG ROAD"] == reasons["KARNATAKA"] == "continuation"


def test_skip_pattern_under_a_row_is_not_continuation(tmp_path):
    (twin, *_) = parse_pdf_report(
        _lines(tmp_path, "a.pdf", domestic=[DOMESTIC[0]]), TEMPLATE
    ).candidates
    lines = _lines(
        tmp_path,
        "b.pdf",
        domestic=[DOMESTIC[0]],
        extra_lines={0: ["International Transactions"]},
    )
    report = parse_pdf_report(lines, TEMPLATE)
    (cand,) = report.candidates
    assert _reasons(report)["International Transactions"] == "skip_pattern"
    assert cand.raw_narration == "ACME STORE BENGALURU"
    assert cand.id == twin.id


_LOOSE_ROWS = {
    "date": (
        r"^(?:(?P<date>\d{2}/\d{2}/\d{4}) )?(?P<description>.+?) C (?P<amount>[\d,]+\.\d{2})$",
        "NO DATE ROW C 5.00",
        "bad_date",
    ),
    "description": (
        r"^(?P<date>\d{2}/\d{2}/\d{4})(?: (?P<description>[A-Z]+))? C (?P<amount>[\d,]+\.\d{2})$",
        "21/09/2026 C 5.00",
        "no_match",
    ),
    "blank description": (
        r"^(?P<date>\d{2}/\d{2}/\d{4})(?P<description>\s*) C (?P<amount>[\d,]+\.\d{2})$",
        "21/09/2026 C 5.00",
        "no_match",
    ),
    "amount": (
        r"^(?P<date>\d{2}/\d{2}/\d{4}) (?P<description>[A-Z ]+?)(?: C (?P<amount>[\d,]+\.\d{2}))?$",
        "21/09/2026 ONLY TEXT",
        "bad_amount",
    ),
}


@pytest.mark.parametrize("group", list(_LOOSE_ROWS))
def test_row_group_that_did_not_participate_is_skipped(group):
    pattern, text, reason = _LOOSE_ROWS[group]
    lines = _built("DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI", text)
    report = parse_pdf_report(lines, _variant(row_pattern=pattern))
    assert report.candidates == []
    assert _reasons(report)[text] == reason


def test_bad_date_in_a_matching_row_is_skipped():
    lines = _built(
        "DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI",
        "31/02/2026 10:00 NOT A DAY C 5.00",
    )
    report = parse_pdf_report(lines, TEMPLATE)
    assert report.candidates == []
    assert _reasons(report)["31/02/2026 10:00 NOT A DAY C 5.00"] == "bad_date"


def test_empty_international_section_closes_at_page_end(tmp_path):
    lines = _lines(tmp_path, international=[])
    report = parse_pdf_report(lines, TEMPLATE)
    reasons = _reasons(report)

    assert [s for s in report.skipped if s.reason == "no_match"] == []
    assert len(report.candidates) == 3
    # The footer right under the last page-1 row is not glued onto it.
    assert reasons["Page 1 of 2"] == "skip_pattern"
    assert report.candidates[-1].raw_narration == "UMBRELLA PHARMA"
    assert reasons["International Transactions"] == "skip_pattern"
    assert reasons["TRANSACTIONS TOTAL AMOUNT C 1,559.50"] == "section_end"

    # A page 2 that prints no header is outside any section: the page-1
    # section closed when the page changed.
    headless = [
        ln for ln in lines if not (ln.page == 1 and ln.text.startswith("DATE & TIME"))
    ]
    page2 = _reasons(parse_pdf_report(headless, TEMPLATE))
    assert page2["International Transactions"] == "outside_section"
    assert page2["TRANSACTIONS TOTAL AMOUNT C 1,559.50"] == "outside_section"


def test_summary_reads_next_line_values_by_column(tmp_path):
    rows = [
        Row("21/09/2026", "14:22", "ACME STORE", "1,234.00"),
        Row("22/09/2026", "10:00", "ACME REFUND", "300.00", credit=True),
    ]
    lines = _lines(
        tmp_path,
        domestic=rows,
        summary={"previous_balance": "C 1,23,456.78", "finance_charges": "C 45.60"},
    )
    report = parse_pdf_report(lines, TEMPLATE)
    values = {role: v.value for role, v in report.summary.items()}

    assert report.summary_errors == []
    assert values["previous_balance"] == Decimal("123456.78")
    assert values["payments_credits"] == Decimal("300.00")
    assert values["purchases_debits"] == Decimal("1234.00")
    assert values["finance_charges"] == Decimal("45.60")
    assert values["total_due"] == Decimal("8200.00") - 300 + 1234
    assert values["minimum_due"] == Decimal("456.70")
    assert values["transactions_total"] == Decimal("1234.00")
    assert values["due_date"] == date(2026, 11, 10)
    assert report.summary["total_due"].label_text == "TOTAL AMOUNT DUE"


def test_summary_reads_same_line_value_and_cr_negates():
    template = _with_summary(total_due=SummaryField(label="TOTAL AMOUNT DUE"))
    report = parse_pdf_report(_built("TOTAL AMOUNT DUE C 500.00 Cr"), template)
    assert report.summary["total_due"].value == Decimal("-500.00")
    assert report.summary["total_due"].line == 0

    report = parse_pdf_report(_built("TOTAL AMOUNT DUE C 500.00 Dr"), template)
    assert report.summary["total_due"].value == Decimal("500.00")


@pytest.mark.parametrize("glyph", ["C", "₹", "Rs."])
def test_amount_tokens_with_lakh_grouping_and_glyphs(glyph):
    same_line = _with_summary(total_due=SummaryField(label="TOTAL AMOUNT DUE"))
    report = parse_pdf_report(
        _built(f"TOTAL AMOUNT DUE {glyph} 1,23,456.78"), same_line
    )
    assert report.summary["total_due"].value == Decimal("123456.78")

    # Next line, by column: the value under the label, not its neighbour's.
    lines = _built(
        [("MINIMUM DUE", 0), ("TOTAL AMOUNT DUE", 200)],
        [(f"{glyph} 9,999.00", 0), (f"{glyph} 1,23,456.78", 195)],
    )
    report = parse_pdf_report(lines, TEMPLATE)
    assert report.summary["total_due"].value == Decimal("123456.78")
    assert report.summary["minimum_due"].value == Decimal("9999.00")


def test_label_matching_two_lines_is_a_summary_error(tmp_path):
    lines = _lines(tmp_path, extra_lines={1: ["TOTAL AMOUNT DUE"]})
    report = parse_pdf_report(lines, TEMPLATE)
    hits = [ln.index for ln in lines if "TOTAL AMOUNT DUE" in ln.text]
    assert len(hits) == 2
    assert (
        f"total_due: label TOTAL AMOUNT DUE matches lines {hits[0]}, {hits[1]}"
        " — make it specific"
    ) in report.summary_errors
    assert "total_due" not in report.summary


def test_label_not_found_is_a_summary_error(tmp_path):
    template = _with_summary(
        credit_limit=SummaryField(label="CREDIT LIMIT", line_offset=1)
    )
    report = parse_pdf_report(_lines(tmp_path), template)
    assert report.summary_errors == ["credit_limit: label CREDIT LIMIT not found"]


def test_two_roles_on_one_word_is_a_summary_error(tmp_path):
    lines = _lines(tmp_path)
    template = _with_summary(
        minimum_due=SummaryField(label="TOTAL AMOUNT DUE", line_offset=1)
    )
    report = parse_pdf_report(lines, template)
    value_line = (
        next(ln.index for ln in lines if ln.text.startswith("TOTAL AMOUNT")) + 1
    )
    assert report.summary_errors == [
        f"total_due and minimum_due read the same value on line {value_line}"
    ]
    assert "total_due" not in report.summary
    assert "minimum_due" not in report.summary


def test_period_dates_from_billing_period_label(tmp_path):
    report = parse_pdf_report(_lines(tmp_path), TEMPLATE)
    assert report.summary_errors == []
    assert report.summary["period_start"].value == date(2026, 9, 21)
    assert report.summary["period_end"].value == date(2026, 10, 20)


def test_emi_lines_are_manual_hits(tmp_path):
    lines = _lines(tmp_path, emi_lines=["EMI 1/12 C 2,000.00"])
    report = parse_pdf_report(lines, TEMPLATE)
    n = next(ln.index for ln in lines if ln.text == "EMI 1/12 C 2,000.00")
    assert report.manual_hits == [("EMI", n)]
    assert n not in report.candidate_rows
    assert Decimal("2000.00") not in [c.amount for c in report.candidates]
    assert _reasons(report)["EMI 1/12 C 2,000.00"] == "manual_section"


def test_fx_metadata_from_named_groups(tmp_path):
    fx_pattern = ROW_PATTERN.replace(
        r"(?P<description>.+?)",
        r"(?P<description>.+?)(?:\s(?P<fx_amount>[\d.]+) (?P<fx_currency>[A-Z]{3}))?",
    )
    template = _variant(row_pattern=fx_pattern)
    intl = [Row("25/09/2026", "09:05", "GLOBEX LONDON 12.00 USD", "1,020.00")]
    report = parse_pdf_report(_lines(tmp_path, international=intl), template)

    *domestic, foreign = report.candidates
    assert foreign.fx == "USD 12.00"
    assert foreign.raw_narration == "GLOBEX LONDON"
    assert foreign.amount == Decimal("1020.00")
    assert {c.fx for c in domestic} == {None}
    # fx is display metadata: it does not move the content-derived id.
    assert (
        foreign.id == CandidateTxn(**{**foreign.model_dump(), "id": "", "fx": None}).id
    )


# --- F6: the measured layout facts, end to end on a generated PDF -----------

MEASURED = [
    Row("21/09/2026", "14:22", "ACME STORE", "1,234.00", bar_glued=True, pi=True),
    Row("22/09/2026", "10:00", "INITECH FOODS", "250.50", bar_glued=True),
    Row("23/09/2026", "11:30", "ACME REFUND", "75.00", credit=True, pi=True),
    Row("24/09/2026", "09:05", "UMBRELLA PHARMA", "1,20,000.00", rewards=40, pi=True),
    Row("25/09/2026", "18:40", "HOOLI CLOUD", "12.34", bar_glued=True, wrap="KA IN"),
]


def test_glued_bar_trailing_pi_and_2pt_jitter_parse_end_to_end(tmp_path):
    lines = _lines(tmp_path, domestic=MEASURED, international=MEASURED[:2], jitter=2.0)
    texts = {ln.text for ln in lines}
    # The generated PDF really prints the measured forms.
    assert any(t.startswith("21/09/2026| 14:22 ") and t.endswith(" l") for t in texts)
    report = parse_pdf_report(lines, TEMPLATE)

    assert [r for r, *_ in _bad(report)] == []
    got = [(c.date, c.raw_narration, c.amount, c.direction) for c in report.candidates]
    expected = [
        (date(2026, 9, 21), "ACME STORE", Decimal("1234.00"), Direction.DEBIT),
        (date(2026, 9, 22), "INITECH FOODS", Decimal("250.50"), Direction.DEBIT),
        (date(2026, 9, 23), "ACME REFUND", Decimal("75.00"), Direction.CREDIT),
        (date(2026, 9, 24), "UMBRELLA PHARMA", Decimal("120000.00"), Direction.DEBIT),
        (date(2026, 9, 25), "HOOLI CLOUD KA IN", Decimal("12.34"), Direction.DEBIT),
    ]
    assert got == expected + expected[:2]
    # One line per printed row: 2pt of jitter never splits a row.
    assert [lines[i].page for i in report.candidate_rows] == [0] * 5 + [1] * 2


def test_measured_layout_passes_the_statement_gate(tmp_path):
    from backend.app.service.ingest.validation.pdf import check_pdf

    path = make_statement_pdf(tmp_path / "m.pdf", domestic=MEASURED, jitter=2.0)
    _, validation, statement = check_pdf(path, TEMPLATE, date(2026, 10, 24))
    assert validation.passed, validation.checks
    assert statement is not None


def _bad(report) -> list[tuple[int, str]]:
    return [
        (s.row_index, s.reason)
        for s in report.skipped
        if s.reason in ("no_match", "bad_date", "bad_amount")
    ]


# --- F7: a continuation chain is bounded --------------------------------------


def test_continuation_chain_stops_after_two_lines(tmp_path):
    """A row and at most two continuation lines (three visual lines); text
    printed further under the last row is no_match, never glued on."""
    rows = [dataclasses.replace(DOMESTIC[0], wrap="MG ROAD")]
    lines = _lines(
        tmp_path, domestic=rows, extra_lines={0: ["KARNATAKA", "REWARDS PANEL"]}
    )
    report = parse_pdf_report(lines, TEMPLATE)
    (cand,) = report.candidates
    assert cand.raw_narration == "ACME STORE BENGALURU MG ROAD KARNATAKA"
    reasons = _reasons(report)
    assert reasons["MG ROAD"] == reasons["KARNATAKA"] == "continuation"
    assert reasons["REWARDS PANEL"] == "no_match"


def test_a_line_starting_with_a_date_is_never_a_continuation():
    template = _variant(continuation_pattern=r".+")  # a draft that allows anything
    lines = _built(
        "DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI",
        "21/09/2026 14:22 ACME STORE C 10.00",
        "22/09/2026 ODD LINE",
        "22 Sep 2026 ANOTHER",
    )
    report = parse_pdf_report(lines, template)
    (cand,) = report.candidates
    assert cand.raw_narration == "ACME STORE"
    reasons = _reasons(report)
    assert (
        reasons["22/09/2026 ODD LINE"] == reasons["22 Sep 2026 ANOTHER"] == "no_match"
    )


def test_glued_rupee_summary_value_is_read():
    template = _with_summary(total_due=SummaryField(label="TOTAL AMOUNT DUE"))
    report = parse_pdf_report(_built("TOTAL AMOUNT DUE ₹8,200.00"), template)
    assert report.summary["total_due"].value == Decimal("8200.00")


def test_continuation_joins_a_number_led_line_but_stops_at_a_real_date():
    template = _variant(continuation_pattern=r".+")
    head = [
        "DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI",
        "21/09/2026 14:22 ACME STORE C 10.00",
    ]
    lines = _built(*head, "1 THE MALL", "12 MAY STREET")
    report = parse_pdf_report(lines, template)
    (cand,) = report.candidates
    assert cand.raw_narration == "ACME STORE 1 THE MALL"
    reasons = _reasons(report)
    assert reasons["1 THE MALL"] == "continuation"
    assert reasons["12 MAY STREET"] == "no_match"


# ---------------------------------------------------------------------------
# Ordered summary rows and the card number

AXIS_LABELS = [
    "Previous Balance",
    "Payments",
    "Credits",
    "Purchase",
    "Cash Advance",
    "Other Debit & Charges",
    "Total Payment Due",
]
AXIS_ROLES = [
    "previous_balance",
    "payments",
    "other_credits",
    "purchases_debits",
    "cash_advances",
    "other_charges",
    "total_due",
]
AXIS_VALUES = [
    "9,876.54",
    "1,000.00",
    "50.00",
    "2,345.67",
    "0.00",
    "12.34",
    "11,184.55",
]
_PERIOD = SummaryField(label="^Statement Period", date_format="%d/%m/%Y")


def _placed(*rows: list[tuple[str, float, float]]) -> list[PdfLine]:
    """PdfLines with explicit word geometry: each word is (text, x0, x1)."""
    out: list[PdfLine] = []
    for row in rows:
        words = tuple(PdfWord(t, x0, x1) for t, x0, x1 in row)
        spans, pos = [], 0
        for word in words:
            spans.append((pos, pos + len(word.text)))
            pos += len(word.text) + 1
        out.append(
            PdfLine(
                index=len(out),
                page=0,
                text=" ".join(w.text for w in words),
                words=words,
                spans=tuple(spans),
            )
        )
    return out


def _fields(**update) -> dict:
    base = TEMPLATE.model_dump(exclude={"fingerprint", "card_last4", "origin"})
    base.update(
        summary={"period_start": _PERIOD, "period_end": _PERIOD},
        summary_rows=[],
        card_pattern=r"\d{6}\*{6}(?P<last4>\d{4})",
    )
    base.update(update)
    return base


def _template(**update) -> PdfTemplate:
    return PdfTemplateFields.model_validate(_fields(**update)).to_template(
        card_last4="9999"
    )


def _axis_row(labels=AXIS_LABELS) -> SummaryRow:
    return SummaryRow(
        label="^Previous Balance", line_offset=2, roles=AXIS_ROLES, labels=labels
    )


# The second Axis block, as measured (ANALYSIS doc): the "Previous Balance"
# label spans about 68-125 (centre ~96.5), its own value is centred near 62
# and the payments value near 130 - so the label sits nearer the wrong value.
_AXIS_LABEL_LINE = [
    ("Previous", 68, 98),
    ("Balance", 101, 125),
    ("-", 133, 136),
    ("Payments", 145, 185),
    ("-", 192, 195),
    ("Credits", 200, 230),
    ("+", 236, 239),
    ("Purchase", 245, 285),
    ("+", 291, 294),
    ("Cash", 300, 320),
    ("Advance", 323, 355),
    ("+", 361, 364),
    ("Other", 370, 393),
    ("Debit", 396, 418),
    ("&", 421, 425),
    ("Charges", 428, 460),
    ("=", 467, 470),
    ("Total", 480, 500),
    ("Payment", 503, 538),
    ("Due", 541, 560),
]
_AXIS_VALUE_CENTRES = [62, 130, 190, 250, 310, 380, 470]


def _axis_value_line(
    values=AXIS_VALUES, note=("Includes", "GST", "where", "applicable")
):
    words = []
    for text, centre in zip(values, _AXIS_VALUE_CENTRES, strict=False):
        words.append((text, centre - 2.5 * len(text), centre + 2.5 * len(text)))
    x = 520.0
    for text in note:
        words.append((text, x, x + 5 * len(text)))
        x += 5 * len(text) + 5
    return words


def _axis_lines(
    values=AXIS_VALUES, note=("Includes", "GST", "where", "applicable"), label_line=None
):
    return _placed(
        [
            ("Statement", 0, 45),
            ("Period", 50, 80),
            ("22/09/2026", 90, 140),
            ("-", 145, 150),
            ("21/10/2026", 155, 205),
        ],
        label_line or _AXIS_LABEL_LINE,
        [("(Rs.)", 60, 85)],
        _axis_value_line(values, note),
    )


def test_summary_row_maps_in_order():
    report = parse_pdf_report(_axis_lines(), _template(summary_rows=[_axis_row()]))

    assert report.summary_errors == []
    values = {role: report.summary[role].value for role in AXIS_ROLES}
    assert values == {
        role: Decimal(v.replace(",", "")) for role, v in zip(AXIS_ROLES, AXIS_VALUES)
    }
    assert {report.summary[r].label_text for r in AXIS_ROLES} == set(AXIS_LABELS)
    assert report.summary["other_charges"].label_text == "Other Debit & Charges"
    assert {report.summary[r].line for r in AXIS_ROLES} == {3}
    assert report.summary["period_end"].value == date(2026, 10, 21)


def test_summary_row_count_mismatch_is_an_error():
    template = _template(summary_rows=[_axis_row()])
    report = parse_pdf_report(_axis_lines(values=AXIS_VALUES[:6]), template)
    assert report.summary_errors == [
        "summary row ^Previous Balance: line 3 has 6 amounts, expected 7"
    ]
    assert not set(AXIS_ROLES) & set(report.summary)

    noted = _axis_lines(note=("Includes", "GST", "of", "123.45"))
    report = parse_pdf_report(noted, template)
    assert report.summary_errors == [
        "summary row ^Previous Balance: line 3 has 8 amounts, expected 7"
    ]
    assert not set(AXIS_ROLES) & set(report.summary)


def test_summary_row_labels_out_of_order_or_missing():
    swapped = [AXIS_LABELS[1], AXIS_LABELS[0], *AXIS_LABELS[2:]]
    report = parse_pdf_report(
        _axis_lines(), _template(summary_rows=[_axis_row(swapped)])
    )
    assert report.summary_errors == [
        "summary row ^Previous Balance: label 'Previous Balance' is missing or"
        " out of order on line 1"
    ]
    assert not set(AXIS_ROLES) & set(report.summary)

    no_cash = [w for w in _AXIS_LABEL_LINE if w[0] not in ("Cash", "Advance")]
    report = parse_pdf_report(
        _axis_lines(label_line=no_cash), _template(summary_rows=[_axis_row()])
    )
    assert report.summary_errors == [
        "summary row ^Previous Balance: label 'Cash Advance' is missing or"
        " out of order on line 1"
    ]
    assert not set(AXIS_ROLES) & set(report.summary)


def test_summary_row_label_must_match_exactly_one_line():
    template = _template(summary_rows=[_axis_row()])
    lines = _axis_lines()
    report = parse_pdf_report(lines, template)
    assert report.summary_errors == []

    doubled = _placed(
        [(w.text, w.x0, w.x1) for w in lines[0].words],
        _AXIS_LABEL_LINE,
        [("(Rs.)", 60, 85)],
        _axis_value_line(),
        _AXIS_LABEL_LINE,
    )
    report = parse_pdf_report(doubled, template)
    assert report.summary_errors == [
        "summary row ^Previous Balance: label matches lines 1, 4 — make it specific"
    ]
    report = parse_pdf_report(lines[:1], template)
    assert report.summary_errors == ["summary row ^Previous Balance: label not found"]
    report = parse_pdf_report(lines[:3], template)
    assert report.summary_errors == [
        "summary row ^Previous Balance: no line 2 below the label on line 1"
    ]


def test_two_rows_share_one_label_line():
    labels = [
        "Total Amount due",
        "Previous Balance",
        "Purchases / Charges",
        "Cash Advances",
        "Payments / Credits",
    ]
    rows = [
        SummaryRow(
            label="^Total Amount due",
            line_offset=2,
            roles=["total_due"],
            labels=labels[:1],
        ),
        SummaryRow(
            label="^Total Amount due",
            line_offset=3,
            roles=[
                "previous_balance",
                "purchases_debits",
                "cash_advances",
                "payments_credits",
            ],
            labels=labels[1:],
        ),
    ]
    lines = _built(
        "Statement Period 22/09/2026 - 21/10/2026",
        " ".join(labels),
        "= + + -",
        "₹11,500.00",
        "₹10,000.00 ₹2,000.00 ₹500.00 ₹1,000.00",
    )
    report = parse_pdf_report(lines, _template(summary_rows=rows))

    assert report.summary_errors == []
    got = {role: (v.value, v.line, v.label_text) for role, v in report.summary.items()}
    assert got["total_due"] == (Decimal("11500.00"), 3, "Total Amount due")
    assert got["previous_balance"] == (Decimal("10000.00"), 4, "Previous Balance")
    assert got["purchases_debits"] == (Decimal("2000.00"), 4, "Purchases / Charges")
    assert got["cash_advances"] == (Decimal("500.00"), 4, "Cash Advances")
    assert got["payments_credits"] == (Decimal("1000.00"), 4, "Payments / Credits")


def test_nearest_centre_would_misread_the_axis_row():
    """Why rows exist: the same lines read through single labels by nearest
    centre give previous_balance the payments value, and payments and
    other_credits both claim the credits value; the ordered read is right."""
    singles = {
        role: SummaryField(label=label, line_offset=2)
        for role, label in zip(AXIS_ROLES, AXIS_LABELS, strict=True)
    }
    lines = _axis_lines()
    by_centre = parse_pdf_report(
        lines,
        _template(summary={**singles, "period_start": _PERIOD, "period_end": _PERIOD}),
    )
    assert by_centre.summary["previous_balance"].value == Decimal("1000.00")
    assert by_centre.summary_errors == [
        "payments and other_credits read the same value on line 3"
    ]

    by_row = parse_pdf_report(lines, _template(summary_rows=[_axis_row()]))
    assert by_row.summary_errors == []
    assert by_row.summary["previous_balance"].value == Decimal("9876.54")
    assert by_row.summary["payments"].value == Decimal("1000.00")
    assert by_row.summary["other_credits"].value == Decimal("50.00")


def test_row_and_single_label_on_one_word_collide():
    """The two-roles-one-word rule covers row-read values: a single label
    that lands, by nearest centre, on a word a row already maps is an error."""
    template = _template(
        summary_rows=[_axis_row()],
        summary={
            "period_start": _PERIOD,
            "period_end": _PERIOD,
            "credit_limit": SummaryField(label="Purchase", line_offset=2),
        },
    )
    report = parse_pdf_report(_axis_lines(), template)
    assert report.summary_errors == [
        "credit_limit and purchases_debits read the same value on line 3"
    ]
    assert "purchases_debits" not in report.summary
    assert "credit_limit" not in report.summary
    assert report.summary["previous_balance"].value == Decimal("9876.54")


def test_card_last4s_and_lines_reported():
    lines = _built(
        "Card Number 999999******4321",
        "nothing here",
        "Credit Limit 999999******4321 99,999.99",
        "999999******4321",
    )
    card = _template(summary_rows=[_axis_row()])
    report = parse_pdf_report(lines, card)
    assert report.card_last4s == {"4321"}
    # Line 2 runs the number into the credit-limit figure (Axis line 10 as
    # measured), so it is not a card occurrence.
    assert report.card_lines == [0, 3]

    two = _built("999999******4321", "999999******8765")
    report = parse_pdf_report(two, card)
    assert report.card_last4s == {"4321", "8765"}
    assert report.card_lines == [0, 1]

    report = parse_pdf_report(_built("no card printed"), card)
    assert report.card_last4s == set()
    assert report.card_lines == []


# --- Task 6b: labels deeper than 3 lines, descriptions around the row -------


def test_summary_field_reads_four_lines_below():
    # HDFC prints payments and purchases 4 lines above their values.
    template = _with_summary(
        payments_credits=SummaryField(label="PAYMENTS/CREDITS", line_offset=4)
    )
    lines = _built(
        "PAYMENTS/CREDITS",
        "PREVIOUS STATEMENT DUES",
        "a caption",
        "C 9.00",  # 3 lines below: the wrong value
        "C 1,234.50",
    )
    report = parse_pdf_report(lines, template)
    assert report.summary["payments_credits"].value == Decimal("1234.50")
    assert report.summary["payments_credits"].line == 4


def test_summary_row_reads_four_lines_below():
    row = SummaryRow(
        label="^Previous Balance", line_offset=4, roles=AXIS_ROLES, labels=AXIS_LABELS
    )
    lines = _placed(
        [("Statement", 0, 45), ("Period", 50, 80), ("22/09/2026", 90, 140),
         ("-", 145, 150), ("21/10/2026", 155, 205)],
        _AXIS_LABEL_LINE,
        [("(Rs.)", 60, 85)],
        [("note", 60, 85)],
        [("C", 60, 65), ("1.00", 70, 90)],  # 3 lines below: not the value line
        _axis_value_line(),
    )  # fmt: skip
    report = parse_pdf_report(lines, _template(summary_rows=[row]))
    assert report.summary_errors == []
    values = {role: report.summary[role].value for role in AXIS_ROLES}
    assert values == {
        role: Decimal(v.replace(",", "")) for role, v in zip(AXIS_ROLES, AXIS_VALUES)
    }
    assert {report.summary[r].line for r in AXIS_ROLES} == {5}


# A row whose description group may be blank: the narration is printed on the
# line above and the line below the date/time/amount line (measured on HDFC).
AROUND_ROW = (
    r"^(?P<date>\d{2}/\d{2}/\d{4})\s?\|?\s*\d{2}:\d{2}(?:\s+(?P<description>.+?))??"
    r"(?:\s+\+\s(?P<rewards>\d+))?\s+(?P<credit>\+\s)?C\s(?P<amount>[\d,]+\.\d{2})"
    r"(?:\s+l)?$"
)
# The probe's form: the description starts with the time, the rest optional.
TIME_ROW = (
    r"^(?P<date>\d{2}/\d{2}/\d{4})\s?\|?\s*(?P<description>\d{2}:\d{2}(?:\s+.+?)??)"
    r"(?:\s+\+\s(?P<rewards>\d+))?\s+(?P<credit>\+\s)?C\s(?P<amount>[\d,]+\.\d{2})"
    r"(?:\s+l)?$"
)
HEADER = "DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI"


def _around(on: bool = True, row: str = AROUND_ROW, **update) -> PdfTemplate:
    fields = {"row_pattern": row, "continuation_pattern": None, **update}
    return _variant(description_around=on, **fields)


def _narrations(report) -> list[str]:
    return [c.raw_narration for c in report.candidates]


def test_description_around_joins_the_lines_above_and_below():
    lines = _built(
        HEADER,
        "21/09/2026 14:22 ACME STORE C 100.00",
        "PAYMENT RECEIVED",
        "22/09/2026 10:00 + C 500.00",
        "NETBANKING THANK YOU",
        "23/09/2026 11:30 INITECH FOODS C 50.00",
    )
    report = parse_pdf_report(lines, _around())
    assert _narrations(report) == [
        "ACME STORE",
        "PAYMENT RECEIVED NETBANKING THANK YOU",
        "INITECH FOODS",
    ]
    payment = report.candidates[1]
    assert payment.direction is Direction.CREDIT and payment.amount == Decimal("500")
    assert payment.id == CandidateTxn(**{**payment.model_dump(), "id": ""}).id
    reasons = _reasons(report)
    assert reasons["PAYMENT RECEIVED"] == "continuation"
    assert reasons["NETBANKING THANK YOU"] == "continuation"

    # Off (the default): the row and both halves of its narration are missed.
    off = parse_pdf_report(lines, _around(on=False))
    assert _narrations(off) == ["ACME STORE", "INITECH FOODS"]
    reasons = _reasons(off)
    assert reasons["22/09/2026 10:00 + C 500.00"] == "no_match"
    assert reasons["PAYMENT RECEIVED"] == reasons["NETBANKING THANK YOU"] == "no_match"


def test_description_around_replaces_a_time_only_description():
    lines = _built(
        HEADER, "PAYMENT RECEIVED", "22/09/2026 10:00 + C 500.00", "THANK YOU"
    )
    report = parse_pdf_report(lines, _around(row=TIME_ROW))
    assert _narrations(report) == ["PAYMENT RECEIVED THANK YOU"]
    # Nothing around it: the time-only description stays.
    alone = parse_pdf_report(
        _built(HEADER, "22/09/2026 10:00 C 5.00"), _around(row=TIME_ROW)
    )
    assert _narrations(alone) == ["10:00"]


@pytest.mark.parametrize(
    "above",
    [
        "21/09/2026 14:22 ACME STORE C 100.00",  # a row
        "Domestic Transactions",  # a skip pattern
        HEADER,  # the section header
        "20/09/2026 BROKEN ROW",  # date-led: a missed row, never a narration
        "",  # blank
    ],
    ids=["row", "skip-pattern", "section-start", "date-led", "blank"],
)
def test_description_around_takes_only_the_line_below_when_above_is_taken(above):
    lines = _built(
        *([HEADER] if above != HEADER else []),
        above,
        "22/09/2026 10:00 + C 500.00",
        "THANK YOU",
    )
    report = parse_pdf_report(lines, _around())
    assert _narrations(report)[-1] == "THANK YOU"
    if above.strip():
        assert _reasons(report).get(above) != "continuation"


CONT = r"^(?!\d{2}/\d{2}/\d{4}).+$"


def test_description_around_wraps_still_continue_the_row_above():
    # A wrap that is not directly above a description-less row is still the
    # continuation of the row above, with a continuation_pattern that would
    # match any plain line.
    lines = _built(
        HEADER,
        "21/09/2026 14:22 ACME STORE C 100.00",
        "BENGALURU KA",  # the wrap of ACME STORE
        "PAYMENT RECEIVED",  # directly above the blank row: its narration
        "22/09/2026 10:00 + C 500.00",
        "THANK YOU",
    )
    report = parse_pdf_report(lines, _around(continuation_pattern=CONT))
    assert _narrations(report) == [
        "ACME STORE BENGALURU KA",
        "PAYMENT RECEIVED THANK YOU",
    ]


def test_continuation_does_not_take_the_line_above_a_blank_description_row():
    # Ruling P11: the line directly above a row without a description is that
    # row's narration, even when continuation_pattern would join it onto the
    # row before. Without description_around the old reading stands.
    lines = _built(
        HEADER,
        "21/09/2026 14:22 ACME STORE C 100.00",
        "PAYMENT RECEIVED",
        "22/09/2026 10:00 + C 500.00",
        "THANK YOU",
    )
    on = parse_pdf_report(lines, _around(continuation_pattern=CONT))
    assert _narrations(on) == ["ACME STORE", "PAYMENT RECEIVED THANK YOU"]
    off = parse_pdf_report(lines, _around(on=False, continuation_pattern=CONT))
    assert _narrations(off)[0] == "ACME STORE PAYMENT RECEIVED"


def test_lone_line_between_a_row_and_a_blank_row_goes_to_the_blank_row():
    # Pinned trade-off (re-review N2): a lone line directly above a
    # description-less row is that row's narration even when it was the wrap
    # of the row before. The two shapes are textually the same.
    lines = _built(
        HEADER,
        "21/09/2026 14:22 ACME STORE C 100.00",
        "BENGALURU KA",
        "22/09/2026 10:00 C 500.00",
        "INITECH FOODS",
    )
    on = parse_pdf_report(lines, _around(continuation_pattern=CONT))
    off = parse_pdf_report(lines, _around(on=False, continuation_pattern=CONT))
    assert _narrations(on) == ["ACME STORE", "BENGALURU KA INITECH FOODS"]
    assert _narrations(off) == ["ACME STORE BENGALURU KA"]
    # The first row's amount is the same either way. The second row exists
    # only with the option on: with it off its description is blank, so it is
    # refused (a missed row, check 1), and that is why the counts differ.
    assert on.candidates[0].amount == off.candidates[0].amount == Decimal("100.00")
    assert [c.amount for c in on.candidates] == [Decimal("100.00"), Decimal("500.00")]
    assert [c.amount for c in off.candidates] == [Decimal("100.00")]


def test_one_plain_line_between_two_blank_rows_goes_to_the_first_row():
    # Final review M2: the line below the first blank row is taken as its
    # narration, so it is no longer a plain line and the second blank row
    # does not take it again as its line above.
    lines = _built(
        HEADER,
        "22/09/2026 10:00 + C 500.00",
        "SHARED LINE",
        "23/09/2026 11:00 C 5.00",
        "AFTER",
    )
    report = parse_pdf_report(lines, _around())
    assert _narrations(report) == ["SHARED LINE", "AFTER"]
    assert _reasons(report)["SHARED LINE"] == "continuation"


def test_a_boundary_line_that_also_matches_the_row_pattern_is_not_a_blank_row():
    # Re-review N3: the main loop treats a section end before the row
    # pattern, so the plain line above it stays a continuation.
    lines = _built(
        HEADER,
        "21/09/2026 14:22 ACME STORE C 100.00",
        "BENGALURU KA",
        "22/09/2026 10:00 + C 500.00",
    )
    report = parse_pdf_report(
        lines, _around(continuation_pattern=CONT, section_end=r"^22/09/2026 10:00")
    )
    assert _narrations(report) == ["ACME STORE BENGALURU KA"]


@pytest.mark.parametrize(
    "below",
    [
        "23/09/2026 11:30 INITECH FOODS C 50.00",  # a row
        "Page 1 of 2",  # a skip pattern
        "TRANSACTIONS TOTAL AMOUNT C 550.00",  # the section end
        "24/09/2026 BROKEN ROW",  # date-led
    ],
    ids=["row", "skip-pattern", "section-end", "date-led"],
)
def test_description_around_takes_only_the_line_above_when_below_is_taken(below):
    lines = _built(HEADER, "PAYMENT RECEIVED", "22/09/2026 10:00 + C 500.00", below)
    report = parse_pdf_report(lines, _around())
    assert _narrations(report)[0] == "PAYMENT RECEIVED"
    assert _reasons(report).get(below) != "continuation"


def test_description_around_never_reaches_across_a_page_break():
    lines = _built(
        HEADER,
        "PAYMENT RECEIVED",
        "22/09/2026 10:00 + C 500.00",
        "NEXT PAGE TEXT",
        HEADER,
        "THANK YOU",
        "23/09/2026 11:30 + C 5.00",
    )
    lines = [dataclasses.replace(ln, page=0 if ln.index < 3 else 1) for ln in lines]
    report = parse_pdf_report(lines, _around())
    # The first row keeps only its own page's line; the page-2 line after it
    # is outside any section (the table closed at the page break).
    assert _narrations(report) == ["PAYMENT RECEIVED", "THANK YOU"]
    assert _reasons(report)["NEXT PAGE TEXT"] == "outside_section"


def test_description_around_with_nothing_around_is_still_a_missed_row():
    lines = _built(HEADER, "22/09/2026 10:00 + C 500.00", "Page 1 of 2")
    report = parse_pdf_report(lines, _around())
    assert report.candidates == []
    assert _reasons(report)["22/09/2026 10:00 + C 500.00"] == "no_match"


def test_description_around_on_a_generated_statement(tmp_path):
    from backend.app.service.ingest.validation.pdf import check_pdf

    rows = [
        Row("21/09/2026", "14:22", "ACME STORE", "1,000.00"),
        Row(
            "22/09/2026",
            "10:00",
            "",
            "8,200.00",
            credit=True,
            above="PAYMENT RECEIVED",
            below="NETBANKING THANK YOU",
        ),
        Row("23/09/2026", "11:30", "INITECH FOODS", "250.00"),
    ]
    path = make_statement_pdf(tmp_path / "around.pdf", domestic=rows)
    texts = [ln.text for ln in read_lines(path)]
    i = texts.index("22/09/2026 10:00 + C 8,200.00")
    assert texts[i - 1 : i + 2 : 2] == ["PAYMENT RECEIVED", "NETBANKING THANK YOU"]

    _, on, _ = check_pdf(path, _around(row=TIME_ROW), date(2026, 10, 24))
    assert on.passed, on.checks
    report = parse_pdf_report(read_lines(path), _around(row=TIME_ROW))
    assert _narrations(report) == [
        "14:22 ACME STORE",
        "PAYMENT RECEIVED NETBANKING THANK YOU",
        "11:30 INITECH FOODS",
    ]
    # Off: the narration lines are missed in the table, so coverage refuses.
    _, off, _ = check_pdf(path, _around(on=False, row=TIME_ROW), date(2026, 10, 24))
    assert not off.passed
    assert next(c for c in off.checks if c.id == 1).status == "fail"
