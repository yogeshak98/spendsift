"""Conditional-GET client for the AMFI NAVAll dump.

The backend's first outbound network call: short timeout, best-effort, and
OFFLINE-safe — any transport error or timeout returns FetchStatus.OFFLINE
instead of raising. Tests inject an httpx.MockTransport, so no socket is opened.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import httpx


class FetchStatus(enum.Enum):
    OK = 200
    NOT_MODIFIED = 304
    OFFLINE = -1


@dataclass(frozen=True)
class FetchResult:
    status: FetchStatus
    body: str | None
    etag: str | None
    last_modified: str | None


def conditional_get(
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 8.0,
) -> FetchResult:
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        with httpx.Client(
            timeout=timeout, transport=transport, follow_redirects=True
        ) as client:
            resp = client.get(url, headers=headers)
    except httpx.HTTPError:
        return FetchResult(
            status=FetchStatus.OFFLINE, body=None, etag=None, last_modified=None
        )
    if resp.status_code == 304:
        return FetchResult(
            status=FetchStatus.NOT_MODIFIED,
            body=None,
            etag=resp.headers.get("ETag", etag),
            last_modified=resp.headers.get("Last-Modified", last_modified),
        )
    if resp.status_code == 200:
        return FetchResult(
            status=FetchStatus.OK,
            body=resp.text,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
        )
    # Any other status (5xx etc.) is treated as offline -> fall back to cache.
    return FetchResult(
        status=FetchStatus.OFFLINE, body=None, etag=None, last_modified=None
    )
