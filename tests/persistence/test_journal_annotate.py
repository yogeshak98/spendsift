import hashlib
from datetime import date
from decimal import Decimal

import pytest

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.core.errors.ledger import (
    HashMismatchError,
    PostingNotFoundError,
    TransactionNotFoundError,
)


def _commit_redemption(ledger, investment_ledger):
    candidate = CandidateTxn(
        date=date(2026, 3, 18),
        amount=Decimal("21500.00"),
        raw_narration="Groww redemption",
        normalized_narration="GROWW REDEMPTION",
        source_account="Assets:Bank:HDFC",
        direction=Direction.CREDIT,
        suggested_other_leg="Assets:Investments:PPFCF",
    )
    commit_candidates(ledger, investment_ledger, [candidate])
    return candidate


def _current_hash(ledger, year):
    year_file = ledger.journal_dir / f"{year}.beancount"
    return hashlib.sha256(year_file.read_bytes()).hexdigest()


def test_annotate_posting_writes_metadata_and_stays_valid(ledger, investment_ledger):
    candidate = _commit_redemption(ledger, investment_ledger)
    expected_hash = _current_hash(ledger, 2026)

    ledger.annotate_posting(
        candidate.id,
        "Assets:Investments:PPFCF",
        {"units": "100.0", "nav": "215.0"},
        expected_hash,
    )

    text = (ledger.journal_dir / "2026.beancount").read_text(encoding="utf-8")
    assert 'units: "100.0"' in text
    assert 'nav: "215.0"' in text
    assert ledger.validate() == []


def test_annotate_posting_unknown_txn_id_raises(ledger, investment_ledger):
    _commit_redemption(ledger, investment_ledger)
    with pytest.raises(TransactionNotFoundError):
        ledger.annotate_posting("does-not-exist", "Assets:Investments:PPFCF", {}, "x")


def test_annotate_posting_unknown_account_raises(ledger, investment_ledger):
    candidate = _commit_redemption(ledger, investment_ledger)
    expected_hash = _current_hash(ledger, 2026)
    with pytest.raises(PostingNotFoundError):
        ledger.annotate_posting(
            candidate.id, "Assets:Investments:NoSuchFund", {}, expected_hash
        )


def test_annotate_posting_hash_mismatch_raises_and_leaves_file_untouched(
    ledger, investment_ledger
):
    candidate = _commit_redemption(ledger, investment_ledger)
    before = (ledger.journal_dir / "2026.beancount").read_text(encoding="utf-8")

    with pytest.raises(HashMismatchError):
        ledger.annotate_posting(
            candidate.id, "Assets:Investments:PPFCF", {"units": "1.0"}, "stale-hash"
        )

    after = (ledger.journal_dir / "2026.beancount").read_text(encoding="utf-8")
    assert after == before
