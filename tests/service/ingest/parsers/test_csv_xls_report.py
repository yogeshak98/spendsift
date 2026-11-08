from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.core.paths import REPO_ROOT
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.normalize import normalize_narration
from backend.app.service.ingest.parsers.csv_xls import (
    _money,
    _parse_date,
    load_frame,
    parse_balance,
    parse_statement,
    parse_statement_report,
)
from backend.app.models.storage.template import Template


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "s.csv"
    p.write_text(text, encoding="utf-8")
    return p


def _tpl(**kw) -> Template:
    fields = dict(
        fingerprint="fp",
        institution="X",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Dr",
        credit_column="Cr",
        default_account="Assets:Checking:HDFC",
    )
    fields.update(kw)
    return Template(**fields)


def test_every_non_candidate_row_is_reported_with_a_reason(tmp_path):
    p = _write(
        tmp_path,
        "Date,Narration,Dr,Cr\n"
        "01/04/2026,SWIGGY,100.00,\n"
        ",,,\n"
        "Date,Narration,Dr,Cr\n"
        "xx/04/2026,BROKEN,5.00,\n"
        "02/04/2026,,5.00,\n"
        "03/04/2026,NOAMT,,\n"
        "04/04/2026,BADAMT,abc,\n"
        ",Summary,,\n",
    )
    r = parse_statement_report(p, _tpl())
    assert [c.raw_narration for c in r.candidates] == ["SWIGGY"]
    assert r.candidate_rows == [1]
    assert [(s.row_index, s.reason) for s in r.skipped] == [
        (2, "empty"),
        (3, "header_repeat"),
        (4, "bad_date"),
        (5, "no_narration"),
        (6, "no_amount"),
        (7, "bad_amount"),
        (8, "no_date"),
    ]


def test_stop_pattern_ignores_a_parseable_row_that_merely_mentions_it(tmp_path):
    p = _write(
        tmp_path,
        "Date,Narration,Dr,Cr\n"
        "01/04/2026,TOTAL ENERGIES PETROL,500.00,\n"
        "Total,,500.00,\n"
        "05/04/2026,AFTER STOP,1.00,\n",
    )
    r = parse_statement_report(p, _tpl(stop_pattern="(?i)total"))
    assert [c.raw_narration for c in r.candidates] == ["TOTAL ENERGIES PETROL"]
    assert r.stop_row == 2
    assert r.parseable_after_stop == 1
    assert [s.reason for s in r.skipped] == ["after_stop", "after_stop"]


def test_skip_pattern_never_swallows_a_parseable_row(tmp_path):
    p = _write(
        tmp_path,
        "Date,Narration,Dr,Cr\nPage 2 of 3,,,\n01/04/2026,Page 2 Cafe,80.00,\n",
    )
    r = parse_statement_report(p, _tpl(skip_row_patterns=["^Page"]))
    assert [c.raw_narration for c in r.candidates] == ["Page 2 Cafe"]
    assert [(s.row_index, s.reason) for s in r.skipped] == [(1, "skip_pattern")]


def test_skip_exact_excludes_a_parseable_opening_balance_row(tmp_path):
    p = _write(
        tmp_path,
        "Date,Narration,Dr,Cr\n"
        "01/04/2026, opening balance ,,10000.00\n"
        "02/04/2026,SWIGGY,100.00,\n",
    )
    r = parse_statement_report(p, _tpl(skip_exact_narrations=["Opening Balance"]))
    assert [c.raw_narration for c in r.candidates] == ["SWIGGY"]
    assert [(s.row_index, s.reason) for s in r.skipped] == [(1, "skip_exact")]


def test_signed_amount_column(tmp_path):
    p = _write(
        tmp_path,
        "Date,Narration,Amount\n"
        "01/04/2026,SWIGGY,-412.00\n"
        "02/04/2026,SALARY,50000.00\n"
        "03/04/2026,ZERO,0\n",
    )
    t = _tpl(
        debit_column=None,
        credit_column=None,
        amount_column="Amount",
        amount_sign="signed",
    )
    r = parse_statement_report(p, t)
    assert [(c.amount, c.direction) for c in r.candidates] == [
        (Decimal("412.00"), Direction.DEBIT),
        (Decimal("50000.00"), Direction.CREDIT),
    ]
    assert [(s.row_index, s.reason) for s in r.skipped] == [(3, "no_amount")]


def test_account_marker_rows_are_reported(tmp_path):
    p = _write(
        tmp_path,
        "Date,Narration,Amount,Sign\n4399XXXXXXXX0001,,,\n10/03/2026,UBER,315.00,\n",
    )
    t = _tpl(
        debit_column=None,
        credit_column=None,
        amount_column="Amount",
        sign_column="Sign",
        account_marker_pattern=r"\d{4}XXXXXXXX(\d{4})",
        account_marker_map={"0001": "Liabilities:CreditCard:ICICI"},
    )
    r = parse_statement_report(p, t)
    assert [(s.row_index, s.reason) for s in r.skipped] == [(1, "account_marker")]
    assert r.candidates[0].source_account == "Liabilities:CreditCard:ICICI"


