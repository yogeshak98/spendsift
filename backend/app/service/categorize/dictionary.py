from __future__ import annotations

import yaml

from backend.app.persistence.seeds import DICTIONARY_SEED


class MerchantDictionary:
    """Shipped merchant-token -> category lookup. Substring match against normalized narration."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = {k.upper(): v for k, v in mapping.items()}

    @classmethod
    def from_seed(cls) -> "MerchantDictionary":
        return cls(yaml.safe_load(DICTIONARY_SEED.read_text(encoding="utf-8")) or {})

    def lookup(self, normalized_narration: str) -> str | None:
        text = normalized_narration.upper()
        for token, category in self.mapping.items():
            if token in text:
                return category
        return None
