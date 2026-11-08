"""Models for the raw-ledger transaction routes."""

from __future__ import annotations

from pydantic import BaseModel


class RawLedgerResponse(BaseModel):
    """Response of `GET /api/ledger/raw` — the exact text of one calendar-year file."""

    year: int
    text: str
    hash: str


class RawLedgerWriteRequest(BaseModel):
    """Body of `PUT /api/ledger/raw`."""

    year: int
    text: str
    expected_hash: str


class RawLedgerWriteResponse(BaseModel):
    """Response of `PUT /api/ledger/raw` — the new file hash after a successful write."""

    year: int
    hash: str
