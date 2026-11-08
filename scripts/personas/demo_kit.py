"""The live-demo kit: a workdir for running `/ingest-stmt` over a persona.

    python -m scripts.personas demo <slug> <workdir> [--port 8790]

Creates `<workdir>/statements/` (the persona's statement files and nothing
else) and `<workdir>/data/` (a fresh app data dir seeded with the NAV history
and the AMFI scheme master, but no import templates: in the live demo the
agent proves the formats itself). Then prints the commands to run by hand. It
never starts a server.

The answer key stays out of the agent's reach: the files are built in a scratch
dir, only the statement paths are copied to `statements/`, and the scratch dir
(with its `.truth.json` sidecars) is deleted. The checker re-simulates the
truth deterministically.

The user's real app lives on port 8765, so that port is refused, as is any
privileged port.
"""

from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

from scripts.personas.model import Persona
from scripts.personas.naming import PLATFORM_CASH, PLATFORM_ENV

DEFAULT_PORT = 8790
REAL_APP_PORT = 8765
PLATFORM_CASH_JSON = json.dumps(PLATFORM_CASH, separators=(",", ":"))
REPO_ROOT = Path(__file__).resolve().parents[2]


def check_port(port: int) -> None:
    if port == REAL_APP_PORT:
        raise ValueError(
            f"port {REAL_APP_PORT} is the real SpendSift app; pick another port"
        )
    if not 1024 <= port <= 65535:
        raise ValueError(f"port {port} is outside 1024-65535")


def _nonempty(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def prepare(persona: Persona, workdir: Path, port: int = DEFAULT_PORT) -> list[Path]:
    """Build the statements and seed the data dir under `workdir`; return the
    statement paths. Writes nothing outside `workdir`."""
    from scripts.personas.build import build
    from scripts.personas.snapshot import seed_data_dir

    check_port(port)
    workdir = workdir.resolve()
    statements, data, scratch = (
        workdir / "statements",
        workdir / "data",
        workdir / ".build",
    )
    for path in (data, statements, scratch):
        if _nonempty(path):
            raise FileExistsError(
                f"{path} is not empty; the demo kit never reuses a previous"
                " run's directory. Pick a new workdir or remove it yourself."
            )
    statements.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        built = build(persona, scratch)
        copied: list[Path] = []
        for src in built:
            dst = statements / src.resolve().relative_to(scratch.resolve())
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            copied.append(dst)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    seed_data_dir(data, templates=False)
    return copied


def commands(persona: Persona, workdir: Path, port: int = DEFAULT_PORT) -> list[str]:
    """The lines to print, in the order the user runs them."""
    workdir = workdir.resolve()
    python = REPO_ROOT / ".venv" / "bin" / "python"
    env = [f"SPENDSIFT_STORAGE_DIR={shlex.quote(str(workdir / 'data'))}"]
    if any(a.institution == "Zerodha" for a in persona.accounts):
        env.append(f"{PLATFORM_ENV}={shlex.quote(PLATFORM_CASH_JSON)}")
    start = (
        f"cd {shlex.quote(str(REPO_ROOT))} && {' '.join(env)} "
        f"{shlex.quote(str(python))} -m uvicorn backend.app.server.main:app "
        f"--host 127.0.0.1 --port {port}"
    )
    statements = shlex.quote(str(workdir / "statements"))
    check = (
        f"cd {shlex.quote(str(REPO_ROOT))} && {' '.join(env)} "
        f"{shlex.quote(str(python))} -m scripts.personas check --persona "
        f"{persona.slug} --data-dir {shlex.quote(str(workdir / 'data'))} --live"
    )
    return [
        "1. Start the app (its own terminal):",
        f"   {start}",
        "2. In the terminal where you run Claude Code:",
        f"   export SPENDSIFT_AGENT_URL=http://127.0.0.1:{port}",
        "3. Suggested prompt for Claude Code:",
        f"   run /ingest-stmt on {statements}, one file at a time",
        "4. When the import is done, check it (keeps <data>/imports):",
        f"   {check}",
    ]
