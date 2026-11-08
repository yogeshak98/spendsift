from __future__ import annotations

import io
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.server.main import create_app

HDFC = Path("tests/fixtures/statements/hdfc_bank.csv").read_bytes()
DRAFT = {
    "institution": "HDFC",
    "header_row_index": 0,
    "date_column": "Date",
    "date_format": "%Y-%m-%d",
    "narration_column": "Narration",
    "debit_column": "Withdrawal Amt.",
    "credit_column": "Deposit Amt.",
    "default_account": "Assets:Checking:HDFC",
}


def _client(tmp_path):
    return TestClient(
        create_app(storage_dir=tmp_path / "s", clock=lambda: date(2026, 10, 16))
    )


def _inspect(client):
    return client.post(
        "/api/agent/inspect",
        files={"file": ("h.csv", io.BytesIO(HDFC), "text/csv")},
        data={"kind": "bank"},
    ).json()


def test_full_agent_round_trip(tmp_path):
    client = _client(tmp_path)
    run = _inspect(client)
    peek = client.get(f"/api/agent/peek/{run['prove_id']}").json()
    assert peek["head"][0]["row"] == 0
    window = client.get(
        f"/api/agent/peek/{run['prove_id']}", params={"rows": "1:1"}
    ).json()
    assert [r["row"] for r in window["head"]] == [1]
    dry = client.post(
        "/api/agent/dry-run",
        json={
            "prove_id": run["prove_id"],
            "draft": DRAFT,
            "claims": {"first_data_row": 1, "last_data_row": 2},
        },
    ).json()
    assert dry["report"]["passed"]
    win = client.get(
        f"/api/agent/dry-run/{run['prove_id']}", params={"rows": "2:2"}
    ).json()
    assert [c["row"] for c in win["candidates"]] == [2]
    staged = client.post(
        "/api/agent/stage", json={"prove_id": run["prove_id"], "use": "proof"}
    ).json()
    done = client.post(f"/api/agent/commit/{staged['batch_id']}")
    assert done.status_code == 200


def test_errors_render_as_coded_json(tmp_path):
    client = TestClient(create_app(storage_dir=tmp_path / "s"))
    resp = client.get("/api/agent/peek/nope")
    assert resp.status_code == 404
    assert "E1075" in resp.text
    bad = client.get("/api/agent/peek/nope", params={"rows": "x"})
    assert bad.status_code == 422


def test_bad_stop_pattern_is_a_failed_template_check_not_an_error(tmp_path):
    client = _client(tmp_path)
    run = _inspect(client)
    resp = client.post(
        "/api/agent/dry-run",
        json={
            "prove_id": run["prove_id"],
            "draft": {**DRAFT, "stop_pattern": "(["},
            "claims": {"first_data_row": 1, "last_data_row": 2},
        },
    )
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert report["passed"] is False
    assert any(
        c["name"] == "template" and c["status"] == "fail" for c in report["checks"]
    )


def test_service_is_exposed_on_app_state(tmp_path):
    app = create_app(storage_dir=tmp_path / "s")
    assert app.state.agent_service is not None
