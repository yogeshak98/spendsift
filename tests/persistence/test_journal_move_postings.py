"""`Ledger.move_postings`: open plan accounts, move postings and close repaid
plans under one snapshot, all-or-nothing. Synthetic rows only."""

from __future__ import annotations

from datetime import date

import pytest
from beancount.core import data

from backend.app.core.errors.errors import CodedError
from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.persistence.journal import Ledger
from backend.app.service.reports.health.ledger_view import (
    all_postings,
    closed_accounts,
    open_meta,
)
from tests.conftest import make_txn

PARENT = "Liabilities:Loan:CardEMI"
CARD = "Liabilities:CreditCard:Acme1234"
PLAN = f"{PARENT}:Acme1234-20250820"


def _txn(d, narration, debit, credit, amount, txn_id):
    txn = make_txn(d, narration, debit, credit, amount)
    txn.meta["txn-id"] = txn_id
    return txn


def _ledger(tmp_path) -> Ledger:
    led = Ledger(tmp_path)
    led.bootstrap()
    led.open_named_accounts([PARENT, CARD])
    led.append(
        [
            _txn(date(2025, 8, 20), "EMI CONVERSION", CARD, PARENT, "900", "t1"),
            _txn(date(2025, 9, 20), "EMI PRIN 1", PARENT, CARD, "450", "t2"),
            _txn(date(2026, 1, 20), "EMI PRIN 2", PARENT, CARD, "450", "t3"),
        ]
    )
    return led


def _journal_bytes(led: Ledger) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(led.journal_dir.glob("*.beancount"))}


def _snapshots(led: Ledger) -> int:
    return len(list(led.snapshots_dir.iterdir()))


def _plan(*ids: str) -> dict[str, str]:
    return {txn_id: PLAN for txn_id in ids}


META = {PLAN: {"emi-ref": "555000111", "emi-card": CARD, "label": None}}


def test_move_postings_opens_targets_with_meta_and_moves(tmp_path):
    led = _ledger(tmp_path)
    moved = led.move_postings(_plan("t1", "t2", "t3"), PARENT, META)
    assert moved == 3
    assert open_meta(led)[PLAN] == {"emi-ref": "555000111", "emi-card": CARD}
    assert [r for r in all_postings(led) if r.account == PARENT] == []
    assert len([r for r in all_postings(led) if r.account == PLAN]) == 3
    assert led.validate() == []


def test_move_postings_takes_exactly_one_snapshot(tmp_path):
    led = _ledger(tmp_path)
    before = _snapshots(led)
    led.move_postings(
        _plan("t1", "t2", "t3"), PARENT, META, close=[PLAN], close_on=date(2026, 11, 1)
    )
    assert _snapshots(led) == before + 1


def test_move_postings_closes_in_the_same_write(tmp_path):
    led = _ledger(tmp_path)
    led.move_postings(
        _plan("t1", "t2", "t3"), PARENT, META, close=[PLAN], close_on=date(2026, 11, 1)
    )
    assert closed_accounts(led) == frozenset({PLAN})
    assert (
        "2026-11-01 close " + PLAN in (led.journal_dir / "2026.beancount").read_text()
    )
    assert led.validate() == []


