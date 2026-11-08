from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.models.api.agent import Claims
from backend.app.service.ingest.validation.csv_xls import validate
from backend.app.service.ingest.parsers.csv_xls import (
    detect_delimiter,
    load_frame,
    parse_statement_report,
)
from backend.app.models.storage.template import Template
from tests.llm.flows.prove_template.reference_drafts import REFERENCE

TODAY = date(2026, 10, 16)


def _run(tmp_path: Path, text: str, *, kind="bank", claims=None, reused=False, **tpl):
    p = tmp_path / "s.csv"
    p.write_text(text, encoding="utf-8")
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
    fields.update(tpl)
    t = Template(**fields)
    r = parse_statement_report(p, t)
    return validate(
        frame=load_frame(p, t.delimiter),
        template=t,
        kind=kind,
        claims=claims,
        report=r,
        today=TODAY,
        reused=reused,
    )


def _check(rep, name):
    return next(c for c in rep.checks if c.name == name)


GOOD = "Date,Narration,Dr,Cr\n01/04/2026,SWIGGY,100.00,\n02/04/2026,SALARY,,500.00\n"


def test_clean_file_passes(tmp_path):
    rep = _run(tmp_path, GOOD, claims=Claims(first_data_row=1, last_data_row=2))
    assert rep.passed, rep.checks
    assert _check(rep, "running_balance").status == "not_applicable"
    assert rep.sign_verified is False


def test_coverage_fails_on_a_broken_row_inside_the_range(tmp_path):
    text = GOOD + "03/04/2026,BROKEN,abc,\n"
    rep = _run(tmp_path, text, claims=Claims(first_data_row=1, last_data_row=3))
    c = _check(rep, "coverage")
    assert (c.status, c.rows) == ("fail", [3])
    assert not rep.passed


def test_nothing_outside_the_claimed_range(tmp_path):
    rep = _run(tmp_path, GOOD, claims=Claims(first_data_row=1, last_data_row=1))
    assert _check(rep, "nothing_outside").rows == [2]


def _signed(rows: list[str]) -> str:
    return "Date,Narration,Amount,Balance\n" + "\n".join(rows) + "\n"


SIGNED = dict(
    debit_column=None,
    credit_column=None,
    amount_column="Amount",
    amount_sign="signed",
    balance_column="Balance",
)


def test_running_balance_ascending_and_descending(tmp_path):
    asc = ["01/04/2026,A,-100.00,900.00", "02/04/2026,B,500.00,1400.00 Cr"]
    for rows in (asc, list(reversed(asc))):
        rep = _run(
            tmp_path,
            _signed(rows),
            claims=Claims(first_data_row=1, last_data_row=2),
            **SIGNED,
        )
        assert _check(rep, "running_balance").status == "pass"
        assert rep.sign_verified


def test_running_balance_reconciles_a_debit_after_the_first_row(tmp_path):
    # The walk starts at the second row, so a debit there proves the sign is applied.
    asc = ["01/04/2026,A,500.00,1000.00", "02/04/2026,B,-100.00,900.00"]
    for rows in (asc, list(reversed(asc))):
        rep = _run(
            tmp_path,
            _signed(rows),
            claims=Claims(first_data_row=1, last_data_row=2),
            **SIGNED,
        )
        assert _check(rep, "running_balance").status == "pass"


def test_running_balance_catches_a_flipped_sign(tmp_path):
    rows = ["01/04/2026,A,100.00,900.00", "02/04/2026,B,-500.00,1400.00"]
    rep = _run(
        tmp_path,
        _signed(rows),
        claims=Claims(first_data_row=1, last_data_row=2),
        **SIGNED,
    )
    assert _check(rep, "running_balance").status == "fail"
    assert not rep.passed


def test_running_balance_single_date_tries_both_orders(tmp_path):
    rows = ["01/04/2026,B,500.00,1400.00", "01/04/2026,A,-100.00,900.00"]
    rep = _run(
        tmp_path,
        _signed(rows),
        claims=Claims(first_data_row=1, last_data_row=2),
        **SIGNED,
    )
    assert _check(rep, "running_balance").status == "pass"


