from __future__ import annotations

import functools
import hashlib
import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Concatenate, ParamSpec, TypeVar

from beancount import loader
from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.inventory import Inventory
from beancount.core.number import MISSING
from beancount.parser import printer

from backend.app.models.domain.accounts import (
    OPERATING_CURRENCY,
    SEED_ACCOUNTS,
    Vehicle,
    holding_account,
)
from backend.app.core.errors.errors import ENTRY_NOT_LOCATABLE, CodedError
from backend.app.core.errors.ledger import (
    HashMismatchError,
    LedgerValidationError,
    PostingNotFoundError,
    TransactionNotFoundError,
)
from backend.app.models.domain.nav import NavRow
from backend.app.persistence.atomic import replace_atomic, write_text_atomic
from backend.app.persistence.seeds import DICTIONARY_SEED

# A value `Ledger.set_open_meta` can write under an `open` directive: a string
# is written quoted, a number bare. Services import it from here.
MetaValue = str | int | Decimal

# A metadata key as beancount's lexer accepts it on an indented line.
_META_KEY = re.compile(r"[a-z][A-Za-z0-9_-]*")
_META_LINE = re.compile(r"^[ \t]+([a-z][A-Za-z0-9_-]*):")
# Keys beancount itself attaches to every parsed directive.
_RESERVED_META = frozenset({"filename", "lineno"})


def _render_meta_value(key: str, value: MetaValue) -> str:
    """A metadata value as beancount source text, or ValueError/TypeError."""
    if isinstance(value, bool):
        raise TypeError(f"metadata {key}: booleans are not supported")
    if isinstance(value, str):
        if "\n" in value or "\r" in value:
            raise ValueError(f"metadata {key}: a value may not span lines")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"metadata {key}: {value} is not a finite number")
        return format(value, "f")
    raise TypeError(f"metadata {key}: unsupported value type {type(value).__name__}")


def _same_meta_value(current: object, value: MetaValue) -> bool:
    """True when the parsed `current` is what writing `value` would yield."""
    if isinstance(value, str):
        return isinstance(current, str) and current == value
    return isinstance(current, Decimal) and format(current, "f") == format(
        Decimal(value), "f"
    )


# The copy-paste prompts are not journal data: `service.prompts` creates and
# seeds their folder after this bootstrap.
_SUBDIRS = ("journal", "templates", "rules", "imports", "snapshots")


def _initial_journal(seed_accounts: tuple[str, ...], title: str) -> str:
    lines = [
        'option "operating_currency" "INR"',
        f'option "title" "{title}"',
        "",
        f"2000-01-01 commodity {OPERATING_CURRENCY}",
        "",
    ]
    for account in seed_accounts:
        lines.append(f"2000-01-01 open {account} {OPERATING_CURRENCY}")
    lines.append("")
    return "\n".join(lines)


@dataclass
class LoadResult:
    entries: list
    errors: list[str]
    options_map: dict


# --- parsed-journal cache ----------------------------------------------------
#
# Every report request loads the journal, often several times; re-parsing it
# each time dominated read latency. The parse is cached in memory per main file
# (keyed on its real path, so every Ledger on that file shares it) and reused
# only while every tracked file is unchanged by (path, mtime_ns, size, inode).
#
# Tracked files: main, every file any earlier parse read (one that has since
# gone missing is tracked as missing, so its return is noticed), and the literal
# `include "..."` targets written in main -- even ones that do not exist yet.
# Glob includes (`include "*.beancount"`) are NOT expanded here: a file that
# newly matches a glob is not noticed until another tracked file changes.
#
# A parse is cached only when it is trustworthy: it reported no include error
# (a missing file, a glob that "does not match any files" -- the parse saw an
# incomplete journal), it read no fewer files than the previous parse, no
# tracked file changed while it ran, and every tracked file was last modified
# more than _MTIME_MARGIN_NS before it began -- so an edit landing in the same
# mtime tick as an earlier write (same size, same inode) is never masked, and a
# file caught half-written by an outside editor is never kept. Other errors (a
# failing balance assertion) belong to the data and do not block caching.
# Writes through Ledger also drop the entry explicitly.
#
# Readers share the cached entries. Their meta dicts (entry and posting) are
# frozen -- mutating one raises TypeError -- and each load returns fresh outer
# lists and a fresh options dict, so appending to or sorting a result is safe.
#
# One lock serialises cache lookups and parses: concurrent readers never see a
# half-built entry, and a second reader waits for the parse in flight rather
# than starting another one.

_Stat = tuple[str, int, int, int]
_Fingerprint = tuple[_Stat, ...]
_MISSING = (-1, -1, -1)
_MTIME_MARGIN_NS = 2_000_000_000
_INCLUDE_RE = re.compile(r'^\s*include\s+"([^"]+)"', re.MULTILINE)
_GLOB_CHARS = frozenset("*?[")


class _FrozenMeta(dict):
    """A dict that refuses mutation: cached entries are shared by every reader."""

    def _refuse(self, *_args, **_kwargs):
        raise TypeError("journal entries from Ledger.load() are read-only")

    __setitem__ = __delitem__ = _refuse
    clear = pop = popitem = setdefault = update = _refuse
    __ior__ = _refuse

    def __reduce__(self):  # copy/deepcopy/pickle rebuild without __setitem__
        return (_FrozenMeta, (dict(self),))


def _freeze(meta):
    return meta if meta is None or isinstance(meta, _FrozenMeta) else _FrozenMeta(meta)


def _freeze_entries(entries: list) -> list:
    out = []
    for entry in entries:
        replaced = {}
        if getattr(entry, "meta", None) is not None:
            replaced["meta"] = _freeze(entry.meta)
        if isinstance(entry, data.Transaction):
            replaced["postings"] = [
                p if p.meta is None else p._replace(meta=_freeze(p.meta))
                for p in entry.postings
            ]
        out.append(entry._replace(**replaced) if replaced else entry)
    return out


@dataclass(frozen=True)
class _CachedLoad:
    fingerprint: _Fingerprint
    entries: list
    errors: list[str]
    options_map: dict


_LOAD_LOCK = threading.RLock()
_LOAD_CACHE: dict[str, _CachedLoad] = {}
# Every file a parse of each main file has read (main first). Only grows, so a
# file that disappears stays tracked -- as missing -- and its return is seen.
_FILES_SEEN: dict[str, tuple[str, ...]] = {}
# The files the most recent parse read, to refuse caching a parse that shrank.
_LAST_READ: dict[str, set[str]] = {}


def _stat(name: str) -> _Stat:
    try:
        st = os.stat(name)
    except OSError:  # a missing include is part of the state too
        return (name, -1, -1, -1)
    return (name, st.st_mtime_ns, st.st_size, st.st_ino)


def _fingerprint(files: tuple[str, ...]) -> _Fingerprint:
    return tuple(_stat(name) for name in files)


def _literal_includes(main: str) -> set[str]:
    """`include "..."` targets written in main, resolved next to it (no globs)."""
    try:
        text = Path(main).read_text(encoding="utf-8")
    except OSError:
        return set()
    base = os.path.dirname(main)
    return {
        os.path.realpath(os.path.join(base, target))
        for target in _INCLUDE_RE.findall(text)
        if not _GLOB_CHARS & set(target)
    }


def _files_read(main: str, options_map: dict) -> set[str]:
    # The parse ran on `main` (an absolute path in this data folder), so its
    # include list is resolved relative to this folder's journal: a copied
    # folder fingerprints its own files, never the original's.
    base = os.path.dirname(main)
    return {
        os.path.realpath(os.path.join(base, p))
        for p in options_map.get("include") or ()
    } | {os.path.realpath(main)}


def _tracked(main: str, *groups: set[str]) -> tuple[str, ...]:
    files: set[str] = set().union(*groups)
    files.discard(main)
    return (main, *sorted(files))


