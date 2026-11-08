"""Pure parser for the AMFI NAVAll.txt full daily dump.

Two column shapes, both semicolon-delimited, interleaved with section-banner
lines (no ';'), blank lines, and a header line:

    6-column (legacy): Scheme Code;ISIN.../ISIN Growth;ISIN...;Scheme Name;
                        Net Asset Value;Date
    8-column (current, confirmed against a real fetch of
              https://www.amfiindia.com/spages/NAVAll.txt on 2026-09-05 --
              AMFI split "Scheme Name" into three columns at some point
              after 2026-08-14):
                        Scheme Code;ISIN.../ISIN Growth;ISIN...;Scheme Name;
                        Plan;Option;Net Asset Value;Date

The 8-column shape's Scheme Name/Plan/Option are joined with " - " to
reconstruct the same combined-name string the 6-column shape always carried
in one field — every consumer (SchemeMaster, the ledger's commodity names)
expects that convention regardless of which shape the feed happens to use
that day.

No IO and no framework imports — this is a primitive sibling of service/ingest/parsers/csv_xls.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from backend.app.models.domain.nav import NavRow

SchemeCategory = Literal["equity", "debt", "other"]

# Tolerant date parsing: AMFI emits "24-Jun-2026".
_DATE_FORMATS = ("%d-%b-%Y", "%d-%B-%Y")


@dataclass(frozen=True)
class NavAllDump:
    schemes: dict[str, str]  # code -> scheme name
    navs: dict[str, NavRow]  # code -> latest NavRow (only when NAV parses)
    categories: dict[str, SchemeCategory]  # code -> classified category


def _parse_date(value: str) -> date | None:
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _classify_category(banner: str) -> SchemeCategory:
    """Classify an AMFI section-banner line into a broad tax-lot category.

    Case-insensitive substring match. Conservative default: anything that
    doesn't clearly say "equity scheme" or "debt/income scheme" falls to
    "other" (e.g. AMC/fund-house name lines, "Index Funds" groups)."""
    lowered = banner.lower()
    if "equity scheme" in lowered:
        return "equity"
    if "debt scheme" in lowered or "income scheme" in lowered:
        return "debt"
    return "other"


def _is_category_banner(line: str) -> bool:
    """Heuristic to distinguish a real category-group banner (contains the
    word "scheme") from an AMC/fund-house name line (doesn't) among the
    non-semicolon lines interleaved in the dump."""
    return "scheme" in line.lower()


def _parse_nav(value: str) -> Decimal | None:
    text = value.strip()
    if not text:
        return None
    try:
        nav = Decimal(text)
    except InvalidOperation:
        return None
    if nav <= 0:
        return None
    return nav


def parse_navall(text: str) -> NavAllDump:
    schemes: dict[str, str] = {}
    navs: dict[str, NavRow] = {}
    categories: dict[str, SchemeCategory] = {}
    current_category: SchemeCategory = "other"
    for line in text.splitlines():
        if ";" not in line:
            if _is_category_banner(line):
                current_category = _classify_category(line)
            continue  # section banner, AMC name, or blank line
        parts = [p.strip() for p in line.split(";")]
        if len(parts) >= 8:
            # Current shape: Scheme Name;Plan;Option split into three fields.
            code = parts[0]
            name = " - ".join(p for p in (parts[3], parts[4], parts[5]) if p)
            nav_text, date_text = parts[6], parts[7]
        elif len(parts) >= 6:
            # Legacy shape: the full descriptive name in one field.
            code, name, nav_text, date_text = parts[0], parts[3], parts[4], parts[5]
        else:
            continue  # malformed row
        if not code.isdigit() or not name:
            continue  # header row ("Scheme Code") or nameless row
        schemes[code] = name
        categories[code] = current_category
        nav = _parse_nav(nav_text)
        dt = _parse_date(date_text)
        if nav is not None and dt is not None:
            navs[code] = NavRow(scheme_code=code, nav=nav, date=dt)
    return NavAllDump(schemes=schemes, navs=navs, categories=categories)
