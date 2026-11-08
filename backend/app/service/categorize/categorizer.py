from __future__ import annotations

from collections.abc import Callable, Collection

from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.service.categorize.patterns import upi_identity
from backend.app.service.categorize.review_flags import UNCATEGORIZED_FLAG
from backend.app.persistence.account_aliases import AccountAliases
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.models.domain.candidate import CandidateTxn, CategorySource


_SOURCE: dict[str, CategorySource] = {
    "learned": "rule",
    "dictionary": "dictionary",
    "unknown": "none",
}


_APP_OWNED_ELSEWHERE = ("user", "taught_batch", "fee_follows")


class Categorizer:
    """Learned rules first (user corrections), then the shipped dictionary, else unknown."""

    def __init__(
        self,
        dictionary: MerchantDictionary,
        rules: LearnedRules,
        aliases: AccountAliases | None = None,
        open_accounts: Callable[[], Collection[str]] | None = None,
    ) -> None:
        self.dictionary = dictionary
        self.rules = rules
        # Renamed accounts (old -> new). Only the shipped dictionary's
        # suggestions are resolved through them, and only to an account that
        # is open (`open_accounts`): learned rules are rewritten by the rename
        # itself, and a rule written later means what it says.
        self.aliases = aliases
        self._open_accounts = open_accounts

    def categorize(
        self, normalized_narration: str, raw_narration: str | None = None
    ) -> tuple[str | None, float, str]:
        """`raw_narration`, when given, lets a UPI payee rule match the row."""
        identity = upi_identity(raw_narration) if raw_narration else None
        learned = self.rules.match(normalized_narration, identity)
        if learned:
            return learned, 1.0, "learned"
        shipped = self.dictionary.lookup(normalized_narration)
        if shipped:
            return self._resolve(shipped), 0.9, "dictionary"
        return None, 0.0, "unknown"

    def _resolve(self, account: str) -> str:
        if self.aliases is None or self._open_accounts is None:
            return account
        return self.aliases.resolve(account, self._open_accounts)

    def apply_to_candidates(self, candidates: list[CandidateTxn]) -> list[CandidateTxn]:
        for c in candidates:
            # do not overwrite a transfer's proposed other leg
            if c.suggested_other_leg:
                continue
            # only rows the rules or dictionary own are refreshed; a user,
            # batch-taught or fee-linked category is never overridden
            if c.category_source in _APP_OWNED_ELSEWHERE:
                continue
            category, confidence, source = self.categorize(
                c.normalized_narration, c.raw_narration
            )
            c.suggested_category = category
            c.category_source = _SOURCE[source]
            if category is None:
                if UNCATEGORIZED_FLAG not in c.flags:
                    c.flags.append(UNCATEGORIZED_FLAG)
            else:
                if UNCATEGORIZED_FLAG in c.flags:
                    c.flags.remove(UNCATEGORIZED_FLAG)
                c.confidence = min(c.confidence, confidence)
        return candidates
