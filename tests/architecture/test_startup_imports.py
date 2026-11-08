"""App startup must stay cheap: no heavy parsing libraries, no side effects.

pandas, openpyxl, pdfplumber, pypdf and pyarrow together cost seconds of import
time and are only needed once a statement is parsed or NAV history is read.
They are imported inside the functions that use them; this guards against a
top-level import creeping back in.

Runs in a subprocess so the result does not depend on what other tests have
already imported into this interpreter. Each subprocess runs in ``tmp_path``
so nothing can ever be written into the repo or the user's data folder.

Importing ``server.main`` must build nothing: uvicorn runs ``create_app`` in
factory mode, so a module-level ``app`` (which bootstrapped a ``storage/``
folder in whatever directory imported it -- e.g. ``frontend/`` during
``npm run gen:api``) must not come back.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

HEAVY = ("pandas", "openpyxl", "pdfplumber", "pypdf", "pyarrow")

_PROBE = """
import json, sys
heavy = json.loads(sys.argv[1])
storage = sys.argv[2]

def loaded():
    return sorted(h for h in heavy if h in sys.modules)

import backend.app.server.main as main
after_import = loaded()
from pathlib import Path
main.create_app(storage_dir=Path(storage))
after_create = loaded()
print(json.dumps({"import": after_import, "create_app": after_create}))
"""


def test_app_startup_does_not_import_heavy_parsers(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "SPENDSIFT_STORAGE_DIR": str(tmp_path / "storage"),
    }
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, json.dumps(HEAVY), str(tmp_path / "storage2")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    at_import, at_create = loaded["import"], loaded["create_app"]
    assert at_import == [], f"imported at module load: {at_import}"
    assert at_create == [], f"imported by create_app(): {at_create}"


def _env_without_storage_override() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "SPENDSIFT_STORAGE_DIR"}
    env["PYTHONPATH"] = str(ROOT)
    return env


def test_importing_the_app_module_creates_no_storage_dir(tmp_path: Path) -> None:
    # No SPENDSIFT_STORAGE_DIR: building the app here would bootstrap the
    # default ./storage in the cwd, which is exactly what must not happen.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import backend.app.server.main as m; assert not hasattr(m, 'app')",
        ],
        cwd=tmp_path,
        env=_env_without_storage_override(),
        check=True,
    )
    assert not (tmp_path / "storage").exists()


def test_openapi_export_creates_no_storage_dir(tmp_path: Path) -> None:
    # The frontend's `npm run gen:api` runs this from frontend/.
    subprocess.run(
        [sys.executable, "-m", "backend.app.server.openapi", "openapi.json"],
        cwd=tmp_path,
        env=_env_without_storage_override(),
        check=True,
    )
    assert (tmp_path / "openapi.json").is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["openapi.json"]
