"""Account names held in the data dir's YAML stores, rewritten when an account
is renamed.

`Ledger.rename_account` rewrites the journal only. These files also name
accounts, and a stale name in one of them makes the next import or report use
the old account (re-proposing it as a new account to open):

* `rules/learned.yaml` -- each rule's `category` (an identity rule keeps its
  `match: identity`);
* `templates/*.yaml` -- a CSV template's `default_account` and the values of
  its `account_marker_map`;
* `templates/pdf/*.yaml` -- a PDF template's `default_account`;
* `rules/loan-terms.yaml` -- terms keyed by the full account name (keys);
* `config/merge_booking.yaml` -- the MF charge accounts (`*_account` values);
* `config/budget.yaml` -- `targets`, keyed by top category (see `_budget`).

Every rename also records `old: new` in `rules/account_aliases.yaml`
(`account_aliases`), which the categorizer resolves its suggestions through:
that is how the shipped merchant dictionary, read from the package seed so its
updates keep flowing, follows a renamed category.

A name matches only when it equals the old account exactly, the same rule the
journal rename applies: a prefix sibling (`HDFCBank`) and a child (`HDFC:Sub`)
keep their names in the journal, so they keep them here too.

Planning reads and rewrites in memory and writes nothing; `apply` writes the
planned files atomically and `restore` puts every one back. Files are
round-tripped with ruamel so comments (including those attached to a renamed
key), key order and a leading `---` in a hand-edited file survive. A file
that does not parse, or is not the shape its store reads, is left alone: its
store already ignores it.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from backend.app.core.errors.errors import CodedError
from backend.app.models.storage.merge_booking import MergeBookingConfig
from backend.app.models.storage.pdf_template import PdfTemplate
from backend.app.models.storage.template import Template
from backend.app.persistence.account_aliases import aliases_path
from backend.app.persistence.atomic import write_text_atomic
from backend.app.persistence.yaml_stores import (
    DEPRECATED_BOOKING_KEYS,
    validate_booking,
)

logger = logging.getLogger(__name__)

LEARNED_RULES = "learned_rules"
TEMPLATES = "templates"
PDF_TEMPLATES = "pdf_templates"
LOAN_TERMS = "loan_terms"
MERGE_BOOKING = "merge_booking"
BUDGET = "budget"
ALIASES = "aliases"

STORES = (
    LEARNED_RULES,
    TEMPLATES,
    PDF_TEMPLATES,
    LOAN_TERMS,
    MERGE_BOOKING,
    BUDGET,
    ALIASES,
)


class TemplateRenameInvalid(ValueError):
    """The renamed account is not valid in a template (or the MF booking
    config) that names the old one and loads today."""

    def __init__(self, path: Path, label: str, reason: str) -> None:
        super().__init__(f"{label}: {reason}")
        self.path = path
        self.label = label
        self.reason = reason


@dataclass(frozen=True)
class FileRewrite:
    store: str
    path: Path
    # None when the file did not exist before (restore deletes it).
    original: str | None
    updated: str
    count: int


def _yaml(*, explicit_start: bool = False) -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096  # never re-wrap a line the user typed
    y.explicit_start = explicit_start
    return y


def _has_explicit_start(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped == "---" or stripped.startswith("--- ")
    return False


def _load(text: str) -> Any:
    try:
        return _yaml().load(text)
    except YAMLError:
        return None


def _dump(doc: Any, *, explicit_start: bool = False) -> str:
    out = io.StringIO()
    _yaml(explicit_start=explicit_start).dump(doc, out)
    return out.getvalue()


def _plain(doc: Any) -> Any:
    """`doc` as plain dicts/lists, for the pydantic models."""
    if isinstance(doc, dict):
        return {str(k): _plain(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_plain(v) for v in doc]
    return doc


def _rename_key(mapping: CommentedMap, old: str, new: str) -> None:
    """Rename `old` to `new` in place, keeping its position and the comments
    ruamel attaches to it (its end-of-line comment and any comment lines
    below it, which ruamel stores on the preceding key)."""
    position = list(mapping.keys()).index(old)
    comment = mapping.ca.items.pop(old, None)
    value = mapping.pop(old)
    mapping.insert(position, new, value)
    if comment is not None:
        mapping.ca.items[new] = comment


# Each rewriter edits the parsed document in place and returns the number of
# references it changed.
_Rewriter = Callable[[Any, str, str], int]


def _learned(doc: Any, old: str, new: str) -> int:
    if not isinstance(doc, CommentedSeq):
        return 0
    count = 0
    for rule in doc:
        if isinstance(rule, CommentedMap) and rule.get("category") == old:
            rule["category"] = new
            count += 1
    return count


def _csv_template(doc: Any, old: str, new: str) -> int:
    if not isinstance(doc, CommentedMap):
        return 0
    count = 0
    if doc.get("default_account") == old:
        doc["default_account"] = new
        count += 1
    markers = doc.get("account_marker_map")
    if isinstance(markers, CommentedMap):
        for marker, account in markers.items():
            if account == old:
                markers[marker] = new
                count += 1
    return count


def _pdf_template(doc: Any, old: str, new: str) -> int:
    if isinstance(doc, CommentedMap) and doc.get("default_account") == old:
        doc["default_account"] = new
        return 1
    return 0


def _loan_terms(doc: Any, old: str, new: str) -> int:
    # A stale entry already under the new name is the user's; it is not
    # overwritten, so the old entry is left too.
    if isinstance(doc, CommentedMap) and old in doc and new not in doc:
        _rename_key(doc, old, new)
        return 1
    return 0


def _merge_booking(doc: Any, old: str, new: str) -> int:
    if not isinstance(doc, CommentedMap):
        return 0
    count = 0
    for key, value in doc.items():
        if isinstance(key, str) and key.endswith("_account") and value == old:
            doc[key] = new
            count += 1
    return count


def _aliases(doc: Any, old: str, new: str) -> int:
    """Record `old -> new`. An alias AWAY from `new` is dropped: `new` is a
    live account again, so it must resolve to itself."""
    if not isinstance(doc, CommentedMap):
        return 0
    if new in doc:
        doc.ca.items.pop(new, None)
        del doc[new]
    doc[old] = new
    return 1


def _top_category(account: str) -> str:
    parts = account.split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else account


def _budget(still_open: Iterable[str]) -> _Rewriter:
    """Budget targets are keyed by top category (`Expenses:Food`), a rollup of
    every account under it, not one account. The key follows the rename only
    when the old account IS that rollup on its own: it is a top category, no
    open account is left under it, and the new name is a top category not
    already budgeted."""
    remaining = list(still_open)

    def rewrite(doc: Any, old: str, new: str) -> int:
        if not isinstance(doc, CommentedMap):
            return 0
        targets = doc.get("targets")
        if not isinstance(targets, CommentedMap) or old not in targets:
            return 0
        if _top_category(old) != old or _top_category(new) != new:
            return 0
        if new in targets or any(a.startswith(old + ":") for a in remaining):
            return 0
        _rename_key(targets, old, new)
        return 1

    return rewrite


def _reason(exc: BaseException) -> str:
    """The one line a user can act on: the first validation message (with the
    field it is about), or a coded error's description."""
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            first = errors[0]
            msg = str(first.get("msg", "")).removeprefix("Value error, ")
            loc = ".".join(str(part) for part in first.get("loc", ()))
            return f"{loc}: {msg}" if loc else msg
    if isinstance(exc, CodedError):
        return exc.description
    return str(exc)


