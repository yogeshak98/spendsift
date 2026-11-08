"""Opening-balance onboarding on every catalog persona (Phase 6).

QA books each bank and card opening from the first import's prompt
(`GET /api/review/{batch}/opening`, then `POST /api/openings` with the
suggestion as shown). These tests pin that the prompt suggests each
persona's true opening, on the date the date rule gives, and that what the
importer wrote is exactly that suggestion. The persona's truth is the
reference: a suggestion that differs from it is a finding, never a reason to
change the truth.

The date rule (API_CONTRACT "Opening balances"), computed here from the
truth, not read back from the app:

- a non-card account: a first row in April gives 31 March of that year,
  any other the day before the first row;
- a card: the day before the earlier of the statement's `period_start` (a
  PDF prints it; a CSV/XLS card has none, so its start is its first row)
  and the account's first row in the batch.

Each layout says where its suggestion comes from: `statement` (a figure the
file prints: a card PDF's previous balance, or a template's
`opening_balance` cell), `derived` (the running balance), or None (the user
types it). Kotak's file prints its opening twice (the account block and the
closing summary carry the same "Opening Balance" label), so no template cell
can single one out and its suggestion is derived.

Opt-in (`SPENDSIFT_PERSONAS_QA=1`): each persona is imported once per
session, shared with the rule book (`conftest.Personas`).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.personas.build import WRITERS
from scripts.personas.catalog import ALL
from scripts.personas.formats._common import SIDECAR_SUFFIX, card_statements
from scripts.personas.importer import ImportedFile
from scripts.personas.model import PERIOD_END, PERIOD_START, Account
from scripts.personas.naming import account_name
from tests.demo import optin
from tests.demo.conftest import Personas

# (kind, institution) -> (layout, expected suggestion source).
LAYOUTS: dict[tuple[str, str], tuple[str, str | None]] = {
    ("bank", "HDFC"): ("HDFC savings .xls (STATEMENT SUMMARY)", "statement"),
    ("bank", "IDFC"): ("IDFC savings .xlsx (ACCOUNT SUMMARY)", "statement"),
    ("bank", "SBI"): ("SBI savings .xlsx (Balance as on)", "statement"),
    ("bank", "Kotak"): ("Kotak savings .csv (label printed twice)", "derived"),
    ("card", "ICICI"): ("ICICI card PDF (previous balance)", "statement"),
    ("card", "Axis"): ("Axis card PDF (previous balance)", "statement"),
    ("card", "HDFC"): ("HDFC card .xlsx (Account Summary)", "statement"),
}


def _accounts() -> list[tuple[str, Account]]:
    return [
        (slug, a)
        for slug, persona in ALL.items()
        for a in persona.accounts
        if a.kind in ("bank", "card")
    ]


def _cases() -> list:
    return [
        pytest.param(slug, a.key, marks=[optin.personas_qa], id=f"{slug}-{a.key}")
        for slug, a in _accounts()
    ]


def test_every_statement_layout_declares_its_suggestion_source():
    """No persona account escapes the table above: a new layout must say
    where its opening comes from (or that it has none)."""
    layouts = {(a.kind, a.institution) for _, a in _accounts()}
    assert layouts == set(LAYOUTS)
    assert layouts <= set(WRITERS)


def _sidecar(path: Path) -> dict:
    return json.loads(path.with_name(path.name + SIDECAR_SUFFIX).read_text("utf-8"))


def _first_file(imported: list[ImportedFile], key: str) -> ImportedFile:
    """The account's first statement file in import order."""
    return next(f for f in imported if _sidecar(f.path)["account_key"] == key)


def _expected_dates(
    account: Account, first: ImportedFile, events: list
) -> tuple[date, date]:
    """(statement_start, suggested_date) by the date rule, from the truth."""
    rows = [date.fromisoformat(r["date"]) for r in _sidecar(first.path)["rows"]]
    earliest = min(rows)
    if account.kind == "card":
        if first.path.suffix == ".pdf":
            start = card_statements(account, events, (PERIOD_START, PERIOD_END))[
                0
            ].start
        else:
            start = earliest
        return start, min(start, earliest) - timedelta(days=1)
    if earliest.month == 4:
        return earliest, date(earliest.year, 3, 31)
    return earliest, earliest - timedelta(days=1)


@pytest.mark.parametrize(("slug", "key"), _cases())
def test_first_import_suggests_the_persona_opening(
    personas: Personas, slug: str, key: str
) -> None:
    run = personas[slug]
    persona = ALL[slug]
    account = persona.account(key)
    name = account_name(account)
    layout, source = LAYOUTS[(account.kind, account.institution)]
    first = _first_file(run.imported, key)
    assert first.refused is None, (first.path.name, first.refused, first.detail)

    prompts = [p for p in first.prompts if p["account"] == name]
    assert len(prompts) == 1, f"{layout}: no first-import prompt for {name}"
    prompt = prompts[0]
    events = [e for e in run.truth.events if e.account_key == key]
    start, day = _expected_dates(account, first, events)
    assert prompt["kind"] == account.kind
    assert prompt["statement_start"] == start.isoformat(), layout
    assert prompt["suggested_date"] == day.isoformat(), layout

    suggestion = prompt["suggestion"]
    booked = [b for b in first.openings if b.account == name]
    assert len(booked) == 1, f"{layout}: {name} opening not booked"
    written = booked[0].written
    truth = account.opening_balance.quantize(Decimal("0.01"))
    if source is None:
        assert suggestion is None, f"{layout} prints no opening: {suggestion}"
        assert booked[0].fallback and written["source"] == "manual"
    else:
        assert suggestion is not None, f"{layout}: no suggestion ({prompt['note']})"
        assert suggestion["source"] == source, (layout, suggestion)
        assert Decimal(suggestion["ledger_amount"]) == truth, (layout, suggestion)
        assert suggestion["ledger_amount"] == str(truth), (layout, suggestion)
        assert prompt["mismatch"] is False, (layout, prompt["alternatives"])
        for alt in prompt["alternatives"]:
            assert alt["ledger_amount"] == str(truth), (layout, alt)
        # The importer posted the suggestion exactly as shown.
        assert not booked[0].fallback
        assert written["source"] == source
        assert written["ledger_amount"] == suggestion["ledger_amount"]
    assert written["date"] == day.isoformat()
    assert Decimal(written["ledger_amount"]) == truth
