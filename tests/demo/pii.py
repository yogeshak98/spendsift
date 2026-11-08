"""The real-PII denylist, read the way `scripts/build_demo.py` reads it.

`build_demo._load_pii_tokens()` takes no path, so the tests mirror its parse
(one token per line, blank lines and `#` comments skipped) instead of
patching its private module constant. A token is matched case-insensitively
(statement text is upper-cased, a denylist entry may not be), and a purely
numeric token only as a whole number, never inside a longer digit run.

A failure never prints a token: callers count hits and report the count.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

DENYLIST_ENV = "SPENDSIFT_PII_DENYLIST"
DEFAULT_DENYLIST = Path(__file__).resolve().parents[2] / "scripts" / ".pii-denylist.txt"


def parse_denylist(path: Path) -> list[str]:
    """build_demo's parse: stripped lines, minus blanks and `#` comments."""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def denylist_tokens() -> list[str]:
    """The tokens, lower-cased. `SPENDSIFT_PII_DENYLIST` names the file when
    set (a missing file fails); otherwise the gitignored default is used if
    present. With neither there is nothing real to match, so the caller is
    skipped, not passed."""
    env = os.environ.get(DENYLIST_ENV)
    if env:
        path = Path(env)
        if not path.is_file():
            pytest.fail(f"{DENYLIST_ENV} names a missing file: {path}")
    elif DEFAULT_DENYLIST.is_file():
        path = DEFAULT_DENYLIST
    else:
        pytest.skip(
            f"no PII denylist: set {DENYLIST_ENV} or add "
            "scripts/.pii-denylist.txt (gitignored) to run the real-token check"
        )
    tokens = [t.lower() for t in parse_denylist(path)]
    if not tokens:
        pytest.fail("the PII denylist file holds no tokens")
    return tokens


def _matcher(token: str) -> re.Pattern[str]:
    """A purely numeric token matches only as a whole number (no digit right
    before or after it): random references, serials and amounts are long
    digit runs, and a short real number inside one is a chance collision, not
    that number. Any other token matches as a case-insensitive substring."""
    escaped = re.escape(token.lower())
    if token.isdigit():
        return re.compile(rf"(?<!\d){escaped}(?!\d)")
    return re.compile(escaped)


def count_hits(texts: list[str], tokens: list[str]) -> int:
    """How many (text, token) pairs match, by the rule in `_matcher`."""
    patterns = [_matcher(t) for t in tokens]
    return sum(1 for s in texts for p in patterns if p.search(s.lower()))