# A checker validates a parsed document the way its store would on load (or
# save); it raises on an invalid one.
_Checker = Callable[[Any], None]


def _check(path: Path, label: str, before: Any, after: Any, check: _Checker) -> None:
    """Refuse a rewrite that turns a file its store uses into one it would
    skip or refuse (e.g. a card renamed out of `Liabilities:CreditCard:`). A
    file already invalid is already skipped by its store: not checked."""
    try:
        check(before)
    except Exception:  # noqa: BLE001 -- any refusal: CodedError, ValidationError, ...
        return
    try:
        check(after)
    except Exception as exc:  # noqa: BLE001
        raise TemplateRenameInvalid(path, label, _reason(exc)) from exc


def _model_check(model: Any) -> _Checker:
    return lambda doc: model.model_validate(doc)


def _booking_check(doc: Any) -> None:
    validate_booking(MergeBookingConfig(**doc))


def _csv_label(path: Path, doc: dict) -> str:
    return f"the CSV import template for {doc.get('institution', '?')} ({path.name})"


def _pdf_label(path: Path, doc: dict) -> str:
    card = doc.get("card_last4")
    card_text = f" card {card}" if card else ""
    return (
        f"the PDF import template for {doc.get('institution', '?')}{card_text}"
        f" ({path.name})"
    )


