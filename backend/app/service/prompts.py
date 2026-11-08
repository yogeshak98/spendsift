"""The user-owned copy-paste prompts under ``<data_dir>/prompts/``."""

from __future__ import annotations

import shutil
from pathlib import Path

from backend.app.core.errors.errors import PROMPT_FILE_MISSING
from backend.app.llm.constants import (
    CATEGORIZE_MERCHANTS_PROMPT,
    CATEGORIZE_PROMPT,
    DEFAULT_PROMPTS_DIR,
    USER_PROMPTS_SUBDIR,
)


def seed_default_prompts(storage_dir: Path | str) -> None:
    """Copy each shipped default prompt into the data dir unless a copy is
    already there. Call it right after the main ledger's ``bootstrap()``.

    A copy that exists is the user's, edited or not, and is never overwritten.
    """
    prompts_dir = Path(storage_dir) / USER_PROMPTS_SUBDIR
    prompts_dir.mkdir(parents=True, exist_ok=True)
    # A SECOND prompt, not a shared one: the per-batch flow legitimately
    # allows Income:/Assets:/Liabilities: targets, while the journal-wide
    # merchant flow accepts Expenses: only (and refuses the backlog account
    # itself). One file cannot instruct a model for both without telling it to
    # write lines the other path rejects.
    for name in (CATEGORIZE_PROMPT, CATEGORIZE_MERCHANTS_PROMPT):
        dest = prompts_dir / name
        if not dest.exists():
            shutil.copyfile(DEFAULT_PROMPTS_DIR / name, dest)


def read_prompt(storage_dir: Path | str, filename: str) -> str:
    """Read one user-owned prompt from `<data_dir>/prompts/`.

    The single reader for every copy-paste prompt. These files are seeded
    once and then owned by the user, so a hand-deleted one is reachable --
    and a bare `read_text` turns that into an unclaimed FileNotFoundError
    (a generic 500 with no remedy) on a path whose fix is "put the file
    back". Coded instead, so the UI can say so.
    """
    path = Path(storage_dir) / USER_PROMPTS_SUBDIR / filename
    if not path.exists():
        raise PROMPT_FILE_MISSING.exc({"path": str(path)})
    return path.read_text(encoding="utf-8")
