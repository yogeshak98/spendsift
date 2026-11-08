from backend.app.llm.flows.prove_template.redact import (
    STATEMENT_VOCAB,
    is_transaction_shaped,
    redact_free_text,
)


def test_transaction_shape():
    assert is_transaction_shaped("05/10/2026| 12:00 SWIGGY C 1.00")
    assert is_transaction_shaped("5-Oct-26 X 1.00")
    assert not is_transaction_shaped("12 LAKE VIEW ROAD")
    assert not is_transaction_shaped("")


def test_name_address_pin_redacted():
    assert redact_free_text("ASHA RAO") == "▢ ▢"
    assert redact_free_text("12 LAKE VIEW ROAD BENGALURU 560001") == "## ▢ ▢ ▢ ▢ ######"


def test_structure_kept():
    assert redact_free_text("PAYMENTS/CREDITS C 1,00,000.50") == (
        "PAYMENTS/CREDITS C 1,00,000.50"
    )
    assert redact_free_text("DUE DATE 10 Nov, 2026") == "DUE DATE 10 Nov, 2026"
    assert redact_free_text("Page 1 of 2") == "Page # of #"
    # A card number is normalised to 12 X + last 4 (see test_card_tokens...).
    assert redact_free_text("Card No: XXXX XXXX XXXX 0001") == (
        "Card No: XXXXXXXXXXXX0001"
    )


def test_vocab_has_no_names():
    assert not {"ASHA", "RAO", "BENGALURU", "LAKE", "JANE", "DOE"} & STATEMENT_VOCAB


import pytest  # noqa: E402

HOUSE_LINES = [
    "12-3-45 ASHA RAO NAGAR HYDERABAD 500001",
    "8-2-120 ROAD NO 5 BANJARA HILLS 500034",
    "1/2/34 LAKE VIEW",
]


@pytest.mark.parametrize("line", HOUSE_LINES)
def test_house_number_lines_are_not_transactions_and_are_redacted(line):
    assert not is_transaction_shaped(line)
    out = redact_free_text(line)
    for word in (
        "ASHA",
        "RAO",
        "NAGAR",
        "HYDERABAD",
        "BANJARA",
        "500001",
        "500034",
        "LAKE",
    ):
        assert word not in out


def test_real_transaction_line_is_shaped():
    assert is_transaction_shaped("21/09/2026 14:22 ACME STORE C 1,234.00")
    assert is_transaction_shaped("21/09/2026| 14:22 ACME C 1.00")
    assert is_transaction_shaped("21-Sep-26 ACME 1.00")


def test_invalid_date_is_not_transaction_shaped():
    assert not is_transaction_shaped("31/13/2026 X C 1.00")
    assert not is_transaction_shaped("32/01/2026 X C 1.00")
    assert not is_transaction_shaped("21-Foo-26 X C 1.00")


def test_glued_amounts_and_full_month_dates_survive():
    assert redact_free_text("Total ₹9,999.99") == "Total ₹9,999.99"
    assert redact_free_text("Rs.1,00,000.50 INR5.00") == "Rs.1,00,000.50 INR5.00"
    # The backtick a font prints for the rupee sign (ICICI): peek and the
    # parser share one amount definition, so the value shows.
    assert redact_free_text("Total `9,999.99") == "Total `9,999.99"
    assert redact_free_text("` `ABC") == "` ▢"
    assert redact_free_text("Due September 22, 2026") == "Due September 22, 2026"
    assert redact_free_text("Due Sep 22, 2026") == "Due Sep 22, 2026"
    assert redact_free_text("Due 21 September 2026") == "Due 21 September 2026"


def test_glued_suffix_is_not_an_amount():
    assert redact_free_text("9,999.99CR") == "▢"


def test_card_tokens_are_normalised_to_twelve_x_and_last4():
    for printed in (
        "XXXX XXXX XXXX 1234",
        "438628******1234",
        "5522XXXXXXXX1234",
        "4386-28XX-XXXX-1234",
    ):
        assert redact_free_text(f"Card {printed}") == "Card XXXXXXXXXXXX1234", printed
    assert redact_free_text("Card 4386281234561234").endswith("XXXXXXXXXXXX1234")


def test_non_card_numbers_are_not_normalised():
    assert redact_free_text("Call 98765432109") == "▢ ###########"
    assert "XXXXXXXXXXXX" not in redact_free_text("Year 2026 Call 1860 500 5555")
    # Masked mobile and account numbers are too short to be a card.
    for text in ("Mobile +91 XXXXXX1234", "Autopay from A/c XXXXXXXX5678"):
        assert "XXXXXXXXXXXX" not in redact_free_text(text), text