_INCLUDE_ERROR_MARKERS = ("does not exist", "does not match any files")


def _include_errors(raw_errors: list) -> bool:
    """Whether the parse hit an include problem, i.e. saw an incomplete set of
    files. Other errors (a failing balance assertion, a bad posting) are a
    property of the data itself: such a parse is complete and may be cached."""
    return any(
        isinstance(e, loader.LoadError)
        and any(marker in e.message for marker in _INCLUDE_ERROR_MARKERS)
        for e in raw_errors
    )


def _parse_uncached(filename: str) -> tuple[list, list, dict]:
    """A full beancount parse that never reads or writes `.<name>.picklecache`.

    `loader.load_file` goes through beancount's pickle cache, which stores the
    ABSOLUTE paths of the files it read and is validated against those paths --
    so in a copied or restored data folder it hands back the ORIGINAL folder's
    entries -- and several processes sharing it have truncated it mid-write.
    The in-memory cache above replaces it. `loader._load` is the parse that
    `load_file` itself runs when its cache is off (its signature is pinned by a
    test, and pyproject pins beancount below 3.3).
    """
    return loader._load([(filename, True)], None, None, None)


def _invalidate_load_cache(key: str) -> None:
    with _LOAD_LOCK:
        _LOAD_CACHE.pop(key, None)


def _as_result(cached: _CachedLoad) -> LoadResult:
    return LoadResult(
        list(cached.entries), list(cached.errors), dict(cached.options_map)
    )


# --- write lock ------------------------------------------------------------------
#
# Every Ledger write snapshots, writes, validates and restores its snapshot on
# failure. Two writers interleaving in one journal folder (an import's append
# and the background price refresh's write_prices_file) could restore a
# snapshot taken before the other's write and silently erase it. So writes
# hold one re-entrant lock per journal folder (keyed on its real path: main and
# investments journals share a folder, and so share the lock) from snapshot
# through validate/restore. A service doing several writes as one commit
# (ensure_commodity_accounts + append on both ledgers) holds it across the whole
# sequence with `hold_write_locks(...)`. Readers do not take it.