def _booking_label(path: Path, doc: dict) -> str:
    return f"the MF booking config ({path.name})"


def _plan_file(
    store: str,
    path: Path,
    rewrite: _Rewriter,
    old: str,
    new: str,
    *,
    check: _Checker | None = None,
    label: Callable[[Path, dict], str] | None = None,
) -> FileRewrite | None:
    if not path.is_file():
        return None
    try:
        original = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    doc = _load(original)
    if doc is None:
        return None
    before = _plain(doc)
    count = rewrite(doc, old, new)
    if not count:
        return None
    if check is not None:
        name = label(path, before) if label and isinstance(before, dict) else path.name
        _check(path, name, before, _plain(doc), check)
    updated = _dump(doc, explicit_start=_has_explicit_start(original))
    return FileRewrite(store, path, original, updated, count)


def _plan_default_booking(path: Path, old: str, new: str) -> FileRewrite | None:
    """With no merge_booking.yaml the import books MF charges to the model's
    defaults; renaming one of those accounts writes the file, so the next
    import books to the new name instead of re-opening the old one. The file
    holds what `MergeBookingStore.save` would write, less the deprecated
    keys, and must pass its validation."""
    defaults = MergeBookingConfig().model_dump(exclude=set(DEPRECATED_BOOKING_KEYS))
    doc = CommentedMap(sorted(defaults.items()))
    count = _merge_booking(doc, old, new)
    if not count:
        return None
    _check(
        path,
        _booking_label(path, {}),
        defaults,
        _plain(doc),
        _booking_check,
    )
    return FileRewrite(MERGE_BOOKING, path, None, _dump(doc), count)


_ALIAS_LABEL = "the account alias file (account_aliases.yaml)"


def _plan_aliases(path: Path, old: str, new: str) -> FileRewrite:
    """Always records the alias. An absent, empty or comment-only file gets a
    new mapping (comments kept above it); a file that is some other shape is
    the user's to fix, so the rename is refused rather than reporting an
    alias it did not record."""
    entry = CommentedMap()
    _aliases(entry, old, new)
    if not path.exists():
        return FileRewrite(ALIASES, path, None, _dump(entry), 1)
    try:
        original = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        raise TemplateRenameInvalid(path, _ALIAS_LABEL, str(exc)) from exc
    doc = _load(original)
    if doc is None:
        if original.strip() and not all(
            line.strip().startswith("#") or not line.strip()
            for line in original.splitlines()
        ):
            raise TemplateRenameInvalid(
                path, _ALIAS_LABEL, "it does not parse as YAML; fix or empty it"
            )
        head = original if not original or original.endswith("\n") else original + "\n"
        return FileRewrite(ALIASES, path, original, head + _dump(entry), 1)
    if not isinstance(doc, CommentedMap):
        raise TemplateRenameInvalid(
            path,
            _ALIAS_LABEL,
            "it is not a mapping of old: new account names; fix or empty it",
        )
    _aliases(doc, old, new)
    updated = _dump(doc, explicit_start=_has_explicit_start(original))
    return FileRewrite(ALIASES, path, original, updated, 1)


