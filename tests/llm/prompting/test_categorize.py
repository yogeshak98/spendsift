from datetime import date
from decimal import Decimal

import pytest

from backend.app.llm.prompting.categorize import (
    ALLOWED_PREFIXES,
    build_export,
    build_merchant_export,
    parse_merchant_result,
    parse_result,
)
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.models.domain.categorize import MerchantBacklog


def unknown(narr, idx):
    return CandidateTxn(
        id=f"id{idx}",
        date=date(2026, 4, 1),
        amount=Decimal("1"),
        raw_narration=narr,
        normalized_narration=narr.upper(),
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
    )


def test_build_export_numbers_unknowns():
    prompt = "PROMPT HEADER\n"
    text, index = build_export(
        [unknown("SUNRISE TIFFINS", 1), unknown("RAZP 9XK2", 2)], prompt
    )
    assert "PROMPT HEADER" in text
    assert "1\tSUNRISE TIFFINS" in text
    assert "2\tRAZP 9XK2" in text
    assert index[1] == "id1" and index[2] == "id2"


def test_allowed_prefixes_exposed():
    assert "Expenses:" in ALLOWED_PREFIXES


def test_parse_result_maps_ids():
    index = {1: "idA", 2: "idB"}
    result = parse_result("1\tExpenses:Food\n2\tExpenses:Travel\n", index)
    assert result == {"idA": "Expenses:Food", "idB": "Expenses:Travel"}


def test_parse_result_rejects_bad_category():
    with pytest.raises(ValueError):
        parse_result("1\tFood\n", {1: "idA"})


def test_parse_result_rejects_unknown_number():
    with pytest.raises(ValueError):
        parse_result("9\tExpenses:Food\n", {1: "idA"})


def test_parse_result_ignores_noise_lines():
    index = {1: "idA"}
    assert parse_result("Here you go:\n1\tExpenses:Food\nThanks!\n", index) == {
        "idA": "Expenses:Food"
    }


# ── Journal-wide merchant flow ───────────────────────────────────────────────
#
# `parse_merchant_result` is STRUCTURAL only. Whether the target account is
# usable is judged in exactly one place, `service.categorize.service._target_error`,
# so those cases live in tests/service/categorize/test_service.py rather than
# being re-pinned here against a second copy of the rules.


def test_build_merchant_export_numbers_merchant_keys():
    prompt = "PROMPT HEADER\n"
    merchants = [
        MerchantBacklog(
            merchant="SWIGGY",
            count=3,
            total=Decimal("829.50"),
            first=date(2026, 3, 5),
            last=date(2026, 3, 12),
            sample="UPI-SWIGGY-9912@ybl-YESB-3333",
        ),
        MerchantBacklog(
            merchant="BLUEBIRD CAB",
            count=1,
            total=Decimal("240.00"),
            first=date(2026, 3, 7),
            last=date(2026, 3, 7),
            sample="UPI-BLUEBIRD CAB-221@ybl",
        ),
    ]
    text, index = build_merchant_export(merchants, prompt)

    assert "PROMPT HEADER" in text
    assert "1\tSWIGGY" in text
    assert "2\tBLUEBIRD CAB" in text
    # index maps number -> the merchant KEY a learned rule matches on, not an id
    assert index == {1: "SWIGGY", 2: "BLUEBIRD CAB"}


def test_parse_merchant_result_carries_the_account_and_the_raw_line():
    index = {1: "SWIGGY", 2: "BLUEBIRD CAB"}
    accepted, rejected, ignored = parse_merchant_result(
        "1\tExpenses:Food\n  2\tIncome:Salary  \n", index
    )
    # Income: is NOT judged here -- it is accepted structurally and rejected
    # by the service's single account authority. The raw line travels with it
    # so that reject can quote the user's own text.
    assert accepted == {
        "SWIGGY": ("Expenses:Food", "1\tExpenses:Food"),
        "BLUEBIRD CAB": ("Income:Salary", "2\tIncome:Salary"),
    }
    assert rejected == []
    assert ignored == 0


def test_parse_merchant_result_rejects_an_unknown_number():
    accepted, rejected, ignored = parse_merchant_result(
        "1\tExpenses:Food\n9\tExpenses:Travel\n", {1: "SWIGGY"}
    )
    assert accepted == {"SWIGGY": ("Expenses:Food", "1\tExpenses:Food")}
    assert len(rejected) == 1
    assert rejected[0]["line"] == "9\tExpenses:Travel"
    assert "unknown item 9" in rejected[0]["reason"].lower()
    assert ignored == 0


def test_parse_merchant_result_lets_a_later_line_win_for_the_same_number():
    # index is number -> merchant, so a re-answer must overwrite, not duplicate
    # or silently keep the first answer.
    accepted, rejected, ignored = parse_merchant_result(
        "1\tExpenses:Food\n1\tExpenses:Dining\n", {1: "SWIGGY"}
    )
    assert accepted == {"SWIGGY": ("Expenses:Dining", "1\tExpenses:Dining")}
    assert rejected == [] and ignored == 0


def test_parse_merchant_result_tolerates_a_numbered_list_ornament():
    # "1." / "1)" is a model answering, not chatting. Dropping those lines
    # silently is the failure this count and this tolerance exist to stop.
    accepted, _, ignored = parse_merchant_result(
        "1.\tExpenses:Food\n2)\tExpenses:Travel\n", {1: "SWIGGY", 2: "BLUEBIRD CAB"}
    )
    assert accepted == {
        "SWIGGY": ("Expenses:Food", "1.\tExpenses:Food"),
        "BLUEBIRD CAB": ("Expenses:Travel", "2)\tExpenses:Travel"),
    }
    assert ignored == 0


def test_parse_merchant_result_counts_prose_as_ignored_not_as_an_answer():
    # Prose is still skipped -- a chat UI wraps the answer block in
    # conversation -- but it is COUNTED, so an all-misread paste cannot
    # return a silent all-zero success.
    accepted, rejected, ignored = parse_merchant_result(
        "Here you go:\n1\tExpenses:Food\n\nHope that helps!\n", {1: "SWIGGY"}
    )
    assert accepted == {"SWIGGY": ("Expenses:Food", "1\tExpenses:Food")}
    assert rejected == []
    # two prose lines; the blank line is not content and is not counted
    assert ignored == 2


def test_parse_merchant_result_counts_an_unparseable_number_as_ignored():
    accepted, rejected, ignored = parse_merchant_result(
        "one\tExpenses:Food\n1 Expenses:Food\n", {1: "SWIGGY"}
    )
    assert accepted == {} and rejected == []
    assert ignored == 2
