"""ICICI- and Axis-style card statements end to end: parse, validate, stage,
commit and peek, on generated fictional PDFs that reproduce the measured
layouts, with the reference templates the prover is expected to write."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import data
from fastapi.testclient import TestClient

from backend.app.llm.flows.prove_template.peek import pdf_line_text, peek_pdf
from backend.app.llm.flows.prove_template.service import AgentIngestService
from backend.app.service.ingest.validation.pdf import check_pdf
from backend.app.server.main import create_app
from backend.app.models.domain.candidate import Direction
from backend.app.service.ingest.parsers.pdf_parser import parse_pdf_report
from backend.app.service.ingest.parsers.pdf_reader import PdfLine, read_lines
from backend.app.service.ingest.parsers.pdf_words import find_card_last4
from backend.app.models.storage.statement_record import STATEMENT_TYPE
from tests.service.ingest.pdf_fixtures import Row, make_statement_pdf
from tests.service.ingest.pdf_fixtures_issuers import (
    AXIS_HOLDER,
    ICICI_HOLDER,
    Txn,
    make_axis_pdf,
    make_icici_pdf,
)
from tests.service.ingest.pdf_reference import (
    AXIS_LAST4,
    AXIS_PDF_DRAFT,
    HDFC_LAST4,
    HDFC_PDF_DRAFT,
    ICICI_LAST4,
    ICICI_PDF_DRAFT,
    ICICI_ROW_PATTERN,
    axis_template,
    hdfc_template,
    icici_template,
)

TODAY = date(2026, 10, 25)
D = Decimal

# --- ICICI -------------------------------------------------------------------
# Previous 6,400.00 - payments/credits 6,749.00 + purchases 4,989.75
# (+ cash advances 3,000.00 in the cash case) = total due 4,640.75 (7,640.75).
ICICI_PAGE1 = [
    Txn(
        "22/09/2026",
        "AMAZON PAY INDIA ONLINE",
        "1,249.00",
        ref="20260922001",
        points=12,
    ),
    Txn("25/09/2026", "INITECH FOODS MUMBAI", "560.50", ref="20260925002", points=5),
    Txn(
        "28/09/2026",
        "BBPS PAYMENT RECEIVED",
        "6,400.00",
        kind="payment",
        ref="20260928003",
    ),
    Txn("02/10/2026", "GLOBEX FUEL STATION", "1,350.00", ref="20261002004"),
]
ICICI_PAGE2 = [
    Txn(
        "09/10/2026", "AMAZON REFUND ONLINE", "349.00", kind="refund", ref="20261009005"
    ),
]
ICICI_INTL = [
    Txn("15/10/2026", "ACME CLOUD SERVICES", "1,830.25", ref="20261015006", points=18),
]
ICICI_CASH = Txn(
    "12/10/2026", "CASH WITHDRAWAL ATM", "3,000.00", kind="cash", ref="20261012007"
)
ICICI_CARD = "Liabilities:CreditCard:ICICI"

# --- Axis --------------------------------------------------------------------
# Previous 9,850.00 - payments 9,850.00 - credits 420.00 + purchases 3,385.40
# (+ cash advance 2,500.00 + other charges 590.00 in the charges case)
# = total due 2,965.40 (6,055.40).
AXIS_PAGE1 = [
    Txn(
        "17/09/2026",
        "ACME MART KOCHI",
        "2,140.00",
        category="SHOPPING",
        cashback="21.40",
    ),
    Txn(
        "20/09/2026",
        "PAYMENT RECEIVED THANK YOU",
        "9,850.00",
        kind="payment",
        category="PAYMENT",
    ),
    Txn(
        "26/09/2026",
        "INITECH CAFE",
        "845.40",
        category="FOOD & DINING",
        cashback="8.45",
    ),
]
AXIS_PAGE2 = [
    Txn(
        "03/10/2026", "GLOBEX TRAVEL REFUND", "420.00", kind="refund", category="TRAVEL"
    ),
    Txn(
        "08/10/2026", "UMBRELLA PHARMACY", "400.00", category="HEALTH", cashback="4.00"
    ),
]
AXIS_CHARGES = [
    Txn("10/10/2026", "ATM CASH WITHDRAWAL", "2,500.00", kind="cash", category="CASH"),
    Txn("10/10/2026", "CASH ADVANCE FEE AND GST", "590.00", kind="other",
        category="FEES"),
]  # fmt: skip
AXIS_CARD = "Liabilities:CreditCard:Axis"


def _icici(tmp_path: Path, name: str = "icici.pdf", *, cash: bool = False, **kw):
    page2 = [*ICICI_PAGE2, ICICI_CASH] if cash else ICICI_PAGE2
    return make_icici_pdf(
        tmp_path / name, page1=ICICI_PAGE1, page2=page2, international=ICICI_INTL, **kw
    )


def _axis(tmp_path: Path, name: str = "axis.pdf", *, charges: bool = False, **kw):
    page2 = [*AXIS_PAGE2, *AXIS_CHARGES] if charges else AXIS_PAGE2
    return make_axis_pdf(tmp_path / name, page1=AXIS_PAGE1, page2=page2, **kw)


def _rows(report) -> list[tuple[date, Decimal, Direction, str]]:
    return [(c.date, c.amount, c.direction, c.raw_narration) for c in report.candidates]


def _summary(report) -> dict[str, object]:
    return {role: found.value for role, found in report.summary.items()}


def _check_status(validation) -> dict[int, str]:
    return {c.id: c.status for c in validation.checks if 12 <= c.id <= 17}


def _holder_words(holder: tuple[str, str], shown: str) -> list[str]:
    """Holder name and address words (and the PIN code) that peek shows."""
    words = [w for line in holder for w in line.split()]
    return [w for w in words if (w.isalpha() or len(w) >= 5) and w in shown]


ALL_PASS = {12: "pass", 13: "pass", 14: "pass", 15: "pass", 16: "pass", 17: "pass"}

# --- the app ----------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: TODAY)
    TestClient(app)  # runs startup
    app.state.ledger.open_named_accounts([ICICI_CARD, AXIS_CARD])
    return app


@pytest.fixture
def svc(app) -> AgentIngestService:
    return AgentIngestService(
        import_service=app.state.import_service,
        template_store=app.state.template_store,
        pdf_template_store=app.state.pdf_template_store,
        review_store=app.state.review_store,
        ledger=app.state.ledger,
        clock=lambda: TODAY,
    )


def _journal_text(ledger) -> str:
    paths = [p for p in sorted(ledger.journal_dir.glob("*")) if p.is_file()]
    assert paths, f"no journal files in {ledger.journal_dir}"
    return "".join(p.read_text(encoding="utf-8") for p in paths)


def _records(ledger) -> list[data.Custom]:
    return [
        e
        for e in ledger.load().entries
        if isinstance(e, data.Custom) and e.type == STATEMENT_TYPE
    ]


def _stage_and_commit_twice(svc, app, first: Path, again: Path, draft, expected):
    run = svc.inspect(first.name, first.read_bytes())
    out = svc.dry_run_pdf(run["prove_id"], draft)
    assert out["report"]["passed"], out["report"]
    assert out["fingerprint"] == expected.fingerprint
    staged = svc.stage(run["prove_id"], "proof")
    assert staged["template_saved"] is True
    committed = svc.commit(staged["batch_id"])
    assert committed["statement_recorded"] is True
    written = committed["committed"]

    rerun = svc.inspect(again.name, again.read_bytes())
    assert rerun["template_match"] is not None
    assert rerun["template_match"]["fingerprint"] == expected.fingerprint
    restaged = svc.stage(rerun["prove_id"], "template_match")
    second = svc.commit(restaged["batch_id"])
    assert second["committed"] == 0 and second["statement_recorded"] is False
    return written


# --- ICICI tests ------------------------------------------------------------


def test_icici_parses_rows_and_summary(tmp_path):
    lines = read_lines(_icici(tmp_path, cash=True))
    report = parse_pdf_report(lines, icici_template())

    debit, credit = Direction.DEBIT, Direction.CREDIT
    # Reward points (12, 5, 18) never become the amount, and CR marks credits.
    assert _rows(report) == [
        (date(2026, 9, 22), D("1249.00"), debit, "AMAZON PAY INDIA ONLINE"),
        (date(2026, 9, 25), D("560.50"), debit, "INITECH FOODS MUMBAI"),
        (date(2026, 9, 28), D("6400.00"), credit, "BBPS PAYMENT RECEIVED"),
        (date(2026, 10, 2), D("1350.00"), debit, "GLOBEX FUEL STATION"),
        (date(2026, 10, 9), D("349.00"), credit, "AMAZON REFUND ONLINE"),
        (date(2026, 10, 12), D("3000.00"), debit, "CASH WITHDRAWAL ATM"),
        (date(2026, 10, 15), D("1830.25"), debit, "ACME CLOUD SERVICES"),
    ]
    assert report.summary_errors == []
    assert _summary(report) == {
        "total_due": D("7640.75"),
        "previous_balance": D("6400.00"),
        "purchases_debits": D("4989.75"),
        "cash_advances": D("3000.00"),
        "payments_credits": D("6749.00"),
        "period_start": date(2026, 9, 22),
        "period_end": date(2026, 10, 21),
    }
    assert report.card_last4s == {ICICI_LAST4}
    # The rows sit at a different x on each page (the parser reads text only).
    first_rows = [ln for ln in lines if ln.text.startswith("22/09/2026 ")]
    later_rows = [ln for ln in lines if ln.text.startswith("09/10/2026 ")]
    assert first_rows[0].page == 0 and later_rows[0].page == 1
    assert first_rows[0].words[0].x0 - later_rows[0].words[0].x0 > 100
    # The terms page's worked example looks like a row but is outside the table.
    example = [s for s in report.skipped if "Purchase on Sep" in s.cells[0]]
    assert len(example) == 2 and {s.reason for s in example} == {"outside_section"}
    row_like = [s for s in example if re.match(ICICI_ROW_PATTERN, s.cells[0])]
    assert len(row_like) == 1 and lines[row_like[0].row_index].page == 2


@pytest.mark.parametrize("cash", [False, True], ids=["no-cash", "cash-advance-row"])
def test_icici_validates_and_reconciles(tmp_path, cash):
    path = _icici(tmp_path, cash=cash)
    _, validation, statement = check_pdf(path, icici_template(), TODAY)
    assert _check_status(validation) == ALL_PASS, validation.checks
    assert validation.passed and validation.sign_verified
    assert statement is not None
    assert statement.cash_advances == (D("3000.00") if cash else D("0"))
    assert statement.payments_credits == D("6749.00")
    assert statement.total_due == (D("7640.75") if cash else D("4640.75"))

    # One paisa off on the printed total is refused, never reconciled.
    off = _icici(
        tmp_path,
        "off.pdf",
        cash=cash,
        summary={"total_due": D("7640.76") if cash else D("4640.76")},
    )
    _, refused, _ = check_pdf(off, icici_template(), TODAY)
    assert not refused.passed
    assert _check_status(refused)[13] == "fail"


def test_icici_cash_advance_printed_only_in_the_summary_is_refused(tmp_path):
    # The unmeasured assumption fails closed: a summary cash advance with no
    # row leaves the parsed debits short, so check 14 refuses the statement.
    path = _icici(tmp_path, summary={"cash_advances": D("3000.00")})
    _, validation, _ = check_pdf(path, icici_template(), TODAY)
    assert _check_status(validation)[13] == "fail"  # the printed total omits it
    path = _icici(
        tmp_path,
        "summary-only.pdf",
        summary={"cash_advances": D("3000.00"), "total_due": D("7640.75")},
    )
    _, validation, _ = check_pdf(path, icici_template(), TODAY)
    status = _check_status(validation)
    assert status[13] == "pass" and status[14] == "fail"
    assert not validation.passed


@pytest.mark.parametrize("cash", [False, True], ids=["no-cash", "cash-advance-row"])
def test_icici_stages_and_commits_once(svc, app, tmp_path, cash):
    first = _icici(tmp_path, cash=cash)
    again = _icici(tmp_path, "again.pdf", cash=cash)
    written = _stage_and_commit_twice(
        svc, app, first, again, ICICI_PDF_DRAFT, icici_template()
    )
    assert written == len(ICICI_PAGE1) + len(ICICI_PAGE2) + len(ICICI_INTL) + (
        1 if cash else 0
    )
    records = _records(app.state.ledger)
    assert len(records) == 1 and records[0].date == date(2026, 10, 21)
    text = _journal_text(app.state.ledger)
    assert text.count(f'custom "{STATEMENT_TYPE}" {ICICI_CARD}') == 1
    if cash:
        assert "cash-advances: 3000.00 INR" in text
    else:
        assert "cash-advances:" not in text
    assert "other-charges:" not in text


def test_icici_peek_shows_values_dates_and_card(tmp_path):
    shown = json.dumps(peek_pdf(_icici(tmp_path, cash=True)), ensure_ascii=False)
    for value in ("₹7,640.75", "₹6,400.00", "₹4,989.75", "₹3,000.00", "₹6,749.00"):
        assert value in shown
    assert "September 22, 2026" in shown and "October 21, 2026" in shown
    assert f"XXXXXXXXXXXX{ICICI_LAST4}" in shown
    assert "5241" not in shown  # the printed BIN digits are hidden
    assert _holder_words(ICICI_HOLDER, shown) == []


# --- Axis tests -------------------------------------------------------------


def test_axis_parses_rows_and_summary(tmp_path):
    report = parse_pdf_report(
        read_lines(_axis(tmp_path, charges=True)), axis_template()
    )

    debit, credit = Direction.DEBIT, Direction.CREDIT
    # The cashback column (second amount) never becomes the amount; the Dr/Cr
    # after the first amount gives the direction. The category is part of the
    # description.
    assert _rows(report) == [
        (date(2026, 9, 17), D("2140.00"), debit, "ACME MART KOCHI SHOPPING"),
        (date(2026, 9, 20), D("9850.00"), credit, "PAYMENT RECEIVED THANK YOU PAYMENT"),
        (date(2026, 9, 26), D("845.40"), debit, "INITECH CAFE FOOD & DINING"),
        (date(2026, 10, 3), D("420.00"), credit, "GLOBEX TRAVEL REFUND TRAVEL"),
        (date(2026, 10, 8), D("400.00"), debit, "UMBRELLA PHARMACY HEALTH"),
        (date(2026, 10, 10), D("2500.00"), debit, "ATM CASH WITHDRAWAL CASH"),
        (date(2026, 10, 10), D("590.00"), debit, "CASH ADVANCE FEE AND GST FEES"),
    ]
    assert report.summary_errors == []
    assert _summary(report) == {
        "minimum_due": D("302.77"),
        "period_start": date(2026, 9, 16),
        "period_end": date(2026, 10, 15),
        "due_date": date(2026, 11, 5),
        "previous_balance": D("9850.00"),
        "payments": D("9850.00"),
        "other_credits": D("420.00"),
        "purchases_debits": D("3385.40"),
        "cash_advances": D("2500.00"),
        "other_charges": D("590.00"),
        "total_due": D("6055.40"),
    }
    assert report.card_last4s == {AXIS_LAST4}


def test_axis_card_on_the_credit_limit_line_does_not_count(tmp_path):
    # Judged one line at a time: the card printed straight into the credit
    # limit amounts is not a match, the two lines printing it alone each are.
    lines = read_lines(_axis(tmp_path))
    card_lines = [ln for ln in lines if ln.text.startswith("Card No: ")]
    assert len(card_lines) == 3
    found = [find_card_last4([ln], AXIS_PDF_DRAFT.card_pattern)[0] for ln in card_lines]
    limit_line = next(i for i, ln in enumerate(card_lines) if "2,00,000.00" in ln.text)
    assert found[limit_line] == set()
    assert [f for i, f in enumerate(found) if i != limit_line] == [{AXIS_LAST4}] * 2


@pytest.mark.parametrize(
    "charges", [False, True], ids=["no-charges", "cash-advance-and-fee-rows"]
)
def test_axis_validates_and_reconciles(tmp_path, charges):
    path = _axis(tmp_path, charges=charges)
    _, validation, statement = check_pdf(path, axis_template(), TODAY)
    assert _check_status(validation) == ALL_PASS, validation.checks
    assert validation.passed and validation.sign_verified
    assert statement is not None
    # The split form is stored combined: payments + other credits.
    assert statement.payments_credits == D("10270.00")
    assert statement.cash_advances == (D("2500.00") if charges else D("0"))
    assert statement.other_charges == (D("590.00") if charges else D("0"))
    assert statement.total_due == (D("6055.40") if charges else D("2965.40"))

    off = _axis(
        tmp_path,
        "off.pdf",
        charges=charges,
        summary={"total_due": D("6055.41") if charges else D("2965.41")},
    )
    _, refused, _ = check_pdf(off, axis_template(), TODAY)
    assert not refused.passed
    assert _check_status(refused)[13] == "fail"


def test_axis_cash_advance_printed_only_in_the_summary_is_refused(tmp_path):
    path = _axis(
        tmp_path,
        summary={"cash_advances": D("2500.00"), "total_due": D("5465.40")},
    )
    _, validation, _ = check_pdf(path, axis_template(), TODAY)
    status = _check_status(validation)
    assert status[13] == "pass" and status[14] == "fail"
    assert not validation.passed


@pytest.mark.parametrize(
    "charges", [False, True], ids=["no-charges", "cash-advance-and-fee-rows"]
)
def test_axis_stages_and_commits_once(svc, app, tmp_path, charges):
    first = _axis(tmp_path, charges=charges)
    again = _axis(tmp_path, "again.pdf", charges=charges)
    written = _stage_and_commit_twice(
        svc, app, first, again, AXIS_PDF_DRAFT, axis_template()
    )
    expected = len(AXIS_PAGE1) + len(AXIS_PAGE2) + (len(AXIS_CHARGES) if charges else 0)
    assert written == expected
    records = _records(app.state.ledger)
    assert len(records) == 1 and records[0].date == date(2026, 10, 15)
    text = _journal_text(app.state.ledger)
    assert text.count(f'custom "{STATEMENT_TYPE}" {AXIS_CARD}') == 1
    assert "payments: 10270.00 INR" in text
    if charges:
        assert "cash-advances: 2500.00 INR" in text
        assert "other-charges: 590.00 INR" in text
    else:
        assert "cash-advances:" not in text and "other-charges:" not in text


def test_axis_peek_shows_values_dates_and_card(tmp_path):
    shown = json.dumps(peek_pdf(_axis(tmp_path, charges=True)), ensure_ascii=False)
    for value in ("6,055.40", "9,850.00", "420.00", "3,385.40", "2,500.00", "590.00"):
        assert value in shown
    for printed in ("16/09/2026", "15/10/2026", "05/11/2026"):
        assert printed in shown
    assert f"XXXXXXXXXXXX{AXIS_LAST4}" in shown
    assert "543210" not in shown and "******" not in shown  # BIN and mask hidden
    assert _holder_words(AXIS_HOLDER, shown) == []


# --- ICICI with the backtick rupee glyph (Task 6b) ---------------------------


def test_icici_backtick_rupee_glyph_reads_validates_and_peeks(tmp_path):
    # A real ICICI statement's font prints the rupee sign as a backtick glued
    # to every summary value. The same reference template reads it.
    path = _icici(tmp_path, cash=True, glyph="`")
    lines = read_lines(path)
    assert "`7,640.75" in [ln.text for ln in lines]
    report = parse_pdf_report(lines, icici_template())
    assert report.summary_errors == []
    assert _summary(report)["total_due"] == D("7640.75")
    assert _summary(report)["payments_credits"] == D("6749.00")
    _, validation, _ = check_pdf(path, icici_template(), TODAY)
    assert _check_status(validation) == ALL_PASS, validation.checks
    shown = json.dumps(peek_pdf(path), ensure_ascii=False)
    for value in ("`7,640.75", "`6,400.00", "`4,989.75", "`3,000.00", "`6,749.00"):
        assert value in shown


# --- the references are writable from what peek shows (Task 6b) -------------

# The measured HDFC shapes, with fictional values: a normal row whose
# description wraps onto the next line, then a split credit row and a split
# debit row (no description on the date line; the narration is printed on the
# line above and the line below). The statement prints its total due in whole
# rupees, rounded down, over components with paise.
HDFC_ROWS = [
    Row("21/09/2026", "14:22", "ACME STORE BENGALURU", "1,234.50", wrap="KARNATAKA IN"),
    Row(
        "22/09/2026",
        "10:00",
        "",
        "500.25",
        credit=True,
        above="PAYMENT RECEIVED",
        below="NETBANKING THANK YOU",
    ),
    Row("23/09/2026", "11:30", "", "250.75", above="INITECH FOODS", below="PUNE IN"),
]


def _hdfc(tmp_path: Path) -> Path:
    return make_statement_pdf(
        tmp_path / "hdfc.pdf",
        domestic=HDFC_ROWS,
        international=HDFC_ROWS[:1],
        emi_lines=["EMI 1 of 6 C 1,000.00"],
        whole_rupee_total=True,
    )


def _peeked(lines: list[PdfLine]) -> list[PdfLine]:
    """The lines as the prover sees them in peek."""
    return [
        PdfLine(index=ln.index, page=ln.page, text=pdf_line_text(ln.text), words=(),
                spans=())
        for ln in lines
    ]  # fmt: skip


def _anchors(draft) -> list[tuple[str, str]]:
    """(where, regex) of every pattern a prover writes to locate something."""
    found = [("section_start", p) for p in draft.section_start]
    if draft.section_end:
        found.append(("section_end", draft.section_end))
    found += [("skip_patterns", p) for p in draft.skip_patterns]
    for m in draft.manual_sections:
        found += [(f"manual {m.name} start", m.start), (f"manual {m.name} end", m.end)]
    found += [(f"summary {role}", f.label) for role, f in draft.summary.items()]
    found += [("summary_rows label", row.label) for row in draft.summary_rows]
    if draft.continuation_pattern:
        found.append(("continuation_pattern", draft.continuation_pattern))
    return found


def _literal_words(pattern: str) -> list[str]:
    """The literal words (2+ letters) of a regex: what is left after group
    names, escapes like \\d and character classes are removed."""
    stripped = re.sub(r"\(\?P<\w+>|\\[A-Za-z]|\[[^\]]*\]", " ", pattern)
    return re.findall(r"[A-Za-z]{2,}", stripped)


@pytest.mark.parametrize(
    ("draft", "make", "last4"),
    [
        (HDFC_PDF_DRAFT, _hdfc, HDFC_LAST4),
        (ICICI_PDF_DRAFT, lambda p: _icici(p, cash=True), ICICI_LAST4),
        (AXIS_PDF_DRAFT, lambda p: _axis(p, charges=True), AXIS_LAST4),
    ],
    ids=["hdfc", "icici", "axis"],
)
def test_reference_templates_are_peek_derivable(tmp_path, draft, make, last4):
    # Every literal and anchor in a reference matches the redacted peek text
    # (and the raw text), so a prover can write it from what it sees.
    raw = read_lines(make(tmp_path))
    peeked = _peeked(raw)
    for where, pattern in _anchors(draft):
        in_peek = [ln.index for ln in peeked if re.search(pattern, ln.text)]
        in_raw = [ln.index for ln in raw if re.search(pattern, ln.text)]
        assert in_peek, f"{where} {pattern!r} matches no peek line"
        # The same lines: a branch or part that matches only redacted text
        # (a name in an alternation, an address word) shows up here.
        assert in_peek == in_raw, f"{where} {pattern!r}: peek {in_peek} raw {in_raw}"
    # The row pattern's literal words (markers such as CR, Dr, Cr) show in peek.
    for word in _literal_words(draft.row_pattern):
        assert any(word in ln.text for ln in peeked), f"row_pattern word {word!r}"
    for row in draft.summary_rows:
        label_line = next(ln.text for ln in peeked if re.search(row.label, ln.text))
        for printed in row.labels:
            assert printed in label_line, f"row label {printed!r} not in peek"
    # The card pattern finds the card in what peek shows as well as raw.
    assert find_card_last4(peeked, draft.card_pattern)[0] == {last4}
    assert find_card_last4(raw, draft.card_pattern)[0] == {last4}


# --- HDFC: split rows and a whole-rupee total due (fix round 1, P11) ----------


def _hdfc_validation(tmp_path: Path, template):
    return check_pdf(_hdfc(tmp_path), template, TODAY)


def test_hdfc_reference_reads_every_split_and_wrapped_row(tmp_path):
    path = _hdfc(tmp_path)
    report = parse_pdf_report(read_lines(path), hdfc_template())
    assert [(c.raw_narration, c.direction) for c in report.candidates] == [
        ("ACME STORE BENGALURU KARNATAKA IN", Direction.DEBIT),
        ("PAYMENT RECEIVED NETBANKING THANK YOU", Direction.CREDIT),
        ("INITECH FOODS PUNE IN", Direction.DEBIT),
        ("ACME STORE BENGALURU KARNATAKA IN", Direction.DEBIT),
    ]
    _, validation, statement = check_pdf(path, hdfc_template(), TODAY)
    assert validation.passed, validation.checks
    by_id = {c.id: c.status for c in validation.checks}
    assert by_id[1] == "pass" and by_id[14] == "pass"  # coverage, totals
    assert statement is not None
    assert statement.total_due == D("10419.00")  # 10,419.50 rounded down


def test_hdfc_split_rows_need_description_around(tmp_path):
    off = hdfc_template(description_around=False)
    _, validation, _ = _hdfc_validation(tmp_path, off)
    assert not validation.passed
    assert next(c for c in validation.checks if c.id == 1).status == "fail"


def test_hdfc_whole_rupee_total_needs_the_rupee_option(tmp_path):
    exact = hdfc_template(total_due_rounding="exact")
    _, validation, _ = _hdfc_validation(tmp_path, exact)
    assert not validation.passed
    equation = next(c for c in validation.checks if c.id == 13)
    assert equation.status == "fail" and "to the paisa" in equation.message
    # Rows are never rounded: the only failure is the equation.
    assert next(c for c in validation.checks if c.id == 14).status == "pass"
