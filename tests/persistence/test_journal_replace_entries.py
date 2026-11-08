"""Ledger.replace_entries rewrites directives in place under one snapshot,
and Ledger.restore puts a snapshot back. Every refusal or failure leaves
every journal file byte-identical."""

from __future__ import annotations

import hashlib
import os
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import data
from beancount.core.amount import Amount

from backend.app.core.errors.errors import CodedError
from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.models.domain.accounts import EQUITY_OPENING
from backend.app.persistence import journal as journal_mod
from backend.app.persistence.journal import Ledger
from tests.conftest import make_txn

BANK = "Assets:Bank:Alpha"
FOOD = "Expenses:Food"

# Hand-written, as an older journal holds it: no txn-id, four-space
# indentation, aligned amounts, an elided leg, and a paired balance.
LEGACY_2025 = f"""\
2025-03-31 * "Opening balance"
    {BANK}          1000.00 INR
    {EQUITY_OPENING}

2025-04-01 balance {BANK}  1000.00 INR

2025-04-02 * "Coffee"
  txn-id: "t-coffee"
  {FOOD}   50.00 INR
  {BANK}  -50.00 INR
"""


def _hashes(ledger: Ledger) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(ledger.journal_dir.iterdir())
    }


def _snapshot_count(ledger: Ledger) -> int:
    return len(list(ledger.snapshots_dir.iterdir()))


def _year_text(ledger: Ledger, year: int) -> str:
    return (ledger.journal_dir / f"{year}.beancount").read_text(encoding="utf-8")


def _write_legacy(ledger: Ledger, text: str = LEGACY_2025) -> None:
    ledger.open_named_accounts([BANK, FOOD, EQUITY_OPENING])
    (ledger.journal_dir / "2025.beancount").write_text(text, encoding="utf-8")
    main = ledger.main_file.read_text(encoding="utf-8")
    ledger.main_file.write_text(main + 'include "2025.beancount"\n', encoding="utf-8")
    assert ledger.validate() == []


def _txn(ledger: Ledger, narration: str) -> data.Transaction:
    return next(
        e
        for e in ledger.load(fresh=True).entries
        if isinstance(e, data.Transaction) and e.narration == narration
    )


def _balance(ledger: Ledger) -> data.Balance:
    return next(
        e for e in ledger.load(fresh=True).entries if isinstance(e, data.Balance)
    )


def _with_amount(
    txn: data.Transaction, amount: str, *, on: date | None = None
) -> data.Transaction:
    """`txn` (a two-posting opening) with its first posting at `amount` and
    the second balancing it."""
    first, second = txn.postings
    value = Decimal(amount)
    return txn._replace(
        date=on or txn.date,
        postings=[
            first._replace(units=Amount(value, "INR")),
            second._replace(units=Amount(-value, "INR")),
        ],
    )


def _bank_amounts(ledger: Ledger) -> list[Decimal]:
    return [
        p.units.number
        for e in ledger.load().entries
        if isinstance(e, data.Transaction) and e.narration == "Opening balance"
        for p in e.postings
        if p.account == BANK
    ]


def test_replace_single_entry_in_place(ledger):
    ledger.open_named_accounts([BANK, FOOD, EQUITY_OPENING])
    ledger.append(
        [
            make_txn(date(2025, 3, 1), "Before", FOOD, BANK, "10.00"),
            make_txn(
                date(2025, 3, 31), "Opening balance", BANK, EQUITY_OPENING, "1000.00"
            ),
            make_txn(date(2025, 4, 2), "After", FOOD, BANK, "20.00"),
        ]
    )
    old = _txn(ledger, "Opening balance")
    before = _year_text(ledger, 2025)
    lines = before.splitlines(keepends=True)
    start = old.meta["lineno"] - 1
    end = start + 3  # header + two postings
    assert "Opening balance" in lines[start] and not lines[end].strip()
    count = _snapshot_count(ledger)

    snapshot = ledger.replace_entries([(old, [_with_amount(old, "1234.56")])])

    after = _year_text(ledger, 2025)
    assert "1234.56 INR" in after
    assert "1000.00 INR" not in after
    # Every byte outside the replaced block is as it was.
    head, tail = "".join(lines[:start]), "".join(lines[end:])
    assert after.startswith(head) and after.endswith(tail)
    assert ledger.validate() == []
    assert _snapshot_count(ledger) == count + 1
    assert snapshot.parent == ledger.snapshots_dir


