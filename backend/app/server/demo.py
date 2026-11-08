"""Demo app factory: serves the app bound to the fictional ``demo/data`` ledger.

Used by ``scripts/demo`` via
``uvicorn backend.app.server.demo:create_demo_app --factory``.
It passes ``storage_dir`` explicitly so the demo never depends on (and is never
overridden by) the ``SPENDSIFT_STORAGE_DIR`` environment variable.
"""

from __future__ import annotations

from backend.app.core.paths import REPO_ROOT
from backend.app.server.main import create_app


def create_demo_app():
    return create_app(storage_dir=REPO_ROOT / "demo" / "data")
