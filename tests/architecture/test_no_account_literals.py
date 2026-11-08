"""No vendor-specific account is spelled out in service or llm code.

A platform's cash and clearing accounts (Groww's wallet, its mutual-fund
clearing account, ...) are configuration: they come from
``Settings.platform_cash_accounts`` / ``Settings.platform_clearing_accounts``,
read through ``backend.app.service.ingest.platform_accounts``. A string literal
naming such an account in code would silently ignore that setting. So under
``backend/app/service`` and ``backend/app/llm`` no string literal may

* start with ``Assets:Broker:`` or ``Assets:Equity:``, or
* be an account name (``Assets:``, ``Liabilities:``, ``Income:``,
  ``Expenses:`` or ``Equity:``) with a segment equal, case-insensitively, to a
  key of the default ``platform_*_accounts`` settings.
"""

from __future__ import annotations

import ast
from pathlib import Path

from backend.app.core.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
SCANNED = (ROOT / "backend" / "app" / "service", ROOT / "backend" / "app" / "llm")
FORBIDDEN_PREFIXES = ("Assets:Broker:", "Assets:Equity:")
ACCOUNT_ROOTS = ("Assets:", "Liabilities:", "Income:", "Expenses:", "Equity:")


def _platform_keys() -> frozenset[str]:
    """Every key of the default platform_*_accounts settings, lower-cased."""
    keys: set[str] = set()
    for name, field in Settings.model_fields.items():
        if name.startswith("platform_") and name.endswith("_accounts"):
            keys.update(k.lower() for k in field.get_default(call_default_factory=True))
    return frozenset(keys)


PLATFORM_KEYS = _platform_keys()


def _forbidden(value: str) -> bool:
    if value.startswith(FORBIDDEN_PREFIXES):
        return True
    if not value.startswith(ACCOUNT_ROOTS):
        return False
    return any(segment.lower() in PLATFORM_KEYS for segment in value.split(":"))


def _fold(node: ast.AST, consts: dict[str, str]) -> str | None:
    """`node` as a string when it is built only from str constants: a literal
    (implicit concatenation is already one literal in the AST), a module-level
    str constant of the same file, or a `+` chain of those. Else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left, consts), _fold(node.right, consts)
        if left is not None and right is not None:
            return left + right
    return None


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = <foldable str>` assignments, in source order."""
    consts: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        else:
            continue
        if isinstance(target, ast.Name):
            folded = _fold(value, consts)
            if folded is not None:
                consts[target.id] = folded
    return consts


def _account_literals(source: str, label: str) -> list[str]:
    """Every string in `source` that is a forbidden account literal, as
    "label:line: value": each str constant (f-string pieces included) and the
    constant-folded value of each outermost `+` chain of str constants."""
    tree = ast.parse(source, filename=label)
    consts = _module_constants(tree)
    inner: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            inner.update(id(child) for child in (node.left, node.right))
    hits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value: str | None = node.value
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Add)
            and id(node) not in inner
        ):
            value = _fold(node, consts)
        else:
            continue
        if value is not None and _forbidden(value):
            hits.append((node.lineno, node.col_offset, value))
    hits.sort()
    return [f"{label}:{line}: {value!r}" for line, _col, value in hits]


def test_platform_keys_come_from_the_settings_defaults():
    assert PLATFORM_KEYS == {"groww"}


def test_no_account_literals_in_service():
    files = [p for root in SCANNED for p in sorted(root.rglob("*.py"))]
    assert files, "scanned no files: the service/llm paths moved"
    hits = [
        hit
        for path in files
        for hit in _account_literals(
            path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix()
        )
    ]
    assert hits == []


def test_the_scan_catches_plain_and_f_string_literals():
    source = (
        'A = "Assets:Broker:X"\n'
        'B = f"Assets:Equity:{A}"\n'
        'C = "Assets:Bank:Y"\n'
        'D = "Assets:Investment:MutualFund:GROWW:Cash"\n'
        'E = f"Expenses:Charges:groww:{A}"\n'
        'F = "Groww: Purchase"\n'
        'G = "Assets:Investment:Growwth"\n'
    )
    assert _account_literals(source, "probe") == [
        "probe:1: 'Assets:Broker:X'",
        "probe:2: 'Assets:Equity:'",
        "probe:4: 'Assets:Investment:MutualFund:GROWW:Cash'",
        "probe:5: 'Expenses:Charges:groww:'",
    ]


def test_the_scan_folds_concatenated_constants():
    source = (
        'A = "Assets:Investment:" "MutualFund:Groww:"\n'
        'B = "Assets:Investment:" + "MutualFund:" + "Groww:"\n'
        '_PREFIX = "Assets:Investment:"\n'
        '_MF = _PREFIX + "MutualFund:Groww:"\n'
        '_CASH = _MF + "Cash"\n'
        'OK = _PREFIX + "Stock:"\n'
        "def f(x):\n"
        '    return _PREFIX + x + "Groww:"\n'
    )
    assert _account_literals(source, "probe") == [
        "probe:1: 'Assets:Investment:MutualFund:Groww:'",
        "probe:2: 'Assets:Investment:MutualFund:Groww:'",
        "probe:4: 'Assets:Investment:MutualFund:Groww:'",
        "probe:5: 'Assets:Investment:MutualFund:Groww:Cash'",
    ]