def test_running_balance_not_applicable_for_cards(tmp_path):
    rows = ["01/04/2026,A,-100.00,900.00"]
    tpl = dict(SIGNED, default_account="Liabilities:CreditCard:HDFC")
    rep = _run(
        tmp_path,
        _signed(rows),
        kind="card",
        claims=Claims(first_data_row=1, last_data_row=1),
        **tpl,
    )
    assert _check(rep, "running_balance").status == "not_applicable"


def test_opening_closing(tmp_path):
    ok = Claims(
        first_data_row=1,
        last_data_row=2,
        opening_balance=Decimal("1000"),
        closing_balance=Decimal("1400"),
    )
    assert _check(_run(tmp_path, GOOD, claims=ok), "opening_closing").status == "pass"
    bad = ok.model_copy(update={"closing_balance": Decimal("1399.99")})
    assert _check(_run(tmp_path, GOOD, claims=bad), "opening_closing").status == "fail"
    card = _run(
        tmp_path,
        GOOD,
        kind="card",
        claims=ok,
        default_account="Liabilities:CreditCard:HDFC",
    )
    assert _check(card, "opening_closing").status == "fail"


def test_dates_outside_period_and_future_fail(tmp_path):
    claims = Claims(
        first_data_row=1,
        last_data_row=2,
        period_start=date(2026, 4, 2),
        period_end=date(2026, 4, 30),
    )
    assert _check(_run(tmp_path, GOOD, claims=claims), "dates").rows == [1]
    future = "Date,Narration,Dr,Cr\n01/12/2026,X,1.00,\n"
    rep = _run(tmp_path, future, claims=Claims(first_data_row=1, last_data_row=1))
    assert _check(rep, "dates").status == "fail"


def test_out_of_order_dates_are_low_confidence_not_failure(tmp_path):
    text = (
        "Date,Narration,Dr,Cr\n02/04/2026,A,1.00,\n01/04/2026,B,1.00,\n"
        "03/04/2026,C,1.00,\n"
    )
    rep = _run(tmp_path, text, claims=Claims(first_data_row=1, last_data_row=3))
    assert rep.passed
    assert any("out of order" in m for m in rep.low_confidence)


def test_multi_card_dates_restart_per_section_without_flag(tmp_path):
    text = (
        "Date,Narration,Amount,Sign\n4399XXXXXXXX0001,,,\n01/04/2026,A,1.00,\n"
        "05/04/2026,B,1.00,\n4399XXXXXXXX0002,,,\n02/04/2026,C,1.00,\n"
    )
    rep = _run(
        tmp_path,
        text,
        kind="card",
        claims=Claims(first_data_row=1, last_data_row=5),
        debit_column=None,
        credit_column=None,
        amount_column="Amount",
        sign_column="Sign",
        default_account="Liabilities:CreditCard:ICICI",
        account_marker_pattern=r"\d{4}XXXXXXXX(\d{4})",
    )
    assert _check(rep, "coverage").status == "pass"
    assert not any("out of order" in m for m in rep.low_confidence)


def test_day_month_ambiguity_flag_only_for_numeric_day_first_formats(tmp_path):
    rep = _run(tmp_path, GOOD, claims=Claims(first_data_row=1, last_data_row=2))
    assert any("day/month" in m for m in rep.low_confidence)
    iso = "Date,Narration,Dr,Cr\n2026-04-01,A,1.00,\n"
    rep2 = _run(
        tmp_path,
        iso,
        date_format="%Y-%m-%d",
        claims=Claims(first_data_row=1, last_data_row=1),
    )
    assert not any("day/month" in m for m in rep2.low_confidence)


def test_header_only_file_fails_sanity_without_crashing(tmp_path):
    rep = _run(
        tmp_path,
        "Date,Narration,Dr,Cr\n",
        claims=Claims(first_data_row=1, last_data_row=1),
    )
    assert _check(rep, "sanity").status == "fail"
    assert not rep.passed


