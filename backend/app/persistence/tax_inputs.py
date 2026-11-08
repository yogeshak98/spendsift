"""Read/write the user's declared tax inputs: config/tax/<fy>-inputs.yaml.

This file is BOTH hand-editable and form-editable, which drives every choice
here:

* `ruamel.yaml` round-trip is the ONLY parser for `*-inputs.yaml` -- loaders
  included (`backend.app.service.tax.rules` imports `read_mapping` from here). One
  parser means the estimate and the form cannot read the same file two
  different ways, and a form write keeps the comments and key order the user
  typed. Rules files (`<fy>.yaml`) are reference data, not form-edited, and
  stay on PyYAML.
* Reads never guess. A missing key is "not declared" and a declared `"0"` is an
  answer; the two are distinguishable all the way to the screen, because the
  regime recommendation is honest only if it knows which one it has.
* Writes are atomic (temp file + `os.replace`) and backed up, because the file
  carries figures the user typed off a paper Form 16 and cannot re-derive.

Deliberately not modelled on `yaml_stores.BudgetStore`: that is a bare `write_text`
with no backup and no revision check, which is tolerable for a budget target
and not for this.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from backend.app.core.errors.errors import (
    TAX_INPUTS_FILE_UNPARSEABLE,
    TAX_INPUTS_REVISION_MISMATCH,
    TAX_INPUTS_UNKNOWN_KEY,
    TAX_INPUTS_VALUE_INVALID,
)

# `income` keeps its name and its two shipped keys: `INCOME_FIELDS` is what
# `tax_rules.INCOME_KEYS` is, and the notice at DeclaredNotices.tsx:27-31
# already teaches `gross_salary`/`tds` to the user.
#
# One exemption field, not two: `exempt_hra_lta` is HRA s.10(13A) + LTA s.10(5)
# together, the two that s.115BAC withdraws and therefore the pair that decides
# the regime. Gratuity and leave encashment are exempt under BOTH regimes and
# need no field at all: a `gross_salary` declared from payroll's taxable salary
# is already net of those s.10 exemptions. (The s.10(10) ceiling is a lifetime
# limit across employers, so tracking it would need history, not an FY field.)
INCOME_FIELDS = ("gross_salary", "tds", "exempt_hra_lta")

# Chapter VI-A sections accepted when the FY's rules file cannot be read (it is
# absent, or it is broken). When the rules ARE readable its own `deductions`
# caps are the list, so a user who adds a section to their rules file can
# declare it. A static list is used only as the fallback so that the form still
# functions as the repair tool for a broken rules file.
FALLBACK_DEDUCTION_SECTIONS = ("80c", "80ccd_1b", "80ccd_2", "80d", "80tta")

REGIME_VALUES = ("new", "old")

SECTION_INCOME = "income"
SECTION_DEDUCTIONS = "deductions"
KEY_REGIME = "regime"
# `taxpayer` is accepted and preserved without being understood: a later unit
# owns it. Everything else at the top level is rejected by name -- an
# unrecognised key would author a declaration that no loader reads, which is
# the one failure the user cannot debug from the screen.
KEY_TAXPAYER = "taxpayer"
TOP_LEVEL_KEYS = (KEY_REGIME, SECTION_INCOME, SECTION_DEDUCTIONS, KEY_TAXPAYER)

REVISION_ABSENT = "absent"


class _Absent:
    """Type of the ABSENT sentinel below."""


# "This key was not in the PATCH body", which is a third state distinct from a
# `null` that deletes the value. A dedicated sentinel rather than `...` so the
# three-valued contract is in the type signature and checkable.
ABSENT = _Absent()

_BACKUPS_KEPT = 10

# Plain digits, optional 2-dp fraction: finite, non-negative, scale <= 2 and
# below 10**12 in one predicate. Written as a regex rather than Decimal
# comparisons on purpose -- `Decimal("Infinity") >= 0` is True and passes a
# naive bounds check, `Decimal("NaN") >= 0` RAISES InvalidOperation (so the
# naive check 500s on it), and `Decimal("1_000")` silently becomes 1000.
_AMOUNT_RE = re.compile(r"^[0-9]{1,12}(\.[0-9]{1,2})?$")

# Indian digit grouping is the expected hand-edit, so it gets its own message
# instead of the generic one.
_GROUPED_RE = re.compile(r"^[0-9][0-9,]*(\.[0-9]+)?$")


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096  # never re-wrap a line the user typed
    return y


def inputs_path(storage_dir: Path | str, fy: int) -> Path:
    return Path(storage_dir) / "config" / "tax" / f"{fy}-inputs.yaml"


def _revision_of(path: Path) -> str:
    if not path.exists():
        return REVISION_ABSENT
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _amount_text(raw: object) -> str | None:
    """`raw` as the text the predicate judges, or None if it is not a scalar
    a figure could be written as. Unquoted YAML numbers are included because
    the file is hand-edited and `80c: 150000` is a reasonable thing to type."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        return None
    return str(raw).strip()