def test_replace_entry_without_txn_id(ledger):
    _write_legacy(ledger)
    old = _txn(ledger, "Opening balance")
    assert "txn-id" not in old.meta
    balance = _balance(ledger)

    ledger.replace_entries(
        [
            (old, [_with_amount(old, "1500.00")]),
            (balance, [balance._replace(amount=Amount(Decimal("1500.00"), "INR"))]),
        ]
    )

    after = _year_text(ledger, 2025)
    assert "    " + BANK not in after  # the four-space legacy block is gone
    assert "1500.00 INR" in after and "1000.00 INR" not in after
    assert after.endswith(LEGACY_2025[LEGACY_2025.index('2025-04-02 * "Coffee"') :])
    assert _bank_amounts(ledger) == [Decimal("1500.00")]
    assert ledger.validate() == []


def test_replace_refuses_block_that_does_not_parse_back(ledger):
    _write_legacy(ledger)
    old = _txn(ledger, "Opening balance")
    year_file = ledger.journal_dir / "2025.beancount"
    year_file.write_text(
        year_file.read_text(encoding="utf-8")
        .replace(f"{BANK}          1000.00 INR", f"{BANK}          1200.00 INR")
        .replace("balance " + BANK + "  1000.00", "balance " + BANK + "  1200.00"),
        encoding="utf-8",
    )
    hashes, count = _hashes(ledger), _snapshot_count(ledger)

    with pytest.raises(CodedError) as caught:
        ledger.replace_entries([(old, [_with_amount(old, "1200.00")])])

    assert caught.value.code == "E1047"
    assert _hashes(ledger) == hashes
    assert _snapshot_count(ledger) == count


def test_replace_refuses_an_entry_with_no_source_line(ledger):
    _write_legacy(ledger)
    stray = make_txn(
        date(2025, 3, 31), "Opening balance", BANK, EQUITY_OPENING, "1000.00"
    )
    hashes, count = _hashes(ledger), _snapshot_count(ledger)

    with pytest.raises(CodedError) as caught:
        ledger.replace_entries([(stray, [])])

    assert caught.value.code == "E1047"
    assert _hashes(ledger) == hashes
    assert _snapshot_count(ledger) == count


def test_replace_with_empty_deletes_and_append_adds(ledger):
    _write_legacy(ledger)
    old = _txn(ledger, "Coffee")
    new = make_txn(date(2025, 4, 3), "Tea", FOOD, BANK, "30.00")
    count = _snapshot_count(ledger)

    ledger.replace_entries([(old, [])], append=[new])

    after = _year_text(ledger, 2025)
    assert "Coffee" not in after and "t-coffee" not in after
    assert '"Tea"' in after
    assert after.startswith(LEGACY_2025[: LEGACY_2025.index('2025-04-02 * "Coffee"')])
    assert _snapshot_count(ledger) == count + 1
    assert ledger.validate() == []


def test_replace_moves_entry_across_years(ledger):
    _write_legacy(ledger)
    old = _txn(ledger, "Opening balance")
    moved = _with_amount(old, "1000.00", on=date(2024, 12, 31))

    ledger.replace_entries([(old, [moved])])

    assert "Opening balance" not in _year_text(ledger, 2025)
    assert "Opening balance" in _year_text(ledger, 2024)
    assert 'include "2024.beancount"' in ledger.main_file.read_text(encoding="utf-8")
    assert [
        t.date
        for t in ledger.load().entries
        if getattr(t, "narration", "") == "Opening balance"
    ] == [date(2024, 12, 31)]
    assert ledger.validate() == []


