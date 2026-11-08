"""Columnar NAV history store, backed by nav_history.parquet.

This is the ONLY module in the codebase that imports pyarrow — the heavyweight
dependency is isolated behind this one swappable seam (the spec's boundary rule).
No FastAPI, no service imports. Logical primary key: (scheme_code, date).
"""

from __future__ import annotations

import bisect
import os
import threading
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from backend.app.core.errors.errors import NAV_STORE_CORRUPT
from backend.app.models.domain.nav import NavRow
from backend.app.persistence.atomic import replace_atomic

# pyarrow is imported inside the methods that touch the parquet file: it is
# heavy at import and only needed when NAV history is read or written, so
# keeping it off the module top level keeps app startup fast.
if TYPE_CHECKING:
    import pyarrow as pa


def _nav_type() -> pa.DataType:
    """decimal128(12,4): NAV in INR with 4 fractional digits."""
    import pyarrow as pa

    return pa.decimal128(12, 4)


def _schema() -> pa.Schema:
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("scheme_code", pa.string()),
            pa.field("date", pa.date32()),
            pa.field("nav", _nav_type()),
        ]
    )


# --- parsed-file cache ----------------------------------------------------------
#
# Every report builds its own NavStore and calls several lookups, each of which
# used to re-read the whole parquet file and rebuild a Decimal dict -- 75 reads
# for one /investments/changes request. The parsed rows (and a per-code index,
# ascending by date) are cached per file and reused while the file's
# (mtime_ns, size, inode) is unchanged. append() replaces the file atomically
# (new inode) and drops the entry itself. The cached rows are shared: treat them
# as read-only.

_Fingerprint = tuple[int, int, int]


@dataclass(frozen=True)
class _Parsed:
    fingerprint: _Fingerprint
    rows: dict[tuple[str, date], Decimal]
    by_code: dict[str, list[tuple[date, Decimal]]]  # ascending by date
    dates_by_code: dict[str, list[date]]  # same order, for bisect


_CACHE: dict[str, _Parsed] = {}
_CACHE_LOCK = threading.Lock()


def _fingerprint(path: Path) -> _Fingerprint:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return (-1, -1, -1)
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _index(fingerprint: _Fingerprint, rows: dict[tuple[str, date], Decimal]) -> _Parsed:
    by_code: dict[str, list[tuple[date, Decimal]]] = {}
    for (code, d), nav in rows.items():
        by_code.setdefault(code, []).append((d, nav))
    for series in by_code.values():
        series.sort(key=lambda item: item[0])
    dates_by_code = {code: [d for d, _ in s] for code, s in by_code.items()}
    return _Parsed(fingerprint, rows, by_code, dates_by_code)


class NavStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @property
    def _key(self) -> str:
        return os.path.abspath(self.path)

    def _parsed(self) -> _Parsed:
        key = self._key
        # Stat BEFORE reading: if the file is replaced in between, the entry is
        # stored under the older fingerprint and simply re-read next time.
        fingerprint = _fingerprint(self.path)
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and cached.fingerprint == fingerprint:
                return cached
            parsed = _index(fingerprint, self._read_rows())
            _CACHE[key] = parsed
            return parsed

    def _invalidate(self) -> None:
        with _CACHE_LOCK:
            _CACHE.pop(self._key, None)

    def _read(self) -> pa.Table:
        import pyarrow.parquet as pq

        if not self.path.exists():
            return _schema().empty_table()
        try:
            return pq.read_table(self.path)
        except Exception as exc:  # corrupt/unreadable parquet
            raise NAV_STORE_CORRUPT.exc({"path": str(self.path)}) from exc

    def _rows(self) -> dict[tuple[str, date], Decimal]:
        """Every stored row (shared, read-only)."""
        return self._parsed().rows

    def _read_rows(self) -> dict[tuple[str, date], Decimal]:
        table = self._read()
        out: dict[tuple[str, date], Decimal] = {}
        codes = table.column("scheme_code").to_pylist()
        dates = table.column("date").to_pylist()
        navs = table.column("nav").to_pylist()
        for code, d, nav in zip(codes, dates, navs):
            out[(code, d)] = Decimal(str(nav))
        return out

    def append(self, rows: list[NavRow]) -> None:
        if not rows:
            return
        merged = dict(self._rows())  # the cached dict is shared: never mutate it
        for row in rows:  # last-write-wins on (code, date)
            merged[(row.scheme_code, row.date)] = row.nav
        import pyarrow as pa
        import pyarrow.parquet as pq

        keys = sorted(merged)
        table = pa.table(
            {
                "scheme_code": pa.array([k[0] for k in keys], pa.string()),
                "date": pa.array([k[1] for k in keys], pa.date32()),
                "nav": pa.array([merged[k] for k in keys], _nav_type()),
            },
            schema=_schema(),
        )
        # Atomic: a reader (a screen, while the startup refresh runs in the
        # background) must never open a half-written parquet file.
        try:
            replace_atomic(self.path, lambda tmp: pq.write_table(table, str(tmp)))
        finally:
            self._invalidate()

    def latest(self, codes: set[str]) -> dict[str, NavRow]:
        by_code = self._parsed().by_code
        best: dict[str, NavRow] = {}
        for code in codes:
            series = by_code.get(code)
            if series:
                d, nav = series[-1]
                best[code] = NavRow(scheme_code=code, nav=nav, date=d)
        return best

    def as_of(self) -> date | None:
        by_code = self._parsed().by_code
        return max((s[-1][0] for s in by_code.values()), default=None)

    def date_range(self, code: str) -> tuple[date, date] | None:
        """Min and max stored dates for a scheme_code, or None if no data exists."""
        series = self._parsed().by_code.get(code)
        return (series[0][0], series[-1][0]) if series else None

    def history(self, code: str) -> list[NavRow]:
        """Every stored row for a single code, ascending by date. No
        forward-fill and no interpolation -- exactly the dates a real price
        was recorded, nothing else."""
        series = self._parsed().by_code.get(code, [])
        return [NavRow(scheme_code=code, nav=nav, date=d) for d, nav in series]

    def exact(self, keys: set[tuple[str, date]]) -> dict[tuple[str, date], Decimal]:
        """The NAV stored for exactly each requested (scheme_code, date), with
        no forward-fill: a key with no row on that very date is absent. One
        read of the file for the whole set."""
        if not keys:
            return {}
        rows = self._rows()
        return {k: rows[k] for k in keys if k in rows}

    def nav_as_of(self, codes: set[str], target_date: date) -> dict[str, NavRow]:
        """Closest stored NAV on or before target_date for each requested code."""
        parsed = self._parsed()
        best: dict[str, NavRow] = {}
        for code in codes:
            dates = parsed.dates_by_code.get(code)
            if not dates:
                continue
            i = bisect.bisect_right(dates, target_date)
            if i:
                d, nav = parsed.by_code[code][i - 1]
                best[code] = NavRow(scheme_code=code, nav=nav, date=d)
        return best
