"""Models are pure shapes: ``backend/app/models/**`` imports nothing with behaviour.

Allowed: the standard library, ``pydantic`` (and ``pydantic_core``), the
``beancount.core`` data types, and ``backend.app.models`` / ``backend.app.core``.
Anything else (a parser, a store, an HTTP client, ``yaml``) belongs in a layer
above, which imports the model instead. Standard-library modules that do I/O
or load code (``os``, ``subprocess``, ``socket``, ``shutil``, ``importlib``) are
refused too, and so are ``__import__(...)`` / ``import_module(...)`` calls.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "backend" / "app" / "models"

_THIRD_PARTY = ("pydantic", "pydantic_core", "beancount.core")
_OWN = ("backend.app.models", "backend.app.core")
# Standard library, but with behaviour: process/socket/filesystem I/O or
# dynamic code loading.
_IMPURE_STDLIB = ("os", "subprocess", "socket", "shutil", "importlib")
_DYNAMIC_IMPORTS = ("__import__", "import_module")


def _within(mod: str, prefix: str) -> bool:
    return mod == prefix or mod.startswith(prefix + ".")


def is_allowed(mod: str) -> bool:
    top = mod.split(".")[0]
    if top in _IMPURE_STDLIB:
        return False
    if top == "__future__" or top in sys.stdlib_module_names:
        return True
    return any(_within(mod, p) for p in (*_THIRD_PARTY, *_OWN))


def _package(path: Path, root: Path) -> list[str]:
    parts = list(path.relative_to(root).with_suffix("").parts)
    return parts[:-1]  # the package a relative import resolves against


def find_impurities(models: Path = MODELS, root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    for path in sorted(models.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if name in _DYNAMIC_IMPORTS:
                    problems.append(f"{rel}:{node.lineno}: calls {name}")
                continue
            if isinstance(node, ast.Import):
                bad = [a.name for a in node.names if not is_allowed(a.name)]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    pkg = _package(path, root)
                    base = pkg[: len(pkg) - (node.level - 1)]
                    mod = ".".join(base + ([node.module] if node.module else []))
                else:
                    mod = node.module or ""
                # `from beancount import core` names a submodule, so check it.
                names = [f"{mod}.{a.name}" for a in node.names]
                ok = is_allowed(mod) or all(is_allowed(n) for n in names)
                bad = [] if ok else [mod]
            else:
                continue
            problems.extend(f"{rel}:{node.lineno}: imports {m}" for m in bad)
    return problems


def test_models_import_only_pure_modules() -> None:
    assert list(MODELS.rglob("*.py")), f"no modules found under {MODELS}"
    problems = find_impurities()
    assert not problems, "impure imports in models:\n" + "\n".join(problems)


def test_detector_flags_an_impure_import(tmp_path: Path) -> None:
    models = tmp_path / "backend" / "app" / "models"
    models.mkdir(parents=True)
    (models / "x.py").write_text(
        "from decimal import Decimal\n"
        "from pydantic import BaseModel\n"
        "from beancount.core import data\n"
        "from backend.app.core.errors import errors\n"
        "from . import y\n"
        "def f():\n"
        "    import yaml\n"
        "    from beancount.parser import parser\n"
        "    from backend.app.service.ingest.pipeline.normalize import normalize\n"
    )
    found = find_impurities(models, tmp_path)
    assert [line.split(": imports ")[1] for line in found] == [
        "yaml",
        "beancount.parser",
        "backend.app.service.ingest.pipeline.normalize",
    ], found


def test_detector_flags_impure_stdlib_and_dynamic_imports(tmp_path: Path) -> None:
    models = tmp_path / "backend" / "app" / "models"
    models.mkdir(parents=True)
    (models / "x.py").write_text(
        "import json\n"
        "import os\n"
        "from os import path\n"
        "import subprocess\n"
        "import socket\n"
        "import shutil\n"
        "from importlib import import_module\n"
        "def f():\n"
        "    __import__('yaml')\n"
        "    import_module('yaml')\n"
    )
    found = find_impurities(models, tmp_path)
    assert [line.split(": ", 1)[1] for line in found] == [
        "imports os",
        "imports os",
        "imports subprocess",
        "imports socket",
        "imports shutil",
        "imports importlib",
        "calls __import__",
        "calls import_module",
    ], found