def test_stop_check(tmp_path):
    text = GOOD + "Total,,100.00,\n04/04/2026,LATE,9.00,\n"
    rep = _run(
        tmp_path,
        text,
        stop_pattern="^Total",
        claims=Claims(first_data_row=1, last_data_row=2),
    )
    assert _check(rep, "stop_skip").status == "fail"  # a parseable row after the stop
    rep2 = _run(
        tmp_path,
        GOOD + "Total,,100.00,\n",
        stop_pattern="^Total",
        claims=Claims(first_data_row=1, last_data_row=3),
    )
    assert _check(rep2, "stop_skip").status == "fail"  # stop is not after last_data_row


def test_balance_column_declared_warning(tmp_path):
    text = "Date,Narration,Dr,Cr,Closing Balance\n01/04/2026,A,1.00,,99.00\n"
    rep = _run(tmp_path, text, claims=Claims(first_data_row=1, last_data_row=1))
    assert _check(rep, "balance_column_declared").status == "warn"
    assert rep.passed


def test_reused_template_footer_is_a_warning(tmp_path):
    text = GOOD + "Total debits,,100.00,\nGenerated on 05/04/2026,,,\n"
    rep = _run(tmp_path, text, reused=True)
    assert rep.passed
    assert any("footer" in w for w in rep.warnings)
    mid = (
        "Date,Narration,Dr,Cr\n01/04/2026,A,1.00,\nsubtotal,,1.00,\n"
        "02/04/2026,B,1.00,\n"
    )
    assert not _run(tmp_path, mid, reused=True).passed


def test_single_balance_row_is_not_a_verified_sign(tmp_path):
    rows = ["15/04/2026,SALARY,-500.00,1500.00"]
    rep = _run(
        tmp_path,
        _signed(rows),
        claims=Claims(first_data_row=1, last_data_row=1),
        **SIGNED,
    )
    assert _check(rep, "running_balance").status == "not_applicable"
    assert not rep.sign_verified
    assert any("sign convention unverified" in w for w in rep.warnings)


def test_dated_rows_that_fail_outside_the_claimed_range_warn(tmp_path):
    text = GOOD + "17/04/2026,C,1O0.00,\n17/04/2026,D,2O0.00,\n"
    rep = _run(tmp_path, text, claims=Claims(first_data_row=1, last_data_row=2))
    assert rep.passed
    assert any("outside the claimed rows" in w and "[3, 4]" in w for w in rep.warnings)


def test_skip_pattern_hiding_dated_rows_is_low_confidence(tmp_path):
    text = "Date,Narration,Dr,Cr\n2026-04-01,A,1.00,\n2026-04-02,B,2.00,\n01/04/2026,C,3.00,\n"
    rep = _run(
        tmp_path,
        text,
        skip_row_patterns=["/|-"],
        claims=Claims(first_data_row=1, last_data_row=3),
    )
    assert any("skipped by skip_row_patterns" in m for m in rep.low_confidence)


def test_skip_pattern_on_undated_rows_is_not_flagged(tmp_path):
    text = GOOD + ",Page 1 of 3,,\n"
    rep = _run(
        tmp_path,
        text,
        skip_row_patterns=[r"Page \d+"],
        claims=Claims(first_data_row=1, last_data_row=3),
    )
    assert not any("skip_row_patterns" in m for m in rep.low_confidence)


def test_future_date_fails_even_when_a_period_is_claimed(tmp_path):
    text = "Date,Narration,Dr,Cr\n01/12/2026,X,1.00,\n"
    rep = _run(
        tmp_path,
        text,
        claims=Claims(first_data_row=1, last_data_row=1, period_end=date(2030, 12, 31)),
    )
    assert _check(rep, "dates").status == "fail"


CARD = dict(
    debit_column=None,
    credit_column=None,
    amount_column="Amount",
    sign_column="Sign",
    default_account="Liabilities:CreditCard:HDFC",
)


