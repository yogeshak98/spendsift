from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from backend.app.server.main import create_app


def _offline_transport():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no net", request=req)

    return httpx.MockTransport(handler)


def _client(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    # Inject mock transports into both wired services so no socket opens.
    app.state.price_service._transport = _offline_transport()
    app.state.equity_price_service._transport = _offline_transport()
    return TestClient(app)


def test_equity_refresh_offline_is_200_not_500(tmp_path):
    resp = _client(tmp_path).post("/api/prices/equity/refresh")
    assert resp.status_code == 200
    assert resp.json()["source"] == "offline"


def test_equity_status_reports_never_on_a_fresh_store(tmp_path):
    resp = _client(tmp_path).get("/api/prices/equity/status")
    assert resp.status_code == 200
    assert resp.json()["source"] == "never"


def test_freshness_lists_both_feeds(tmp_path):
    resp = _client(tmp_path).get("/api/prices/freshness")
    assert resp.status_code == 200
    keys = [s["key"] for s in resp.json()["sources"]]
    assert keys == ["mutual_fund", "equity"]


def test_freshness_reports_counts_and_state_per_source(tmp_path):
    body = _client(tmp_path).get("/api/prices/freshness").json()
    equity = next(s for s in body["sources"] if s["key"] == "equity")
    assert equity["label"] == "NSE bhavcopy"
    assert equity["source"] == "never"
    assert equity["priced_count"] == 0
    assert equity["unpriced_count"] == 0
    assert equity["as_of"] is None


def test_app_state_wires_equity_nav_store(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    assert app.state.equity_nav_store is app.state.equity_price_service._nav_store
