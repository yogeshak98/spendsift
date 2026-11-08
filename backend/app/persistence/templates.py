"""CSV and PDF import templates, persisted as YAML."""

from __future__ import annotations

import builtins
import logging
import threading
from pathlib import Path

import yaml
from pydantic import ValidationError

from backend.app.models.storage.pdf_template import _FINGERPRINT, PdfTemplate
from backend.app.models.storage.template import Template

_OLD_TEMPLATE = (
    "PDF template %s is from an older version and was skipped"
    " — run /ingest-stmt on a statement for that card again"
)

logger = logging.getLogger(__name__)


class TemplateStore:
    """Persists per-institution import templates as YAML under <storage_dir>/templates/."""

    def __init__(
        self, templates_dir: Path | str, *, lock: threading.RLock | None = None
    ) -> None:
        self.dir = Path(templates_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        # The app passes the journal write lock: an account rename rewrites
        # templates under it, so a save never lands between its plan and write.
        self.lock = lock if lock is not None else threading.RLock()

    def _path(self, fingerprint: str) -> Path:
        return self.dir / f"{fingerprint}.yaml"

    def save(self, template: Template) -> None:
        with self.lock:
            self._path(template.fingerprint).write_text(
                yaml.safe_dump(template.model_dump(), sort_keys=False),
                encoding="utf-8",
            )

    def get_by_fingerprint(self, fingerprint: str) -> Template | None:
        path = self._path(fingerprint)
        if not path.exists():
            return None
        return Template(**yaml.safe_load(path.read_text(encoding="utf-8")))

    def list(self) -> list[Template]:  # type: ignore
        templates: list[Template] = []
        for path in sorted(self.dir.glob("*.yaml")):
            templates.append(
                Template(**yaml.safe_load(path.read_text(encoding="utf-8")))
            )
        return templates

    def delete(self, fingerprint: str) -> bool:
        with self.lock:
            path = self._path(fingerprint)
            if not path.exists():
                return False
            path.unlink()
            return True


class PdfTemplateStore:
    """PDF templates as YAML in their own directory (the app uses
    `<storage>/templates/pdf/`). The CSV `TemplateStore` lists only `*.yaml`
    directly inside its own directory, so neither store sees the other's."""

    def __init__(
        self, templates_dir: Path | str, *, lock: threading.RLock | None = None
    ) -> None:
        self.dir = Path(templates_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._warned: set[str] = set()
        # The journal write lock in the app; see TemplateStore.
        self.lock = lock if lock is not None else threading.RLock()

    def _load(self, path: Path) -> PdfTemplate | None:
        """The template in `path`, or None (with one warning per file name)
        when it does not validate, e.g. one saved before templates carried
        their card."""
        try:
            return PdfTemplate.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8"))
            )
        except (ValidationError, yaml.YAMLError, UnicodeDecodeError, OSError):
            if path.name not in self._warned:
                self._warned.add(path.name)
                logger.warning(_OLD_TEMPLATE, path.name)
            return None

    def _path(self, fingerprint: str) -> Path:
        return self.dir / f"{fingerprint}.yaml"

    def save(self, template: PdfTemplate) -> None:
        with self.lock:
            self._path(template.fingerprint).write_text(
                yaml.safe_dump(
                    template.model_dump(), sort_keys=False, allow_unicode=True
                ),
                encoding="utf-8",
            )

    def get_by_fingerprint(self, fingerprint: str) -> PdfTemplate | None:
        if not _FINGERPRINT.match(fingerprint):
            return None
        path = self._path(fingerprint)
        if not path.exists():
            return None
        return self._load(path)

    def delete(self, fingerprint: str) -> bool:
        if not _FINGERPRINT.match(fingerprint):
            return False
        with self.lock:
            path = self._path(fingerprint)
            if not path.exists():
                return False
            path.unlink()
            return True

    def list(self) -> builtins.list[PdfTemplate]:
        """Every valid template; a file that fails validation is skipped."""
        loaded = (self._load(path) for path in sorted(self.dir.glob("*.yaml")))
        return [template for template in loaded if template is not None]
