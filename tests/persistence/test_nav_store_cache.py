"""NavStore caches the parsed parquet per file; it must never serve stale rows."""

from __future__ import annotations

import os
import random
import shutil
from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.models.domain.nav import NavRow
from backend.app.persistence import nav_store as nav_store_mod
from backend.app.persistence.nav_store import NavStore


def _row(code: str, nav: str, d: date) -> NavRow:
    return NavRow(scheme_code=code, nav=Decimal(nav), date=d)


@pytest.fixture
def reads(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = NavStore._read_rows

    def counting(self):
        calls.append(str(self.path))
        return real(self)

    monkeypatch.setattr(NavStore, "_read_rows", counting)
    return calls


def test_lookups_share_one_read_until_the_file_changes(tmp_path, reads):
    path = tmp_path / "nav_history.parquet"
    NavStore(path).append([_row("1", "10.0000", date(2026, 1, 1))])
    reads.clear()
    store = NavStore(path)
    store.latest({"1"})
    store.as_of()
    store.date_range("1")
    NavStore(path).nav_as_of({"1"}, date(2026, 2, 1))  # a fresh instance too
    assert len(reads) == 1


def test_an_append_is_seen_by_every_instance(tmp_path):
    path = tmp_path / "nav_history.parquet"
    reader, writer = NavStore(path), NavStore(path)
    writer.append([_row("1", "10.0000", date(2026, 1, 1))])
    assert reader.latest({"1"})["1"].nav == Decimal("10.0000")
    writer.append([_row("1", "11.0000", date(2026, 1, 2))])
    assert reader.latest({"1"})["1"].nav == Decimal("11.0000")
    assert reader.as_of() == date(2026, 1, 2)


def test_a_file_replaced_outside_the_store_is_picked_up(tmp_path):
    path = tmp_path / "nav_history.parquet"
    other = tmp_path / "elsewhere" / "nav_history.parquet"
    NavStore(path).append([_row("1", "10.0000", date(2026, 1, 1))])
    NavStore(other).append([_row("2", "20.0000", date(2026, 3, 1))])
    store = NavStore(path)
    assert store.latest({"1", "2"}).keys() == {"1"}

    staged = tmp_path / "staged.parquet"
    shutil.copyfile(other, staged)
    os.replace(staged, path)  # as another process would
    assert store.latest({"1", "2"}).keys() == {"2"}


def test_a_deleted_file_reads_as_empty(tmp_path):
    path = tmp_path / "nav_history.parquet"
    store = NavStore(path)
    store.append([_row("1", "10.0000", date(2026, 1, 1))])
    assert store.as_of() == date(2026, 1, 1)
    path.unlink()
    assert store.as_of() is None
    assert store.latest({"1"}) == {}


def test_a_corrupt_file_is_not_cached(tmp_path):
    path = tmp_path / "nav_history.parquet"
    path.write_bytes(b"not parquet")
    with pytest.raises(CodedError):
        NavStore(path).as_of()
    path.unlink()
    NavStore(path).append([_row("1", "10.0000", date(2026, 1, 1))])
    assert NavStore(path).as_of() == date(2026, 1, 1)


def test_results_are_the_callers_to_change(tmp_path):
    path = tmp_path / "nav_history.parquet"
    store = NavStore(path)
    store.append([_row("1", "10.0000", date(2026, 1, 1))])
    store.latest({"1"}).clear()
    store.history("1").clear()
    store.exact({("1", date(2026, 1, 1))}).clear()
    assert store.latest({"1"})["1"].nav == Decimal("10.0000")
    assert len(store.history("1")) == 1
    assert store.exact({("1", date(2026, 1, 1))}) == {
        ("1", date(2026, 1, 1)): Decimal("10.0000")
    }


def test_a_failed_append_leaves_the_cache_consistent(tmp_path, monkeypatch):
    path = tmp_path / "nav_history.parquet"
    store = NavStore(path)
    store.append([_row("1", "10.0000", date(2026, 1, 1))])

    seen_mid_write: list[Decimal] = []

    def fail(*_args, **_kwargs):
        # A concurrent reader during the write must still see the old rows,
        # not the half-merged batch.
        reader = NavStore(path)
        seen_mid_write.append(reader.latest({"1"})["1"].nav)
        assert reader.exact({("1", date(2026, 1, 2))}) == {}
        raise OSError("disk full")

    monkeypatch.setattr(nav_store_mod, "replace_atomic", fail)
    with pytest.raises(OSError):
        store.append([_row("1", "11.0000", date(2026, 1, 2))])
    assert seen_mid_write == [Decimal("10.0000")]
    assert store.latest({"1"})["1"].nav == Decimal("10.0000")


def test_indexed_lookups_match_a_full_scan(tmp_path):
    """latest / nav_as_of / date_range / history / as_of against brute force."""
    rng = random.Random(7)
    rows: dict[tuple[str, date], Decimal] = {}
    start = date(2020, 1, 1)
    for _ in range(600):
        code = str(rng.randint(1, 12))
        d = start + timedelta(days=rng.randint(0, 2000))
        rows[(code, d)] = Decimal(rng.randint(1000, 999999)) / Decimal(100)
    path = tmp_path / "nav_history.parquet"
    store = NavStore(path)
    store.append([NavRow(scheme_code=c, nav=n, date=d) for (c, d), n in rows.items()])

    codes = {str(i) for i in range(0, 15)}  # includes codes with no rows
    expected_latest = {}
    for (c, d), n in rows.items():
        if c in codes and (c not in expected_latest or d > expected_latest[c].date):
            expected_latest[c] = NavRow(scheme_code=c, nav=n, date=d)
    assert store.latest(codes) == expected_latest
    assert store.as_of() == max(d for _, d in rows)

    for _ in range(50):
        target = start + timedelta(days=rng.randint(-10, 2010))
        want = {}
        for (c, d), n in rows.items():
            if c in codes and d <= target and (c not in want or d > want[c].date):
                want[c] = NavRow(scheme_code=c, nav=n, date=d)
        assert store.nav_as_of(codes, target) == want

    for code in codes:
        dates = sorted(d for c, d in rows if c == code)
        assert store.date_range(code) == ((dates[0], dates[-1]) if dates else None)
        assert store.history(code) == [
            NavRow(scheme_code=code, nav=rows[(code, d)], date=d) for d in dates
        ]
