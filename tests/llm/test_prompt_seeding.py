"""The user's prompt copies win over the shipped defaults, on the real
bootstrap paths: the app factory and the demo builder."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.llm.constants import (
    CATEGORIZE_MERCHANTS_PROMPT,
    CATEGORIZE_PROMPT,
    DEFAULT_PROMPTS_DIR,
    USER_PROMPTS_SUBDIR,
)
from backend.app.server.main import create_app
from backend.app.service.prompts import read_prompt
from scripts.build_demo import build_demo

EDITED = "My own instructions. Answer <number>\tExpenses:Account only.\n"


def _default(name: str) -> bytes:
    return (DEFAULT_PROMPTS_DIR / name).read_bytes()


def test_default_prompts_ship_with_the_package() -> None:
    for name in (CATEGORIZE_PROMPT, CATEGORIZE_MERCHANTS_PROMPT):
        assert _default(name).strip(), name


def test_user_prompt_wins_over_default(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    prompts = storage / USER_PROMPTS_SUBDIR
    prompts.mkdir(parents=True)
    (prompts / CATEGORIZE_PROMPT).write_text(EDITED, encoding="utf-8")

    create_app(storage_dir=storage)
    create_app(storage_dir=storage)  # a restart re-runs the seeding

    # the user's edit is never overwritten, and it is what the reader returns
    assert (prompts / CATEGORIZE_PROMPT).read_text(encoding="utf-8") == EDITED
    assert read_prompt(storage, CATEGORIZE_PROMPT) == EDITED
    # the missing one is seeded from the shipped default, byte for byte
    assert (prompts / CATEGORIZE_MERCHANTS_PROMPT).read_bytes() == _default(
        CATEGORIZE_MERCHANTS_PROMPT
    )


def test_the_export_route_reads_the_users_edited_prompt(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    prompts = storage / USER_PROMPTS_SUBDIR
    prompts.mkdir(parents=True)
    (prompts / CATEGORIZE_MERCHANTS_PROMPT).write_text(EDITED, encoding="utf-8")

    client = TestClient(create_app(storage_dir=storage))
    text = client.get("/api/reports/uncategorized/export").json()["text"]

    assert text.startswith(EDITED.rstrip())
    assert (prompts / CATEGORIZE_PROMPT).read_bytes() == _default(CATEGORIZE_PROMPT)


def test_a_fresh_data_dir_gets_both_defaults(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    create_app(storage_dir=storage)
    for name in (CATEGORIZE_PROMPT, CATEGORIZE_MERCHANTS_PROMPT):
        assert (storage / USER_PROMPTS_SUBDIR / name).read_bytes() == _default(name)


def test_the_demo_builder_seeds_the_prompts(tmp_path: Path) -> None:
    storage = tmp_path / "data"
    build_demo(storage)
    for name in (CATEGORIZE_PROMPT, CATEGORIZE_MERCHANTS_PROMPT):
        assert (storage / USER_PROMPTS_SUBDIR / name).read_bytes() == _default(name)
