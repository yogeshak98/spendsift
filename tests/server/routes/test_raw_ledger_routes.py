from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.server.main import create_app


def _client(tmp_path):
    return TestClient(create_app(storage_dir=tmp_path / "storage"))


def test_get_raw_returns_year_text_and_hash(tmp_path) -> None:
    client = _client(tmp_path)
    ledger = client.app.state.ledger
    body = "; year 2024\n"
    (ledger.journal_dir / "2024.beancount").write_text(body, encoding="utf-8")

    resp = client.get("/api/ledger/raw", params={"year": 2024})

    assert resp.status_code == 200
    j = resp.json()
    assert j["year"] == 2024
    assert j["text"] == body
    assert j["hash"] == ledger.read_year_text(2024)[1]


def test_put_raw_writes_and_returns_new_hash(tmp_path) -> None:
    client = _client(tmp_path)
    ledger = client.app.state.ledger
    _, h = ledger.read_year_text(2024)
    body = '2024-03-01 * "Coffee"\n    Expenses:Food   100 INR\n    Assets:Cash\n'

    resp = client.put(
        "/api/ledger/raw",
        json={"year": 2024, "text": body, "expected_hash": h},
    )

    assert resp.status_code == 200
    assert resp.json()["hash"] == ledger.read_year_text(2024)[1]
    assert ledger.read_year_text(2024)[0] == body


def test_put_raw_stale_hash_is_409(tmp_path) -> None:
    client = _client(tmp_path)
    ledger = client.app.state.ledger
    (ledger.journal_dir / "2024.beancount").write_text("; orig\n", encoding="utf-8")

    resp = client.put(
        "/api/ledger/raw",
        json={"year": 2024, "text": "; new\n", "expected_hash": "deadbeef"},
    )

    assert resp.status_code == 409
    assert ledger.read_year_text(2024)[0] == "; orig\n"


def test_put_raw_invalid_text_is_422_and_does_not_write(tmp_path) -> None:
    client = _client(tmp_path)
    ledger = client.app.state.ledger
    _, h = ledger.read_year_text(2024)
    bad = '2024-03-01 * "X"\n    Expenses:NotOpened   1 INR\n    Assets:AlsoNot\n'

    resp = client.put(
        "/api/ledger/raw",
        json={"year": 2024, "text": bad, "expected_hash": h},
    )

    assert resp.status_code == 422
    assert ledger.read_year_text(2024)[0] == ""
