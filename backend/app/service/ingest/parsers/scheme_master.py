"""Resolve a mutual-fund scheme NAME to its AMFI code, against the AMFI NAVAll
dump (the code/name columns). The NAV column is ignored here; the NAV
PriceService re-uses the same canonical parse. No network access in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, cast

from backend.app.core.errors.errors import SCHEME_MASTER_MISSING
from backend.app.clients.navall import (
    NavAllDump,
    SchemeCategory,
    parse_navall,
)

_NOISE = re.compile(r"[^a-z0-9]+")
_VALID_CATEGORIES = {"equity", "debt", "other"}

# Words that carry no identity, only house style. AMFI publishes
# "X Fund - Direct Plan - Growth Option"; Groww exports the same scheme as
# "X Direct Growth". Dropping these three words from both sides makes the two
# spellings identical without making any two REAL schemes identical -- across
# the full 9,806-scheme AMFI master this collapses to 9,804 canonical keys,
# and both collisions are AMFI listing the same scheme name twice.
_FILLER = re.compile(r"\b(?:plan|option|fund)\b", re.IGNORECASE)


def _is_navall_dump(text: str) -> bool:
    """True when any numeric-coded row has >= 6 fields (legacy or current
    NAVAll layout). The cache CSVs top out at 5 fields."""
    for line in text.splitlines():
        if line.count(";") >= 5 and line.split(";", 1)[0].strip().isdigit():
            return True
    return False


def _normalize(name: str) -> str:
    """Lowercase, drop all non-alphanumeric separators, collapse to a compact
    key so case/whitespace/punctuation differences don't block a match."""
    return _NOISE.sub("", name.lower())


def _canonical(name: str) -> str:
    """`_normalize` with the boilerplate words removed as well.

    Strictly weaker than `_normalize`, and still EXACT: it is cleanup followed
    by an equality test, never a similarity score. "Quant" does not become
    "Quantum", and a scheme the master does not list stays unmatched.
    """
    return _normalize(_FILLER.sub(" ", name))


@dataclass(frozen=True)
class SchemeMatch:
    code: str
    name: str
    category: SchemeCategory = "other"

    @property
    def symbol(self) -> str:
        # Beancount symbols cannot start with a digit, so prefix the AMFI code.
        return f"MF{self.code}"


