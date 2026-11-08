"""Text the frontend must match verbatim because it compares by equality."""

from __future__ import annotations

import re

from backend.app.core.paths import REPO_ROOT
from backend.app.service.ledger.openings.keys import MF_OPENING_FLAG


# A declaration at the start of a line, so a commented-out copy (`// export
# const ...`) never matches; block comments are removed first.
_DECLARATION = re.compile(
    r'^export const MF_OPENING_FLAG\s*=\s*"((?:[^"\\\n]|\\.)*)";', re.MULTILINE
)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _declared(source: str) -> list[str]:
    return _DECLARATION.findall(_BLOCK_COMMENT.sub("", source))


def test_frontend_mf_opening_flag_matches_backend():
    source = (REPO_ROOT / "frontend" / "src" / "openings.ts").read_text()
    found = _declared(source)
    assert found, "frontend/src/openings.ts no longer exports MF_OPENING_FLAG"
    assert found == [MF_OPENING_FLAG]


def test_declaration_match_ignores_comments():
    live = 'export const MF_OPENING_FLAG =\n  "live";\n'
    assert _declared(live) == ["live"]
    assert _declared('// export const MF_OPENING_FLAG = "old";\n' + live) == ["live"]
    assert _declared('/*\nexport const MF_OPENING_FLAG = "old";\n*/\n' + live) == [
        "live"
    ]
    assert _declared('// export const MF_OPENING_FLAG = "old";\n') == []
