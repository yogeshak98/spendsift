"""Ledger.load() caches the parsed journal; it must never serve stale data.

The cache is reused only while every file the parse read is unchanged, and every
write through Ledger drops it. Each mutating method is checked below: after the
write, load() must return the new data, not the cached pre-write parse.
"""

from __future__ import annotations

import functools
import hashlib
import os
import shutil
import threading
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader
from beancount.core import data

from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.models.domain.nav import NavRow
from backend.app.persistence import journal as journal_mod
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from tests.conftest import make_txn

_HOUR_NS = 3600 * 10**9


def _age_journal(ledger: Ledger) -> None:
    """Backdate every journal file so a first parse counts as stable."""
    past = time.time_ns() - _HOUR_NS
    for path in ledger.journal_dir.glob("*.beancount"):
        os.utime(path, ns=(past, past))


@pytest.fixture
def parses(monkeypatch) -> list[str]:
    """Count real beancount parses."""
    calls: list[str] = []
    real = journal_mod._parse_uncached

    def counting(filename, *args, **kwargs):
        calls.append(filename)
        return real(filename, *args, **kwargs)

    monkeypatch.setattr(journal_mod, "_parse_uncached", counting)
    return calls


def _narrations(ledger: Ledger) -> list[str]:
    return sorted(
        e.narration for e in ledger.load().entries if isinstance(e, data.Transaction)
    )


def _accounts(ledger: Ledger) -> set[str]:
    return {e.account for e in ledger.load().entries if isinstance(e, data.Open)}


def _seed(ledger: Ledger) -> None:
    ledger.append(
        [
            make_txn(
                date(2026, 4, 1), "Swiggy", "Expenses:Food", "Assets:Bank:HDFC", "10"
            )
        ]
    )
    _age_journal(ledger)
    ledger.load()  # prime the cache


# --- reuse --------------------------------------------------------------------


def test_repeat_loads_parse_once(ledger, parses):
    _seed(ledger)
    parses.clear()
    first = ledger.load()
    second = ledger.load()
    assert parses == []
    assert first.entries == second.entries


def test_returned_lists_are_private_copies(ledger):
    _seed(ledger)
    result = ledger.load()
    result.entries.clear()
    result.errors.append("caller scribble")
    result.options_map["title"] = "scribbled"
    again = ledger.load()
    assert again.entries
    assert "caller scribble" not in again.errors
    assert again.options_map["title"] != "scribbled"


def test_ledgers_on_the_same_file_share_one_parse(ledger, storage_dir, parses):
    _seed(ledger)
    parses.clear()
    Ledger(storage_dir).load()
    assert parses == []


# --- external edits -----------------------------------------------------------


def test_external_edit_changing_size_is_picked_up(ledger):
    _seed(ledger)
    year_file = ledger.journal_dir / "2026.beancount"
    text = year_file.read_text(encoding="utf-8")
    year_file.write_text(text.replace("Swiggy", "Swiggy Instamart"), encoding="utf-8")
    assert _narrations(ledger) == ["Swiggy Instamart"]


def test_external_edit_keeping_size_but_changing_mtime_is_picked_up(ledger):
    _seed(ledger)
    year_file = ledger.journal_dir / "2026.beancount"
    text = year_file.read_text(encoding="utf-8")
    year_file.write_text(text.replace("Swiggy", "Zomato"), encoding="utf-8")
    assert len("Swiggy") == len("Zomato")
    assert _narrations(ledger) == ["Zomato"]


def test_external_edit_to_main_adding_an_include_is_picked_up(ledger):
    _seed(ledger)
    extra = ledger.journal_dir / "extra.beancount"
    extra.write_text(
        '2026-05-01 * "Added outside the app"\n'
        "  Expenses:Food  5 INR\n  Assets:Bank:HDFC  -5 INR\n",
        encoding="utf-8",
    )
    with ledger.main_file.open("a", encoding="utf-8") as handle:
        handle.write('include "extra.beancount"\n')
    assert "Added outside the app" in _narrations(ledger)


