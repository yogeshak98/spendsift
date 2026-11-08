from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.server.middleware.errors import register_error_handlers
from backend.app.core.errors.errors import UPLOAD_NOT_FOUND


def _app_with_probe_routes() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/raise-coded")
    def raise_coded() -> dict:
        raise UPLOAD_NOT_FOUND.exc({"filename": "hdfc.csv"})

    @app.get("/raise-http")
    def raise_http() -> dict:
        raise HTTPException(status_code=404, detail="plain not found")

    @app.get("/raise-boom")
    def raise_boom() -> dict:
        raise RuntimeError("internal detail that must not leak")

    return app


def test_coded_error_renders_full_envelope_with_detail_for_backcompat():
    client = TestClient(_app_with_probe_routes())
    resp = client.get("/raise-coded")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "E1004"
    assert "hdfc.csv" in body["description"]
    assert body["resolution"]
    assert body["resolvedByRefresh"] is False
    # Backward-compat: detail equals description so the existing FE keeps working.
    assert body["detail"] == body["description"]


def test_plain_httpexception_still_returns_detail_only_body():
    client = TestClient(_app_with_probe_routes())
    resp = client.get("/raise-http")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "plain not found"}


def test_unexpected_exception_returns_generic_e0000_envelope():
    # raise_server_exceptions=False so TestClient returns the 500 response
    # instead of re-raising the RuntimeError.
    client = TestClient(_app_with_probe_routes(), raise_server_exceptions=False)
    resp = client.get("/raise-boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "E0000"
    assert "internal detail that must not leak" not in json.dumps(body)
    assert body["detail"] == body["description"]