def test_replace_rolls_back_on_validation_error(ledger):
    _write_legacy(ledger)
    old = _txn(ledger, "Opening balance")
    first, second = old.postings
    unbalanced = old._replace(
        postings=[
            first._replace(units=Amount(Decimal("1000.00"), "INR")),
            second._replace(units=Amount(Decimal("-900.00"), "INR")),
        ]
    )
    hashes = _hashes(ledger)

    with pytest.raises(LedgerValidationError):
        ledger.replace_entries([(old, [unbalanced])])

    assert _hashes(ledger) == hashes
    assert ledger.validate() == []


def test_load_after_replace_shows_new_amount(ledger):
    """A primed cache never serves the pre-write parse. (The final cache
    invalidation itself is defence in depth and deliberately unpinned.)"""
    _write_legacy(ledger)
    past = time.time_ns() - 3600 * 10**9
    for path in ledger.journal_dir.glob("*.beancount"):
        os.utime(path, ns=(past, past))
    old = _txn(ledger, "Opening balance")
    balance = _balance(ledger)
    assert _bank_amounts(ledger) == [Decimal("1000.00")]  # cache primed

    ledger.replace_entries(
        [
            (old, [_with_amount(old, "777.00")]),
            (balance, [balance._replace(amount=Amount(Decimal("777.00"), "INR"))]),
        ]
    )

    assert _bank_amounts(ledger) == [Decimal("777.00")]


def test_restore_public_reverts_and_invalidates(ledger):
    ledger.open_named_accounts([BANK, FOOD])
    ledger.append([make_txn(date(2025, 4, 1), "Kept", FOOD, BANK, "5.00")])
    ledger.load()  # prime the cache
    snapshot = ledger.append([make_txn(date(2025, 4, 2), "Undone", FOOD, BANK, "7.00")])
    assert "Undone" in {getattr(e, "narration", None) for e in ledger.load().entries}
    assert snapshot is not None

    ledger.restore(snapshot)

    narrations = {getattr(e, "narration", None) for e in ledger.load().entries}
    assert "Kept" in narrations and "Undone" not in narrations
    assert ledger.validate() == []


def test_restore_refuses_a_path_that_is_not_a_snapshot(ledger, tmp_path: Path):
    hashes = _hashes(ledger)
    with pytest.raises(ValueError):
        ledger.restore(tmp_path / "nowhere")
    assert _hashes(ledger) == hashes


def test_replace_balance_directive(ledger):
    _write_legacy(ledger)
    old = _txn(ledger, "Opening balance")
    balance = _balance(ledger)
    count = _snapshot_count(ledger)

    ledger.replace_entries(
        [
            (old, [_with_amount(old, "2500.00")]),
            (balance, [balance._replace(amount=Amount(Decimal("2500.00"), "INR"))]),
        ]
    )

    after = _year_text(ledger, 2025)
    assert f"2025-04-01 balance {BANK}" in after
    assert "1000.00 INR" not in after
    assert [
        b.amount.number for b in ledger.load().entries if isinstance(b, data.Balance)
    ] == [Decimal("2500.00")]
    assert ledger.validate() == []
    assert _snapshot_count(ledger) == count + 1


def test_replacing_only_the_transaction_breaks_its_balance_and_rolls_back(ledger):
    _write_legacy(ledger)
    old = _txn(ledger, "Opening balance")
    hashes = _hashes(ledger)

    with pytest.raises(LedgerValidationError):
        ledger.replace_entries([(old, [_with_amount(old, "2500.00")])])

    assert _hashes(ledger) == hashes


def test_replace_keeps_a_crlf_file_crlf(ledger):
    ledger.open_named_accounts([BANK, FOOD, EQUITY_OPENING])
    year_file = ledger.journal_dir / "2025.beancount"
    year_file.write_bytes(LEGACY_2025.replace("\n", "\r\n").encode("utf-8"))
    main = ledger.main_file.read_text(encoding="utf-8")
    ledger.main_file.write_text(main + 'include "2025.beancount"\n', encoding="utf-8")
    old = _txn(ledger, "Opening balance")
    balance = _balance(ledger)

    ledger.replace_entries(
        [
            (old, [_with_amount(old, "300.00")]),
            (balance, [balance._replace(amount=Amount(Decimal("300.00"), "INR"))]),
        ]
    )

    raw = year_file.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert _bank_amounts(ledger) == [Decimal("300.00")]
    assert ledger.validate() == []