class SchemeMaster:
    """An in-memory normalized-name -> (code, canonical name) index."""

    def __init__(self, by_key: dict[str, SchemeMatch]) -> None:
        self._by_key = by_key
        # Secondary index keyed by the raw AMFI numeric code (e.g. "122639",
        # not the "MF"-prefixed symbol) — needed at commit time, where only
        # the commodity symbol (MF<code>) is available and the scheme's
        # category must be looked up to classify a realized gain.
        self._by_code = {match.code: match for match in by_key.values()}
        # Canonical index. A key is usable only when exactly ONE scheme owns
        # it -- see `resolve_canonical`.
        self._by_canonical: dict[str, list[SchemeMatch]] = {}
        for match in by_key.values():
            self._by_canonical.setdefault(_canonical(match.name), []).append(match)

    def __len__(self) -> int:
        return len(self._by_key)

    @classmethod
    def from_navall(cls, dump: NavAllDump) -> "SchemeMaster":
        by_key = {
            _normalize(name): SchemeMatch(
                code=code, name=name, category=dump.categories.get(code, "other")
            )
            for code, name in dump.schemes.items()
        }
        return cls(by_key)

    @classmethod
    def from_text(cls, text: str) -> "SchemeMaster":
        """Parse a NAVAll dump (6- or 8-column), the bumped 5-column cache
        format (code;name;nav;date;category), or the original 4-column cache
        format (code;name;nav;date).

        A text is a NAVAll dump when ANY line with a numeric scheme code has
        six or more `;`-separated fields. Cache rows never exceed five fields
        (name, nav, date and category cannot contain `;` in a file this app
        writes), so a cache CSV cannot be mistaken for a dump. A dump goes
        wholesale through `parse_navall`, the one parser that joins Scheme
        Name / Plan / Option and tracks the category banners; this module
        keeps no second implementation of that shape.
          - == 5: category read directly from index 4 (falls back to "other"
            if not a recognized value).
          - == 4: no category available -> "other".
        """
        if _is_navall_dump(text):
            return cls.from_navall(parse_navall(text))
        by_key: dict[str, SchemeMatch] = {}
        for line in text.splitlines():
            if ";" not in line:
                continue
            parts = [p.strip() for p in line.split(";")]
            code = parts[0]
            if not code.isdigit():
                continue
            if len(parts) == 5:
                name = parts[1]
                raw_category = parts[4]
                category = (
                    cast(SchemeCategory, raw_category)
                    if raw_category in _VALID_CATEGORIES
                    else "other"
                )
            elif len(parts) == 4:
                name = parts[1]
                category = "other"
            else:
                continue
            if not name:
                continue
            by_key[_normalize(name)] = SchemeMatch(
                code=code, name=name, category=category
            )
        return cls(by_key)

    @classmethod
    def from_csv(
        cls, path: Path | str, searched: Sequence[Path | str] | None = None
    ) -> "SchemeMaster":
        """Load a scheme master file (cache CSV or NAVAll dump). `searched`
        lists every location that was tried, so a missing-file error can name
        them all rather than only `path`."""
        path = Path(path)
        tried = ", ".join(str(p) for p in (searched or [path]))
        if not path.exists():
            raise SCHEME_MASTER_MISSING.exc({"path": tried})
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SCHEME_MASTER_MISSING.exc({"path": tried}) from exc
        return cls.from_text(text)

    def resolve(self, name: str) -> SchemeMatch | None:
        return self._by_key.get(_normalize(name))

    def resolve_canonical(self, name: str) -> SchemeMatch | None:
        """Resolve a scheme whose name differs from AMFI's only in boilerplate.

        `resolve()` is exact and therefore brittle against a broker's house
        style: measured against the user's real Groww exports it matched 1 of
        26 distinct scheme names, blocking all 294 order rows at review. This
        matches on the canonical form instead and lifts that to 22 of 26.

        Returns None when the canonical key is owned by two or more schemes.
        That case is genuine ambiguity -- the name no longer identifies one
        fund -- and the caller must send it to review rather than pick a side.
        """
        owners = self._by_canonical.get(_canonical(name), [])
        return owners[0] if len(owners) == 1 else None

    def resolve_by_code(self, code: str) -> SchemeMatch | None:
        """Look up a scheme by its raw AMFI numeric code (e.g. "122639"),
        not the "MF"-prefixed commodity symbol."""
        return self._by_code.get(code)

    def search(self, query: str, limit: int = 20) -> list[SchemeMatch]:
        """Find schemes a human could plausibly mean by `query`.

        `resolve()` is exact: it answers "is this string the scheme's name",
        which is the right question during parsing and the wrong one during
        review. A row reaches review precisely BECAUSE the exact match failed,
        so the screen that fixes it needs a way to look the scheme up by the
        fragments a person remembers -- "parag flexi", "sbi small cap".

        Every query token must appear in the scheme's compacted name (AND, not
        OR): "sbi small" must not return every SBI fund and every small-cap
        fund. Separators are already stripped from both sides, so "flexi cap"
        matches "Flexi-Cap" and "FlexiCap" alike. A numeric query is also
        tried as an AMFI code prefix, since the code is what the user may be
        copying off a statement.

        Ranked closest-first: exact name, then name-prefix, then shortest name
        -- the plain scheme outranks its dozen plan variants, which is what
        makes the first result usually right. Ties break alphabetically so the
        order is stable across calls.

        Returns [] for a query under three characters rather than the whole
        12,000-scheme master: an unfiltered dump is not a search result, and
        picking blindly from one is exactly the silent misidentification this
        screen exists to prevent.
        """
        normalized = _normalize(query)
        if len(normalized) < 3:
            return []
        tokens = [t for t in _NOISE.split(query.lower()) if t]
        if not tokens:
            return []

        hits: list[SchemeMatch] = []
        for key, match in self._by_key.items():
            if all(token in key for token in tokens) or match.code.startswith(
                normalized
            ):
                hits.append(match)

        def rank(match: SchemeMatch) -> tuple[int, int, str]:
            key = _normalize(match.name)
            if key == normalized:
                tier = 0
            elif key.startswith(normalized):
                tier = 1
            else:
                tier = 2
            return (tier, len(match.name), match.name)

        hits.sort(key=rank)
        return hits[:limit]


def scheme_master_search_paths(
    storage_dir: Path, override: Path | str | None = None
) -> list[Path]:
    """Every location the scheme master is looked for, in preference order.

    An explicit choice (the `override` argument, or a scheme_master_path the
    user actually set, e.g. via SPENDSIFT_SCHEME_MASTER_PATH) is the only
    candidate. Otherwise the AMFI dump the price pipeline keeps fresh at
    `<storage_dir>/prices/navall.txt` comes first, then the configured default
    CSV: only the dump carries scheme categories."""
    if override is not None:
        return [Path(override)]
    from backend.app.core.settings import get_settings

    settings = get_settings()
    configured = Path(settings.scheme_master_path)
    if not configured.is_absolute():
        configured = storage_dir / configured
    if "scheme_master_path" in settings.model_fields_set:
        return [configured]
    return [storage_dir / "prices" / "navall.txt", configured]


def resolve_scheme_master_path(
    storage_dir: Path, override: Path | str | None = None
) -> Path:
    """Resolve the AMFI scheme-master file path: the first existing candidate
    of `scheme_master_search_paths`, else the last one (which then does not
    exist, so callers report it missing)."""
    candidates = scheme_master_search_paths(storage_dir, override)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]