def test_transaction_shape_accepts_glued_amount():
    assert is_transaction_shaped("05/10/2026 SWIGGY ₹1.00")
    assert not is_transaction_shaped("05/10/2026 SWIGGY 1.00CR")


# Standard statement words measured on the three issuers' summaries and table
# headers (Task 6b): shown in peek so a prover can anchor on them.
STATEMENT_WORDS_6B = (
    "ADVANCE ADVANCES OTHER DETAILS MERCHANT CATEGORY REWARD REWARDS SPENDS SPEND"
    " SERNO SER POINTS SUMMARY DEBITS CREDITS CHARGES DEBIT SERIAL REFERENCE REF"
    " DESCRIPTION PARTICULARS CASHBACK EARNED GENERATION LIMIT AVAILABLE OUTSTANDING"
    " OPENING CLOSING INTEREST FEE FEES TAX GST IGST CGST SGST INTERNATIONAL DOMESTIC"
    " TRANSACTIONS"
).split()


@pytest.mark.parametrize("word", STATEMENT_WORDS_6B)
def test_statement_words_survive_redaction(word):
    assert word in STATEMENT_VOCAB
    assert redact_free_text(word) == word
    assert redact_free_text(word.title()) == word.title()


def test_holder_name_and_address_still_redact_with_the_new_words():
    from tests.service.ingest.pdf_fixtures_issuers import AXIS_HOLDER, ICICI_HOLDER

    for line in (*ICICI_HOLDER, *AXIS_HOLDER, "ASHA RAO", "12 LAKE VIEW ROAD"):
        shown = redact_free_text(line).split(" ")
        assert set(shown) <= {"▢", "##", "######"}, (line, shown)
    # Mixed: the statement words show, the name beside them does not.
    assert redact_free_text("Reward Points NEHA KULKARNI") == "Reward Points ▢ ▢"
    assert redact_free_text("SerNo. Details ORCHID TOWERS") == "SerNo. Details ▢ ▢"


# --- Month words only inside a date (final review I1) ------------------------
# A month word is a first name (April, June, May) or a street (August Kranti
# Marg, 21 March Road) as often as a date. It stays in the clear only as part
# of one of the two month forms DATE_TEXT reads, both of which need a year:
# day-first "21 Sep, 2026" / "21 September 2026" and month-first
# "September 22, 2026" / "Sep 22 2026". A house number before a month is not
# a date unless a year follows.

MONTH_NAMED_PEOPLE_AND_PLACES = [
    ("APRIL MEHTA", "▢ ▢"),
    ("JUNE FERNANDES", "▢ ▢"),
    ("MAY DSOUZA", "▢ ▢"),
    ("Mr AUGUST NORONHA", "▢ ▢ ▢"),
    ("12 AUGUST KRANTI MARG MUMBAI 400036", "## ▢ ▢ ▢ ▢ ######"),
    ("FLAT 21 MARCH ROAD", "▢ ## ▢ ▢"),
    ("12 AUGUST", "## ▢"),
    ("AUGUST 12", "▢ ##"),
    ("JUNE 12 MG ROAD", "▢ ## ▢ ▢"),
    ("APRIL 2026", "▢ ####"),  # no day: not a form DATE_TEXT reads
    ("45 March 2026", "## ▢ ####"),  # 45 is not a day
]


@pytest.mark.parametrize(("line", "shown"), MONTH_NAMED_PEOPLE_AND_PLACES)
def test_a_month_word_outside_a_date_is_masked(line, shown):
    assert redact_free_text(line) == shown


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        (
            "Statement period : September 22, 2026 to October 21, 2026",
            "Statement period : September 22, 2026 ▢ October 21, 2026",
        ),
        ("Billing Period 21 Sep, 2026 - 20 Oct, 2026",) * 2,
        ("DUE DATE 10 Nov, 2026",) * 2,
        ("Due 21 September 2026",) * 2,
        ("Due Sep 22 2026",) * 2,
        ("Due Sept 22, 2026",) * 2,
        ("Purchase on Sep 10, 2026 of ₹10,000.00",) * 2,
    ],
)
def test_a_month_word_inside_a_date_stays(line, shown):
    assert redact_free_text(line) == shown


def test_no_month_word_is_statement_vocabulary():
    from backend.app.service.ingest.parsers.pdf_words import MONTHS

    assert not (MONTHS | {"SEPT"}) & STATEMENT_VOCAB
