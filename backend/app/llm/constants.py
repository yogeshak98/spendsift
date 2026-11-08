"""Where the copy-paste prompts live, named once.

Every other module reads a prompt through these names; none spells a prompt
path itself (enforced by ``tests/llm/test_prompt_paths.py``).
"""

from __future__ import annotations

from pathlib import Path

# The shipped defaults, package-relative so they resolve wherever the package
# is installed.
DEFAULT_PROMPTS_DIR = Path(__file__).parent / "prompts"
# The user-owned copies, under the data dir. Seeded once, then never overwritten.
USER_PROMPTS_SUBDIR = "prompts"
CATEGORIZE_PROMPT = "categorize.md"
CATEGORIZE_MERCHANTS_PROMPT = "categorize-merchants.md"
