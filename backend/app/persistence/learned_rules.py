from __future__ import annotations

import threading
from collections.abc import Collection
from pathlib import Path

import yaml

from backend.app.persistence.atomic import write_text_atomic

# The `match` value of an identity rule. A rule without a `match` key is a
# substring rule: every rule written before identity rules existed, and every
# hand-written one, whatever its pattern looks like (a backlog rule
# "UPI:SWIGGY ORDER" stays a substring rule). Any other value is a kind this
# build does not know, so the rule is kept but never matches.
IDENTITY = "identity"


def _kind(rule: dict) -> str | None:
    return rule.get("match")


class LearnedRules:
    """User-owned category rules, persisted as YAML. Later rules win.

    A rule is `{pattern, category}` -- `pattern` a substring of the
    normalised narration -- or `{pattern, category, match: identity}`, a UPI
    payee identity ("UPI:<PAYEE>|<VPA>", see `categorize.patterns.
    upi_identity`) that matches only a row with exactly that identity.

    The file is a plain YAML list of mappings and nothing validates its keys:
    an older build reads an identity rule without error, as a substring rule
    whose pattern never occurs in a normalised narration (dead, harmless), and
    keeps its `match` key when it rewrites the file.
    """

    def __init__(
        self, path: Path | str, *, lock: threading.RLock | None = None
    ) -> None:
        self.path = Path(path)
        # Every write (and reload) holds this. The app passes the journal
        # write lock, so a rule write never interleaves with an account
        # rename rewriting this file; re-entrant, so a caller already holding
        # it (an import commit) is not blocked.
        self.lock = lock if lock is not None else threading.RLock()
        self._rules: list[dict[str, str]] = []
        self.reload()

    def reload(self) -> None:
        """Re-read the file, e.g. after an account rename rewrote it on disk."""
        with self.lock:
            rules: list[dict[str, str]] = []
            if self.path.exists():
                rules = yaml.safe_load(self.path.read_text(encoding="utf-8")) or []
            self._rules = rules

    def add(self, pattern: str, category: str, *, identity: bool = False) -> None:
        self.add_many([(pattern, category)], identity=[pattern] if identity else ())

    def add_many(
        self, pairs: list[tuple[str, str]], *, identity: Collection[str] = ()
    ) -> None:
        """Persist several pattern -> category decisions in ONE file write.

        A pattern listed in `identity` is written as an identity rule
        (`match: identity`); every other one as a substring rule. A rule
        replaces only an earlier rule of the same pattern AND kind.

        `add` delegates here, so both paths share exactly one implementation
        of the remove-then-append ordering below. The bulk backlog classify
        saves ~241 rules in a single request; one `write_text` per rule meant
        241 whole-file rewrites, and a failure partway left the rules file
        half-written AFTER the journal had already been rewritten.
        """
        # Remove-then-append, not replace-in-place: re-answering the same
        # merchant (which the backlog screen invites -- a merchant whose rows
        # carry no txn-id stays listed, so it gets clicked again) must not
        # grow the file with duplicate patterns, but `match` is later-wins, so
        # leaving the rule at its old index would DEMOTE the user's newest
        # decision below any later rule that also matches (re-classify AMAZON
        # as Household and "AMAZON PAY" keeps resolving to the older Work).
        # Appending last keeps the newest decision outranking everything.
        # `.get` because the file is user-owned: a hand-edited row with no
        # pattern key must not make this raise.
        #
        # Applied once per pair, in the order given: a pair later in `pairs`
        # outranks an earlier one for the same pattern, exactly as two
        # successive `add` calls did.
        #
        # The new list is written (atomically) before it replaces the
        # in-memory one, so a failed write leaves memory matching the file.
        if not pairs:
            return
        identities = {p.upper() for p in identity}
        with self.lock:
            rules = list(self._rules)
            for pattern, category in pairs:
                normalized = pattern.upper()
                kind = IDENTITY if normalized in identities else None
                rules = [
                    r
                    for r in rules
                    if not (r.get("pattern") == normalized and _kind(r) == kind)
                ]
                rule = {"pattern": normalized, "category": category}
                if kind is not None:
                    rule["match"] = kind
                rules.append(rule)
            write_text_atomic(self.path, yaml.safe_dump(rules, sort_keys=False))
            self._rules = rules

    def match(
        self, normalized_narration: str, identity: str | None = None
    ) -> str | None:
        """The category of the last rule that matches.

        An identity rule (`match: identity`) matches only a row whose
        `identity` is exactly its pattern, never as a substring. A rule with
        no `match` is a substring of the normalised narration, as always --
        even one whose pattern starts with "UPI:". A rule of an unknown kind
        never matches.
        """
        text = normalized_narration.upper()
        wanted = identity.upper() if identity else None
        result: str | None = None
        for rule in self._rules:  # later rules override earlier
            pattern = rule.get("pattern")
            if not pattern:
                continue
            kind = _kind(rule)
            if kind == IDENTITY:
                if pattern == wanted:
                    result = rule.get("category")
            elif kind is None and pattern in text:
                result = rule.get("category")
        return result

    def exact(self, pattern: str, *, identity: bool = False) -> str | None:
        """Category of the LAST rule whose pattern is exactly `pattern`
        (upper-cased) and whose kind is an identity rule (`identity`) or a
        substring rule.

        Unlike `match`, a rule that is only a substring of `pattern` does not
        count: this answers "which rule would `add_many` replace?". `.get`
        because the file is user-owned and a hand-edited row may lack keys.
        """
        normalized = pattern.upper()
        kind = IDENTITY if identity else None
        result: str | None = None
        for rule in self._rules:
            if rule.get("pattern") == normalized and _kind(rule) == kind:
                result = rule.get("category")
        return result
