"""A ledger-merge batch held between preview and commit, and its mapping tables."""

from __future__ import annotations

from dataclasses import dataclass

from beancount.core import data

from backend.app.models.domain.ledger_cli import ParsedLedger, UnparsedLine


@dataclass(frozen=True)
class AccountMapping:
    raw: str
    suggested: str
    status: str


@dataclass(frozen=True)
class CommodityMapping:
    raw: str
    suggested: str
    display_name: str | None
    status: str


@dataclass
class MergeBatch:
    entries: list[data.Transaction]
    accounts: list[AccountMapping]
    commodities: list[CommodityMapping]
    unparsed: list[UnparsedLine]
    ignored_lines: int
    duplicate_ids: set[str]
    unbookable_ids: set[str]
    parsed: ParsedLedger
    filename: str = ""
