from __future__ import annotations

import hashlib

import pytest

from backend.app.core.errors.ledger import HashMismatchError, LedgerValidationError


def test_read_year_text_returns_text_and_matching_hash(ledger) -> None:
    body = '2024-03-01 * "Coffee"\n    Expenses:Food   100 INR\n    Assets:Cash\n'
    (ledger.journal_dir / "2024.beancount").write_text(body, encoding="utf-8")

    text, digest = ledger.read_year_text(2024)

    assert text == body
    assert digest == hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_read_year_text_missing_file_is_empty_with_empty_hash(ledger) -> None:
    text, digest = ledger.read_year_text(1999)

    assert text == ""
    assert digest == hashlib.sha256(b"").hexdigest()


def test_write_year_text_writes_on_matching_hash_and_returns_new_hash(ledger) -> None:
    # Expenses:Food and Assets:Cash are already opened by bootstrap (SEED_ACCOUNTS).
    _, empty_hash = ledger.read_year_text(2024)
    body = '2024-03-01 * "Coffee"\n    Expenses:Food   100 INR\n    Assets:Cash\n'

    new_hash = ledger.write_year_text(2024, body, empty_hash)

    text, digest = ledger.read_year_text(2024)
    assert text == body
    assert new_hash == digest
    assert new_hash != empty_hash


def test_write_year_text_rejects_stale_hash_without_writing(ledger) -> None:
    (ledger.journal_dir / "2024.beancount").write_text("; original\n", encoding="utf-8")

    with pytest.raises(HashMismatchError):
        ledger.write_year_text(2024, "; new\n", "deadbeef")

    assert (ledger.journal_dir / "2024.beancount").read_text(
        encoding="utf-8"
    ) == "; original\n"


def test_write_year_text_rejects_invalid_text_without_writing(ledger) -> None:
    _, empty_hash = ledger.read_year_text(2024)
    # Posts to accounts main.beancount never opened -> beancount validation error.
    bad = '2024-03-01 * "X"\n    Expenses:NotOpened   1 INR\n    Assets:AlsoNot\n'

    with pytest.raises(LedgerValidationError):
        ledger.write_year_text(2024, bad, empty_hash)

    assert ledger.read_year_text(2024)[0] == ""