def _legacy_parse_statement(path: Path, template: Template) -> list[CandidateTxn]:
    """FROZEN copy of parse_statement + _row_to_candidate at commit 7f9c4cc."""
    frame = load_frame(path, template.delimiter)
    header_cells = [c.strip() for c in frame.iloc[template.header_row_index].tolist()]
    col_pos = {cell: i for i, cell in enumerate(header_cells) if cell}
    marker_re = (
        re.compile(template.account_marker_pattern)
        if template.account_marker_pattern
        else None
    )
    current_marker_key: str | None = None

    def row_to_candidate(cells, marker_key):
        def cell(col_name):
            if col_name is None or col_name not in col_pos:
                return ""
            return str(cells[col_pos[col_name]]).strip()

        date_text = cell(template.date_column)
        if not date_text:
            return None
        txn_date = _parse_date(date_text, template.date_format)
        if txn_date is None:
            return None
        raw = cell(template.narration_column)
        if not raw:
            return None
        if template.amount_column:
            amount = _money(cell(template.amount_column))
            if amount is None:
                return None
            sign_text = cell(template.sign_column).strip().upper()
            indicators = {i.strip().upper() for i in template.credit_indicators}
            direction = Direction.CREDIT if sign_text in indicators else Direction.DEBIT
        else:
            debit = _money(cell(template.debit_column))
            credit = _money(cell(template.credit_column))
            if debit:
                amount, direction = debit, Direction.DEBIT
            elif credit:
                amount, direction = credit, Direction.CREDIT
            else:
                return None
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

    out: list[CandidateTxn] = []
    for row_idx in range(len(frame)):
        cells = frame.iloc[row_idx].tolist()
        if marker_re:
            match = marker_re.search(" ".join(cells))
            if match:
                current_marker_key = match.group(1)
        if row_idx <= template.header_row_index:
            continue
        candidate = row_to_candidate(cells, current_marker_key)
        if candidate is not None:
            out.append(candidate)
    return out


_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "statements"


def _fixture_header_index(name: str, first_cell: str) -> int:
    for i, line in enumerate((_FIXTURES / name).read_text().splitlines()):
        if line.lstrip('"').startswith(first_cell):
            return i
    raise AssertionError(name)


def _fixture_templates() -> list[tuple[str, Template]]:
    return [
        (
            "hdfc_bank.csv",
            _tpl(
                date_format="%Y-%m-%d",
                debit_column="Withdrawal Amt.",
                credit_column="Deposit Amt.",
            ),
        ),
        (
            "icici_bank.csv",
            _tpl(
                date_column="Txn Date",
                date_format="%Y-%m-%d",
                narration_column="Particulars",
                debit_column="Debit",
                credit_column="Credit",
            ),
        ),
        (
            "idfc_bank.csv",
            _tpl(narration_column="Description", debit_column="Dr", credit_column="Cr"),
        ),
        (
            "idfc_card.csv",
            _tpl(
                date_column="Transaction Date",
                narration_column="Description",
                debit_column="Debit",
                credit_column="Credit",
            ),
        ),
        (
            "kite_orders.csv",
            _tpl(
                date_format="%d-%m-%Y",
                narration_column="Instrument",
                debit_column=None,
                credit_column=None,
                amount_column="Amount",
                sign_column="Transaction Type",
                credit_indicators=["SELL"],
            ),
        ),
        (
            "icici_card.csv",
            _tpl(
                header_row_index=_fixture_header_index("icici_card.csv", "Date"),
                narration_column="Transaction Details",
                debit_column=None,
                credit_column=None,
                amount_column="Amount(in Rs)",
                sign_column="BillingAmountSign",
                account_marker_pattern=r"\d{4}XXXXXXXX(\d{4})",
                default_account=None,
            ),
        ),
    ]


