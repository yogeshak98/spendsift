import pytest

from backend.app.service.categorize.patterns import (
    REASON_EMPTY,
    REASON_GENERIC,
    REASON_SHORT,
    is_fee_narration,
    learn_pattern,
    learnability,
)
from backend.app.service.ingest.pipeline.normalize import normalize_narration as norm

SHAPES = [  # (this month, next month, expected pattern)
    ("FICTIVEBIKES BANGALORE", "FICTIVEBIKES BENGALURU", "FICTIVEBIKES"),
    ("FICTIVEAI SAN FRANCISCO US", "FICTIVEAI SAN FRANCISCO US", "FICTIVEAI"),
    (
        "UPI-FICTIVE TIFFINS-fictive@okbank-FICT0000001-612345678901-UPI",
        "UPI-FICTIVE TIFFINS-fictive@okbank-FICT0000001-698765432101-UPI",
        "FICTIVE TIFFINS",
    ),
    (
        "12345678901 FICTIVE STAYS BANGALORE IN",
        "10987654321 FICTIVE STAYS BANGALORE IN",
        "FICTIVE STAYS",
    ),
    ("FICTIVEMART XX1234", "FICTIVEMART XX9876", "FICTIVEMART"),
    ("FICTIVE GYM 21/09/2026", "FICTIVE GYM 21/10/2026", "FICTIVE GYM"),
    ("FICTIVE CAFE, BENGALURU", "FICTIVE CAFE, BENGALURU", "FICTIVE CAFE"),
    (
        "PAYU*FICTIVEFOODS 0042 BANGALORE",
        "PAYU*FICTIVEFOODS 0043 BANGALORE",
        "PAYU*FICTIVEFOODS",
    ),
]


@pytest.mark.parametrize("now,later,expected", SHAPES)
def test_pattern_strips_refs_dates_digits_and_place_tails_and_matches_next_month(
    now, later, expected
):
    assert learn_pattern(norm(now)) == expected
    assert expected in norm(later)  # LearnedRules.match is a substring test


def test_a_reference_number_never_reaches_the_pattern():
    p = learn_pattern(norm("FICTIVESHOP REF 99887766554433"))
    assert not any(ch.isdigit() for ch in p)


def test_learnability_reasons():
    assert learnability("FICTIVEBIKES") is None
    assert learnability("") == REASON_EMPTY
    assert learnability("ABC") == REASON_SHORT
    assert learnability(learn_pattern(norm("OFFUS EMI 03/06"))) == REASON_GENERIC
    assert learnability("PAYMENT THANK YOU") == REASON_GENERIC
    assert learnability("IGST") == REASON_GENERIC


@pytest.mark.parametrize(
    "raw",
    [
        "IGST @18% ON FX MARKUP REF 1234567890",
        "CGST ON FEE",
        "FX MARKUP FEE",
        "DCC FEE FICTIVEAI",
        "CROSS CURRENCY MARKUP",
        "foreign currency txn fee",
    ],
)
def test_fee_narrations(raw):
    assert is_fee_narration(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "FICTIVEAI SAN FRANCISCO US",
        "GSTOREFICTIVE",
        "Finance charges (statement summary)",
    ],
)
def test_non_fee_narrations(raw):
    assert not is_fee_narration(raw)  # whole words only


def test_short_leading_digits_and_markers_are_skipped():
    # norm() only drops refs of 5+ digits; a 4-digit lead must not empty the pattern
    assert learn_pattern("1234 FICTIVE STAYS 5678 X") == "FICTIVE STAYS"
    assert learn_pattern("# FICTIVE STAYS") == "FICTIVE STAYS"
