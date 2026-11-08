from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.service.ingest.parsers.pdf_reader import PdfLine
from backend.app.service.ingest.parsers.pdf_words import (
    DATE_TEXT,
    card_shaped,
    find_card_last4,
    normalise_card_token,
    parse_amount_word,
    parse_money,
    starts_with_date,
)


@pytest.mark.parametrize(
    "text",
    ["₹9,999.99", "Rs.9,999.99", "Rs9,999.99", "INR9,999.99", "C9,999.99", "`9,999.99"],
)
def test_amount_words_accept_each_prefix(text):
    assert parse_amount_word(text) == Decimal("9999.99")
    assert parse_amount_word("9,999.99") == Decimal("9999.99")
    assert parse_amount_word("1,23,456.78") == Decimal("123456.78")


@pytest.mark.parametrize(
    "text",
    ["C/O", "Rs.", "12.5", "9,999.99CR", "9,999.99Dr", "ABC1.00", "1.000", "`", "`ABC"],
)
def test_non_amounts_rejected(text):
    assert parse_amount_word(text) is None


def test_parse_money_covers_row_groups():
    assert parse_money("1,234.00") == Decimal("1234.00")
    assert parse_money("₹ 1,234.00") == Decimal("1234.00")
    # A font that encodes the rupee sign as a backtick (measured on ICICI).
    assert parse_money("`1,234.00") == Decimal("1234.00")
    assert parse_money("-5.50") == Decimal("-5.50")
    assert parse_money("nan") is None
    assert parse_money("abc") is None


@pytest.mark.parametrize(
    "text",
    [
        "Due 21 Sep, 2026 end",
        "Due 21 September 2026 end",
        "Due Sep 22, 2026 end",
        "Due September 22, 2026 end",
        "Due 21/09/2026 end",
    ],
)
def test_date_text_finds_each_form(text):
    assert DATE_TEXT.search(text)


def test_starts_with_date():
    for yes in ("21/09/2026 X", "Sep 22, 2026 X", "12 MAY STREET"):
        assert starts_with_date(yes), yes
    for no in ("1 THE MALL", "24 HRS SUPPORT", "CONT LINE"):
        assert not starts_with_date(no), no


@pytest.mark.parametrize(
    "text",
    [
        "XXXX XXXX XXXX 1234",
        "5522XXXXXXXX0002",
        "438628******1234",
        "4386-28XX-XXXX-1234",
        "4386281234561234",
        "3782 XXXXXX X0005",  # a masked 15-character Amex number
    ],
)
def test_card_shape_accepts(text):
    assert card_shaped(text)


@pytest.mark.parametrize(
    "text",
    [
        "2026",
        "98765432109",
        "1860 500 5555",
        "XX12",
        # Fewer than 15 card characters: a masked mobile or bank account.
        "XXXXXXXX1234",
        "91 XXXXXX1234",
        "XXXXXXXXXX1234",
        # An unmasked number needs 16 digits; 15 bare digits are refused.
        "3782 822463 10005",
    ],
)
def test_card_shape_rejects(text):
    assert not card_shaped(text)


def _lines(*texts: str) -> list[PdfLine]:
    return [
        PdfLine(index=i, page=0, text=t, words=(), spans=())
        for i, t in enumerate(texts)
    ]


def test_find_card_last4_rejects_a_year_pattern():
    lines = _lines("Statement Date 21/10/2026", "XXXX XXXX XXXX 1234")
    assert find_card_last4(lines, r"(?P<last4>20\d\d)") == (set(), [1])
    pattern = r"[\dX]{4} [\dX]{4} [\dX]{4} (?P<last4>\d{4})"
    assert find_card_last4(lines, pattern) == ({"1234"}, [1])