def test_deleting_an_include_is_picked_up(ledger):
    _seed(ledger)
    (ledger.journal_dir / "2026.beancount").unlink()
    result = ledger.load()
    assert result.errors  # the include now points at a missing file
    assert _narrations(ledger) == []


# --- every write through Ledger -----------------------------------------------


def test_append_is_seen(ledger):
    _seed(ledger)
    ledger.append(
        [
            make_txn(
                date(2026, 4, 2), "Uber", "Expenses:Transport", "Assets:Bank:HDFC", "7"
            )
        ]
    )
    assert _narrations(ledger) == ["Swiggy", "Uber"]


def test_commit_is_seen(ledger, investment_ledger):
    _seed(ledger)
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
    assert "Committed via the pipeline" in _narrations(ledger)


def test_raw_write_is_seen(ledger):
    _seed(ledger)
    text, digest = ledger.read_year_text(2026)
    ledger.write_year_text(2026, text.replace("Swiggy", "Rewritten"), digest)
    assert _narrations(ledger) == ["Rewritten"]


def test_rename_account_is_seen(ledger):
    _seed(ledger)
    ledger.rename_account("Expenses:Food", "Expenses:Dining")
    assert "Expenses:Dining" in _accounts(ledger)
    assert "Expenses:Food" not in _accounts(ledger)


def test_open_named_accounts_is_seen(ledger):
    _seed(ledger)
    ledger.open_named_accounts(["Expenses:Subscriptions:Music"])
    assert "Expenses:Subscriptions:Music" in _accounts(ledger)


def test_close_account_is_seen(ledger):
    _seed(ledger)
    ledger.open_named_accounts(["Expenses:Old"])
    ledger.close_account("Expenses:Old", date(2026, 6, 1))
    closes = [e.account for e in ledger.load().entries if isinstance(e, data.Close)]
    assert closes == ["Expenses:Old"]


def test_recategorize_is_seen(ledger):
    _seed(ledger)
    tagged = make_txn(
        date(2026, 4, 2), "Tagged", "Expenses:Food", "Assets:Bank:HDFC", "3"
    )._replace(meta={"txn-id": "t-1"})
    ledger.append([tagged])
    ledger.load()  # cache the pre-recategorize parse
    ledger.recategorize(["t-1"], "Expenses:Food", "Expenses:Dining")
    accounts = {
        p.account
        for e in ledger.load().entries
        if isinstance(e, data.Transaction) and e.meta.get("txn-id") == "t-1"
        for p in e.postings
    }
    assert "Expenses:Dining" in accounts
    assert "Expenses:Food" not in accounts


def test_annotate_posting_is_seen(ledger, investment_ledger):
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
    ledger.load()  # cache the pre-annotation parse
    year_file = ledger.journal_dir / "2026.beancount"
    digest = hashlib.sha256(year_file.read_bytes()).hexdigest()
    ledger.annotate_posting(
        candidate.id, "Assets:Investments:PPFCF", {"units": "100.0"}, digest
    )
    metas = [
        p.meta
        for e in ledger.load().entries
        if isinstance(e, data.Transaction)
        for p in e.postings
        if p.account == "Assets:Investments:PPFCF"
    ]
    assert metas and metas[0].get("units") == "100.0"


def test_write_prices_file_is_seen(ledger):
    _seed(ledger)
    ledger.ensure_commodity_accounts([("MF100", "Fund")])
    row = NavRow(scheme_code="100", nav=Decimal("12.5"), date=date(2026, 4, 1))
    ledger.write_prices_file("prices-test.beancount", {"MF100": row})
    prices = [e for e in ledger.load().entries if isinstance(e, data.Price)]
    assert [(p.currency, p.amount.number) for p in prices] == [
        ("MF100", Decimal("12.5"))
    ]