@pytest.mark.parametrize("cost", ["{100.00 INR}", "{{1000.00 INR}}"])
def test_replace_locates_a_block_written_with_a_cost(ledger, cost):
    lots = "Assets:Investment:Fund:TESTFUND"
    ledger.open_named_accounts([EQUITY_OPENING])
    main = ledger.main_file.read_text(encoding="utf-8")
    ledger.main_file.write_text(
        main
        + "2000-01-01 commodity TESTFUND\n"
        + f'2000-01-01 open {lots} "FIFO"\n'
        + 'include "2025.beancount"\n',
        encoding="utf-8",
    )
    (ledger.journal_dir / "2025.beancount").write_text(
        f'2025-03-31 * "Fund opening"\n'
        f"  {lots}  10 TESTFUND {cost}\n"
        f"  {EQUITY_OPENING}\n",
        encoding="utf-8",
    )
    assert ledger.validate() == []
    old = _txn(ledger, "Fund opening")

    ledger.replace_entries([(old, [old._replace(narration="Fund opening (fixed)")])])

    assert "Fund opening (fixed)" in _year_text(ledger, 2025)
    assert ledger.validate() == []


TWINS_2025 = f"""\
2025-04-01 * "Other"
  txn-id: "t0"
  {FOOD}   5.00 INR
  {BANK}  -5.00 INR

2025-04-02 * "Coffee"
  txn-id: "t1"
  {FOOD}   50.00 INR
  {BANK}  -50.00 INR

2025-04-02 * "Coffee"
  txn-id: "t2"
  {FOOD}   50.00 INR
  {BANK}  -50.00 INR
"""


def _by_txn_id(ledger: Ledger, txn_id: str) -> data.Transaction:
    return next(
        e
        for e in ledger.load(fresh=True).entries
        if isinstance(e, data.Transaction) and e.meta.get("txn-id") == txn_id
    )


def test_replace_refuses_a_stale_line_landing_on_a_twin(ledger):
    """Two entries alike in all but txn-id: a deletion above them makes t1's
    recorded line point at t2, which must not be rewritten in t1's place."""
    _write_legacy(ledger, TWINS_2025)
    other, stale_t1 = _by_txn_id(ledger, "t0"), _by_txn_id(ledger, "t1")
    ledger.replace_entries([(other, [])])
    assert _by_txn_id(ledger, "t2").meta["lineno"] == stale_t1.meta["lineno"]
    hashes, count = _hashes(ledger), _snapshot_count(ledger)

    with pytest.raises(CodedError) as caught:
        ledger.replace_entries([(stale_t1, [_with_amount(stale_t1, "60.00")])])

    assert caught.value.code == "E1047"
    assert _hashes(ledger) == hashes
    assert _snapshot_count(ledger) == count


def _write_split(ledger: Ledger) -> None:
    """The opening in 2025.beancount, its balance in main.beancount."""
    ledger.open_named_accounts([BANK, FOOD, EQUITY_OPENING])
    (ledger.journal_dir / "2025.beancount").write_text(
        LEGACY_2025.replace(f"2025-04-01 balance {BANK}  1000.00 INR\n\n", ""),
        encoding="utf-8",
    )
    main = ledger.main_file.read_text(encoding="utf-8")
    ledger.main_file.write_text(
        main
        + 'include "2025.beancount"\n'
        + f"\n2025-04-01 balance {BANK}  1000.00 INR\n",
        encoding="utf-8",
    )
    assert ledger.validate() == []
    assert Path(_balance(ledger).meta["filename"]).name == "main.beancount"