def test_find_card_last4_counts_the_trailing_card_run_of_a_labelled_match():
    lines = _lines("Statement Date 21/10/2026", "Card No: XXXX XXXX XXXX 1234")
    labelled = r"Card No: (?:[\dX]{4} ){3}(?P<last4>\d{4})"
    assert find_card_last4(lines, labelled) == ({"1234"}, [1])
    ref = _lines("Ref 12 Card No: XXXX XXXX XXXX 1234")
    assert find_card_last4(ref, r"Ref.*?(?P<last4>\d{4})$") == ({"1234"}, [0])
    # A label before a non-card number still finds nothing.
    assert find_card_last4(lines, r"Statement.*?(?P<last4>20\d\d)") == (set(), [1])
    phone = _lines("Call 1860 500 5555 or Card No: XXXX XXXX XXXX 1234")
    assert find_card_last4(phone, r"Call (?:\d+ )+(?P<last4>\d{4})") == (set(), [0])
    branch = _lines("Branch Code 98765432109 Card No: XXXX XXXX XXXX 1234")
    assert find_card_last4(branch, r"Branch Code \d{7}(?P<last4>\d{4})") == (
        set(),
        [0],
    )


def test_find_card_last4_refuses_masked_mobile_and_account_numbers():
    mobile = _lines("Mobile +91 XXXXXX1234")
    assert find_card_last4(mobile, r"Mobile.*?(?P<last4>\d{4})") == (set(), [])
    autopay = _lines("Autopay from A/c XXXXXXXX5678")
    assert find_card_last4(autopay, r"A/c X+(?P<last4>\d{4})") == (set(), [])


def test_find_card_last4_needs_the_run_to_end_at_the_match():
    longer = _lines("Card XXXX XXXX XXXX 1234 5678")
    assert find_card_last4(longer, r"Card (?:X{4} ){3}(?P<last4>\d{4})") == (
        set(),
        [0],
    )
    ref = _lines("Ref 1234 5678 9012 3456 7890")
    assert find_card_last4(ref, r"Ref (?:\d{4} ){3}(?P<last4>\d{4})") == (
        set(),
        [0],
    )
    glued = _lines("Card XXXX XXXX XXXX 1234-5678")
    assert find_card_last4(glued, r"Card (?:X{4} ){3}(?P<last4>\d{4})") == (
        set(),
        [0],
    )
    # A run that does end at the match, followed by text, still counts.
    ended = _lines("Card XXXX XXXX XXXX 1234 Holder ASHA")
    assert find_card_last4(ended, r"Card (?:X{4} ){3}(?P<last4>\d{4})") == (
        {"1234"},
        [0],
    )


def test_find_card_last4_needs_the_last4_group_to_end_the_match():
    # Final review M2: a pattern that matches more digits after its last4
    # group would take the middle of a longer run as the card's last 4. The
    # whole 20-character run is card-shaped and ends at the match, so only
    # "last4 ends the match" refuses it.
    longer = _lines("Card XXXXXXXXXXXX12345678")
    pattern = r"Card [\dX]{12}(?P<last4>\d{4})\d{4}"
    assert find_card_last4(longer, pattern) == (set(), [0])
    # The same run read so that last4 ends the match is found.
    ends = r"Card [\dX]{16}(?P<last4>\d{4})"
    assert find_card_last4(longer, ends) == ({"5678"}, [0])


def test_find_card_last4_finds_nothing_through_a_lookbehind():
    # The prover doc (M5) says a lookbehind-only card_pattern never passes:
    # the card run must be inside the match.
    lines = _lines("Card No: XXXXXXXXXXXX1234")
    assert find_card_last4(lines, r"(?<=X{12})(?P<last4>\d{4})") == (set(), [0])
    assert find_card_last4(lines, r"Card No: X{12}(?P<last4>\d{4})") == ({"1234"}, [0])


def test_find_card_last4_tolerates_bad_patterns():
    lines = _lines("XXXX XXXX XXXX 1234")
    assert find_card_last4(lines, r"X{4} (\d{4})") == (set(), [0])
    assert find_card_last4(lines, r"(?P<last4>") == (set(), [0])


def test_normalise_card_token():
    for token in ("XXXX XXXX XXXX 1234", "438628******1234", "5522XXXXXXXX1234"):
        assert normalise_card_token(token) == "XXXXXXXXXXXX1234"
    assert normalise_card_token("2026") is None
