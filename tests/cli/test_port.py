from __future__ import annotations

import json
from pathlib import Path

import httpx

from backend.app.cli.port import HttpIngestPort

_LEARNING = Path("tests/fixtures/agent/categorize/review-learning.json")


def test_learning_gets_the_review_learning_route():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json=json.loads(_LEARNING.read_text()))

    port = HttpIngestPort(
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    )
    out = port.learning("batch1")
    assert seen == [("GET", "/api/review/batch1/learning")]
    assert out["will_learn"][0]["pattern"] == "FICTIVEAI"
