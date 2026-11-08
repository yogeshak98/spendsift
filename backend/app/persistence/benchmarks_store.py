"""config/benchmarks.yaml: the user-editable benchmark returns.

The file is written once, with comments, the first time it is read, so there is
always something to find and edit. It is never rewritten afterwards.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from backend.app.core.errors.errors import BENCHMARKS_FILE_INVALID
from backend.app.models.storage.benchmarks import (
    BenchmarksConfig,
    default_benchmarks,
    default_blend,
)
from backend.app.persistence.atomic import write_text_atomic

FILENAME = "benchmarks.yaml"

_HEADER = """\
# Benchmarks for Net Worth > Overview. Edit freely; nothing is fetched online.
#
# benchmarks: key -> label, kind (equity | gold | debt | inflation) and
#   annual_return_pct, the yearly return in percent you want to compare against.
#   The seeded numbers are rough long-run figures for orientation, not data.
#   Add a key to add a benchmark to the dropdown.
# blend: how the "Blended" benchmark judges each investment vehicle. `equity`
#   means whichever equity index is chosen on the screen; any other value is a
#   key above. A vehicle not listed is left out of the blend.
"""


class BenchmarksStore:
    def __init__(self, config_dir: Path | str) -> None:
        self.dir = Path(config_dir)

    @property
    def path(self) -> Path:
        return self.dir / FILENAME

    def load(self) -> BenchmarksConfig:
        try:
            if not self.path.exists():
                self._seed()
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            return BenchmarksConfig(**raw)
        except (
            yaml.YAMLError,
            ValidationError,
            TypeError,
            UnicodeDecodeError,
            OSError,
        ) as exc:
            raise BENCHMARKS_FILE_INVALID.exc(
                {"file": FILENAME, "reason": str(exc).replace("\n", " ")}
            ) from exc

    def _seed(self) -> None:
        body = yaml.safe_dump(
            {
                "version": 1,
                "benchmarks": {
                    k: v.model_dump() for k, v in default_benchmarks().items()
                },
                "blend": default_blend(),
            },
            sort_keys=False,
        )
        write_text_atomic(self.path, _HEADER + body)
