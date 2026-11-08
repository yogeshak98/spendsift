from __future__ import annotations

import httpx

from backend.app.clients.amfi import FetchStatus, conditional_get

_URL = "https://example.invalid/NAVAll.txt"
_BODY = "122639;;;Parag Parikh Flexi Cap;215.05;24-Jun-2026"


def test_200_returns_body_and_conditional_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_BODY,
            headers={"ETag": '"abc"', "Last-Modified": "Tue, 24 Jun 2026 18:30:00 GMT"},
        )

    res = conditional_get(_URL, transport=httpx.MockTransport(handler))
    assert res.status is FetchStatus.OK
    assert res.body == _BODY
    assert res.etag == '"abc"'
    assert res.last_modified == "Tue, 24 Jun 2026 18:30:00 GMT"


def test_sends_if_none_match_and_if_modified_since_when_known():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304)

    res = conditional_get(
        _URL,
        etag='"abc"',
        last_modified="Tue, 24 Jun 2026 18:30:00 GMT",
        transport=httpx.MockTransport(handler),
    )
    assert res.status is FetchStatus.NOT_MODIFIED
    assert res.body is None
    assert seen["if-none-match"] == '"abc"'
    assert seen["if-modified-since"] == "Tue, 24 Jun 2026 18:30:00 GMT"


def test_transport_error_maps_to_offline_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network", request=request)

    res = conditional_get(_URL, transport=httpx.MockTransport(handler))
    assert res.status is FetchStatus.OFFLINE
    assert res.body is None


def test_timeout_maps_to_offline():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    res = conditional_get(_URL, transport=httpx.MockTransport(handler))
    assert res.status is FetchStatus.OFFLINE
