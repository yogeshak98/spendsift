"""`set_open_meta` and `move_postings` under the journal write lock and the
load cache (synthetic rows only).

Both act on line numbers from a fresh parse and rewrite whole files, so a
concurrent write (an import's append, the background price refresh) must wait
for them -- otherwise a failing one restores a snapshot taken before the other
write and erases it. And the next plain `load()` after either must see the
change, not the cached pre-write parse.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from beancount.core import data

from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.models.domain.nav import NavRow
from backend.app.persistence import journal as journal_mod
from backend.app.persistence.journal import Ledger
from backend.app.service.reports.health.ledger_view import (
    all_postings,
    open_meta_from_entries,
)
from tests.conftest import make_txn

PARENT = "Liabilities:Loan:CardEMI"
CARD = "Liabilities:CreditCard:Acme1234"
PLAN = f"{PARENT}:Acme1234-20250820"
PRICES = "prices-test.beancount"
_HOUR_NS = 3600 * 10**9


def _txn(d, narration, debit, credit, amount, txn_id=None):
    txn = make_txn(d, narration, debit, credit, amount)
    if txn_id is not None:
        txn.meta["txn-id"] = txn_id
    return txn


def _ledger(tmp_path) -> Ledger:
    led = Ledger(tmp_path)
    led.bootstrap()
    led.ensure_commodity_accounts([("MF100", "Fund")])
    led.open_named_accounts([PARENT, CARD])
    led.append(
        [
            _txn(date(2025, 8, 20), "EMI CONVERSION", CARD, PARENT, "900", "t1"),
            _txn(date(2025, 9, 20), "EMI PRIN 1", PARENT, CARD, "450", "t2"),
        ]
    )
    return led


def _price(nav: str) -> dict[str, NavRow]:
    return {"MF100": NavRow(scheme_code="100", nav=Decimal(nav), date=date(2026, 4, 1))}


def _narrations(led: Ledger) -> list[str]:
    return sorted(
        e.narration
        for e in led.load(fresh=True).entries
        if isinstance(e, data.Transaction)
    )


def _row(i: int) -> data.Transaction:
    return _txn(
        date(2026, 4, 1 + i), f"Row {i}", "Expenses:Food", "Assets:Bank:HDFC", "1"
    )


def _race_a_failing_write(
    led: Ledger,
    monkeypatch,
    failing: Callable[[], None],
    other: Callable[[], None],
) -> list[BaseException]:
    """Run `failing` (forced to fail validation after it snapshots) on one
    thread and `other` on another, started once `failing` has snapshotted.
    Without the write lock `other` would land between the snapshot and the
    restore and be erased by it; with the lock it waits."""
    snapped = threading.Event()
    other_done = threading.Event()
    real_snapshot = Ledger.snapshot
    real_validate = Ledger._validate_after_write

    def snapshot(self):
        path = real_snapshot(self)
        if threading.current_thread().name == "failing":
            snapped.set()
            other_done.wait(timeout=1.0)  # times out while the lock holds
        return path

    def validate(self):
        if threading.current_thread().name == "failing":
            return ["forced failure"]
        return real_validate(self)

    monkeypatch.setattr(Ledger, "snapshot", snapshot)
    monkeypatch.setattr(Ledger, "_validate_after_write", validate)
    failures: list[BaseException] = []

    def run_failing() -> None:
        try:
            failing()
        except LedgerValidationError:
            pass
        except BaseException as exc:
            failures.append(exc)

    def run_other() -> None:
        try:
            snapped.wait(timeout=5)
            other()
            other_done.set()
        except BaseException as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=run_failing, name="failing"),
        threading.Thread(target=run_other, name="other"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    monkeypatch.undo()
    # The race really ran: the failing write snapshotted, and both threads
    # finished (neither is stuck holding or waiting on the lock).
    assert snapped.is_set(), "the failing write never reached its snapshot"
    assert [t.name for t in threads if t.is_alive()] == []
    return failures


# --- set_open_meta vs append -------------------------------------------------


def test_a_failing_set_open_meta_cannot_roll_back_a_concurrent_append(
    tmp_path, monkeypatch
):
    led = _ledger(tmp_path)
    failures = _race_a_failing_write(
        led,
        monkeypatch,
        lambda: led.set_open_meta(CARD, {"statement-day": 15}),
        lambda: led.append([_row(0)]),
    )
    assert failures == []
    assert "Row 0" in _narrations(led)
    assert open_meta_from_entries(led.load(fresh=True).entries)[CARD] == {}
    assert led.validate() == []


def test_concurrent_set_open_meta_and_append_lose_nothing(tmp_path):
    led = _ledger(tmp_path)
    failures: list[BaseException] = []

    def meta_writer() -> None:
        try:
            for day in range(1, 11):
                led.set_open_meta(CARD, {"statement-day": day, "due-day": day + 1})
        except BaseException as exc:
            failures.append(exc)

    def appender() -> None:
        try:
            for i in range(10):
                led.append([_row(i)])
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=meta_writer), threading.Thread(target=appender)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert failures == []
    result = led.load(fresh=True)
    assert result.errors == []
    rows = {f"Row {i}" for i in range(10)}
    assert rows <= set(_narrations(led))
    meta = open_meta_from_entries(result.entries)[CARD]
    assert meta == {"statement-day": 10, "due-day": 11}
    # Every append opened its accounts in main; the meta rewrites of main kept them.
    opens = {e.account for e in result.entries if isinstance(e, data.Open)}
    assert {"Expenses:Food", "Assets:Bank:HDFC"} <= opens


# --- move_postings vs write_prices_file ---------------------------------------


def test_a_failing_move_postings_cannot_roll_back_a_concurrent_price_write(
    tmp_path, monkeypatch
):
    led = _ledger(tmp_path)
    failures = _race_a_failing_write(
        led,
        monkeypatch,
        lambda: led.move_postings({"t1": PLAN, "t2": PLAN}, PARENT, {}),
        lambda: led.write_prices_file(PRICES, _price("12.5")),
    )
    assert failures == []
    assert "price MF100 12.5 INR" in (led.journal_dir / PRICES).read_text()
    assert f'include "{PRICES}"' in led.main_file.read_text()
    entries = led.load(fresh=True).entries
    assert any(isinstance(e, data.Price) for e in entries)
    assert PLAN not in open_meta_from_entries(entries)  # the move rolled back
    assert led.validate() == []


def test_move_postings_racing_a_price_write_keeps_both(tmp_path):
    led = _ledger(tmp_path)
    failures: list[BaseException] = []
    start = threading.Barrier(2)

    def mover() -> None:
        try:
            start.wait(timeout=5)
            led.move_postings(
                {"t1": PLAN, "t2": PLAN}, PARENT, {PLAN: {"emi-card": CARD}}
            )
        except BaseException as exc:
            failures.append(exc)

    def refresher() -> None:
        try:
            start.wait(timeout=5)
            for i in range(10):
                led.write_prices_file(PRICES, _price(f"1{i}"))
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=mover), threading.Thread(target=refresher)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert failures == []
    result = led.load(fresh=True)
    assert result.errors == []
    assert open_meta_from_entries(result.entries)[PLAN] == {"emi-card": CARD}
    accounts = {
        e.meta["txn-id"]: sorted(p.account for p in e.postings)
        for e in result.entries
        if isinstance(e, data.Transaction)
    }
    assert accounts == {"t1": sorted([CARD, PLAN]), "t2": sorted([CARD, PLAN])}
    assert len([r for r in all_postings(led) if r.account == PLAN]) == 2
    assert [r for r in all_postings(led) if r.account == PARENT] == []
    assert f'include "{PRICES}"' in led.main_file.read_text()
    prices = [e for e in result.entries if isinstance(e, data.Price)]
    assert [p.amount.number for p in prices] == [Decimal("19")]


# --- cache invalidation --------------------------------------------------------


def _prime_cache(led: Ledger, monkeypatch) -> list[str]:
    """Backdate the journal so a parse is cached, prime it, and return the
    list every later real parse is recorded in (empty: the prime was hit)."""
    past = time.time_ns() - _HOUR_NS
    for path in led.journal_dir.glob("*.beancount"):
        os.utime(path, ns=(past, past))
    led.load(fresh=True)
    calls: list[str] = []
    real = journal_mod._parse_uncached

    def counting(filename, *args, **kwargs):
        calls.append(filename)
        return real(filename, *args, **kwargs)

    monkeypatch.setattr(journal_mod, "_parse_uncached", counting)
    led.load()
    assert calls == [], "the primed parse was not served from the cache"
    return calls


def _assert_next_load_reparses(led: Ledger, calls: list[str]) -> None:
    """The write parsed fresh (its validation), and the next plain load()
    parsed again rather than serving the primed pre-write cache: the files
    were just written, so no parse of them is trusted yet."""
    after_write = len(calls)
    assert after_write >= 1, "the write did not validate against a fresh parse"
    led.load()
    assert len(calls) == after_write + 1, "load() served a cached parse"


def test_plain_load_sees_set_open_meta(tmp_path, monkeypatch):
    led = _ledger(tmp_path)
    calls = _prime_cache(led, monkeypatch)
    invalidated: list[str] = []
    real_invalidate = journal_mod._invalidate_load_cache
    monkeypatch.setattr(
        journal_mod,
        "_invalidate_load_cache",
        lambda key: (invalidated.append(key), real_invalidate(key))[1],
    )

    led.set_open_meta(CARD, {"statement-day": 15})

    assert led._cache_key in invalidated  # snapshot() dropped the cached parse
    _assert_next_load_reparses(led, calls)
    assert open_meta_from_entries(led.load().entries)[CARD] == {"statement-day": 15}


def test_plain_load_sees_move_postings(tmp_path, monkeypatch):
    led = _ledger(tmp_path)
    calls = _prime_cache(led, monkeypatch)
    assert PLAN not in open_meta_from_entries(led.load().entries)
    assert calls == []

    led.move_postings(
        {"t1": PLAN, "t2": PLAN},
        PARENT,
        {PLAN: {"emi-card": CARD}},
        close=[PLAN],
        close_on=date(2026, 11, 1),
    )

    _assert_next_load_reparses(led, calls)
    entries = led.load().entries
    assert open_meta_from_entries(entries)[PLAN] == {"emi-card": CARD}
    assert any(isinstance(e, data.Close) and e.account == PLAN for e in entries)
    moved = [
        p.account
        for e in entries
        if isinstance(e, data.Transaction)
        for p in e.postings
        if p.account.startswith(PARENT)
    ]
    assert moved == [PLAN, PLAN]


# --- set_open_meta_many vs append ---------------------------------------------


def test_a_failing_set_open_meta_many_cannot_roll_back_a_concurrent_append(
    tmp_path, monkeypatch
):
    led = _ledger(tmp_path)
    failures = _race_a_failing_write(
        led,
        monkeypatch,
        lambda: led.set_open_meta_many(
            {CARD: {"statement-day": 15}, PARENT: {"label": "Pool"}}
        ),
        lambda: led.append([_row(0)]),
    )
    assert failures == []
    assert "Row 0" in _narrations(led)
    meta = open_meta_from_entries(led.load(fresh=True).entries)
    assert (meta[CARD], meta[PARENT]) == ({}, {})
    assert led.validate() == []
