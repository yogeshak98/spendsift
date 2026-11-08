from __future__ import annotations

import argparse
import json
from fnmatch import fnmatchcase

from backend.app.cli import ingest as cli
from backend.app.core.paths import REPO_ROOT

SCRIPT = "scripts/ingest_cli.py"
PREFIXES = (".venv/bin/python", "PYTHONPATH=. .venv/bin/python", "uv run")
# Every subcommand that does not write through the port. A new subcommand must
# either be added here (read-only) or be named stage*/commit* (gated).
READ_ONLY = {
    "inspect", "peek", "dry-run", "show", "accounts", "opening-check", "openings",
    # These edit a staged review batch in memory (never the journal).
    "reject", "account", "category", "tag", "untag", "learn", "resolve-marker",
}  # fmt: skip


def _subcommands() -> set[str]:
    sub = next(
        a for a in cli._parser()._actions if isinstance(a, argparse._SubParsersAction)
    )
    return set(sub.choices)


def _ask_patterns() -> list[str]:
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    return [
        p.removeprefix("Bash(").removesuffix(")")
        for p in settings["permissions"]["ask"]
    ]


def _asked(cmd: str, prefix: str) -> bool:
    line = f"{prefix} {SCRIPT} {cmd} x"
    return any(fnmatchcase(line, pat) for pat in _ask_patterns())


def test_cli_write_commands_are_ask_gated():
    cmds = _subcommands()
    assert {
        "commit-opening",
        "commit-opening-correction",
        "commit-opening-zero",
    } <= cmds
    writers = {c for c in cmds if c.startswith(("stage", "commit"))}
    assert writers
    # Every invocation form that the ask rules spell out must be gated.
    assert {p for p in PREFIXES if any(p in a for a in _ask_patterns())} == set(
        PREFIXES
    )
    ungated = sorted(
        f"{prefix} {c}" for prefix in PREFIXES for c in writers if not _asked(c, prefix)
    )
    assert ungated == []


def test_read_only_opening_commands_are_not_gated():
    cmds = _subcommands()
    assert {"opening-check", "openings"} <= cmds
    for prefix in PREFIXES:
        for cmd in ("opening-check", "openings"):
            assert not _asked(cmd, prefix), (prefix, cmd)


def test_every_subcommand_is_read_only_or_named_stage_or_commit():
    """A new writer cannot slip past the ask rules by its name."""
    writers = _subcommands() - READ_ONLY
    outside = sorted(c for c in writers if not c.startswith(("stage", "commit")))
    assert outside == [], f"not listed as read-only and not stage*/commit*: {outside}"
    assert READ_ONLY <= _subcommands(), sorted(READ_ONLY - _subcommands())
