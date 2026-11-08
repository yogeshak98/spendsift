"""The backend half of the tag-normalisation parity pair.

`tag-normalize-cases.json` is the shared oracle: this test replays every case
through `normalize_tag`, and the browser's `normalizeTag` replays the same file
(frontend/src/__tests__/fixtures/importReview/). `tag: null` means refused.
"""

from __future__ import annotations

import json

import pytest

from backend.app.core.paths import REPO_ROOT
from backend.app.models.domain.tags import InvalidTag, normalize_tag

_CASES = json.loads(
    (
        REPO_ROOT
        / "tests"
        / "fixtures"
        / "agent"
        / "categorize"
        / "tag-normalize-cases.json"
    ).read_text()
)["cases"]


def test_the_oracle_has_both_accepted_and_refused_cases():
    assert len(_CASES) >= 10
    assert any(c["tag"] is None for c in _CASES)
    assert any(c["tag"] is not None for c in _CASES)


@pytest.mark.parametrize("case", _CASES, ids=[repr(c["raw"]) for c in _CASES])
def test_normalize_tag_matches_the_shared_oracle(case):
    if case["tag"] is None:
        with pytest.raises(InvalidTag) as exc:
            normalize_tag(case["raw"])
        assert exc.value.raw == case["raw"]
    else:
        assert normalize_tag(case["raw"]) == case["tag"]
