"""Offline OpenAPI exporter.

Builds the FastAPI app against a throwaway temp data dir (never the real
`storage/`) and dumps `app.openapi()` as JSON. No running server needed.

Usage:
    python -m backend.app.server.openapi [outfile]

With `outfile`, writes the JSON there; otherwise prints to stdout.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from backend.app.server.main import create_app


def export_openapi() -> dict:
    """Build the app in an isolated temp data dir and return its OpenAPI schema.

    The temp dir is created and cleaned up here so the exporter never reads or
    writes the real data dir.
    """
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(storage_dir=Path(tmp))
        return app.openapi()


def main(argv: list[str]) -> int:
    schema = export_openapi()
    text = json.dumps(schema, indent=2)
    if argv:
        Path(argv[0]).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
