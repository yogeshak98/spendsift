"""Enforce the backend/app dependency rule: a layer imports only what LAYERS allows.

Layer of a module = first path segment under ``backend/app/``; for ``llm`` it is
``llm.flows``, ``llm.prompting`` or ``llm.constants``. ``backend/app/__init__.py``
and ``backend/app/__main__.py`` are the package entry (layer ``entry``, R22): they
may import only ``server``, and no layer imports them. With ``ALLOW_SKIPS`` False
a module, or an import of one, outside a known layer is a violation, and every
``backend/**/*.py`` but ``backend/__init__.py`` must sit under ``backend/app``.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
APP = BACKEND / "app"
APP_PREFIX = "backend.app"

# Every module lives under a known layer: nothing is skipped.
ALLOW_SKIPS = False

# The package entry: `python -m backend.app` delegates to server (R3, R22).
ENTRY_FILES = ("__init__.py", "__main__.py")

# Grouping packages that hold layers but are not one (``llm`` holds llm.flows,
# llm.prompting, llm.constants). Their ``__init__.py`` may hold only a
# docstring and ``__future__`` imports, since it runs on every import below it.
NAMESPACES = ("llm",)

_DYNAMIC_IMPORTS = ("__import__", "import_module")

LAYERS: dict[str, set[str]] = {
    "server": {"server", "service", "llm.flows", "models", "core"},
    "llm.flows": {
        "llm.flows",
        "service",
        "llm.prompting",
        "llm.constants",
        "models",
        "persistence",
        "clients",
        "core",
    },
    "service": {
        "service",
        "llm.prompting",
        "llm.constants",
        "models",
        "persistence",
        "clients",
        "core",
    },
    "llm.prompting": {"llm.prompting", "models", "core"},
    "llm.constants": {"llm.constants", "models", "core"},
    "persistence": {"persistence", "models", "core"},
    "clients": {"clients", "models", "core"},
    "models": {"models", "core"},
    "core": {"core"},
    # cli/render.py is the bounded, redacted view the model sees; it is built
    # from the pure mask/redact helpers (llm.flows) and replace_labels
    # (service), so cli may import those (R18). Like server it never reaches
    # persistence or clients: journal changes go over HTTP only.
    "cli": {"cli", "llm.flows", "service", "models", "core"},
    # backend/app/__init__.py and __main__.py only; nothing may import entry.
    "entry": {"server"},
}


def layer_of_parts(parts: list[str]) -> str | None:
    """Layer for path/dotted segments *below* ``backend.app``; None if unknown."""
    if not parts:
        return None
    head = parts[0]
    if len(parts) == 1 and head in (*ENTRY_FILES, "__main__"):
        return "entry"
    if head == "llm":
        if len(parts) < 2:
            return None
        child = parts[1].removesuffix(".py")
        key = f"llm.{child}"
        return key if key in LAYERS else None
    head = head.removesuffix(".py")
    return head if head in LAYERS else None


def layer_of_file(path: Path) -> str | None:
    return layer_of_parts(list(path.relative_to(APP).parts))


def _module_parts(path: Path, root: Path) -> list[str]:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return parts


def imported_modules(path: Path, tree: ast.AST, root: Path) -> list[tuple[int, str]]:
    """(lineno, absolute dotted name) for every import, nested ones included.

    ``importlib.import_module("x")`` and ``__import__("x")`` with a literal
    module name count as importing ``x``."""
    pkg = _module_parts(path, root)
    if path.name != "__init__.py":
        pkg = pkg[:-1]
    out: list[tuple[int, str]] = []
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
            first = node.args[0] if node.args else None
            if (
                name in _DYNAMIC_IMPORTS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                out.append((node.lineno, first.value))
        elif isinstance(node, ast.Import):
            out.extend((node.lineno, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            out.append((node.lineno, mod))
            # `from backend.app import service` imports a submodule, not an attribute.
            out.extend((node.lineno, f"{mod}.{a.name}") for a in node.names)
    return out


def is_bare_namespace(tree: ast.Module) -> bool:
    """Only a docstring and ``from __future__`` imports."""
    body = list(tree.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return all(
        isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__"
        for stmt in body
    )


def find_violations(app: Path = APP, allow_skips: bool = ALLOW_SKIPS) -> list[str]:
    problems: list[str] = []
    paths = sorted(app.rglob("*.py"))
    if not allow_skips:
        backend = app.parent
        for path in sorted(backend.rglob("*.py")):
            if path != backend / "__init__.py" and app not in path.parents:
                rel = path.relative_to(app.parents[1])
                problems.append(f"{rel}: outside backend/app")
    for path in paths:
        src_layer = layer_of_parts(list(path.relative_to(app).parts))
        rel = path.relative_to(app.parents[1])
        if src_layer is None:
            rel_parts = path.relative_to(app).parts
            if (
                len(rel_parts) == 2
                and rel_parts[0] in NAMESPACES
                and rel_parts[1] == "__init__.py"
                and is_bare_namespace(ast.parse(path.read_text(encoding="utf-8")))
            ):
                continue
            if not allow_skips:
                problems.append(f"{rel}: not under a known layer")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, mod in imported_modules(path, tree, app.parents[1]):
            segs = mod.split(".")
            if mod in ("backend", APP_PREFIX) or (
                segs[:2] == APP_PREFIX.split(".")
                and len(segs) == 3
                and segs[2] in NAMESPACES
            ):
                # The bare package roots and namespaces (no code); `from
                # backend.app.llm import constants` also yields
                # "backend.app.llm.constants", which is checked.
                continue
            if segs[:2] != APP_PREFIX.split(".") or len(segs) < 3:
                if segs[:1] == ["backend"] and not allow_skips:
                    problems.append(f"{rel}:{lineno}: {mod} is outside backend.app")
                continue
            dst_layer = layer_of_parts(segs[2:])
            if dst_layer is None:
                # `from backend.app import x` yields the bare "backend.app" entry
                # plus "backend.app.x"; only the latter can be unknown.
                if not allow_skips:
                    problems.append(f"{rel}:{lineno}: {mod} is not under a known layer")
                continue
            if dst_layer not in LAYERS[src_layer]:
                problems.append(
                    f"{rel}:{lineno}: layer '{src_layer}' may not import "
                    f"layer '{dst_layer}' ({mod})"
                )
    return sorted(set(problems))


def test_layering_rule_holds() -> None:
    problems = find_violations()
    assert not problems, "layering violations:\n" + "\n".join(problems)


def test_service_is_fully_covered() -> None:
    """Service imports nothing outside a known layer (kept from before skips
    were turned off; the full rule now covers it too)."""
    assert any((APP / "service").rglob("*.py"))
    problems = [
        p
        for p in find_violations(allow_skips=False)
        if p.startswith("backend/app/service/")
    ]
    assert not problems, "service coverage gaps:\n" + "\n".join(problems)


def test_layer_table_is_closed() -> None:
    for layer, allowed in LAYERS.items():
        assert allowed <= set(LAYERS), f"{layer} allows unknown layers"


def test_detector_flags_a_forbidden_edge(tmp_path: Path) -> None:
    app = tmp_path / "backend" / "app"
    (app / "persistence").mkdir(parents=True)
    (app / "persistence" / "x.py").write_text(
        "def f():\n    import backend.app.service.y\n"
    )
    found = find_violations(app, allow_skips=True)
    assert (
        len(found) == 1 and "'persistence' may not import layer 'service'" in found[0]
    )


def _probe(tmp_path: Path, layer: str, body: str) -> list[str]:
    app = tmp_path / "backend" / "app"
    (app / layer).mkdir(parents=True)
    (app / layer / "x.py").write_text(body)
    return find_violations(app, allow_skips=True)


def test_cli_may_not_import_persistence(tmp_path: Path) -> None:
    found = _probe(
        tmp_path, "cli", "from backend.app.persistence.journal import Journal\n"
    )
    assert any("'cli' may not import layer 'persistence'" in p for p in found)


def test_cli_may_not_import_server(tmp_path: Path) -> None:
    found = _probe(tmp_path, "cli", "import backend.app.server.main\n")
    assert any("'cli' may not import layer 'server'" in p for p in found)


def test_entry_is_exactly_the_package_entry_files() -> None:
    entry = [p for p in APP.rglob("*.py") if layer_of_file(p) == "entry"]
    assert sorted(p.relative_to(APP).as_posix() for p in entry) == sorted(ENTRY_FILES)


def test_no_layer_may_import_entry() -> None:
    assert [layer for layer, allowed in LAYERS.items() if "entry" in allowed] == []


def test_detector_flags_an_import_of_entry(tmp_path: Path) -> None:
    found = _probe(tmp_path, "server", "from backend.app.__main__ import main\n")
    assert any("'server' may not import layer 'entry'" in p for p in found)


def test_entry_may_import_only_server(tmp_path: Path) -> None:
    app = tmp_path / "backend" / "app"
    app.mkdir(parents=True)
    (app / "__main__.py").write_text(
        "from backend.app.server.__main__ import main\n"
        "import backend.app.service.x\n"
    )
    found = find_violations(app, allow_skips=True)
    assert len(found) == 1 and "'entry' may not import layer 'service'" in found[0]


def test_detector_flags_a_module_outside_backend_app(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    (backend / "app" / "core").mkdir(parents=True)
    (backend / "__init__.py").write_text("")
    (backend / "app" / "core" / "x.py").write_text("")
    assert find_violations(backend / "app", allow_skips=False) == []
    (backend / "stray.py").write_text("")
    (backend / "ingest").mkdir()
    (backend / "ingest" / "__init__.py").write_text("")
    assert find_violations(backend / "app", allow_skips=False) == [
        "backend/ingest/__init__.py: outside backend/app",
        "backend/stray.py: outside backend/app",
    ]


def test_detector_flags_a_module_outside_a_layer(tmp_path: Path) -> None:
    app = tmp_path / "backend" / "app"
    (app / "misc").mkdir(parents=True)
    (app / "misc" / "x.py").write_text("")
    (app / "core").mkdir()
    (app / "core" / "y.py").write_text("import backend.app.misc.x\n")
    assert find_violations(app, allow_skips=False) == [
        "backend/app/core/y.py:1: backend.app.misc.x is not under a known layer",
        "backend/app/misc/x.py: not under a known layer",
    ]


def test_a_namespace_init_may_not_import(tmp_path: Path) -> None:
    app = tmp_path / "backend" / "app"
    (app / "llm").mkdir(parents=True)
    (app / "llm" / "__init__.py").write_text("")
    assert find_violations(app, allow_skips=False) == []
    (app / "llm" / "__init__.py").write_text("import backend.app.core.x\n")
    assert find_violations(app, allow_skips=False) == [
        "backend/app/llm/__init__.py: not under a known layer"
    ]


def test_a_namespace_init_may_hold_only_a_docstring(tmp_path: Path) -> None:
    app = tmp_path / "backend" / "app"
    (app / "llm").mkdir(parents=True)
    init = app / "llm" / "__init__.py"
    init.write_text('"""The llm layers."""\n\nfrom __future__ import annotations\n')
    assert find_violations(app, allow_skips=False) == []
    init.write_text('X = open("/dev/null").read()\n')
    assert find_violations(app, allow_skips=False) == [
        "backend/app/llm/__init__.py: not under a known layer"
    ]


def test_importing_a_layer_through_its_namespace_resolves(tmp_path: Path) -> None:
    found = _probe(
        tmp_path,
        "service",
        "from backend.app.llm import constants, prompting\n",
    )
    assert found == []
    assert _probe(tmp_path / "s", "server", "from backend.app.llm import flows\n") == []
    found = _probe(tmp_path / "p", "persistence", "from backend.app.llm import flows\n")
    assert found == [
        "backend/app/persistence/x.py:1: layer 'persistence' may not import "
        "layer 'llm.flows' (backend.app.llm.flows)"
    ]


def test_detector_flags_a_dynamic_import(tmp_path: Path) -> None:
    found = _probe(
        tmp_path,
        "service",
        "import importlib\n"
        "def f():\n"
        '    importlib.import_module("backend.app.server.main")\n'
        '    __import__("backend.app.server.routes")\n',
    )
    assert found == [
        "backend/app/service/x.py:3: layer 'service' may not import "
        "layer 'server' (backend.app.server.main)",
        "backend/app/service/x.py:4: layer 'service' may not import "
        "layer 'server' (backend.app.server.routes)",
    ]
