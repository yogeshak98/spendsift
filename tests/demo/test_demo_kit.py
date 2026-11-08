"""The live-demo kit: a workdir the agent can ingest from, with no answer key.

The kit never starts a server and never touches the user's real app on port
8765. Everything is written under `tmp_path`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.personas import demo_kit
from scripts.personas.__main__ import main
from scripts.personas.catalog import ALL


def _run(workdir: Path, *extra: str, slug: str = "p30l") -> int:
    return main(["demo", slug, str(workdir), *extra])


def test_demo_kit_refuses_8765(tmp_path, capsys) -> None:
    work = tmp_path / "w"
    assert _run(work, "--port", "8765") == 2
    assert "8765" in capsys.readouterr().err
    assert not work.exists()
    with pytest.raises(ValueError):
        demo_kit.prepare(ALL["p30l"], work, 8765)
    assert not work.exists()


@pytest.mark.parametrize("port", [0, 80, 1023])
def test_demo_kit_refuses_privileged_ports(tmp_path, port) -> None:
    with pytest.raises(ValueError):
        demo_kit.check_port(port)
    assert _run(tmp_path / "w", "--port", str(port)) == 2


def test_demo_kit_writes_only_under_workdir(tmp_path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    work = parent / "w"
    assert _run(work) == 0
    assert {p.name for p in parent.iterdir()} == {"w"}
    assert {p.name for p in work.iterdir()} == {"statements", "data"}
    assert any((work / "statements").iterdir())
    assert (work / "data" / "prices" / "nav_history.parquet").is_file()
    assert (work / "data" / "scheme_master" / "amfi_cache.csv").is_file()


def test_demo_kit_seeds_no_templates(tmp_path) -> None:
    work = tmp_path / "w"
    assert _run(work) == 0
    assert not (work / "data" / "templates").exists()
    assert not list(work.rglob("*.yaml"))


def test_demo_kit_statements_hold_no_sidecars(tmp_path) -> None:
    work = tmp_path / "w"
    assert _run(work) == 0
    assert not list(work.rglob("*.truth.json"))
    assert not (work / ".build").exists()
    assert not [p for p in work.rglob("*") if "truth" in p.name]


def test_demo_kit_refuses_a_non_empty_data_dir(tmp_path, capsys) -> None:
    work = tmp_path / "w"
    (work / "data").mkdir(parents=True)
    (work / "data" / "keep.txt").write_text("previous run", encoding="utf-8")
    assert _run(work) == 2
    assert "not empty" in capsys.readouterr().err
    assert [p.name for p in (work / "data").iterdir()] == ["keep.txt"]
    assert not (work / "statements").exists()


def test_demo_kit_prints_the_commands_in_order(tmp_path, capsys) -> None:
    work = tmp_path / "w"
    assert _run(work, "--port", "8801") == 0
    out = capsys.readouterr().out
    data, statements = str(work.resolve() / "data"), str(work.resolve() / "statements")
    start, export, prompt, check = (
        out.index("uvicorn"),
        out.index("export SPENDSIFT_AGENT_URL=http://127.0.0.1:8801"),
        out.index(f"/ingest-stmt on {statements}"),
        out.index("scripts.personas check"),
    )
    assert start < export < prompt < check
    assert f"SPENDSIFT_STORAGE_DIR={data}" in out
    assert "--port 8801" in out
    assert "--persona p30l" in out and f"--data-dir {data} --live" in out
    assert not re.search(r"8765", out)


def test_demo_kit_default_port_and_platform_env(tmp_path, capsys) -> None:
    zerodha = next(
        s for s, p in ALL.items() if any(a.institution == "Zerodha" for a in p.accounts)
    )
    assert _run(tmp_path / "z", slug=zerodha) == 0
    out = capsys.readouterr().out
    assert "--port 8790" in out and "8765" not in out
    assert "SPENDSIFT_PLATFORM_CASH_ACCOUNTS=" in out
    assert _run(tmp_path / "n") == 0
    assert "SPENDSIFT_PLATFORM_CASH_ACCOUNTS" not in capsys.readouterr().out
