"""Whole-file rewrites of the journal go through an atomic replace (review I1/I3).

A reader -- a screen while the background refresh writes, or another process --
must never parse a truncated main.beancount or year file. Appends to a year
file stay in place (an append never truncates).
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.models.domain.nav import NavRow
from backend.app.persistence import journal as journal_mod
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from tests.conftest import make_txn


def _forbid_in_place_rewrites(mp: pytest.MonkeyPatch) -> None:
    """Fail on any Path.write_text of a journal .beancount file itself (the
    atomic helper writes a dot-prefixed temp file, then os.replace)."""
    real = Path.write_text

    def guarded(self, *args, **kwargs):
        if self.suffix == ".beancount" and not self.name.startswith("."):
            raise AssertionError(f"in-place rewrite of {self.name}")
        return real(self, *args, **kwargs)

    mp.setattr(Path, "write_text", guarded)


@pytest.fixture
def no_in_place_rewrites(monkeypatch):
    _forbid_in_place_rewrites(monkeypatch)


def _txn(narration: str, day: int = 1, account: str = "Expenses:Food"):
    return make_txn(date(2026, 4, day), narration, account, "Assets:Bank:HDFC", "5")


def test_bootstrap(storage_dir, no_in_place_rewrites):
    Ledger(storage_dir).bootstrap()


def test_append_new_year_and_new_account(ledger, no_in_place_rewrites):
    ledger.append([_txn("A", account="Expenses:Brand:New")])


def test_open_named_accounts(ledger, no_in_place_rewrites):
    ledger.open_named_accounts(["Expenses:Fresh"])


def test_rename_account(ledger):
    ledger.append([_txn("A")])
    with pytest.MonkeyPatch.context() as mp:
        _forbid_in_place_rewrites(mp)
        ledger.rename_account("Expenses:Food", "Expenses:Dining")


def test_ensure_commodity_accounts(ledger, no_in_place_rewrites):
    ledger.ensure_commodity_accounts([("MF1", "Fund")])


def test_write_year_text(ledger):
    ledger.append([_txn("A")])
    text, digest = ledger.read_year_text(2026)
    with pytest.MonkeyPatch.context() as mp:
        _forbid_in_place_rewrites(mp)
        ledger.write_year_text(2026, text.replace('"A"', '"B"'), digest)


def test_recategorize(ledger):
    ledger.append([_txn("A")._replace(meta={"txn-id": "t1"})])
    with pytest.MonkeyPatch.context() as mp:
        _forbid_in_place_rewrites(mp)
        ledger.recategorize(["t1"], "Expenses:Food", "Expenses:Dining")


def test_annotate_posting(ledger, investment_ledger):
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
    year = ledger.journal_dir / "2026.beancount"
    digest = hashlib.sha256(year.read_bytes()).hexdigest()
    with pytest.MonkeyPatch.context() as mp:
        _forbid_in_place_rewrites(mp)
        ledger.annotate_posting(
            candidate.id, "Assets:Investments:PPFCF", {"units": "1"}, digest
        )


def test_first_price_write_creates_the_file_before_its_include(ledger, monkeypatch):
    """A reader between the two writes must never see an include of a file
    that does not exist yet (I3)."""
    ledger.ensure_commodity_accounts([("MF1", "Fund")])
    order: list[str] = []
    real = journal_mod.write_text_atomic

    def recording(path: Path, text: str) -> None:
        if path.name == "main.beancount" and 'include "prices-x.beancount"' in text:
            order.append(
                "include (file exists)"
                if (path.parent / "prices-x.beancount").exists()
                else "include (file missing)"
            )
        elif path.name == "prices-x.beancount":
            order.append("prices file")
        real(path, text)

    monkeypatch.setattr(journal_mod, "write_text_atomic", recording)
    row = NavRow(scheme_code="1", nav=Decimal("10"), date=date(2026, 4, 1))
    ledger.write_prices_file("prices-x.beancount", {"MF1": row})
    assert order == ["prices file", "include (file exists)"]