def test_replace_spans_two_files_in_one_call(ledger):
    _write_split(ledger)
    old, balance = _txn(ledger, "Opening balance"), _balance(ledger)
    count = _snapshot_count(ledger)

    ledger.replace_entries(
        [
            (old, [_with_amount(old, "1800.00")]),
            (balance, [balance._replace(amount=Amount(Decimal("1800.00"), "INR"))]),
        ]
    )

    main = ledger.main_file.read_text(encoding="utf-8")
    assert f"2025-04-01 balance {BANK}" in main and "1800.00 INR" in main
    assert "1000.00" not in main
    assert "1800.00 INR" in _year_text(ledger, 2025)
    assert "1000.00" not in _year_text(ledger, 2025)
    assert _snapshot_count(ledger) == count + 1
    assert ledger.validate() == []


def test_replace_restores_when_a_later_file_write_fails(ledger, monkeypatch):
    _write_split(ledger)
    old, balance = _txn(ledger, "Opening balance"), _balance(ledger)
    hashes = _hashes(ledger)
    real = journal_mod.replace_atomic
    calls: list[Path] = []

    def second_write_fails(path, write):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(path, write)

    monkeypatch.setattr(journal_mod, "replace_atomic", second_write_fails)

    with pytest.raises(OSError, match="disk full"):
        ledger.replace_entries(
            [
                (old, [_with_amount(old, "1800.00")]),
                (balance, [balance._replace(amount=Amount(Decimal("1800.00"), "INR"))]),
            ]
        )

    assert len(calls) == 2  # the first file had been rewritten
    assert _hashes(ledger) == hashes
    assert ledger.validate() == []


def test_restore_returns_a_snapshot_that_undoes_it(ledger):
    ledger.open_named_accounts([BANK, FOOD])
    snapshot = ledger.append([make_txn(date(2025, 4, 2), "Undone", FOOD, BANK, "7.00")])
    assert snapshot is not None
    after_append = _hashes(ledger)

    undo = ledger.restore(snapshot)
    assert "Undone" not in {
        getattr(e, "narration", None) for e in ledger.load().entries
    }

    ledger.restore(undo)
    assert _hashes(ledger) == after_append


# A legacy opening with no txn-id: the identity check cannot lean on one, so
# every other field it says must match before a block is taken for it.
_LEGACY_SHAPE = f"""\
2025-03-31 * "Bank" "Initial Balance" #setup
  {BANK}          1000.00 INR @ 1 INR
  {EQUITY_OPENING}  -1000.00 INR
"""


@pytest.mark.parametrize(
    ("written", "same"),
    [
        (_LEGACY_SHAPE, True),
        # Elided equity leg: the same entry as hand-written.
        (_LEGACY_SHAPE.replace("  -1000.00 INR", ""), True),
        (_LEGACY_SHAPE.replace('"Bank"', '"Other Bank"'), False),
        (_LEGACY_SHAPE.replace(' "Bank"', ""), False),
        (_LEGACY_SHAPE.replace(" @ 1 INR", ""), False),
        (_LEGACY_SHAPE.replace("#setup", "#other"), False),
        (_LEGACY_SHAPE.replace(" #setup", ""), False),
        (_LEGACY_SHAPE.replace(" * ", " ! "), False),
        (_LEGACY_SHAPE.replace('"Initial Balance"', '"Opening"'), False),
    ],
    ids=[
        "identical",
        "elided-leg",
        "payee-differs",
        "payee-missing",
        "price-vs-no-price",
        "tag-differs",
        "tag-missing",
        "flag-differs",
        "narration-differs",
    ],
)
def test_same_directive_without_txn_id_compares_every_field(written, same):
    [loaded] = [e for e in _load_text(_LEGACY_SHAPE) if isinstance(e, data.Transaction)]
    assert "txn-id" not in loaded.meta
    assert (
        journal_mod._same_directive(journal_mod._parse_directive(written), loaded)
        is same
    )


def _load_text(text: str) -> list:
    """The booked entries of `text` with BANK and EQUITY_OPENING open, as a
    load hands them to replace_entries."""
    from beancount import loader

    entries, errors, _ = loader.load_string(
        f"2020-01-01 open {BANK}\n2020-01-01 open {EQUITY_OPENING}\n" + text
    )
    assert errors == []
    return entries
