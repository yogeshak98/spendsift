def test_fresh_journal_loads_clean(ledger):
    result = ledger.load()
    assert result.errors == []
    # only directives so far are commodity + opens, no transactions
    assert result.options_map["operating_currency"] == ["INR"]


def test_validate_returns_empty_for_valid_journal(ledger):
    assert ledger.validate() == []


def test_validate_flags_unbalanced_transaction(ledger):
    # write an unbalanced transaction directly into the journal text
    bad = (
        '\n2026-04-01 * "Broken"\n'
        "  Expenses:Food   100.00 INR\n"
        "  Assets:Bank:HDFC  -90.00 INR\n"
    )
    ledger.main_file.write_text(ledger.main_file.read_text() + bad, encoding="utf-8")
    errors = ledger.validate()
    assert errors
    assert any(
        "balance" in e.lower() or "does not balance" in e.lower() for e in errors
    )


def test_validate_flags_a_transaction_living_in_the_wrong_year_file(ledger):
    # journal/<year>.beancount is the convention read_year_text/write_year_text
    # and the Raw Ledger screen rely on, but beancount itself only follows
    # includes — it has no opinion on which physical file a transaction sits
    # in. A bulk import/rebuild script (or a hand edit) can write a 2026-dated
    # transaction into 2025.beancount, or straight into main.beancount, and
    # nothing would notice until a year-scoped screen came up empty or wrong.
    year_file = ledger.journal_dir / "2025.beancount"
    year_file.write_text(
        '2026-04-01 * "Misfiled"\n'
        "  Expenses:Food   100.00 INR\n"
        "  Assets:Bank:HDFC  -100.00 INR\n",
        encoding="utf-8",
    )
    ledger._ensure_include(2025)

    errors = ledger.validate()

    assert errors
    assert any("2026" in e and "2025.beancount" in e for e in errors)


def test_validate_flags_a_transaction_written_directly_into_main_beancount(ledger):
    bad = (
        '\n2026-04-01 * "Bypassed the year-file convention"\n'
        "  Expenses:Food   100.00 INR\n"
        "  Assets:Bank:HDFC  -100.00 INR\n"
    )
    ledger.main_file.write_text(ledger.main_file.read_text() + bad, encoding="utf-8")

    errors = ledger.validate()

    assert errors
    assert any("main.beancount" in e for e in errors)


def test_validate_does_not_flag_open_and_commodity_directives_in_main_beancount(
    ledger,
):
    # Only Transaction entries are year-file-scoped; open/commodity directives
    # are structural and correctly live in main.beancount forever.
    assert ledger.validate() == []
