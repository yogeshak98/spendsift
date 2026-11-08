"""One-command QA: build a persona's statements, import them into a fresh data
dir the way the app's defaults would, and run the rule book on the result.

    python -m scripts.personas qa [slug|all]

`import_persona` is the import half. It writes the statements (and their
truth sidecars) into `<data_dir>/../inbox-<slug>/`, seeds the data dir, and
hands the app exactly the files `build` returned (never a sidecar) through
`importer.import_persona_files` with no review hook: no hand categorisation,
no forced pairing, only what the app itself proposes (ruling P29).

Zerodha is configured as a platform for the run
(`SPENDSIFT_PLATFORM_CASH_ACCOUNTS`), which is app configuration a user sets,
not a code change. `platform_settings` sets it and restores the environment
afterwards; the checks run inside it too, since the app reads settings at
call time.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from scripts.personas import snapshot
from scripts.personas.build import build
from scripts.personas.check import (
    client_for,
    exit_code,
    render,
    run_view,
    status_of,
)
from scripts.personas.formats._common import SIDECAR_SUFFIX as SIDECAR
from scripts.personas.importer import ImportedFile, import_persona_files
from scripts.personas.model import Persona, Truth
from scripts.personas.naming import PLATFORM_CASH, PLATFORM_ENV
from scripts.personas.rules import ZERODHA_READER, Rule, RuleResult, zerodha_accounts
from scripts.personas.simulate import simulate

# The one refusal the app is expected to make today: a Zerodha tradebook has
# no narration and no amount column, so no header row is detected (P20).
TRADEBOOK_REFUSAL = "E1002"


@contextmanager
def platform_settings() -> Iterator[None]:
    """Configure Groww and Zerodha as platforms, then restore the
    environment exactly as it was."""
    from backend.app.core.settings import get_settings

    before = os.environ.get(PLATFORM_ENV)
    os.environ[PLATFORM_ENV] = json.dumps(PLATFORM_CASH)
    get_settings.cache_clear()
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(PLATFORM_ENV, None)
        else:
            os.environ[PLATFORM_ENV] = before
        get_settings.cache_clear()


def truth_of(persona: Persona) -> Truth:
    return simulate(persona, nav=snapshot.load_nav(), prices=snapshot.load_prices())


def inbox_of(persona: Persona, data_dir: Path) -> Path:
    return data_dir.parent / f"inbox-{persona.slug}"


def import_persona(
    persona: Persona, data_dir: Path, *, truth: Truth | None = None
) -> list[ImportedFile]:
    """Build `persona`'s statements into `<data_dir>/../inbox-<slug>`, seed
    `data_dir` (created if missing; it must be fresh) and import the built
    files in `build`'s order with the app's defaults. Returns one record per
    file; a file the app refuses is recorded, not raised."""
    data_dir = Path(data_dir)
    inbox = inbox_of(persona, data_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    paths = build(persona, inbox, truth=truth or truth_of(persona))
    if any(p.name.endswith(SIDECAR) for p in paths):
        raise ValueError(f"{persona.slug}: build returned a truth sidecar")
    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot.seed_data_dir(data_dir)
    with platform_settings():
        return import_persona_files(client_for(data_dir), persona, paths)


def _account_key(path: Path) -> str | None:
    """The persona account a statement file was written for, from its truth
    sidecar (read here, by QA, never sent to the app)."""
    sidecar = path.with_name(path.name + SIDECAR)
    if not sidecar.is_file():
        return None
    return json.loads(sidecar.read_text(encoding="utf-8")).get("account_key")


def refusal_cause(persona: Persona, truth: Truth, refused: ImportedFile) -> str | None:
    """The pending cause a refused file is expected under, or None when the
    refusal is not expected (a FAIL of the run)."""
    zerodha = {a.key for a in zerodha_accounts(persona, truth)}
    if refused.refused == TRADEBOOK_REFUSAL and _account_key(refused.path) in zerodha:
        return ZERODHA_READER
    return None


def unexpected_refusals(
    persona: Persona, truth: Truth, imported: Iterable[ImportedFile]
) -> list[ImportedFile]:
    return [
        f for f in imported if f.refused and refusal_cause(persona, truth, f) is None
    ]


@dataclass
class QaRun:
    persona: Persona
    truth: Truth
    data_dir: Path
    inbox: Path
    paths: list[Path]
    imported: list[ImportedFile]
    results: list[tuple[Rule, RuleResult]]

    def result(self, rule_id: str) -> RuleResult:
        return next(res for r, res in self.results if r.id == rule_id)

    @property
    def unexpected_refusals(self) -> list[ImportedFile]:
        return unexpected_refusals(self.persona, self.truth, self.imported)

    @property
    def exit_code(self) -> int:
        """0 only when no rule FAILs or passes unexpectedly, and every file
        the app refused is refused under an expected cause."""
        return max(exit_code(self.results), 1 if self.unexpected_refusals else 0)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, result in self.results:
            status = status_of(result)
            out[status] = out.get(status, 0) + 1
        return out

    def table(self, *, max_failures: int = 4) -> str:
        lines = [f"== {self.persona.slug} · {len(self.paths)} statement file(s)"]
        for f in self.imported:
            if not f.refused:
                continue
            cause = refusal_cause(self.persona, self.truth, f)
            what = (
                f"refused (waits for: {cause})"
                if cause
                else "FAIL refused (not expected)"
            )
            lines.append(f"   {what} {f.path.name}: {f.refused} {f.detail}")
        lines.append(render(self.results, max_failures=max_failures))
        lines.append(" · ".join(f"{n} {s}" for s, n in sorted(self.counts().items())))
        return "\n".join(lines)


def run_persona(persona: Persona, root: Path) -> QaRun:
    """Import `persona` into `<root>/data` (statements in
    `<root>/inbox-<slug>`) and run every rule on it."""
    from scripts.personas.check import AppView

    truth = truth_of(persona)
    data_dir = Path(root) / "data"
    imported = import_persona(persona, data_dir, truth=truth)
    with platform_settings():
        view = AppView.from_data_dir(data_dir)
        results = run_view(view, persona, truth)
    return QaRun(
        persona=persona,
        truth=truth,
        data_dir=data_dir,
        inbox=inbox_of(persona, data_dir),
        paths=[f.path for f in imported],
        imported=imported,
        results=results,
    )