@pytest.mark.parametrize(
    "name,tpl", _fixture_templates(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_parse_statement_matches_frozen_legacy_on_fixtures(name, tpl):
    path = _FIXTURES / name
    got = parse_statement(path, tpl)
    assert got  # the template really reads the fixture
    assert got == _legacy_parse_statement(path, tpl)


def test_parse_statement_matches_frozen_legacy_on_edge_inputs(tmp_path):
    p = _write(
        tmp_path,
        "Date,Narration,Dr,Cr\n"
        "01/04/2026,ZERO DEBIT CREDIT SET,0.00,50.00\n"
        "02/04/2026,ZERO DEBIT EMPTY CREDIT,0.00,\n"
        "03/04/2026,GARBAGE DEBIT VALID CREDIT,abc,20.00\n"
        "04/04/2026,GARBAGE CREDIT,,abc\n"
        "Date,Narration,Dr,Cr\n"
        "05/04/2026,MARKER 1234XXXX5678 ROW,7.00,\n"
        "06/04/2026,PLAIN,9.00,\n",
    )
    t = _tpl(
        account_marker_pattern=r"\d{4}XXXX(\d{4})",
        account_marker_map={"5678": "Assets:Other"},
    )
    got = parse_statement(p, t)
    assert got == _legacy_parse_statement(p, t)
    assert [c.raw_narration for c in got] == [
        "ZERO DEBIT CREDIT SET",
        "GARBAGE DEBIT VALID CREDIT",
        "MARKER 1234XXXX5678 ROW",
        "PLAIN",
    ]


def test_parse_statement_matches_frozen_legacy_on_zero_indicator_amount(tmp_path):
    p = _write(
        tmp_path, "Date,Narration,Amount,Sign\n01/04/2026,ZERO,0,\n02/04/2026,X,5,CR\n"
    )
    t = _tpl(
        debit_column=None,
        credit_column=None,
        amount_column="Amount",
        sign_column="Sign",
        credit_indicators=["CR"],
    )
    assert parse_statement(p, t) == _legacy_parse_statement(p, t)
    assert len(parse_statement(p, t)) == 2


def test_skip_exact_is_exact_not_substring(tmp_path):
    p = _write(
        tmp_path,
        "Date,Narration,Dr,Cr\n"
        "01/04/2026, opening balance ,,10.00\n"
        "02/04/2026,Opening Balance Transfer,,20.00\n",
    )
    r = parse_statement_report(p, _tpl(skip_exact_narrations=["Opening Balance"]))
    assert [c.raw_narration for c in r.candidates] == ["Opening Balance Transfer"]
    assert [(s.row_index, s.reason) for s in r.skipped] == [(1, "skip_exact")]


def test_garbage_credit_with_empty_debit_is_bad_amount(tmp_path):
    p = _write(tmp_path, "Date,Narration,Dr,Cr\n01/04/2026,X,,abc\n")
    r = parse_statement_report(p, _tpl())
    assert [(s.row_index, s.reason) for s in r.skipped] == [(1, "bad_amount")]


def test_zero_debit_with_empty_credit_is_no_amount(tmp_path):
    p = _write(tmp_path, "Date,Narration,Dr,Cr\n01/04/2026,X,0.00,\n")
    r = parse_statement_report(p, _tpl())
    assert [(s.row_index, s.reason) for s in r.skipped] == [(1, "no_amount")]


def test_garbage_debit_with_valid_credit_is_a_credit_candidate(tmp_path):
    p = _write(tmp_path, "Date,Narration,Dr,Cr\n01/04/2026,X,abc,20.00\n")
    r = parse_statement_report(p, _tpl())
    assert [(c.amount, c.direction) for c in r.candidates] == [
        (Decimal("20.00"), Direction.CREDIT)
    ]
    assert r.skipped == []


def test_parse_balance_handles_cr_dr_and_minus():
    assert parse_balance("12,345.00 Cr") == Decimal("12345.00")
    assert parse_balance("500.00 DR") == Decimal("-500.00")
    assert parse_balance("-75.10") == Decimal("-75.10")
    assert parse_balance("") is None
    assert parse_balance("n/a") is None


def test_anchored_patterns_match_the_row_text_without_padding(tmp_path):
    # Trailing empty cells add trailing spaces to the joined row; ^\*+$ must
    # still match a separator row, and a stop pattern must too.
    p = _write(
        tmp_path,
        "Date,Narration,Dr,Cr\n"
        "01/04/2026,Cafe,80.00,\n"
        "********,,,\n"
        "TOTAL,,,\n"
        "02/04/2026,After,5.00,\n",
    )
    r = parse_statement_report(
        p, _tpl(skip_row_patterns=[r"^\*+$"], stop_pattern=r"^TOTAL$")
    )
    assert [c.raw_narration for c in r.candidates] == ["Cafe"]
    assert [(s.row_index, s.reason) for s in r.skipped][:2] == [
        (2, "skip_pattern"),
        (3, "after_stop"),
    ]


def test_account_marker_key_keeps_trailing_padding(tmp_path):
    # Pre-B8 the joined row was not stripped, so `Card No: (.*)` captured the
    # padded key "0001   " (one space per trailing empty cell). Browser
    # templates key account_marker_map by that exact string.
    p = _write(
        tmp_path,
        "Date,Narration,Dr,Cr\n" "Card No: 0001,,,\n" "01/04/2026,Cafe,80.00,\n",
    )
    r = parse_statement_report(
        p,
        _tpl(
            account_marker_pattern=r"Card No: (.*)",
            account_marker_map={"0001   ": "Liabilities:Card:Mapped"},
        ),
    )
    assert [c.source_account for c in r.candidates] == ["Liabilities:Card:Mapped"]