def test_ensure_commodity_accounts_is_seen(ledger):
    _seed(ledger)
    ledger.ensure_commodity_accounts([("MF200", "Another Fund")])
    commodities = {
        e.currency for e in ledger.load().entries if isinstance(e, data.Commodity)
    }
    assert "MF200" in commodities


def test_failed_write_rolls_back_and_load_shows_the_restored_journal(ledger):
    _seed(ledger)
    text, digest = ledger.read_year_text(2026)
    unbalanced = text + (
        '\n2026-04-05 * "Broken"\n  Expenses:Food  5 INR\n  Assets:Bank:HDFC  -4 INR\n'
    )
    with pytest.raises(LedgerValidationError):
        ledger.write_year_text(2026, unbalanced, digest)
    assert _narrations(ledger) == ["Swiggy"]
    assert ledger.load().errors == []


def test_snapshot_restore_is_seen(ledger):
    _seed(ledger)
    snap = ledger.snapshot()
    ledger.append(
        [
            make_txn(
                date(2026, 4, 2), "Uber", "Expenses:Transport", "Assets:Bank:HDFC", "7"
            )
        ]
    )
    assert _narrations(ledger) == ["Swiggy", "Uber"]
    ledger._restore(snap)
    assert _narrations(ledger) == ["Swiggy"]


# --- concurrency --------------------------------------------------------------


def test_concurrent_readers_see_whole_states_while_a_writer_appends(ledger):
    _seed(ledger)
    valid_states = {("Swiggy",)}
    names: list[str] = ["Swiggy"]
    for i in range(5):
        names.append(f"Row {i}")
        valid_states.add(tuple(sorted(names)))

    seen: list[tuple[str, ...]] = []
    failures: list[BaseException] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            while not stop.is_set():
                seen.append(tuple(_narrations(ledger)))
        except BaseException as exc:  # surfaced below
            failures.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        for i in range(5):
            ledger.append(
                [
                    make_txn(
                        date(2026, 4, 10 + i),
                        f"Row {i}",
                        "Expenses:Food",
                        "Assets:Bank:HDFC",
                        "1",
                    )
                ]
            )
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=30)

    assert failures == []
    assert seen, "readers never ran"
    # Year-file appends are not atomic, so a reader racing a write may parse a
    # pre- or post-write state; what it must never get is a mixture, and the
    # cache must not keep serving an old state once the writer is done.
    assert set(seen) <= valid_states, set(seen) - valid_states
    assert tuple(_narrations(ledger)) == tuple(sorted(names))


def test_a_parse_racing_a_write_is_not_cached(ledger, monkeypatch):
    """If a file changes while it is being parsed, that parse must not be kept."""
    _seed(ledger)
    year_file = ledger.journal_dir / "2026.beancount"
    real = journal_mod._parse_uncached
    edited = False

    def parse_then_edit(filename, *args, **kwargs):
        nonlocal edited
        result = real(filename, *args, **kwargs)
        if not edited:
            edited = True
            text = year_file.read_text(encoding="utf-8")
            year_file.write_text(text.replace("Swiggy", "Changed"), encoding="utf-8")
        return result

    journal_mod._invalidate_load_cache(os.path.abspath(ledger.main_file))
    monkeypatch.setattr(journal_mod, "_parse_uncached", parse_then_edit)
    assert _narrations(ledger) == ["Swiggy"]  # the racing parse itself
    assert _narrations(ledger) == ["Changed"]  # ...was not cached


def test_cache_key_is_per_main_file(storage_dir, ledger, investment_ledger):
    _seed(ledger)
    assert investment_ledger.load().entries != ledger.load().entries
    assert Path(investment_ledger.main_file).name == "investments.beancount"


# --- copied data folders --------------------------------------------------------


