"""A data folder copied or restored together with beancount's pickle cache
must never be written through to the ORIGINAL folder (synthetic data only)."""

from __future__ import annotations

import pickle
import shutil
from pathlib import Path

import pytest
from beancount import loader

from backend.app.persistence import journal
from backend.app.persistence.journal import Ledger
from backend.app.service.reports.health.ledger_view import open_meta

CARD = "Liabilities:CreditCard:Acme1234"


def _snapshots(led: Ledger) -> int:
    if not led.snapshots_dir.exists():
        return 0
    return len(list(led.snapshots_dir.iterdir()))


def _copy_with_stale_cache(tmp_path: Path) -> tuple[Ledger, Ledger]:
    """An original ledger and a copy of its folder carrying a `.picklecache`
    that holds the original's parse (every entry's `filename` points at the
    original). Ledger never reads that cache: even a plain load of the copy
    sees the copy's own files."""
    orig = Ledger(tmp_path / "orig")
    orig.bootstrap(seed_accounts=("Assets:Checking:Bank",))
    orig.open_named_accounts([CARD])
    shutil.copytree(orig.storage_dir, tmp_path / "copy")
    copy = Ledger(tmp_path / "copy")
    parsed = journal._parse_uncached(str(orig.main_file.resolve()))
    cache = Path(
        loader.get_cache_filename(loader.PICKLE_CACHE_FILENAME, str(copy.main_file))
    )
    cache.write_bytes(pickle.dumps(parsed))
    for load in (copy.load(), copy.load(fresh=True)):
        opened = next(e for e in load.entries if getattr(e, "account", None) == CARD)
        assert (
            Path(opened.meta["filename"])
            .resolve()
            .is_relative_to(copy.journal_dir.resolve())
        )
    return orig, copy


def test_copied_folder_with_stale_cache_writes_only_into_the_copy(tmp_path):
    orig, copy = _copy_with_stale_cache(tmp_path)
    before = orig.main_file.read_bytes()

    copy.set_open_meta(CARD, {"statement-day": 15})

    assert orig.main_file.read_bytes() == before
    assert "statement-day: 15" in copy.main_file.read_text(encoding="utf-8")
    assert open_meta(orig)[CARD] == {}
    assert copy.validate() == []


def test_open_directive_outside_the_journal_dir_is_refused(tmp_path, monkeypatch):
    orig, copy = _copy_with_stale_cache(tmp_path)
    before = orig.main_file.read_bytes()
    # Force a parse of the ORIGINAL folder onto the copy's fresh write-path
    # load (`load(fresh=True)`): the `_journal_path` guard must refuse it.
    orig_main = str(orig.main_file.resolve())
    monkeypatch.setattr(
        journal,
        "_parse_uncached",
        lambda _f: loader._load([(orig_main, True)], None, None, None),
    )
    snapshots = _snapshots(copy)

    with pytest.raises(ValueError, match="outside the journal dir"):
        copy.set_open_meta(CARD, {"statement-day": 15})

    assert orig.main_file.read_bytes() == before
    assert _snapshots(copy) == snapshots
    monkeypatch.undo()
    copy._invalidate()
