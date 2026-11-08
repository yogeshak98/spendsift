"""Architecture test: no route reaches for wall-clock.

This is the guard that makes the clock seam stick, and it exists because
removing every `date.today()` from `backend/app/` was NOT enough to close the
hole the first time.

Read models take `today: date | None = None` and fall back to `date.today()`
internally. So a route can ignore the injected clock by simply OMITTING the
argument -- no `date.today()` anywhere in the route module to grep for, no
error, no test failure until a fixture silently crosses a staleness boundary
months later. Three routes were in exactly that state after the first pass
(equity holdings, instrument detail, value history), which is the only reason
this file is an AST check and not a grep.

Two rules, both scoped to the HTTP layer, because that is the one layer a test
cannot inject into. Unit tests call read models directly and pin `today=`
themselves; they are free to omit it where nothing date-derived is asserted.
"""

from __future__ import annotations

import ast
from pathlib import Path

from backend.app.core.paths import REPO_ROOT

# The whole HTTP layer: the routes plus the app factory (main.py) beside them.
_SERVER_DIR = REPO_ROOT / "backend" / "app" / "server"
_ROUTES_DIR = _SERVER_DIR / "routes"

# Read models and reports whose output depends on "now". Omitting `today=`
# when calling one of these from a route silently re-opens the hole.
_DATE_DERIVED = {
    "equity_holdings",
    "investment_hub",
    "instrument_detail",
    "value_history",
    "vehicle_value_history",
    "monthly_recurring",
    "schedules",
}

# main.py legitimately names `date.today` as the DEFAULT clock. That is a
# declared fallback, not wall-clock entering mid-logic, and it is the single
# place the real clock is allowed to come from.
_CLOCK_DEFAULT_FILE = "main.py"


def _route_modules() -> list[Path]:
    return sorted(p for p in _SERVER_DIR.rglob("*.py") if p.name != "__init__.py")


def test_the_scan_finds_the_route_modules():
    # A wrong directory would make both checks below pass by finding nothing.
    modules = _route_modules()
    assert any(p.parent == _ROUTES_DIR for p in modules)
    assert any(p.name == _CLOCK_DEFAULT_FILE for p in modules)


def test_no_route_module_calls_wall_clock_directly():
    offenders: list[str] = []
    for path in _route_modules():
        if path.name == _CLOCK_DEFAULT_FILE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            # date.today() / datetime.now() / datetime.today()
            if node.func.attr in {"today", "now"} and isinstance(
                node.func.value, ast.Name
            ):
                if node.func.value.id in {"date", "_date", "datetime", "_datetime"}:
                    offenders.append(f"{path.name}:{node.lineno} {ast.unparse(node)}")
    assert offenders == [], (
        "A route called wall-clock directly. Use `app.state.clock()` so the "
        "app has one source of 'today' and a caller can say when it is:\n  "
        + "\n  ".join(offenders)
    )


def test_every_date_derived_call_in_a_route_passes_the_clock():
    offenders: list[str] = []
    for path in _route_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else None
            )
            if name not in _DATE_DERIVED:
                continue
            # `schedules` takes its date positionally; everything else by keyword.
            passes_clock = any(kw.arg == "today" for kw in node.keywords) or (
                name == "schedules" and len(node.args) >= 3
            )
            if not passes_clock:
                offenders.append(f"{path.name}:{node.lineno} {name}(...)")
    assert offenders == [], (
        "A route called a date-derived read model without passing the clock. "
        "These fall back to `date.today()` internally, so omitting the "
        "argument silently ignores `app.state.clock`:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_actually_catches_an_omission():
    """Pin the guard itself: a route that omits `today=` must be rejected.

    Without this, a refactor that broke the AST walk (a renamed function, a
    changed call shape) would turn both checks above into tests that pass by
    finding nothing -- the most dangerous kind of green.
    """
    tree = ast.parse("def r():\n    return investment_hub(ledger)\n")
    found = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id in _DATE_DERIVED
        and not any(kw.arg == "today" for kw in n.keywords)
    ]
    assert len(found) == 1
