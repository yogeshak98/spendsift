"""Filesystem cache for the last successful AMFI dump + its conditional-GET
metadata + the last refresh outcome. IO-only primitive; no framework imports.

  <dir>/navall.txt        raw last-fetched dump (written only on a 200)
  <dir>/navall.meta.json  {etag, last_modified, fetched_at, source, as_of}

`source`/`as_of` record the LAST refresh (written on every refresh, even 304 /
offline, which do not rewrite the body) so PriceService.status() reports the true
last-known source after a reload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.app.persistence.atomic import write_text_atomic


@dataclass(frozen=True)
class CachedDump:
    body: str
    etag: str | None
    last_modified: str | None


class PriceCache:
    def __init__(self, directory: Path | str) -> None:
        self.dir = Path(directory)

    @property
    def _body_file(self) -> Path:
        return self.dir / "navall.txt"

    @property
    def _meta_file(self) -> Path:
        return self.dir / "navall.meta.json"

    def _read_meta(self) -> dict:
        if not self._meta_file.exists():
            return {}
        try:
            return json.loads(self._meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _write_meta(self, meta: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self._meta_file, json.dumps(meta))

    def read(self) -> CachedDump | None:
        if not self._body_file.exists():
            return None
        meta = self._read_meta()
        return CachedDump(
            body=self._body_file.read_text(encoding="utf-8"),
            etag=meta.get("etag"),
            last_modified=meta.get("last_modified"),
        )

    def write(self, body: str, etag: str | None, last_modified: str | None) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self._body_file, body)
        meta = self._read_meta()  # preserve any existing source/as_of
        meta.update(
            {
                "etag": etag,
                "last_modified": last_modified,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._write_meta(meta)

    def read_outcome(self) -> tuple[str | None, str | None]:
        meta = self._read_meta()
        return meta.get("source"), meta.get("as_of")

    def write_outcome(self, source: str, as_of: str | None) -> None:
        meta = self._read_meta()  # preserve etag/last_modified/fetched_at
        meta.update({"source": source, "as_of": as_of})
        self._write_meta(meta)
