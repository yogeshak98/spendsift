from decimal import Decimal
from datetime import date

from backend.app.core.paths import REPO_ROOT
from backend.app.service.ingest.parsers.ledger_cli import parse_ledger

FIX = REPO_ROOT / "tests" / "fixtures" / "ledger"


def test_parses_header_and_plain_inr_postings():
    result = parse_ledger((FIX / "basic.ledger").read_text())
    assert len(result.transactions) == 2
    assert result.unparsed == []
    assert result.ignored_lines == 0

    coffee = result.transactions[0]
    assert coffee.date == date(2024, 1, 5)
    assert coffee.narration == "Coffee run"
    assert coffee.source_line == 1
    assert [p.account for p in coffee.postings] == [
        "Expenses:Food",
        "Assets:Checking:HDFC",
    ]
    assert coffee.postings[0].amount == Decimal("250.00")
    assert coffee.postings[0].commodity == "INR"
    assert coffee.postings[1].amount == Decimal("-250.00")


def test_thousands_separator_and_elided_last_posting():
    result = parse_ledger((FIX / "basic.ledger").read_text())
    rent = result.transactions[1]
    assert rent.postings[0].amount == Decimal("20000.00")
    elided = rent.postings[1]
    assert elided.account == "Assets:Checking:HDFC"
    assert elided.amount is None
    assert elided.commodity is None


# ---------------------------------------------------------------------------
# Finding 1(a): single space INSIDE account name is preserved
# ---------------------------------------------------------------------------
def test_single_space_inside_account_name_is_preserved():
    ledger = (
        "2024/03/01 Freelance payment\n"
        "    Income:Consulting Fees  5000.00 INR\n"
        "    Assets:Checking:HDFC  -5000.00 INR\n"
    )
    result = parse_ledger(ledger)
    assert result.unparsed == []
    assert len(result.transactions) == 1
    posting = result.transactions[0].postings[0]
    assert posting.account == "Income:Consulting Fees"
    assert posting.amount == Decimal("5000.00")
    assert posting.commodity == "INR"


# ---------------------------------------------------------------------------
# Finding 1(b): single space before what looks like an amount → elided posting
# ---------------------------------------------------------------------------
def test_single_space_before_amount_is_treated_as_elided_posting():
    # One space only between account and amount-like text means no split occurs;
    # the entire body is treated as the account name (elided posting).
    ledger = (
        "2024/03/02 Grocery run\n"
        "    Expenses:Groceries 300.00 INR\n"  # one space — NOT a separator
        "    Assets:Checking:HDFC  -300.00 INR\n"
    )
    result = parse_ledger(ledger)
    assert result.unparsed == []
    elided = result.transactions[0].postings[0]
    assert elided.amount is None
    # The whole body becomes the account name since no 2-space/tab split occurred.
    assert "Expenses:Groceries" in elided.account


# ---------------------------------------------------------------------------
# Finding 2: ignored_lines counter increments for comments and directives
# ---------------------------------------------------------------------------
def test_ignored_lines_counter_increments_for_comments_and_directives():
    ledger = (
        "; This is a full-line semicolon comment\n"
        "# This is a hash comment\n"
        "% This is a percent comment\n"
        "account Assets:Checking:HDFC\n"  # deferred directive
        "commodity INR\n"  # deferred directive
        "2024/04/01 Swiggy order\n"
        "    Expenses:Food  450.00 INR\n"
        "    Assets:Checking:HDFC  -450.00 INR\n"
    )
    result = parse_ledger(ledger)
    assert len(result.transactions) == 1
    # 3 comments + 2 directives = 5 ignored lines
    assert result.ignored_lines == 5


def test_ignored_lines_are_captured_with_line_no_text_and_reason():
    ledger = (
        "; a comment\n"
        "account Assets:Checking:HDFC\n"
        "P 2024/04/01 INR 1.00 USD\n"
        "2024/04/01 Swiggy order\n"
        "    Expenses:Food  450.00 INR\n"
        "    Assets:Checking:HDFC  -450.00 INR\n"
    )
    result = parse_ledger(ledger)
    assert result.ignored_lines == 3
    assert len(result.ignored) == 3
    # line numbers are 1-based and point at the real source line
    assert [g.line_no for g in result.ignored] == [1, 2, 3]
    assert result.ignored[0].text == "; a comment"
    assert "comment" in result.ignored[0].reason.lower()
    # directive lines name the directive that caused the skip
    assert "account" in result.ignored[1].reason.lower()
    assert result.ignored[2].reason.lower().find("p") != -1


