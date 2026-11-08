"""Whole-file replacement that a concurrent reader can never see half-done.

A writer fills a uniquely named temp file next to the target, then swaps it in
with `os.replace` (atomic on one filesystem). A reader opening the path at any
moment gets either the complete old file or the complete new one, never a
truncated mix. Unique temp names keep two concurrent writers from clobbering
each other's half-written temp file.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path


def replace_atomic(path: Path, write: Callable[[Path], object]) -> None:
    """Call `write(tmp)` to produce the new content, then swap it in at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp = Path(name)
    try:
        write(tmp)
        # mkstemp creates 0600; keep the replaced file's mode (0644 for a new one)
        # so an atomic write never quietly changes who can read the file.
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_text_atomic(path: Path, text: str) -> None:
    replace_atomic(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))
