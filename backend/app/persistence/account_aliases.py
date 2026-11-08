"""Renamed-account aliases: `<data>/rules/account_aliases.yaml`, old -> new.

An account rename records `old: new` here. The categorizer resolves the
shipped merchant dictionary's suggestions through this map -- the dictionary
is read from the package seed so its updates keep flowing, so it is the one
source the rename cannot rewrite. Learned rules are NOT resolved: the rename
rewrites them, and a rule the user writes afterwards means what it says.

A suggestion is redirected only to an OPEN account: resolution walks the chain
(`A -> B -> C`) and stops at the first name that is open, the suggestion
itself included. If no name on the chain is open (the target was closed, say)
the original suggestion is kept and review treats it as usual. A cycle (only
possible by hand-editing) stops at the first repeat.

An alias is retired when its old name is opened again (`prune`): by account
create, by an import or ledger-merge commit that opens it, or by a later
rename onto it. The file is user-owned plain YAML; one that does not parse,
or is not a mapping, aliases nothing here (a rename refuses to write it).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Collection, Iterable
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

FILENAME = "account_aliases.yaml"


def aliases_path(storage_dir: Path) -> Path:
    return storage_dir / "rules" / FILENAME


def _read(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and k != v}


class AccountAliases:
    """The alias map in memory. A rename writes it through `account_refs` and
    then calls `reload`; `prune` retires aliases for names opened again."""

    def __init__(self, path: Path | str, *, lock: threading.RLock | None = None):
        self.path = Path(path)
        self.lock = lock if lock is not None else threading.RLock()
        self._map: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        with self.lock:
            self._map = _read(self.path)

    def resolve(
        self, account: str, open_accounts: Callable[[], Collection[str]]
    ) -> str:
        """The first OPEN name on `account`'s alias chain, `account` itself
        first; `account` unchanged when it has no alias or no name on the
        chain is open. `open_accounts` is called only when an alias applies."""
        if account not in self._map:
            return account
        live = open_accounts()
        seen: set[str] = set()
        current: str | None = account
        while current is not None and current not in seen:
            if current in live:
                return current
            seen.add(current)
            current = self._map.get(current)
        return account

    def prune(self, opened: Iterable[str]) -> int:
        """Retire the aliases away from `opened` (accounts just opened again).
        Never raises: the accounts are open whatever happens here, and
        `resolve` already stops at an open name. Returns how many were
        dropped."""
        from backend.app.persistence import account_refs

        names = [name for name in opened if name in self._map]
        if not names:
            return 0
        with self.lock:
            try:
                rewrite = account_refs.plan_alias_prune(self.path, names)
                if rewrite is not None:
                    account_refs.apply([rewrite])
            except Exception as exc:  # noqa: BLE001 -- best effort, see above
                logger.warning("could not retire aliases for %s: %s", names, exc)
                return 0
            self.reload()
        return rewrite.count if rewrite is not None else 0