# ---------------------------------------------------------------------------
# Finding 3: unparsed path carries correct fields
# ---------------------------------------------------------------------------
def test_unparsed_malformed_block_carries_correct_fields():
    ledger = (
        "2024/05/01 Bad transaction\n"
        "    Expenses:Food  not-a-number INR\n"
        "    Assets:Checking:HDFC  -100.00 INR\n"
    )
    result = parse_ledger(ledger)
    assert len(result.transactions) == 0
    assert len(result.unparsed) == 1
    entry = result.unparsed[0]
    assert entry.line_no == 1
    # text should be the whole block starting from the header line
    assert "Bad transaction" in entry.text
    assert "not-a-number" in entry.text
    # reason should mention the bad posting line (line 2)
    assert "2" in entry.reason


# ---------------------------------------------------------------------------
# Finding 5: star-prefixed line lands in unparsed, not ignored
# ---------------------------------------------------------------------------
def test_star_prefixed_line_goes_to_unparsed_not_ignored():
    ledger = (
        "* cleared-flag line that we do not accept\n"
        "2024/06/01 Normal transaction\n"
        "    Expenses:Food  200.00 INR\n"
        "    Assets:Checking:HDFC  -200.00 INR\n"
    )
    result = parse_ledger(ledger)
    # The * line must NOT count as an ignored comment
    assert result.ignored_lines == 0
    # It must appear in unparsed with a meaningful reason
    star_entries = [u for u in result.unparsed if u.text.startswith("*")]
    assert len(star_entries) == 1
    assert star_entries[0].line_no == 1
    assert star_entries[0].reason  # non-empty reason
    # Valid transaction still parsed
    assert len(result.transactions) == 1


# ---------------------------------------------------------------------------
# Task 2: priced-commodity (MF) postings, inline comments, ignored directives
# ---------------------------------------------------------------------------
def test_priced_commodity_buy_and_inline_comment():
    result = parse_ledger((FIX / "mf_and_comments.ledger").read_text())
    assert result.ignored_lines == 2  # the ; note + the `account` directive
    buy = result.transactions[0]
    lot = buy.postings[0]
    assert lot.amount == Decimal("742.61")
    assert lot.commodity == "AcmeELSS"
    assert lot.cost == Decimal("83.48")
    assert lot.cost_currency == "INR"
    assert lot.comment == "Acme ELSS Tax Saver"


def test_priced_commodity_sell_negative_units():
    result = parse_ledger((FIX / "mf_and_comments.ledger").read_text())
    sell = result.transactions[1]
    assert sell.postings[0].amount == Decimal("-100.00")
    assert sell.postings[0].cost == Decimal("95.00")


def test_unparsable_line_is_reported_with_reason():
    text = "2024/13/40 Bad date\n    Foo  1 INR\nnot a header or posting\n"
    result = parse_ledger(text)
    # The bad-date header must NOT produce a parsed transaction.
    assert result.transactions == []
    # There must be an unparsed entry at line 1 whose reason names the invalid date.
    assert any(u.line_no == 1 and "date" in u.reason for u in result.unparsed)
    # The trailing junk line also lands in unparsed with a "header" reason.
    assert any("header" in u.reason for u in result.unparsed)


# ---------------------------------------------------------------------------
# Finding 6: tab separator splits account and amount correctly
# ---------------------------------------------------------------------------
def test_tab_separator_splits_account_and_amount():
    ledger = (
        "2024/07/01 Zomato order\n"
        "    Expenses:Food\t350.00 INR\n"
        "    Assets:Checking:HDFC\t-350.00 INR\n"
    )
    result = parse_ledger(ledger)
    assert result.unparsed == []
    assert len(result.transactions) == 1
    p0 = result.transactions[0].postings[0]
    assert p0.account == "Expenses:Food"
    assert p0.amount == Decimal("350.00")
    assert p0.commodity == "INR"


def test_parsed_txns_get_distinct_random_ids():
    ledger = (
        "2024/01/05 Coffee run\n"
        "    Expenses:Food  250.00 INR\n"
        "    Assets:Checking:HDFC  -250.00 INR\n"
        "\n"
        "2024/01/10 Rent\n"
        "    Expenses:Rent  20,000.00 INR\n"
        "    Assets:Checking:HDFC\n"
    )
    result = parse_ledger(ledger)
    ids = [t.id for t in result.transactions]
    # Each id is a 32-char hex string (uuid4().hex), and ids are distinct.
    assert all(len(i) == 32 for i in ids), f"unexpected id lengths: {ids}"
    assert all(c in "0123456789abcdef" for i in ids for c in i)
    assert len(set(ids)) == len(ids)


def test_reparsing_same_file_yields_different_ids():
    """uuid4 ids are random per parse — not content-derived."""
    text = (
        "2024/01/05 Coffee run\n"
        "    Expenses:Food  250.00 INR\n"
        "    Assets:Checking:HDFC  -250.00 INR\n"
    )
    r1 = parse_ledger(text)
    r2 = parse_ledger(text)
    assert r1.transactions[0].id != r2.transactions[0].id
