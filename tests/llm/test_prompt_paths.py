"""Prompt locations are named in ONE place: ``backend/app/llm/constants.py``.

Any other module under ``backend/app`` that spells a prompt file name or the
prompts directory as a string literal is a hardcoded path the next move or
rename silently breaks. Scans string literals (docstrings and f-string parts
included) with ``ast``, so comments do not count.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
ALLOWED = {APP / "llm" / "constants.py"}


def _is_prompt_literal(value: str) -> bool:
    return (
        value.endswith(".md")
        or value.endswith(("prompts", "prompts/"))
        or "prompt.md" in value
    )


_KEY_LOOKUPS = {"get", "pop", "setdefault"}


def _key_literals(tree: ast.AST) -> set[int]:
    """ids of string constants used as a mapping key, not as a path segment:
    dict-literal keys, subscripts and the key argument of .get/.pop/.setdefault.
    A bare "prompts" there is a JSON field, not the prompts directory."""
    keys: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys.update(id(k) for k in node.keys if k is not None)
        elif isinstance(node, ast.Subscript):
            keys.add(id(node.slice))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _KEY_LOOKUPS
            and node.args
        ):
            keys.add(id(node.args[0]))
    return keys


def prompt_literals(app: Path = APP) -> tuple[int, list[str]]:
    """(files scanned, "<file>:<line>: <literal>" for every offending literal)."""
    scanned = 0
    hits: list[str] = []
    for path in sorted(app.rglob("*.py")):
        scanned += 1
        if path in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        keys = _key_literals(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _is_prompt_literal(node.value)
                and not (node.value == "prompts" and id(node) in keys)
            ):
                rel = path.relative_to(app.parent.parent)
                hits.append(f"{rel}:{node.lineno}: {node.value!r}")
    return scanned, hits


def test_no_prompt_path_literals_outside_constants() -> None:
    scanned, hits = prompt_literals()
    assert scanned > 0, f"scanned no files under {APP}"
    assert not hits, "prompt path literals outside llm/constants.py:\n" + "\n".join(
        hits
    )


def test_detector_flags_each_literal_shape(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "x.py").write_text(
        'A = "categorize.md"\n'
        'B = "prompts"\n'
        'C = "the default has a .prompt.md suffix, see it"\n'
        'D = f"{A}.md"\n'
        "# a comment naming notes.md is not a literal\n"
        'E = "prompts/ folder is fine mid-sentence"\n'
        'F = "backend/app/llm/prompts/"\n'
        'G = {"prompts": 1}["prompts"]\n'
        'H = "prompts/"\n'
        'I = "llm/prompts"\n'
        'J = {}.get("prompts")\n'
        'K = ROOT / "prompts"\n'
    )
    scanned, hits = prompt_literals(app)
    assert scanned == 1
    # ast.walk visits the f-string's parts after the top-level literals.
    lines = sorted((h.split(": ", 1)[0].rsplit(":", 1)[1] for h in hits), key=int)
    assert lines == [
        "1",
        "2",
        "3",
        "4",
        "7",
        "9",
        "10",
        "12",
    ], hits
