"""Transport between an agent runtime and the app (phase 1: HTTP)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

import httpx

DEFAULT_URL = "http://127.0.0.1:8765"


class AppUnreachable(Exception):
    pass


class AppTimeout(AppUnreachable):
    """The app is reachable but did not answer in time."""


class ApiError(Exception):
    def __init__(self, status: int, body) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


class IngestPort(Protocol):
    def inspect(self, path: Path, kind: str | None, upgrade: str | None) -> dict:
        ...

    def peek(self, prove_id: str, rows: str | None) -> dict:
        ...

    def dry_run(self, prove_id: str, draft: dict, claims: dict) -> dict:
        ...

    def pdf_dry_run(self, prove_id: str, draft: dict) -> dict:
        ...

    def dry_run_window(self, prove_id: str, rows: str) -> dict:
        ...

    def stage(self, prove_id: str, use: str) -> dict:
        ...

    def review(self, batch_id: str) -> dict:
        ...

    def reject(self, batch_id: str, candidate_id: str) -> dict:
        ...

    def edit(self, batch_id: str, candidate_id: str, patch: dict) -> dict:
        ...

    def learning(self, batch_id: str) -> dict:
        ...

    def accounts(self) -> dict:
        ...

    def resolve_marker(
        self, fingerprint: str, batch_id: str, marker_key: str | None, account: str
    ) -> dict:
        ...

    def commit(self, batch_id: str) -> dict:
        ...

    def opening_prompt(self, batch_id: str) -> dict:
        ...

    def openings(self) -> dict:
        ...

    def commit_opening(self, body: dict) -> dict:
        ...

    def correct_opening(self, body: dict) -> dict:
        ...

    def mark_opening_zero(self, account: str, zero: bool) -> dict:
        ...


class HttpIngestPort:
    def __init__(
        self, base_url: str | None = None, client: httpx.Client | None = None
    ) -> None:
        self._client = client or httpx.Client(
            base_url=base_url or os.environ.get("SPENDSIFT_AGENT_URL", DEFAULT_URL),
            timeout=120,
        )

    @property
    def base_url(self) -> str:
        return str(self._client.base_url).rstrip("/")

    def _call(self, method: str, url: str, **kw: Any) -> Any:
        try:
            resp = self._client.request(method, url, **kw)
        except httpx.TimeoutException as exc:
            raise AppTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise AppUnreachable(str(exc)) from exc
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, body)
        return body

    def inspect(self, path, kind, upgrade):
        # source_path: the path as given, so the app can word a working
        # unlock command for a locked PDF. Only the basename is uploaded.
        fields = (("kind", kind), ("upgrade", upgrade), ("source_path", str(path)))
        data = {k: v for k, v in fields if v}
        with Path(path).open("rb") as fh:
            return self._call(
                "POST",
                "/api/agent/inspect",
                files={"file": (Path(path).name, fh)},
                data=data,
            )

    def peek(self, prove_id, rows):
        return self._call(
            "GET",
            f"/api/agent/peek/{prove_id}",
            params={"rows": rows} if rows else None,
        )

    def dry_run(self, prove_id, draft, claims):
        return self._call(
            "POST",
            "/api/agent/dry-run",
            json={"prove_id": prove_id, "draft": draft, "claims": claims},
        )

    def pdf_dry_run(self, prove_id, draft):
        return self._call(
            "POST",
            "/api/agent/pdf/dry-run",
            json={"prove_id": prove_id, "draft": draft},
        )

    def dry_run_window(self, prove_id, rows):
        return self._call(
            "GET", f"/api/agent/dry-run/{prove_id}", params={"rows": rows}
        )

    def stage(self, prove_id, use):
        return self._call(
            "POST", "/api/agent/stage", json={"prove_id": prove_id, "use": use}
        )

    def review(self, batch_id):
        return self._call("GET", f"/api/review/{batch_id}")

    def reject(self, batch_id, candidate_id):
        return self._call("DELETE", f"/api/review/{batch_id}/{candidate_id}")

    def edit(self, batch_id, candidate_id, patch):
        return self._call("POST", f"/api/review/{batch_id}/{candidate_id}", json=patch)

    def learning(self, batch_id):
        return self._call("GET", f"/api/review/{batch_id}/learning")

    def accounts(self):
        return self._call("GET", "/api/accounts")

    def resolve_marker(self, fingerprint, batch_id, marker_key, account):
        return self._call(
            "POST",
            f"/api/templates/{fingerprint}/resolve-marker",
            json={"batch_id": batch_id, "marker_key": marker_key, "account": account},
        )

    def commit(self, batch_id):
        return self._call("POST", f"/api/agent/commit/{batch_id}")

    def opening_prompt(self, batch_id):
        return self._call("GET", f"/api/review/{batch_id}/opening")

    def openings(self):
        return self._call("GET", "/api/openings")

    def commit_opening(self, body):
        return self._call("POST", "/api/openings", json=body)

    def correct_opening(self, body):
        return self._call("PUT", "/api/openings", json=body)

    def mark_opening_zero(self, account, zero):
        return self._call(
            "POST", "/api/openings/zero", json={"account": account, "zero": zero}
        )