def plan_alias_prune(path: Path, names: Iterable[str]) -> FileRewrite | None:
    """Drop the aliases away from `names` (accounts opened again), or None
    when there is nothing to drop or the file is not a readable mapping."""
    wanted = set(names)

    def prune(doc: Any, _old: str, _new: str) -> int:
        if not isinstance(doc, CommentedMap):
            return 0
        gone = [key for key in doc if key in wanted]
        for key in gone:
            doc.ca.items.pop(key, None)
            del doc[key]
        return len(gone)

    return _plan_file(ALIASES, path, prune, "", "")


def plan_account_rename(
    storage_dir: Path, old: str, new: str, *, still_open: Iterable[str]
) -> list[FileRewrite]:
    """Every store file that names `old`, rewritten to `new`, plus the alias
    record. Writes nothing.

    `still_open` is the open accounts other than `old`. Raises
    `TemplateRenameInvalid` when the rename would make a template, or the MF
    booking config, that is valid today invalid."""
    rules = storage_dir / "rules"
    config = storage_dir / "config"
    templates = storage_dir / "templates"
    planned: list[FileRewrite | None] = [
        _plan_file(LEARNED_RULES, rules / "learned.yaml", _learned, old, new),
        *(
            _plan_file(
                TEMPLATES,
                p,
                _csv_template,
                old,
                new,
                check=_model_check(Template),
                label=_csv_label,
            )
            for p in sorted(templates.glob("*.yaml"))
        ),
        *(
            _plan_file(
                PDF_TEMPLATES,
                p,
                _pdf_template,
                old,
                new,
                check=_model_check(PdfTemplate),
                label=_pdf_label,
            )
            for p in sorted((templates / "pdf").glob("*.yaml"))
        ),
        _plan_file(LOAN_TERMS, rules / "loan-terms.yaml", _loan_terms, old, new),
        _plan_file(BUDGET, config / "budget.yaml", _budget(still_open), old, new),
    ]
    booking = config / "merge_booking.yaml"
    if booking.exists():
        planned.append(
            _plan_file(
                MERGE_BOOKING,
                booking,
                _merge_booking,
                old,
                new,
                check=_booking_check,
                label=_booking_label,
            )
        )
    else:
        planned.append(_plan_default_booking(booking, old, new))
    planned.append(_plan_aliases(aliases_path(storage_dir), old, new))
    return [rewrite for rewrite in planned if rewrite is not None]


def apply(rewrites: Iterable[FileRewrite]) -> None:
    for rewrite in rewrites:
        write_text_atomic(rewrite.path, rewrite.updated)


def restore(rewrites: Iterable[FileRewrite]) -> list[tuple[Path, BaseException]]:
    """Put every planned file back as it was before `apply` (deleting one that
    did not exist). Safe on a file `apply` never reached. Every file is
    attempted; the ones that could not be restored are logged and returned,
    never raised, so the caller re-raises the failure that started it."""
    failures: list[tuple[Path, BaseException]] = []
    for rewrite in rewrites:
        try:
            if rewrite.original is None:
                rewrite.path.unlink(missing_ok=True)
            else:
                write_text_atomic(rewrite.path, rewrite.original)
        except Exception as exc:  # noqa: BLE001 -- keep restoring the rest
            logger.error(
                "could not restore %s after a failed rename: %s", rewrite.path, exc
            )
            failures.append((rewrite.path, exc))
    return failures


def counts(rewrites: Iterable[FileRewrite]) -> dict[str, int]:
    """References changed per store, every store present (0 when untouched)."""
    totals = dict.fromkeys(STORES, 0)
    for rewrite in rewrites:
        totals[rewrite.store] += rewrite.count
    return totals
