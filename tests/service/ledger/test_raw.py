from __future__ import annotations

import pytest

from backend.app.core.errors.ledger import HashMismatchError
from backend.app.persistence.journal import Ledger
from backend.app.service.ledger import raw


def _ledger(tmp_path) -> Ledger:
    led = Ledger(tmp_path)
    led.bootstrap()
    return led


def test_read_then_write_round_trips_through_the_journal(tmp_path) -> None:
    led = _ledger(tmp_path)
    text, digest = raw.read_year_text(led, 2024)
    assert (text, digest) == led.read_year_text(2024)

    body = '2024-03-01 * "Coffee"\n    Expenses:Food   100 INR\n    Assets:Cash\n'
    new_hash = raw.write_year_text(led, 2024, body, digest)

    assert raw.read_year_text(led, 2024) == (body, new_hash)


def test_write_keeps_the_journal_hash_guard(tmp_path) -> None:
    led = _ledger(tmp_path)
    with pytest.raises(HashMismatchError):
        raw.write_year_text(led, 2024, "; stale\n", "not-the-hash")
