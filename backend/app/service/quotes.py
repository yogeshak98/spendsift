"""The shipped quotes the dashboard shows, one per line in the seed file."""

from __future__ import annotations

from backend.app.persistence.seeds import QUOTES


def load_quotes() -> list[str]:
    """Every non-blank line of the quotes seed file, stripped; [] if unreadable."""
    try:
        lines = QUOTES.read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip()]
    except OSError:
        return []