def test_move_postings_into_an_already_open_target_adds_no_open(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([PLAN])
    before = _snapshots(led)
    led.move_postings(_plan("t1"), PARENT, META)
    main = led.main_file.read_text()
    assert main.count(f"open {PLAN}") == 1
    # Its metadata is merged in, inside the same single snapshot.
    assert open_meta(led)[PLAN] == {"emi-ref": "555000111", "emi-card": CARD}
    assert _snapshots(led) == before + 1


def test_move_postings_target_opened_in_a_year_file_is_not_reopened(tmp_path):
    led = _ledger(tmp_path)
    year = led.journal_dir / "2025.beancount"
    year.write_text(
        year.read_text() + f'\n2025-08-01 open {PLAN} INR\n  label: "Washer"\n'
    )
    assert led.validate() == []
    moved = led.move_postings(_plan("t1", "t2"), PARENT, META)
    assert moved == 2
    assert led.main_file.read_text().count(f"open {PLAN}") == 0
    assert open_meta(led)[PLAN] == {
        "label": "Washer",
        "emi-ref": "555000111",
        "emi-card": CARD,
    }
    assert led.validate() == []


def test_move_postings_rolls_back_a_meta_merge_on_failure(tmp_path, monkeypatch):
    led = _ledger(tmp_path)
    led.open_named_accounts([PLAN])
    before = _journal_bytes(led)
    monkeypatch.setattr(Ledger, "validate", lambda self: ["boom"])
    with pytest.raises(LedgerValidationError):
        led.move_postings(_plan("t1"), PARENT, META)
    assert _journal_bytes(led) == before


def test_move_postings_restores_earlier_moves_when_a_later_entry_cannot_be_located(
    tmp_path, monkeypatch
):
    led = _ledger(tmp_path)
    # A parse that no longer matches the file: t3's recorded line is not its
    # block, so it cannot be located (a hand-formatted block no longer is a
    # failure -- it is rewritten in place). t1 and t2 (2025) are rewritten
    # first, so the restore must undo them. Postings are moved before any
    # open or meta merge is written, so this failure comes before those;
    # their rollback is covered by the validation-failure tests.
    real = led.load
    calls = {"n": 0}

    def stale(*, fresh: bool = False):
        result = real(fresh=fresh)
        calls["n"] += 1
        if calls["n"] > 1:
            return result  # validation reads the journal as it is
        result.entries = [
            e._replace(meta={**e.meta, "lineno": 1})
            if isinstance(e, data.Transaction) and e.meta.get("txn-id") == "t3"
            else e
            for e in result.entries
        ]
        return result

    monkeypatch.setattr(led, "load", stale)
    before = _journal_bytes(led)

    with pytest.raises(CodedError) as raised:
        led.move_postings(_plan("t1", "t2", "t3"), PARENT, META)
    assert raised.value.code == "E1047"
    assert _journal_bytes(led) == before
    assert PLAN not in open_meta(led)


def test_move_postings_rolls_back_on_validation_failure(tmp_path, monkeypatch):
    led = _ledger(tmp_path)
    before = _journal_bytes(led)
    monkeypatch.setattr(Ledger, "validate", lambda self: ["boom"])
    with pytest.raises(LedgerValidationError):
        led.move_postings(
            _plan("t1", "t2", "t3"),
            PARENT,
            META,
            close=[PLAN],
            close_on=date(2026, 11, 1),
        )
    assert _journal_bytes(led) == before


def test_move_postings_close_needs_a_date(tmp_path):
    led = _ledger(tmp_path)
    before = _snapshots(led)
    with pytest.raises(ValueError):
        led.move_postings(_plan("t1"), PARENT, META, close=[PLAN])
    assert _snapshots(led) == before


def test_move_postings_merges_meta_into_an_open_above_the_moved_entries(tmp_path):
    # The target's `open` sits in the same year file, above the entries being
    # moved, and the merge adds lines to it. The postings are located by their
    # parsed line numbers, so they must be rewritten before the merge shifts
    # them.
    led = _ledger(tmp_path)
    year = led.journal_dir / "2025.beancount"
    year.write_text(
        f'2000-01-01 open {PLAN} INR\n  label: "old"\n\n' + year.read_text()
    )
    assert led.validate() == []

    moved = led.move_postings(_plan("t1", "t2"), PARENT, META)

    assert moved == 2
    assert led.validate() == []
    assert open_meta(led)[PLAN]["emi-ref"] == "555000111"
    assert sorted(r.narration for r in all_postings(led) if r.account == PLAN) == [
        "EMI CONVERSION",
        "EMI PRIN 1",
    ]