def _write_picklecache(main_file: Path) -> Path:
    """Have beancount write its `.main.beancount.picklecache` (threshold 0)."""
    getter = functools.partial(loader.get_cache_filename, loader.PICKLE_CACHE_FILENAME)
    cached_load = loader.pickle_cache_function(getter, 0, loader._uncached_load_file)
    cached_load(str(main_file), None, None, None)
    cache = Path(getter(str(main_file)))
    assert cache.is_file()
    return cache


def test_a_copied_folder_with_a_stale_picklecache_sees_only_its_own_data(tmp_path):
    original = Ledger(tmp_path / "original")
    original.bootstrap()
    original.append(
        [make_txn(date(2026, 4, 1), "Shared", "Expenses:Food", "Assets:Bank:HDFC", "1")]
    )
    _write_picklecache(original.main_file)

    shutil.copytree(tmp_path / "original", tmp_path / "copy")
    copy = Ledger(tmp_path / "copy")
    assert (copy.journal_dir / ".main.beancount.picklecache").is_file()

    # Only the copy changes. Beancount's picklecache inside the copy names the
    # ORIGINAL folder's files, which are untouched, so a load through it would
    # pass its freshness check and return the original's entries.
    copy.append(
        [
            make_txn(
                date(2026, 4, 3), "Copy only", "Expenses:Food", "Assets:Bank:HDFC", "3"
            )
        ]
    )

    assert _narrations(copy) == ["Copy only", "Shared"]
    assert _narrations(original) == ["Shared"]
    copy_dir = str(copy.journal_dir)
    sources = {e.meta["filename"] for e in copy.load().entries if e.meta}
    assert all(
        name.startswith(copy_dir) for name in sources if name.endswith(".beancount")
    )


def test_load_never_writes_a_picklecache(ledger):
    _seed(ledger)
    ledger.load()
    assert not list(ledger.journal_dir.glob(".*.picklecache"))


# --- review C1: deleted / recreated includes, same-tick edits, errors --------


def _two_year_ledger(storage_dir: Path) -> Ledger:
    led = Ledger(storage_dir)
    led.bootstrap()
    led.append(
        [
            make_txn(date(2025, 4, 1), "A", "Expenses:Food", "Assets:Bank:HDFC", "5"),
            make_txn(date(2026, 4, 1), "B", "Expenses:Food", "Assets:Bank:HDFC", "5"),
        ]
    )
    _age_journal(led)
    return led


@pytest.mark.parametrize("preserve_mtime", [True, False])
def test_an_include_deleted_then_recreated_is_seen_again(
    storage_dir, tmp_path, preserve_mtime
):
    led = _two_year_ledger(storage_dir)
    assert _narrations(led) == ["A", "B"]
    year = led.journal_dir / "2025.beancount"
    backup = tmp_path / "backup.beancount"
    shutil.copy2(year, backup)

    year.unlink()
    during = led.load()
    assert during.errors  # the include matches no file
    assert _narrations(led) == ["B"]

    if preserve_mtime:  # cp -p / rsync -t / a sync client putting it back
        shutil.copy2(backup, year)
    else:
        year.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
    assert _narrations(led) == ["A", "B"]


def test_a_same_size_edit_in_the_same_mtime_tick_is_seen(ledger):
    ledger.append(
        [make_txn(date(2026, 4, 1), "Swiggy", "Expenses:Food", "Assets:Bank:HDFC", "1")]
    )
    year = ledger.journal_dir / "2026.beancount"
    assert _narrations(ledger) == ["Swiggy"]
    st = year.stat()
    text = year.read_text(encoding="utf-8")
    with year.open("r+", encoding="utf-8") as handle:  # in place: same inode
        handle.write(text.replace("Swiggy", "Zomato"))
    os.utime(year, ns=(st.st_atime_ns, st.st_mtime_ns))  # same tick
    assert year.stat().st_size == st.st_size
    assert _narrations(ledger) == ["Zomato"]