_WRITE_LOCKS: dict[str, threading.RLock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def _write_lock_for(journal_dir: Path) -> threading.RLock:
    key = os.path.realpath(journal_dir)
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = _WRITE_LOCKS[key] = threading.RLock()
        return lock


@contextmanager
def hold_write_locks(*ledgers: Ledger) -> Iterator[None]:
    """Hold the write lock of every ledger's folder (each once, in a fixed order)."""
    locks = {os.path.realpath(led.journal_dir): led.write_lock() for led in ledgers}
    with ExitStack() as stack:
        for key in sorted(locks):
            stack.enter_context(locks[key])
        yield


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _writes(
    method: Callable[Concatenate[Ledger, _P], _R],
) -> Callable[Concatenate[Ledger, _P], _R]:
    """Run a Ledger write method under its folder's write lock."""

    @functools.wraps(method)
    def locked(self: Ledger, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with self.write_lock():
            return method(self, *args, **kwargs)

    return locked


# --- in-place posting rewrite ------------------------------------------------
#
# `recategorize_many` edits entries where they stand, so a hand-formatted
# entry (its own spacing, aligned amounts, comments, metadata order) is
# rewritten without being re-printed. The entry is located by the `filename`
# and `lineno` the parse recorded; only the account token of each matching
# posting line changes, and every other byte of the file (comments included)
# is kept. The rewrite is verified by parse identity: the old block must parse
# to the loaded entry, and the new block must parse to that entry with the
# account swapped. Byte-matching a re-printed block is not a check.

# An indented posting line: an optional posting flag (any beancount accepts:
# ! * & # ? % P S T C U R M), the account, then the rest. A metadata line
# (`key: value`) never matches: an account starts upper-case.
_POSTING_LINE = re.compile(
    r"^(?P<lead>[ \t]+(?:[!*&#?%PSTCURM][ \t]+)?)(?P<account>[A-Z][^\s;]*)"
    r"(?P<gap>[ \t]*)(?P<rest>[^\r\n]*)(?P<eol>\r?\n?)$"
)
_SOURCE_META = ("filename", "lineno")


def _split_lines(text: str) -> list[str]:
    """`text` as lines with their "\\n" kept, split on "\\n" only -- as the
    parser counts lines (`str.splitlines` also breaks on form feeds and
    Unicode separators). "".join() gives `text` back exactly."""
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _block_end(lines: list[str], start: int) -> int:
    """Index one past the last line of the entry whose header is lines[start]:
    the header plus every following indented, non-blank line."""
    end = start + 1
    while end < len(lines) and lines[end][:1] in (" ", "\t") and lines[end].strip():
        end += 1
    return end


def _without_source(meta) -> dict:
    return {k: v for k, v in (meta or {}).items() if k not in _SOURCE_META}


def _shape(entry: data.Transaction) -> tuple:
    """What an entry says, without where it was read from. Postings compare
    on what was written (a parse leaves an elided amount MISSING)."""
    return (
        entry.date,
        entry.flag,
        entry.payee,
        entry.narration,
        frozenset(entry.tags or ()),
        frozenset(entry.links or ()),
        _without_source(entry.meta),
        [
            (p.account, p.units, p.cost, p.price, p.flag, _without_source(p.meta))
            for p in entry.postings
        ],
    )


def _parse_block(block: str) -> data.Transaction | None:
    """The one transaction `block` holds, or None if it holds anything else."""
    from beancount.parser import parser

    entries, errors, _ = parser.parse_string(block)
    if errors or len(entries) != 1 or not isinstance(entries[0], data.Transaction):
        return None
    return entries[0]


def _swap_account(line: str, old: str, new: str) -> str | None:
    """`line` with its posting account `old` replaced by `new`, or None when
    it is not a posting of `old`. Keeps the amount column where the spacing
    allows: a shorter name pads the gap, a longer one takes from it down to
    two spaces. A gap holding a tab is left as it is."""
    m = _POSTING_LINE.match(line)
    if m is None or m["account"] != old:
        return None
    gap = m["gap"]
    if m["rest"] and not m["rest"].startswith(";") and "\t" not in gap:
        width = len(gap) + len(old) - len(new)
        gap = " " * max(width, min(len(gap), 2))
    return f"{m['lead']}{new}{gap}{m['rest']}{m['eol']}"


def _rewrite_in_place(
    lines: list[str], entry: data.Transaction, old: str, new: str
) -> int | None:
    """Move `entry`'s `old` postings to `new` in `lines` (the entry's file,
    split with line ends kept), in place. Returns the postings rewritten, or
    None -- with `lines` untouched -- when the block at the entry's recorded
    line is not that entry, or the rewrite does not parse to the intended
    entry."""
    lineno = entry.meta.get("lineno")
    if not isinstance(lineno, int) or not 1 <= lineno <= len(lines):
        return None
    start = lineno - 1
    end = _block_end(lines, start)
    old_lines = lines[start:end]
    parsed_old = _parse_block("".join(old_lines))
    if parsed_old is None or parsed_old.meta.get("txn-id") != entry.meta.get("txn-id"):
        return None
    # The located block must say what the loaded entry says (an elided amount
    # aside, which the load interpolates and a bare parse leaves MISSING).
    if (
        parsed_old.date != entry.date
        or parsed_old.narration != entry.narration
        or [p.account for p in parsed_old.postings]
        != [p.account for p in entry.postings]
    ):
        return None
    new_lines = []
    swapped = 0
    for line in old_lines:
        changed = _swap_account(line, old, new)
        new_lines.append(line if changed is None else changed)
        swapped += changed is not None
    intended = parsed_old._replace(
        postings=[
            p._replace(account=new) if p.account == old else p
            for p in parsed_old.postings
        ]
    )
    parsed_new = _parse_block("".join(new_lines))
    if parsed_new is None or _shape(parsed_new) != _shape(intended):
        return None
    expected = sum(1 for p in entry.postings if p.account == old)
    if swapped != expected:
        return None
    lines[start:end] = new_lines
    return swapped


# --- in-place directive replacement ------------------------------------------
#
# `replace_entries` swaps whole directives (an opening's amount or date, its
# paired `balance`) where they stand. As above, the block is located by the
# parsed filename/lineno and must parse back to the directive the caller
# loaded; a bare parse leaves an elided amount MISSING and a cost as the
# written CostSpec, so those are compared as written.


def _block_span(lines: list[str], lineno: int) -> tuple[int, int]:
    """The [start, end) line indexes of the directive on 1-based `lineno`:
    its line plus every following indented, non-blank line."""
    start = lineno - 1
    return start, _block_end(lines, start)


def _blank_before(lines: list[str], start: int) -> bool:
    return start == 0 or not lines[start - 1].strip()


def _parse_directive(block: str) -> data.Directive | None:
    """The one directive `block` holds, or None."""
    from beancount.parser import parser

    entries, errors, _ = parser.parse_string(block)
    if errors or len(entries) != 1:
        return None
    return entries[0]


def _same_value(written: object, loaded: object) -> bool:
    """A written value against its loaded form; MISSING (elided) matches."""
    return written is MISSING or written == loaded


def _same_units(written: object, loaded: object) -> bool:
    if written is MISSING or written is None or loaded is None:
        return written is MISSING or written == loaded
    if not isinstance(written, Amount) or not isinstance(loaded, Amount):
        return False
    return _same_value(written.number, loaded.number) and _same_value(
        written.currency, loaded.currency
    )


def _same_cost(written: object, loaded: object, units: object) -> bool:
    """A cost as parsed (CostSpec) or booked (Cost) against the loaded Cost."""
    if written is None or loaded is None:
        return written is None and loaded is None
    if isinstance(written, data.Cost):
        return written == loaded
    if not isinstance(written, data.CostSpec) or not isinstance(loaded, data.Cost):
        return False
    per, total = written.number_per, written.number_total
    if total not in (None, MISSING):
        # `{{total}}` (or `{per # total}`): booked per unit as total / units.
        if not (isinstance(units, Amount) and isinstance(units.number, Decimal)):
            return False
        count = abs(units.number)
        written_per = per if isinstance(per, Decimal) else Decimal(0)
        number_ok = written_per * count + total == loaded.number * count
    elif per not in (None, MISSING):
        number_ok = per == loaded.number
    else:
        number_ok = per is MISSING
    return (
        number_ok
        and _same_value(written.currency, loaded.currency)
        and (written.date is None or written.date == loaded.date)
        and (written.label is None or written.label == loaded.label)
    )


def _same_directive(written: data.Directive | None, loaded: data.Directive) -> bool:
    """Whether `written` (a block's bare parse) says what `loaded` says:
    same type, date and `txn-id` (strictly: one present on one side only
    refuses), then a transaction's flag, payee, narration, tags, links and
    postings' (account, flag, units, cost, price), a balance's account and
    amount, or every other field of any other directive (source metadata
    aside). Two entries alike in all but their txn-id are different
    entries: a stale lineno landing on such a twin must not match it."""
    if written is None or type(written) is not type(loaded):
        return False
    if written.date != loaded.date:
        return False
    if (written.meta or {}).get("txn-id") != (loaded.meta or {}).get("txn-id"):
        return False
    if isinstance(written, data.Transaction):
        assert isinstance(loaded, data.Transaction)
        return (
            written.flag == loaded.flag
            and written.payee == loaded.payee
            and written.narration == loaded.narration
            and frozenset(written.tags or ()) == frozenset(loaded.tags or ())
            and frozenset(written.links or ()) == frozenset(loaded.links or ())
            and len(written.postings) == len(loaded.postings)
            and all(
                w.account == g.account
                and w.flag == g.flag
                and _same_units(w.units, g.units)
                and _same_cost(w.cost, g.cost, g.units)
                and _same_units(w.price, g.price)
                for w, g in zip(written.postings, loaded.postings, strict=True)
            )
        )
    if isinstance(written, data.Balance):
        assert isinstance(loaded, data.Balance)
        return written.account == loaded.account and written.amount == loaded.amount
    # Every directive's first field is its meta.
    return tuple(written)[1:] == tuple(loaded)[1:]


class Ledger:
    """The only beancount-aware, journal-writing unit. Snapshots before every write."""

    def __init__(
        self,
        storage_dir: Path | str,
        *,
        main_filename: str = "main.beancount",
        year_template: str = "{year}.beancount",
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self._main_filename = main_filename
        self._year_template = year_template

    @property
    def journal_dir(self) -> Path:
        return self.storage_dir / "journal"

    @property
    def main_file(self) -> Path:
        return self.journal_dir / self._main_filename

    def _year_file(self, year: int) -> Path:
        return self.journal_dir / self._year_template.format(year=year)

    @property
    def snapshots_dir(self) -> Path:
        return self.storage_dir / "snapshots"

    def write_lock(self) -> threading.RLock:
        """The re-entrant lock every write to this journal folder holds."""
        return _write_lock_for(self.journal_dir)

    @_writes
    def bootstrap(
        self,
        *,
        seed_accounts: tuple[str, ...] = SEED_ACCOUNTS,
        title: str = "SpendSift Journal",
    ) -> None:
        for sub in _SUBDIRS:
            (self.storage_dir / sub).mkdir(parents=True, exist_ok=True)
        if not self.main_file.exists():
            write_text_atomic(self.main_file, _initial_journal(seed_accounts, title))
            self._invalidate()
        if self._main_filename == "main.beancount":
            dict_dest = self.storage_dir / "rules" / "dictionary.yaml"
            if not dict_dest.exists():
                shutil.copyfile(DICTIONARY_SEED, dict_dest)
        # Tax rules are intentionally NOT seeded — the product ships no opinionated
        # tax config. Users add their own config/tax/<fy>.yaml; the tax workspace
        # degrades to a "not configured" notice until they do.

    @property
    def _cache_key(self) -> str:
        return str(self.main_file.resolve())

    def load(self, *, fresh: bool = False) -> LoadResult:
        """Parse the journal, or reuse the last parse while no tracked file changed.

        `fresh=True` always re-parses (and refreshes the cache with the result
        when it is trustworthy) -- the one API for "I just wrote, show me the
        file as it is now". Returned entries are shared: their meta is frozen.
        """
        key = self._cache_key
        with _LOAD_LOCK:
            if not fresh:
                cached = _LOAD_CACHE.get(key)
                if cached is not None:
                    files = tuple(stat[0] for stat in cached.fingerprint)
                    if _fingerprint(files) == cached.fingerprint:
                        return _as_result(cached)
            return self._parse(key)

    def _parse(self, key: str) -> LoadResult:
        # Caller holds _LOAD_LOCK. See the cache notes above for when a parse
        # may be kept.
        # Parse via the path as given (entry `filename` meta keeps that form);
        # track every file by its real path, like the cache key.
        main = os.path.abspath(self.main_file)
        seen_before = set(_FILES_SEEN.get(key, ()))
        tracked_before = _tracked(key, seen_before, _literal_includes(main))
        before = _fingerprint(tracked_before)
        started_ns = time.time_ns()
        entries, raw_errors, options_map = _parse_uncached(main)
        errors = [e.message for e in raw_errors]
        entries = _freeze_entries(entries)
        read = _files_read(main, options_map)
        tracked = _tracked(key, set(tracked_before), read, _literal_includes(main))
        after = _fingerprint(tracked)
        _FILES_SEEN[key] = tracked

        # Nothing tracked changed while the parse ran (tracked only grew, so
        # every pre-parse stat must still be there, identical).
        unchanged_during_parse = set(before) <= set(after)
        # Every tracked file was last written comfortably before the parse.
        settled = all(
            s[1] < started_ns - _MTIME_MARGIN_NS for s in after if s[1:] != _MISSING
        )
        # The previous parse's files are all still read (a shrunken set means an
        # include vanished or main is mid-edit; the next parse may cache).
        previous = _LAST_READ.get(key)
        shrank = previous is not None and not previous <= read
        _LAST_READ[key] = read
        trustworthy = (
            not _include_errors(raw_errors)
            and not shrank
            and unchanged_during_parse
            and settled
        )
        if trustworthy:
            cached = _CachedLoad(after, entries, errors, options_map)
            _LOAD_CACHE[key] = cached
            return _as_result(cached)
        _LOAD_CACHE.pop(key, None)
        return LoadResult(list(entries), list(errors), dict(options_map))

    def _invalidate(self) -> None:
        _invalidate_load_cache(self._cache_key)

    def _validate_after_write(self) -> list[str]:
        """validate() against a fresh parse: the write just made must be seen."""
        return self.validate()

    def _journal_path(self, filename: object) -> Path:
        """`filename` (an entry's `filename` metadata) resolved, refused with
        ValueError unless it lies inside this ledger's journal dir."""
        path = Path(str(filename)).resolve()
        if not path.is_relative_to(self.journal_dir.resolve()):
            raise ValueError(f"entry lives outside the journal dir: {path}")
        return path

    def _year_file_mismatches(self, entries: list) -> list[str]:
        """A transaction dated <year> must physically live in journal/<year>.beancount
        — the file split that read_year_text/write_year_text/the Raw Ledger screen
        rely on. Nothing in beancount itself enforces this (it just follows includes),
        so a bulk import/rebuild script can silently write a transaction into the
        wrong file, or into main.beancount directly, and every year-scoped screen
        built on that convention would then look empty or wrong for no visible
        reason. Catch that here instead of leaving it to be rediscovered as a bug."""
        errors = []
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            filename = Path(entry.meta.get("filename", ""))
            expected = self._year_template.format(year=entry.date.year)
            if filename.name != expected:
                errors.append(
                    f"transaction dated {entry.date} lives in {filename.name}, "
                    f"expected {expected} (txn-id: {entry.meta.get('txn-id', '?')})"
                )
        return errors

    def validate(self) -> list[str]:
        """Errors in the journal as it is on disk now: always a fresh parse."""
        result = self.load(fresh=True)
        return result.errors + self._year_file_mismatches(result.entries)

    @_writes
    def snapshot(self) -> Path:
        """Copy the journal dir into snapshots/<utc-timestamp>/journal. Returns the snapshot dir."""
        # Every journal write starts with a snapshot: drop the cached parse.
        self._invalidate()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        dest = self.snapshots_dir / stamp
        suffix = 0
        while dest.exists():
            suffix += 1
            dest = self.snapshots_dir / f"{stamp}_{suffix}"
        shutil.copytree(self.journal_dir, dest / "journal")
        return dest

    def _ensure_include(self, year: int) -> None:
        # The year file exists before main names it: a reader in between must
        # never see an include of a missing file.
        year_file = self._year_file(year)
        if not year_file.exists():
            write_text_atomic(year_file, "")
        directive = f'include "{self._year_template.format(year=year)}"'
        main = self.main_file.read_text(encoding="utf-8")
        if directive not in main:
            write_text_atomic(self.main_file, main.rstrip() + "\n" + directive + "\n")

    def _ensure_accounts_open(self, entries: list) -> None:
        main = self.main_file.read_text(encoding="utf-8")
        needed = {p.account for e in entries for p in getattr(e, "postings", [])}
        missing = [a for a in sorted(needed) if not self._is_open(main, a)]
        if missing:
            lines = [f"2000-01-01 open {a} {OPERATING_CURRENCY}" for a in missing]
            write_text_atomic(
                self.main_file, main.rstrip() + "\n" + "\n".join(lines) + "\n"
            )

    @staticmethod
    def _is_open(main_text: str, account: str) -> bool:
        """True when `main_text` carries an `open` directive for exactly this
        account -- anchored to a line and to the name's end, so a prefix
        sibling does not count as a match."""
        return (
            re.search(
                r"^\S+\s+open\s+" + re.escape(account) + r"(\s|$)",
                main_text,
                re.MULTILINE,
            )
            is not None
        )

    @_writes
    def open_named_accounts(self, accounts: list[str]) -> None:
        """Open the given (already-validated) account names in main.beancount.

        Skips names already open, matching the WHOLE account name on its own
        directive line -- a plain substring guard reads an account that is a
        prefix of an open one (Expenses:Shopping against an open
        Expenses:Shopping:Amazon) as already open, skips the directive, and
        leaves the caller's write failing validation. Reachable since the
        backlog screen lets a category be typed. Snapshots before writing and
        rolls back (raising LedgerValidationError) if the result is invalid.
        An empty / all-existing input is a no-op."""
        main = self.main_file.read_text(encoding="utf-8")
        missing = [a for a in accounts if not self._is_open(main, a)]
        if not missing:
            return
        snapshot = self.snapshot()
        try:
            lines = [f"2000-01-01 open {a} {OPERATING_CURRENCY}" for a in missing]
            write_text_atomic(
                self.main_file, main.rstrip() + "\n" + "\n".join(lines) + "\n"
            )
            errors = self._validate_after_write()
            if errors:
                self._restore(snapshot)
                raise LedgerValidationError(errors)
        except LedgerValidationError:
            raise
        except Exception:
            self._restore(snapshot)
            raise

    @_writes
    def rename_account(self, old: str, new: str) -> int:
        r"""Rewrite every whole-token occurrence of `old` to `new` across
        main.beancount and every *.beancount in the journal dir. Returns the
        number of occurrences replaced. Snapshots first; validates the whole
        journal and rolls back (raising LedgerValidationError) on error.

        The token boundary ``(?<![\w:-])...(?![\w:-])`` ensures a prefix sibling
        (HDFCBank) and a child (HDFC:Sub) are never touched. The rewrite is a
        literal whole-token text replacement across all files — if the account
        name appears inside a narration string, metadata value, or comment with
        token boundaries, it is rewritten too (this is harmless for journal
        validity but is intended/expected behaviour)."""
        pattern = re.compile(r"(?<![\w:-])" + re.escape(old) + r"(?![\w:-])")
        targets = [self.main_file] + sorted(
            p for p in self.journal_dir.glob("*.beancount") if p != self.main_file
        )
        snapshot = self.snapshot()
        try:
            total = 0
            for path in targets:
                text = path.read_text(encoding="utf-8")
                new_text, n = pattern.subn(lambda _m: new, text)
                if n:
                    write_text_atomic(path, new_text)
                    total += n
            errors = self._validate_after_write()
            if errors:
                self._restore(snapshot)
                raise LedgerValidationError(errors)
            return total
        except LedgerValidationError:
            raise
        except Exception:
            self._restore(snapshot)
            raise

    def account_balance(self, account: str) -> Inventory:
        """Net position of `account` as a beancount Inventory. Empty inventory
        (is_empty() is True) means the account is settled."""
        inv = Inventory()
        for entry in self.load(fresh=True).entries:
            if not isinstance(entry, data.Transaction):
                continue
            for p in entry.postings:
                if p.account == account and p.units is not None:
                    inv.add_amount(p.units)
        return inv

    @_writes
    def close_account(self, account: str, on_date: date) -> None:
        """Append `<on_date> close <account>` to the year file. Snapshots first;
        validates and rolls back (raising LedgerValidationError) on error. Caller
        guarantees the account is open and settled."""
        year = on_date.year
        snapshot = self.snapshot()
        try:
            self._ensure_include(year)
            year_file = self._year_file(year)
            with year_file.open("a", encoding="utf-8") as handle:
                handle.write(f"\n{on_date.isoformat()} close {account}\n")
            errors = self._validate_after_write()
            if errors:
                self._restore(snapshot)
                raise LedgerValidationError(errors)
        except LedgerValidationError:
            raise
        except Exception:
            self._restore(snapshot)
            raise

    @staticmethod
    def _check_meta_updates(updates: Mapping[str, MetaValue | None]) -> None:
        """ValueError/TypeError for a key or value that cannot be written."""
        for key, value in updates.items():
            if key in _RESERVED_META or not _META_KEY.fullmatch(key):
                raise ValueError(f"invalid metadata key: {key!r}")
            if value is not None:
                _render_meta_value(key, value)

    @staticmethod
    def _meta_changes(
        entry: data.Open, updates: Mapping[str, MetaValue | None]
    ) -> bool:
        current = {k: v for k, v in entry.meta.items() if k not in _RESERVED_META}
        return any(
            (key in current)
            if value is None
            else not (key in current and _same_meta_value(current[key], value))
            for key, value in updates.items()
        )

    @staticmethod
    def _merge_open_block(
        lines: list[str], lineno: int, updates: Mapping[str, MetaValue | None]
    ) -> None:
        """Rewrite, in place, the metadata block under the `open` directive on
        1-based line `lineno` of `lines`: the directive plus the indented
        `key:` lines directly below it (any other line ends the block). Keys
        in `updates` are re-rendered where they stand (None drops the line);
        keys not yet present are appended; other lines are kept verbatim."""
        start = lineno - 1
        end = start + 1
        while end < len(lines) and _META_LINE.match(lines[end]):
            end += 1
        newline = "\n"
        block: list[tuple[str, str]] = []
        for line in lines[start + 1 : end]:
            match = _META_LINE.match(line)
            if match is None:  # Unreachable: the loop above matched it.
                continue
            block.append((match.group(1), line.rstrip("\r\n")))
        seen = {key for key, _ in block}
        rendered: list[str] = []
        for key, line in block:
            if key not in updates:
                rendered.append(line)
                continue
            value = updates[key]
            if value is not None:
                rendered.append(f"  {key}: {_render_meta_value(key, value)}")
        for key, value in updates.items():
            if key not in seen and value is not None:
                rendered.append(f"  {key}: {_render_meta_value(key, value)}")
        directive = lines[start]
        if not directive.endswith(("\n", "\r")):
            directive += newline
        lines[start:end] = [directive] + [line + newline for line in rendered]

    def _write_open_meta(
        self, edits: list[tuple[Path, int, Mapping[str, MetaValue | None]]]
    ) -> None:
        """Apply `(path, lineno, updates)` block merges. Takes no snapshot and
        does not validate: the caller owns both, and calls this only under the
        folder's write lock (`set_open_meta_many`, `move_postings`), so the parsed
        line numbers cannot go stale. Each file is read and written once
        (atomically); its blocks are edited bottom-up so earlier line numbers
        stay valid."""
        by_path: dict[Path, list[tuple[int, Mapping[str, MetaValue | None]]]] = {}
        for path, lineno, updates in edits:
            by_path.setdefault(path, []).append((lineno, updates))
        for path, blocks in by_path.items():
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            for lineno, updates in sorted(blocks, key=lambda b: b[0], reverse=True):
                self._merge_open_block(lines, lineno, updates)
            write_text_atomic(path, "".join(lines))

    @_writes
    def set_open_meta(
        self, account: str, updates: Mapping[str, MetaValue | None]
    ) -> None:
        """Merge `updates` into the metadata block under `account`'s `open`
        directive. A `None` value removes the key; a key already present is
        re-rendered on its own line (keeping its position), new keys are
        appended two-space indented, and keys not in `updates` are kept
        verbatim.

        The block is located through the parsed `Open` entry's filename and
        lineno (from `load(fresh=True)`) -- not a text search -- so a
        prefix sibling's directive (`...:Acme` against `...:Acme1234`) can
        never be matched. It is the directive line plus the indented `key:`
        lines directly below it; any other line ends it.

        Runs under the folder's write lock from that parse through validate,
        so no other write can shift the line numbers in between;
        `_write_open_meta` is only ever called under it.

        Raises, before anything is touched (no snapshot): LookupError when the
        account has no `open` directive; ValueError/TypeError for a key or
        value that cannot be written; ValueError when the directive's file is
        not inside this journal dir. A merge that changes nothing (compared
        with the parsed metadata) is a no-op: no snapshot, no write.
        Otherwise snapshots first and validates the whole journal afterwards;
        a validation failure raises LedgerValidationError, and any failure
        (that one or any other exception, re-raised as is) restores the
        snapshot."""
        self.set_open_meta_many({account: updates})

    @_writes
    def set_open_meta_many(
        self, updates: Mapping[str, Mapping[str, MetaValue | None]]
    ) -> None:
        """`set_open_meta` for several accounts as one write: `updates` maps
        each account to its merge. Every key and value is checked and every
        `open` directive located (LookupError naming the first missing
        account) before anything is touched. Accounts whose merge changes
        nothing are skipped, so their blocks stay verbatim; when none
        changes there is no snapshot and no write. Otherwise one snapshot,
        one atomic write per file touched (blocks edited bottom-up), one
        fresh validate, and a restore of the snapshot on any failure."""
        for account_updates in updates.values():
            self._check_meta_updates(account_updates)
        if not updates:
            return
        opens = {
            e.account: e
            for e in self.load(fresh=True).entries
            if isinstance(e, data.Open) and e.account in updates
        }
        for account in updates:
            if account not in opens:
                raise LookupError(account)
        edits = [
            (
                self._journal_path(opens[account].meta["filename"]),
                opens[account].meta["lineno"],
                account_updates,
            )
            for account, account_updates in updates.items()
            if self._meta_changes(opens[account], account_updates)
        ]
        if not edits:
            return

        snapshot = self.snapshot()
        try:
            self._write_open_meta(edits)
            errors = self._validate_after_write()
            if errors:
                raise LedgerValidationError(errors)
        except Exception:
            self._restore(snapshot)
            raise

    @_writes
    def ensure_commodity_accounts(
        self,
        specs: list[tuple[str, str | None]],
        vehicle: str = Vehicle.MUTUAL_FUND,
        isins: dict[str, str] | None = None,
    ) -> None:
        """Idempotently ensure each commodity directive + its FIFO-booked holding
        account exists in main.beancount.

        `vehicle` selects the holding tree (see accounts.HOLDING_PREFIX); it
        defaults to mutual_fund so every pre-existing call site is unchanged.
        Stock lots live under Assets:Investment:Stock:<SYMBOL> in the main
        journal, and MUST be opened here rather than left to `append`'s
        `_ensure_accounts_open`, which would open them `... INR` (beancount
        rejects a currency constraint on a commodity account) and without
        "FIFO" booking.

        `isins`, when given, maps symbol -> ISIN and is written as `isin:`
        metadata on the commodity directive at the moment the symbol is FIRST
        opened (the directive is never rewritten afterwards). This is the
        instrument identity the deferred ISIN-based price resolution needs;
        the Groww stock export carries it per order, and it would otherwise be
        parsed and thrown away.

        `specs` is a list of (symbol, display_name). A commodity-holding account
        MUST be opened WITHOUT an INR currency constraint and WITH FIFO booking
        (beancount rejects `open <acct> INR` for a commodity account, and an
        empty redeem cost is ambiguous without a booking method). Written to main
        BEFORE Ledger.append runs so its _ensure_accounts_open substring guard
        (`f"open {a}" not in main`) skips these accounts and does not re-open them
        with INR.
        """
        main = self.main_file.read_text(encoding="utf-8")
        additions: list[str] = []
        seen: set[str] = set()
        for symbol, display_name in specs:
            if symbol in seen:
                continue
            seen.add(symbol)
            account = holding_account(symbol, vehicle)
            if f"commodity {symbol}" not in main:
                line = f"2000-01-01 commodity {symbol}"
                if display_name:
                    safe = display_name.replace('"', "'")
                    line += f'\n  name: "{safe}"'
                isin = (isins or {}).get(symbol)
                if isin:
                    safe_isin = isin.replace('"', "'")
                    line += f'\n  isin: "{safe_isin}"'
                additions.append(line)
            if f"open {account}" not in main:
                if vehicle == Vehicle.STOCK:
                    # Stock holding accounts are constrained to their own
                    # symbol, matching the user's pre-existing seven
                    # (main.beancount:98-104) so beancount rejects a posting
                    # of the wrong commodity into a stock account. Mutual
                    # fund accounts intentionally have no such constraint —
                    # see the docstring above re: the INR-constraint pitfall;
                    # a commodity constraint is a different, safe thing, but
                    # this branch must never widen to affect the MF form.
                    additions.append(f'2000-01-01 open {account} {symbol} "FIFO"')
                else:
                    additions.append(f'2000-01-01 open {account} "FIFO"')
        if additions:
            write_text_atomic(
                self.main_file, main.rstrip() + "\n" + "\n".join(additions) + "\n"
            )
            self._invalidate()

    @_writes
    def write_prices_file(self, filename: str, prices: dict[str, NavRow]) -> None:
        """Rewrite journal/<filename> with exactly one latest `price` directive
        per symbol, and ensure main.beancount includes it.

        One file per feed: the AMFI NAV feed owns prices.beancount and the NSE
        bhavcopy feed owns prices-equity.beancount, so neither can erase the
        other's directives (this method always rewrites its file wholesale).

        Snapshots first, validates the whole journal after writing, and rolls
        back (raising LedgerValidationError) if the result is invalid — same
        guarantee as append/write_year_text. An empty `prices` clears the file.
        """
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            raise ValueError(f"invalid price filename: {filename!r}")

        directive = f'include "{filename}"'
        prices_file = self.journal_dir / filename
        lines = [
            f"{row.date.isoformat()} price {symbol} {row.nav} INR"
            for symbol, row in sorted(prices.items())
        ]
        text = ("\n".join(lines) + "\n") if lines else ""

        # Short-circuit no-op writes. refresh() (incl. the best-effort startup
        # refresh on every boot and 304/offline fallbacks) calls this on every
        # run; without this guard each call would snapshot the whole journal and
        # rewrite an identical file — an unbounded disk leak. Only snapshot +
        # write when the rendered text or the include directive actually changes.
        current_text = (
            prices_file.read_text(encoding="utf-8") if prices_file.exists() else ""
        )
        include_present = directive in self.main_file.read_text(encoding="utf-8")
        if current_text == text and include_present:
            return

        snapshot = self.snapshot()
        try:
            # Atomic replaces, price file first: a reader (a screen while the
            # background refresh runs) must never parse a truncated main or
            # price file, nor an include of a price file not written yet.
            write_text_atomic(prices_file, text)
            main = self.main_file.read_text(encoding="utf-8")
            if directive not in main:
                write_text_atomic(
                    self.main_file, main.rstrip() + "\n" + directive + "\n"
                )
            errors = self._validate_after_write()
            if errors:
                self._restore(snapshot)
                raise LedgerValidationError(errors)
        except LedgerValidationError:
            raise
        except Exception:
            self._restore(snapshot)
            raise

    @_writes
    def write_latest_prices(self, prices: dict[str, NavRow]) -> None:
        """The AMFI NAV feed's price file. See write_prices_file."""
        self.write_prices_file("prices.beancount", prices)

    @_writes
    def _restore(self, snapshot: Path) -> None:
        shutil.rmtree(self.journal_dir)
        shutil.copytree(snapshot / "journal", self.journal_dir)
        self._invalidate()

    @_writes
    def restore(self, snapshot: Path) -> Path:
        """Put the whole journal dir back as `snapshot` (a dir `snapshot()`,
        `append()` or `replace_entries()` returned) holds it, and drop the
        cached parse. Returns a snapshot of the state it replaced, taken
        first, so a restore can itself be undone.

        Every write made after `snapshot` was taken is discarded, whoever
        made it. A caller undoing its own write must therefore hold
        `self.write_lock()` from that write through this restore (as
        `with ledger.write_lock(): snap = ledger.append(...); ...;
        ledger.restore(snap)`), or another writer's change in between is
        silently erased.

        Refuses with ValueError, before anything is touched, a path that is
        not one of this ledger's snapshots."""
        source = Path(snapshot) / "journal"
        if not (
            source.is_dir()
            and Path(snapshot).resolve().is_relative_to(self.snapshots_dir.resolve())
        ):
            raise ValueError(f"not a snapshot of this journal: {snapshot}")
        undo = self.snapshot()
        self._restore(Path(snapshot))
        return undo

    def _write_appended(self, entries: Sequence[data.Directive]) -> None:
        """Append each entry to its year file (included from main first).
        No snapshot, no validation: the caller owns both."""
        for entry in entries:
            year = entry.date.year
            self._ensure_include(year)
            year_file = self._year_file(year)
            with year_file.open("a", encoding="utf-8") as handle:
                handle.write("\n" + printer.format_entry(entry))

    @_writes
    def append(self, entries: list) -> Path | None:
        """Snapshot, write entries to their year files, then validate. Roll
        back on any error. Returns the snapshot taken (None for no entries,
        which takes none)."""
        if not entries:
            return None
        snapshot = self.snapshot()
        try:
            self._ensure_accounts_open(entries)
            self._write_appended(entries)
            errors = self._validate_after_write()
            if errors:
                self._restore(snapshot)
                raise LedgerValidationError(errors)
        except LedgerValidationError:
            raise
        except Exception:
            self._restore(snapshot)
            raise
        return snapshot

    def _locate_block(
        self,
        old: data.Directive,
        parsed_at: Mapping[tuple[str, int], data.Directive],
        files: dict[Path, list[str]],
    ) -> tuple[Path, int, int]:
        """`old`'s text block as (file, start, end) line indexes into
        `files[file]` (read here once per file), or ENTRY_NOT_LOCATABLE when
        the fresh parse has no such directive at old's filename/lineno, or the
        block there does not parse back to `old`."""
        meta = old.meta or {}
        filename, lineno = meta.get("filename"), meta.get("lineno")
        label = meta.get("txn-id") or f"at line {lineno}"
        where = Path(str(filename)).name

        def refuse() -> CodedError:
            return ENTRY_NOT_LOCATABLE.exc({"txn_id": label, "year_file": where})

        if not isinstance(filename, str) or not isinstance(lineno, int):
            raise refuse()
        current = parsed_at.get((filename, lineno))
        if current is None or type(current) is not type(old):
            raise refuse()
        try:
            path = self._journal_path(filename)
        except ValueError:
            raise refuse() from None
        if path not in files:
            files[path] = _split_lines(path.read_bytes().decode("utf-8"))
        lines = files[path]
        if not 1 <= lineno <= len(lines):
            raise refuse()
        start, end = _block_span(lines, lineno)
        if not _same_directive(_parse_directive("".join(lines[start:end])), old):
            raise refuse()
        return path, start, end

    @_writes
    def replace_entries(
        self,
        replacements: Sequence[tuple[data.Directive, Sequence[data.Directive]]],
        *,
        append: Sequence[data.Directive] = (),
    ) -> Path:
        """Rewrite directives where they stand, under ONE snapshot.

        Each `(old, new)` pair replaces old's text block -- its directive line
        and the indented lines below it, up to the first blank or unindented
        line -- with `new` printed, blank-line separated; an empty `new`
        deletes the block. A `new` dated in another year than `old` is
        appended to that year's file instead. `append` entries are then
        appended as `append()` writes them (accounts opened as there).

        `old` is located by the `filename`/`lineno` a load recorded on it,
        against a fresh parse taken under the write lock. Its block must
        parse to exactly one directive that is `old` (same type, date,
        narration or account, and postings' account, units and cost);
        otherwise ENTRY_NOT_LOCATABLE is raised before any snapshot or write.
        A block hand-written in its own style (no txn-id, other indentation)
        is fine: only what it says is compared.

        Placement is by position, not date: a same-year `new` takes old's
        place even when its date changed, and a moved one goes to the end of
        its year file. beancount sorts directives by date before booking and
        checking balances, so this is cosmetic only.

        Then the whole journal is validated; a validation failure raises
        LedgerValidationError, and ANY failure restores the snapshot. The
        load cache is dropped either way. Returns the snapshot taken."""
        parsed_at = {
            (e.meta["filename"], e.meta["lineno"]): e
            for e in self.load(fresh=True).entries
            if e.meta and "filename" in e.meta and "lineno" in e.meta
        }
        files: dict[Path, list[str]] = {}
        spans: dict[Path, list[tuple[int, int, list[data.Directive]]]] = {}
        moved: list[data.Directive] = []
        for old, news in replacements:
            path, start, end = self._locate_block(old, parsed_at, files)
            if any(s < end and start < e for s, e, _ in spans.get(path, ())):
                raise ValueError(f"the same entry is replaced twice: {old}")
            here = [n for n in news if n.date.year == old.date.year]
            moved.extend(n for n in news if n.date.year != old.date.year)
            spans.setdefault(path, []).append((start, end, here))

        snapshot = self.snapshot()
        try:
            for path, blocks in spans.items():
                lines = files[path]
                # Bottom-up, so an earlier block's line indexes stay right.
                for start, end, here in sorted(
                    blocks, key=lambda b: b[0], reverse=True
                ):
                    eol = "\r\n" if lines[start].endswith("\r\n") else "\n"
                    text = "\n".join(printer.format_entry(n) for n in here)
                    new_lines = _split_lines(text.replace("\n", eol))
                    if not new_lines and _blank_before(lines, start):
                        # A deleted block takes one separating blank line with it.
                        if end < len(lines) and not lines[end].strip():
                            end += 1
                    lines[start:end] = new_lines
                data_bytes = "".join(lines).encode("utf-8")
                replace_atomic(path, lambda tmp, b=data_bytes: tmp.write_bytes(b))
            if append:
                self._ensure_accounts_open(list(append))
            self._write_appended([*moved, *append])
            errors = self._validate_after_write()
            if errors:
                raise LedgerValidationError(errors)
        except Exception:
            # Several files may already be written: every escape restores.
            self._restore(snapshot)
            raise
        # Defence in depth, deliberately untested: snapshot() and the fresh
        # validate parse already leave no stale cached parse behind.
        self._invalidate()
        return snapshot

    def read_year_text(self, year: int) -> tuple[str, str]:
        """Return the raw text of journal/<year>.beancount and its sha256 hex.
        A missing file reads as empty text with the empty-string hash."""
        year_file = self._year_file(year)
        data_bytes = year_file.read_bytes() if year_file.exists() else b""
        return data_bytes.decode("utf-8"), hashlib.sha256(data_bytes).hexdigest()

    @_writes
    def write_year_text(self, year: int, text: str, expected_hash: str) -> str:
        """Replace journal/<year>.beancount with `text`, guarded by `expected_hash`.
        Snapshots first; validates the whole journal after writing and rolls back
        (raising LedgerValidationError) if the new text leaves it invalid. Returns
        the new file hash. Raises HashMismatchError if the file changed since read."""
        year_file = self._year_file(year)
        current = year_file.read_bytes() if year_file.exists() else b""
        actual_hash = hashlib.sha256(current).hexdigest()
        if actual_hash != expected_hash:
            raise HashMismatchError(expected_hash, actual_hash)

        snapshot = self.snapshot()
        try:
            self._ensure_include(year)
            write_text_atomic(year_file, text)
            errors = self._validate_after_write()
            if errors:
                self._restore(snapshot)
                raise LedgerValidationError(errors)
        except LedgerValidationError:
            raise
        except Exception:
            self._restore(snapshot)
            raise
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @_writes
    def recategorize(self, txn_ids: list[str], old: str, new: str) -> int:
        """Move the `old` posting to `new` on each named transaction.

        Targeted by txn-id rather than by text, unlike `rename_account`: this
        reclassifies a merchant's history (every Swiggy row from
        Expenses:Uncategorized to Expenses:Food) and must not touch a
        narration, a metadata value, or another merchant's rows that happen to
        share the account name.

        One `new` for every txn-id is exactly `recategorize_many` with a plan
        whose every value is the same account, so this delegates rather than
        repeating it: the two had identical snapshot/locate/validate/restore
        bodies, and that agreement was held only by copy-paste -- a fix to
        either had to be remembered twice. Every guarantee below is
        `recategorize_many`'s.

        Amounts are never touched -- only which account a posting names -- so
        the entry balances exactly as before and no rupee moves. Snapshots
        before the first write (including before opening `new`) and restores
        that snapshot on EVERY failure path, so a batch is all-or-nothing:
        rewriting entry 1 and failing to locate entry 2 must not leave entry 1
        rewritten. Returns the number of postings rewritten.
        """
        return self.recategorize_many({txn_id: new for txn_id in txn_ids}, old)

    @_writes
    def recategorize_many(self, plan: Mapping[str, str], old: str) -> int:
        """Move the `old` posting to a per-transaction target account across
        many transactions in one pass.

        Generalizes `recategorize`: that method takes a single `new` account
        for every txn-id, which is exactly what a bulk classify of 241
        merchants cannot use -- different merchants land in different
        categories. `plan` maps txn-id to the account its `old` postings
        become, so one call can carry the whole batch.

        The point of a dedicated method rather than looping `recategorize`
        once per merchant: `recategorize` does its own `load()` (an uncached
        full beancount re-parse), `snapshot()` and `validate()` per call. 241
        calls would mean 241 parses and up to 241 full journal snapshot
        copies on a single-user local app with no snapshot pruning. This
        method pays each of those costs exactly once for the whole plan.

        A txn-id in `plan` that is not found in the journal is ignored, not
        an error -- the caller may be working from a merchant list that is
        slightly stale. Amounts are never touched, only which account a
        posting names, so every entry balances exactly as before. Snapshots
        before opening any target account (so a rollback removes any `open`
        directive this call added) and restores that snapshot on EVERY
        failure path -- locate failure or validation failure alike -- so the
        whole batch is all-or-nothing. Returns the total number of postings
        rewritten.
        """
        if not plan:
            return 0
        entries = [
            e
            for e in self.load(fresh=True).entries
            if isinstance(e, data.Transaction) and e.meta.get("txn-id") in plan
        ]
        if not entries:
            return 0

        # Snapshot BEFORE opening targets: a rolled-back batch into brand-new
        # categories must not leave their `open` directives behind.
        snapshot = self.snapshot()
        self.open_named_accounts(sorted(set(plan.values())))
        try:
            rewritten = self._rewrite_postings(entries, plan, old)
            errors = self._validate_after_write()
            if errors:
                raise LedgerValidationError(errors)
            return rewritten
        except Exception:
            # No `except LedgerValidationError: raise` short-circuit, unlike
            # the single-write methods above: this writes one file per year
            # touched, so ANY escape -- validation failure, or a locate
            # failure in a later year after an earlier one was already written
            # -- has to restore the snapshot.
            self._restore(snapshot)
            raise

    def _rewrite_postings(
        self, entries: list, plan: Mapping[str, str], old: str
    ) -> int:
        """Rewrite each entry's `old` postings to `plan[txn_id]`, in place in
        the file it was parsed from (located by its parsed line number, so
        `entries` must come from a fresh parse under the write lock, before
        any write that adds or removes lines). Takes no snapshot and does not validate: the caller owns both,
        and restores its snapshot on any exception raised here (an entry whose
        text cannot be located raises ENTRY_NOT_LOCATABLE). Returns the number
        of postings rewritten."""
        rewritten = 0
        # Called only under the folder's write lock (`recategorize_many`,
        # `move_postings`), after a fresh parse taken under that same lock.
        # Grouped by the file each entry was parsed from, and each file read
        # once / written once: the whole accepted backlog arrives in ONE call,
        # so a per-entry read-modify-write meant ~2000 full reads and writes
        # of a ~400 KB year file inside a single synchronous request. Each
        # entry's block is located (by its parsed line) and verified before
        # its file is written, and any escape restores the caller's one
        # snapshot.
        by_file: dict[str, list[tuple[str, data.Transaction]]] = {}
        for entry in entries:
            if not any(p.account == old for p in entry.postings):
                continue
            entry_id = entry.meta.get("txn-id")
            if entry_id is None or entry_id not in plan:
                continue
            filename = entry.meta.get("filename") or str(
                self._year_file(entry.date.year)
            )
            by_file.setdefault(filename, []).append((entry_id, entry))
        journal_dir = os.path.realpath(self.journal_dir)
        for filename, file_entries in by_file.items():
            year_file = Path(filename)
            if os.path.dirname(os.path.realpath(year_file)) != journal_dir:
                # Only this folder is snapshotted, so only it is written.
                raise ENTRY_NOT_LOCATABLE.exc(
                    {"txn_id": file_entries[0][0], "year_file": year_file.name}
                )
            # Read and written as bytes, so no newline translation: a CRLF
            # file stays CRLF and untouched lines are written back byte for
            # byte. A rewrite never adds or removes a line, so every other
            # entry's recorded line number stays right.
            lines = _split_lines(year_file.read_bytes().decode("utf-8"))
            for txn_id, entry in file_entries:
                count = _rewrite_in_place(lines, entry, old, plan[txn_id])
                if count is None:
                    # The text at the entry's recorded line is not that entry
                    # (the file changed under the parse, or a hand edit the
                    # in-place rewrite cannot follow), or the rewrite would
                    # not parse back to the intended entry. Not an imbalance:
                    # its own code so the user is not told to "add the missing
                    # leg". Raised before this file is written -- and a file
                    # already written is undone by the caller's restore.
                    raise ENTRY_NOT_LOCATABLE.exc(
                        {"txn_id": txn_id, "year_file": year_file.name}
                    )
                rewritten += count
            data_bytes = "".join(lines).encode("utf-8")
            replace_atomic(year_file, lambda tmp: tmp.write_bytes(data_bytes))
        return rewritten

    @_writes
    def move_postings(
        self,
        plan: Mapping[str, str],
        old: str,
        open_meta: Mapping[str, Mapping[str, MetaValue]],
        *,
        close: Collection[str] = (),
        close_on: date | None = None,
    ) -> int:
        """Move the `old` postings of each txn-id in `plan` to its target,
        opening the targets that are not open yet and closing the `close`
        ones, all under ONE snapshot.

        "Open" means an `open` directive anywhere in the journal (any file),
        from `load(fresh=True)`. Each target not yet open gets
        `2000-01-01 open <target> INR` in main.beancount, followed by its
        `open_meta` (a str quoted, a number bare; a None value is skipped). A
        target already open has its non-None `open_meta` merged into its
        existing block, as `set_open_meta` would, but inside this snapshot.
        Postings are rewritten as in `recategorize_many` (amounts untouched).
        Each account in `close` gets a `<close_on> close <account>` directive
        in that year's file. Then the whole journal is validated. ANY failure
        -- an entry that cannot be located (ENTRY_NOT_LOCATABLE), a validation
        error, anything -- restores the snapshot, opens and merges included.
        Unlike `close_account`, the closes take no snapshot of their own.
        Holds the folder's write lock from the parse through validate, so the
        parsed line numbers `_write_open_meta` and `_rewrite_postings` act on
        cannot go stale. Returns the postings moved."""
        if close and close_on is None:
            raise ValueError("close_on is required to close accounts")
        targets = sorted(set(plan.values()))
        wanted: dict[str, dict[str, MetaValue]] = {}
        for target in targets:
            values = {
                key: value
                for key, value in (open_meta.get(target) or {}).items()
                if value is not None
            }
            self._check_meta_updates(values)
            wanted[target] = values
        loaded = self.load(fresh=True).entries
        opened = {e.account: e for e in loaded if isinstance(e, data.Open)}
        entries = [
            e
            for e in loaded
            if isinstance(e, data.Transaction) and e.meta.get("txn-id") in plan
        ]
        merges: list[tuple[Path, int, Mapping[str, MetaValue | None]]] = []
        new_opens: list[str] = []
        for target in targets:
            existing = opened.get(target)
            if existing is None:
                new_opens.append(f"2000-01-01 open {target} {OPERATING_CURRENCY}")
                new_opens.extend(
                    f"  {key}: {_render_meta_value(key, value)}"
                    for key, value in wanted[target].items()
                )
            elif wanted[target] and self._meta_changes(existing, wanted[target]):
                path = self._journal_path(existing.meta["filename"])
                merges.append((path, existing.meta["lineno"], wanted[target]))

        snapshot = self.snapshot()
        try:
            # Postings first: `_rewrite_postings` locates each entry by its
            # parsed line number, and an open-meta merge adds lines (an `open`
            # can sit in a year file above the entries). A posting rewrite
            # never adds or removes a line, so the merges' line numbers stay
            # right after it. Merge before appending the new opens to main,
            # which cannot shift a merged block's line numbers.
            moved = self._rewrite_postings(entries, plan, old)
            self._write_open_meta(merges)
            if new_opens:
                main = self.main_file.read_text(encoding="utf-8")
                write_text_atomic(
                    self.main_file,
                    main.rstrip() + "\n" + "\n".join(new_opens) + "\n",
                )
            if close:
                assert close_on is not None
                self._ensure_include(close_on.year)
                with self._year_file(close_on.year).open("a", encoding="utf-8") as fh:
                    for account in close:
                        fh.write(f"\n{close_on.isoformat()} close {account}\n")
            errors = self._validate_after_write()
            if errors:
                raise LedgerValidationError(errors)
            return moved
        except Exception:
            self._restore(snapshot)
            raise

    @_writes
    def annotate_posting(
        self, txn_id: str, account: str, fields: dict, expected_hash: str
    ) -> None:
        """Locate the entry by txn-id, check the year file hasn't changed since it was
        loaded, then merge `fields` into the matching posting's metadata. Snapshots
        first; rolls back on any failure. Never touches posting amounts."""
        entries = self.load(fresh=True).entries
        entry = next(
            (
                e
                for e in entries
                if isinstance(e, data.Transaction) and e.meta.get("txn-id") == txn_id
            ),
            None,
        )
        if entry is None:
            raise TransactionNotFoundError(txn_id)
        posting = next((p for p in entry.postings if p.account == account), None)
        if posting is None:
            raise PostingNotFoundError(txn_id, account)

        year_file = self._year_file(entry.date.year)
        actual_hash = hashlib.sha256(year_file.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise HashMismatchError(expected_hash, actual_hash)

        snapshot = self.snapshot()
        try:
            text = year_file.read_text(encoding="utf-8")
            old_block = printer.format_entry(entry)
            if old_block not in text:
                raise LedgerValidationError(
                    ["could not locate the exact entry text to annotate"]
                )
            new_meta = dict(posting.meta or {})
            new_meta.update(fields)
            new_postings = [
                p._replace(meta=new_meta) if p is posting else p for p in entry.postings
            ]
            new_entry = entry._replace(postings=new_postings)
            new_block = printer.format_entry(new_entry)
            write_text_atomic(year_file, text.replace(old_block, new_block, 1))
            errors = self._validate_after_write()
            if errors:
                self._restore(snapshot)
                raise LedgerValidationError(errors)
        except LedgerValidationError:
            raise
        except Exception:
            self._restore(snapshot)
            raise
