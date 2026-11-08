"""Repository root, resolved from this file's location."""

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
