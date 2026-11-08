"""Atomic whole-file replacement used by the price/NAV stores and price files."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.persistence.atomic import replace_atomic, write_text_atomic


def test_write_text_atomic_creates_and_replaces(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "prices.beancount"
    write_text_atomic(target, "one\n")
    assert target.read_text(encoding="utf-8") == "one\n"
    write_text_atomic(target, "two\n")
    assert target.read_text(encoding="utf-8") == "two\n"
    assert [p.name for p in target.parent.iterdir()] == ["prices.beancount"]


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "nav_history.parquet"
    target.write_text("old", encoding="utf-8")

    def half_then_fail(tmp: Path) -> None:
        tmp.write_text("half", encoding="utf-8")
        raise OSError("disk full")

    with pytest.raises(OSError):
        replace_atomic(target, half_then_fail)
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["nav_history.parquet"]


def test_the_replaced_file_keeps_its_mode(tmp_path: Path) -> None:
    target = tmp_path / "main.beancount"
    target.write_text("x", encoding="utf-8")
    os.chmod(target, 0o640)
    write_text_atomic(target, "y")
    assert target.stat().st_mode & 0o777 == 0o640
    fresh = tmp_path / "new.txt"
    write_text_atomic(fresh, "z")
    assert fresh.stat().st_mode & 0o777 == 0o644
