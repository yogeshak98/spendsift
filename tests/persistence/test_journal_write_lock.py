"""Ledger writes are serialised per journal folder (review C2).

The background price refresh writes the journal (write_prices_file) while an
import may be committing (append). Each write snapshots, writes, validates and
restores its snapshot on failure -- so without a lock a failing write could
restore a snapshot taken BEFORE a concurrent append and silently erase it.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

from beancount.core import data

from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.models.domain.nav import NavRow
from backend.app.persistence.journal import Ledger, hold_write_locks
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from tests.conftest import make_txn


def test_one_lock_per_journal_folder(tmp_path, ledger, investment_ledger):
    assert ledger.write_lock() is investment_ledger.write_lock()
    assert ledger.write_lock() is Ledger(ledger.storage_dir).write_lock()
    other = Ledger(tmp_path / "other")
    other.bootstrap()
    assert other.write_lock() is not ledger.write_lock()
    link = tmp_path / "link"
    link.symlink_to(ledger.storage_dir, target_is_directory=True)
    assert Ledger(link).write_lock() is ledger.write_lock()


def test_a_writer_waits_while_another_holds_the_lock(ledger):
    finished = threading.Event()

    def refresh() -> None:
        ledger.write_prices_file("prices-test.beancount", {})
        finished.set()

    with ledger.write_lock():
        worker = threading.Thread(target=refresh)
        worker.start()
        assert not finished.wait(timeout=0.3), "write ran while the lock was held"
    worker.join(timeout=10)
    assert finished.is_set()


def test_a_failing_refresh_cannot_roll_back_a_concurrent_append(ledger, monkeypatch):
    """Deterministic version of the race: the refresh snapshots, then (without
    a lock) an import appends, then the refresh fails validation and restores
    its snapshot -- erasing the import. With the lock the append waits."""
    refresh_snapped = threading.Event()
    append_done = threading.Event()
    real_snapshot = Ledger.snapshot
    real_validate = Ledger._validate_after_write

    def snapshot(self):
        path = real_snapshot(self)
        if threading.current_thread().name == "refresh":
            refresh_snapped.set()
            append_done.wait(timeout=1.0)  # times out when the lock holds
        return path

    def validate(self):
        if threading.current_thread().name == "refresh":
            return ["forced refresh failure"]
        return real_validate(self)

    monkeypatch.setattr(Ledger, "snapshot", snapshot)
    monkeypatch.setattr(Ledger, "_validate_after_write", validate)

    failures: list[BaseException] = []

    def refresh() -> None:
        try:
            ledger.write_prices_file("prices-test.beancount", {})
        except LedgerValidationError:
            pass
        except BaseException as exc:
            failures.append(exc)

    def importer() -> None:
        try:
            refresh_snapped.wait(timeout=5)
            ledger.open_named_accounts(["Expenses:Imported"])
            ledger.append(
                [
                    make_txn(
                        date(2026, 4, 1),
                        "Imported row",
                        "Expenses:Imported",
                        "Assets:Bank:HDFC",
                        "9",
                    )
                ]
            )
            append_done.set()
        except BaseException as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=refresh, name="refresh"),
        threading.Thread(target=importer, name="import"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert failures == []
    result = ledger.load(fresh=True)
    narrations = [
        e.narration for e in result.entries if isinstance(e, data.Transaction)
    ]
    opens = {e.account for e in result.entries if isinstance(e, data.Open)}
    assert narrations == ["Imported row"]
    assert "Expenses:Imported" in opens


def test_concurrent_refreshes_and_appends_lose_nothing(ledger):
    ledger.ensure_commodity_accounts([("MF100", "Fund")])
    row = NavRow(scheme_code="100", nav=Decimal("10"), date=date(2026, 4, 1))
    failures: list[BaseException] = []

    def refresher() -> None:
        try:
            for i in range(10):
                nav = row.nav + i
                ledger.write_prices_file(
                    "prices-test.beancount",
                    {"MF100": NavRow(scheme_code="100", nav=nav, date=row.date)},
                )
        except BaseException as exc:
            failures.append(exc)

    def appender() -> None:
        try:
            for i in range(10):
                ledger.append(
                    [
                        make_txn(
                            date(2026, 4, 1 + i),
                            f"Row {i}",
                            "Expenses:Food",
                            "Assets:Bank:HDFC",
                            "1",
                        )
                    ]
                )
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=refresher), threading.Thread(target=appender)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert failures == []
    result = ledger.load(fresh=True)
    assert result.errors == []
    narrations = sorted(
        e.narration for e in result.entries if isinstance(e, data.Transaction)
    )
    assert narrations == sorted(f"Row {i}" for i in range(10))


def test_hold_write_locks_takes_each_folder_once(tmp_path, ledger, investment_ledger):
    other = Ledger(tmp_path / "other")
    other.bootstrap()
    with hold_write_locks(investment_ledger, ledger, other):
        probe_results: list[bool] = []

        def probe() -> None:
            for lock in (ledger.write_lock(), other.write_lock()):
                got = lock.acquire(blocking=False)
                probe_results.append(got)
                if got:
                    lock.release()

        t = threading.Thread(target=probe)
        t.start()
        t.join(timeout=5)
        assert probe_results == [False, False]
    assert ledger.write_lock().acquire(blocking=False)
    ledger.write_lock().release()


def test_commit_candidates_holds_the_lock_across_its_writes(
    ledger, investment_ledger, monkeypatch
):
    held_by_other_thread: list[bool] = []
    real_append = Ledger.append

    def append(self, entries):
        def probe() -> None:
            got = self.write_lock().acquire(blocking=False)
            held_by_other_thread.append(not got)
            if got:
                self.write_lock().release()

        t = threading.Thread(target=probe)
        t.start()
        t.join(timeout=5)
        return real_append(self, entries)

    monkeypatch.setattr(Ledger, "append", append)
    candidate = CandidateTxn(
        date=date(2026, 4, 3),
        amount=Decimal("99.00"),
        raw_narration="Committed via the pipeline",
        normalized_narration="COMMITTED VIA THE PIPELINE",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
        suggested_other_leg="Expenses:Food",
    )
    commit_candidates(ledger, investment_ledger, [candidate])
    assert held_by_other_thread == [True]