def amount_is_valid(raw: object) -> bool:
    """True when `raw` is a figure this store would accept and write."""
    text = _amount_text(raw)
    return text is not None and bool(_AMOUNT_RE.match(text))


def amount_value(raw: object) -> Decimal:
    """`raw` as a Decimal. Only valid for values `amount_is_valid` accepted."""
    return Decimal(str(_amount_text(raw)))


def _reject_amount(key: str, raw: object, path: Path) -> None:
    shown = "" if raw is None else str(raw)
    hint = (
        f'Type it as plain digits in quotes -- `{key}: "1200000"`. '
        if _GROUPED_RE.match(shown) and "," in shown
        else ""
    )
    raise TAX_INPUTS_VALUE_INVALID.exc(
        {"key": key, "value": shown, "hint": hint, "path": str(path)}
    )


# --- reading ----------------------------------------------------------------


@dataclass(frozen=True)
class InputsRead:
    """The file as it is on disk right now."""

    path: Path
    exists: bool
    revision: str
    # The parsed mapping, or None when the file cannot be read as one. A
    # present-but-empty file parses as an empty mapping, not as None.
    document: dict | None
    parse_error: str | None
    # The file's text, carried ONLY when it could not be parsed, so the form
    # can show the user what to repair.
    raw_text: str | None


def read(storage_dir: Path | str, fy: int) -> InputsRead:
    """Read the file. NEVER raises on content: broken YAML, a top-level list, a
    bad figure -- all come back as data, because the form that repairs the file
    is the caller, and an endpoint that 500s on a broken file cannot be used to
    fix it."""
    path = inputs_path(storage_dir, fy)
    if not path.exists():
        return InputsRead(path, False, REVISION_ABSENT, {}, None, None)
    text = path.read_text(encoding="utf-8")
    revision = _revision_of(path)
    try:
        raw = _yaml().load(text)
    except YAMLError as exc:
        return InputsRead(path, True, revision, None, _one_line(exc), text)
    if raw is None:
        return InputsRead(path, True, revision, {}, None, None)
    if not isinstance(raw, dict):
        return InputsRead(
            path,
            True,
            revision,
            None,
            "the file must be a mapping of sections, not a "
            f"{'list' if isinstance(raw, (list, tuple)) else 'single value'}",
            text,
        )
    return InputsRead(path, True, revision, raw, None, None)