def _card(rows: list[str]) -> str:
    return "Date,Narration,Amount,Sign\n" + "\n".join(rows) + "\n"


def test_indicator_parse_all_one_direction_despite_two_signs_fails(tmp_path):
    text = _card(["01/04/2026,SWIGGY,100.00,Dr", "02/04/2026,PAYMENT,500.00,Cr"])
    rep = _run(
        tmp_path,
        text,
        kind="card",
        claims=Claims(first_data_row=1, last_data_row=2),
        credit_indicators=["C"],
        **CARD,
    )
    c = _check(rep, "sanity")
    assert c.status == "fail" and not rep.passed
    assert c.message == (
        "the sign column has values CR, DR but every row parsed as debit: "
        "credit_indicators probably wrong"
    )


def test_indicator_parse_with_dr_and_blank_only_still_passes(tmp_path):
    text = _card(["01/04/2026,SWIGGY,100.00,Dr", "02/04/2026,UBER,50.00,"])
    rep = _run(
        tmp_path,
        text,
        kind="card",
        claims=Claims(first_data_row=1, last_data_row=2),
        **CARD,
    )
    assert _check(rep, "sanity").status == "pass" and rep.passed, rep.checks


def test_indicator_parse_with_correct_indicators_passes(tmp_path):
    text = _card(["01/04/2026,SWIGGY,100.00,Dr", "02/04/2026,PAYMENT,500.00,Cr"])
    rep = _run(
        tmp_path,
        text,
        kind="card",
        claims=Claims(first_data_row=1, last_data_row=2),
        **CARD,
    )
    assert _check(rep, "sanity").status == "pass" and rep.passed, rep.checks


def test_credit_token_in_sign_column_but_no_credit_parsed_fails(tmp_path):
    # Single distinct value, so R17 does not fire; R19 must.
    text = _card(["01/04/2026,PAYMENT,500.00,C"])
    rep = _run(
        tmp_path,
        text,
        kind="card",
        claims=Claims(first_data_row=1, last_data_row=1),
        credit_indicators=["Cr"],
        **CARD,
    )
    c = _check(rep, "sanity")
    assert c.status == "fail"
    assert c.message == (
        "the sign column contains 'C' but no row parsed as a credit: "
        "credit_indicators probably wrong"
    )


def test_dr_dr_dot_dr_lower_is_one_value_and_all_debit_passes(tmp_path):
    text = _card(
        [
            "01/04/2026,A,1.00,Dr",
            "02/04/2026,B,2.00,DR",
            "03/04/2026,C,3.00,Dr.",
            "04/04/2026,D,4.00,dr",
        ]
    )
    rep = _run(
        tmp_path,
        text,
        kind="card",
        claims=Claims(first_data_row=1, last_data_row=4),
        **CARD,
    )
    assert _check(rep, "sanity").status == "pass" and rep.passed, rep.checks


@pytest.mark.parametrize("name", ["icici_card", "hdfc_card"])
def test_reference_card_drafts_with_wrong_credit_indicators_fail(tmp_path, name):
    ref = REFERENCE[name]
    text = (Path("tests/fixtures/statements") / f"{name}.csv").read_text(
        encoding="utf-8"
    )
    draft = dict(ref["draft"], credit_indicators=["C"])
    draft.pop("institution")
    draft.setdefault("default_account", ref["account"])
    # The service detects the delimiter at inspect time; do the same here, or
    # hdfc_card's `~|~` header is one cell and the test fails for no reason
    # to do with credit indicators.
    draft.setdefault("delimiter", detect_delimiter(text))
    rep = _run(
        tmp_path,
        text,
        kind="card",
        claims=Claims(**ref["claims"]),
        institution=ref["draft"]["institution"],
        debit_column=None,
        credit_column=None,
        **draft,
    )
    assert _check(rep, "coverage").status == "pass", rep.checks  # rows did parse
    assert _check(rep, "sanity").status == "fail" and not rep.passed
