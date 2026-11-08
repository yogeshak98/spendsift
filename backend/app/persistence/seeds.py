"""Shipped seed data files, named in one place.

The files live in `seed_data/` next to this module; consumers import these
constants instead of computing paths from their own location.
"""

from __future__ import annotations

from pathlib import Path

SEEDS_DIR = Path(__file__).parent / "seed_data"
DICTIONARY_SEED = SEEDS_DIR / "dictionary.seed.yaml"
LEARN_WORDS = SEEDS_DIR / "learn_words.yaml"
QUOTES = SEEDS_DIR / "quotes.txt"
