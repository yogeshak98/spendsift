"""Beancount transaction tags: normalise what a user typed, or refuse it.

A tag in the journal is `#name`, and beancount's lexer accepts only
`[A-Za-z0-9\\-_/.]+` for it (`lexer.l:287`). A tag written outside that set
makes the whole append fail and roll back, so every tag is normalised here
before it can reach a candidate, and refused (never silently altered into
something else) when it cannot be made valid.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

TAG_CHARSET = re.compile(r"[A-Za-z0-9\-_/.]+")
_SLASH_SPACES = re.compile(r"\s*/\s*")
_SPACES = re.compile(r"\s*-\s*|\s+")


class InvalidTag(ValueError):
    def __init__(self, raw: str):
        super().__init__(f"invalid tag: {raw!r}")
        self.raw = raw


def normalize_tag(raw: str) -> str:
    """'Madikeri Oct-2026' -> 'madikeri-oct-2026'; '#Trip' -> 'trip'."""
    tag = raw.strip().lstrip("#").strip().lower()
    tag = _SLASH_SPACES.sub("/", tag)
    tag = _SPACES.sub("-", tag).strip("/")
    if not tag or "//" in tag or not TAG_CHARSET.fullmatch(tag):
        raise InvalidTag(raw)
    return tag


def normalize_tags(raws: Iterable[str]) -> list[str]:
    """Normalise each tag; drop duplicates, keeping first-seen order."""
    seen: dict[str, None] = {}
    for raw in raws:
        seen.setdefault(normalize_tag(raw), None)
    return list(seen)


def suggest_tag_prefix(category: str | None) -> str | None:
    """'Expenses:Travel:Bike' -> 'travel/'; anything not Expenses:* -> None."""
    if not category:
        return None
    parts = category.split(":")
    if parts[0] != "Expenses" or len(parts) < 2 or not parts[1]:
        return None
    return parts[1].lower() + "/"
