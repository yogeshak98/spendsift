#!/usr/bin/env python3
"""Sort a plaintext ledger file by transaction date, in-place (with backup)."""

import re
import shutil
import sys
from datetime import date
from pathlib import Path

DATE_RE = re.compile(r"^(\d{4}[/-]\d{2}[/-]\d{2})\b")


def parse_blocks(text: str) -> list[str]:
    """Split ledger text into transaction blocks (non-empty paragraphs)."""
    blocks = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip() == "":
            if current:
                blocks.append("".join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("".join(current))
    return blocks


def block_date(block: str) -> date:
    m = DATE_RE.match(block)
    if not m:
        raise ValueError(f"Block has no leading date:\n{block[:80]!r}")
    return date.fromisoformat(m.group(1).replace("/", "-"))


def sort_ledger(path: Path) -> None:
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    print(f"Backup → {backup}")

    text = path.read_text(encoding="utf-8")
    blocks = parse_blocks(text)
    blocks.sort(key=block_date)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    print(f"Sorted {len(blocks)} transactions → {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <journal.ledger>")
        sys.exit(1)
    sort_ledger(Path(sys.argv[1]))