def test_a_parse_with_errors_is_never_cached(ledger, parses):
    _seed(ledger)
    with ledger.main_file.open("a", encoding="utf-8") as handle:
        handle.write('include "missing.beancount"\n')
    _age_journal(ledger)
    parses.clear()
    assert ledger.load().errors
    assert ledger.load().errors
    assert len(parses) == 2


def test_a_missing_literal_include_is_tracked_from_the_first_parse(ledger):
    _seed(ledger)
    with ledger.main_file.open("a", encoding="utf-8") as handle:
        handle.write('include "later.beancount"\n')
    _age_journal(ledger)
    ledger.load()
    (ledger.journal_dir / "later.beancount").write_text(
        '2026-05-01 * "Arrived later"\n'
        "  Expenses:Food  5 INR\n  Assets:Bank:HDFC  -5 INR\n",
        encoding="utf-8",
    )
    assert "Arrived later" in _narrations(ledger)


def test_a_freshly_written_journal_is_not_cached(ledger, parses):
    """Files modified within the mtime margin are re-parsed on every load."""
    ledger.append(
        [make_txn(date(2026, 4, 1), "Swiggy", "Expenses:Food", "Assets:Bank:HDFC", "1")]
    )
    parses.clear()
    ledger.load()
    ledger.load()
    assert len(parses) == 2


# --- review I2: one forced-fresh parse API --------------------------------------


def test_load_fresh_reparses_and_refreshes_the_cache(ledger, parses):
    _seed(ledger)
    parses.clear()
    ledger.load(fresh=True)
    assert len(parses) == 1
    ledger.load()
    assert len(parses) == 1  # the fresh parse primed the cache


def test_validate_always_parses_fresh(ledger, parses):
    _seed(ledger)
    parses.clear()
    ledger.validate()
    ledger.validate()
    assert len(parses) == 2


def test_the_cache_is_keyed_on_the_real_path(tmp_path, parses):
    real = tmp_path / "real"
    led = _two_year_ledger(real)
    led.load()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    parses.clear()
    assert _narrations(Ledger(link)) == ["A", "B"]
    assert parses == []


# --- review M4 / M1 ----------------------------------------------------------------


def test_mutating_a_returned_entry_does_not_leak_into_the_next_load(ledger):
    _seed(ledger)
    first = next(e for e in ledger.load().entries if isinstance(e, data.Transaction))
    with pytest.raises(TypeError):
        first.meta["scribble"] = "x"
    with pytest.raises(TypeError):
        first.postings[0].meta["scribble"] = "x"
    again = next(e for e in ledger.load().entries if isinstance(e, data.Transaction))
    assert "scribble" not in again.meta
    assert "scribble" not in (again.postings[0].meta or {})


def test_the_private_beancount_parse_keeps_its_signature():
    import inspect

    params = list(inspect.signature(loader._load).parameters)
    assert params == ["sources", "log_timings", "extra_validations", "encoding"]


# --- review m1: only include errors block caching --------------------------------


def test_a_persistent_unrelated_error_is_still_cached(ledger, parses):
    """A failing balance assertion is a property of the data, not of the
    parse: the journal is parsed once and then served from the cache."""
    _seed(ledger)
    with ledger.main_file.open("a", encoding="utf-8") as handle:
        handle.write("2027-01-01 balance Assets:Bank:HDFC  12345 INR\n")
    _age_journal(ledger)
    parses.clear()
    first = ledger.load()
    second = ledger.load()
    assert any("Balance failed" in e for e in first.errors), first.errors
    assert second.errors == first.errors
    assert len(parses) == 1


def test_an_include_error_still_blocks_caching(ledger, parses):
    _seed(ledger)
    with ledger.main_file.open("a", encoding="utf-8") as handle:
        handle.write('include "nowhere/*.beancount"\n')
    _age_journal(ledger)
    parses.clear()
    assert any("does not match any files" in e for e in ledger.load().errors)
    ledger.load()
    assert len(parses) == 2