def plain(obj: object) -> object:
    """A ruamel round-trip tree as plain Python, for anything that crosses the
    API boundary. ruamel's nodes subclass dict/str/int so they serialise, but
    they also carry comment attachments that have no business in a response."""
    if isinstance(obj, dict):
        return {str(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, (int, float)):
        return str(obj)
    return str(obj)


def _one_line(exc: Exception) -> str:
    return " ".join(str(exc).split()) or type(exc).__name__


def read_mapping(storage_dir: Path | str, fy: int) -> dict:
    """The parsed document for the loaders that feed the estimate. `{}` when
    the file is absent or empty.

    Raises the coded E1059 when the file is present but unparseable, rather
    than returning `{}`: a declaration that is on disk and silently has no
    effect is exactly the failure the user cannot see. `read()` -- what the
    form's own endpoint uses -- is the non-raising door to the same file.
    """
    state = read(storage_dir, fy)
    if state.document is None:
        raise TAX_INPUTS_FILE_UNPARSEABLE.exc(
            {"path": str(state.path), "problem": state.parse_error or "unreadable"}
        )
    return state.document


def declared_regime(storage_dir: Path | str, fy: int) -> str | None:
    """The regime the user says they file under, or None.

    An unrecognised value reads as None rather than raising: this loader feeds
    the workspace, whose contract is to stay loadable, and None simply means
    the balance due falls back to quoting the cheaper regime.
    """
    value = read_mapping(storage_dir, fy).get(KEY_REGIME)
    return str(value) if isinstance(value, str) and value in REGIME_VALUES else None


# --- validation -------------------------------------------------------------


def _reject_key(key: str, where: str, allowed: tuple[str, ...], path: Path) -> None:
    raise TAX_INPUTS_UNKNOWN_KEY.exc(
        {
            "key": key,
            "where": where,
            "allowed": ", ".join(allowed),
            "path": str(path),
        }
    )


def _check_structure(
    document: dict, path: Path, deduction_sections: tuple[str, ...]
) -> None:
    """Key names and block shapes. Values are checked separately, because a
    figure already on disk must not block an unrelated field's save."""
    for key in document:
        if str(key) not in TOP_LEVEL_KEYS:
            _reject_key(str(key), "the top level", TOP_LEVEL_KEYS, path)
    regime = document.get(KEY_REGIME)
    if regime is not None and (
        not isinstance(regime, str) or regime not in REGIME_VALUES
    ):
        _reject_key(str(regime), "`regime`", REGIME_VALUES, path)
    for section, allowed in (
        (SECTION_INCOME, INCOME_FIELDS),
        (SECTION_DEDUCTIONS, deduction_sections),
    ):
        block = document.get(section)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise TAX_INPUTS_UNKNOWN_KEY.exc(
                {
                    "key": f"{section}: {block!r}",
                    "where": f"`{section}`",
                    "allowed": ", ".join(allowed),
                    "path": str(path),
                }
            )
        for key in block:
            if str(key) not in allowed:
                _reject_key(str(key), f"`{section}`", allowed, path)


def _check_values(document: dict, path: Path) -> None:
    """Every figure in `document` passes the amount predicate."""
    for section in (SECTION_INCOME, SECTION_DEDUCTIONS):
        block = document.get(section)
        if not isinstance(block, dict):
            continue
        for key, value in block.items():
            if not amount_is_valid(value):
                _reject_amount(str(key), value, path)


# --- writing ----------------------------------------------------------------


def _dump(document: dict) -> str:
    buf = io.StringIO()
    _yaml().dump(document, buf)
    return buf.getvalue()


def _backup(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    backups = path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    dest = backups / f"{path.stem}.{stamp}{path.suffix}"
    dest.write_bytes(path.read_bytes())
    kept = sorted(backups.glob(f"{path.stem}.*{path.suffix}"))
    for stale in kept[:-_BACKUPS_KEPT]:
        stale.unlink()
    return dest


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _backup(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _require_revision(state: InputsRead, expected: str) -> None:
    if expected == state.revision:
        return
    raise TAX_INPUTS_REVISION_MISMATCH.exc(
        {"expected": expected, "current": state.revision, "path": str(state.path)},
        data={
            "revision": state.revision,
            "document": plain(state.document) if state.document is not None else None,
            "parse_error": state.parse_error,
            "raw_text": state.raw_text,
        },
    )


def patch(
    storage_dir: Path | str,
    fy: int,
    *,
    regime: str | None | _Absent = ABSENT,
    income: dict[str, str | None] | None | _Absent = ABSENT,
    deductions: dict[str, str | None] | None | _Absent = ABSENT,
    extra_keys: tuple[str, ...] = (),
    expected_revision: str,
    deduction_sections: tuple[str, ...] = FALLBACK_DEDUCTION_SECTIONS,
) -> InputsRead:
    """Merge a three-valued patch into the file and write it.

    Per field: `ABSENT` leaves the stored value alone, `None` deletes it, a
    string sets it. Per block: absent leaves the block, `None` deletes the
    block, `{}` is a no-op. So a deductions-only save cannot delete the
    declared salary -- the regression this shape exists to prevent.

    Nothing is written unless the whole merged document passes validation, and
    only the values this call supplies are value-checked: a bad figure already
    on disk is reported on read and must not block the repair of a different
    field.
    """
    state = read(storage_dir, fy)
    for key in extra_keys:
        # `taxpayer` is a legal key of the FILE that this form does not own, so
        # a body carrying it is ignored rather than refused; the unit that owns
        # it will set it through its own field. Anything else is refused by
        # name before a byte is written.
        if key not in TOP_LEVEL_KEYS:
            _reject_key(key, "the top level", TOP_LEVEL_KEYS, state.path)
    if state.document is None:
        raise TAX_INPUTS_FILE_UNPARSEABLE.exc(
            {"path": str(state.path), "problem": state.parse_error or "unreadable"}
        )
    _require_revision(state, expected_revision)

    document = state.document
    touched: dict = {}
    if not isinstance(regime, _Absent):
        if regime is None:
            document.pop(KEY_REGIME, None)
        else:
            document[KEY_REGIME] = regime
    # Only the blocks this call actually carries; an ABSENT block is not merged
    # at all, which is what keeps a deductions-only save off the income block.
    pending: list[tuple[str, dict[str, str | None] | None]] = []
    if not isinstance(income, _Absent):
        pending.append((SECTION_INCOME, income))
    if not isinstance(deductions, _Absent):
        pending.append((SECTION_DEDUCTIONS, deductions))
    for section, patch_block in pending:
        if patch_block is None:
            document.pop(section, None)
            continue
        block = document.get(section)
        if not isinstance(block, dict):
            # A scalar or list `income:` is replaced by the mapping the form
            # posts; leaving it in place would make the save a silent no-op.
            block = {}
            document[section] = block
        for key, value in patch_block.items():
            if value is None:
                block.pop(key, None)
            else:
                block[key] = value
                touched.setdefault(section, {})[key] = value
        if not block:
            document.pop(section, None)

    _check_structure(document, state.path, deduction_sections)
    _check_values(touched, state.path)
    _write_atomic(state.path, _dump(document))
    return read(storage_dir, fy)


def replace(
    storage_dir: Path | str,
    fy: int,
    text: str,
    *,
    expected_revision: str,
    deduction_sections: tuple[str, ...] = FALLBACK_DEDUCTION_SECTIONS,
) -> InputsRead:
    """Write the whole document from text, sharing `patch`'s validation.

    This is the repair door: it is the one write that is allowed when the file
    on disk does not parse, so a hand-edit that broke the YAML can be fixed
    from the form instead of from a shell.
    """
    state = read(storage_dir, fy)
    _require_revision(state, expected_revision)
    path = state.path
    try:
        raw = _yaml().load(text)
    except YAMLError as exc:
        raise TAX_INPUTS_FILE_UNPARSEABLE.exc(
            {"path": str(path), "problem": _one_line(exc)}
        ) from exc
    document = {} if raw is None else raw
    if not isinstance(document, dict):
        raise TAX_INPUTS_FILE_UNPARSEABLE.exc(
            {
                "path": str(path),
                "problem": "the document must be a mapping of sections",
            }
        )
    _check_structure(document, path, deduction_sections)
    _check_values(document, path)
    _write_atomic(path, text if raw is not None else _dump(document))
    return read(storage_dir, fy)
