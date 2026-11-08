"""Raw year-file access for the ledger editor.

A thin pass-through to the journal, so routes call a service rather than the
store. The journal owns the hash guard, the snapshot and the rollback."""

from __future__ import annotations

from backend.app.persistence.journal import Ledger


def read_year_text(ledger: Ledger, year: int) -> tuple[str, str]:
    """Return the raw text of the year's journal file and its sha256 hex."""
    return ledger.read_year_text(year)


def write_year_text(ledger: Ledger, year: int, text: str, expected_hash: str) -> str:
    """Replace the year's journal file with `text`, guarded by `expected_hash`.
    Returns the new file hash."""
    return ledger.write_year_text(year, text, expected_hash)
