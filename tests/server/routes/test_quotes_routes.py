from __future__ import annotations
from fastapi.testclient import TestClient
from backend.app.server.main import create_app


def _client(tmp_path):
    return TestClient(create_app(storage_dir=tmp_path / "storage"))


def test_quotes_random_returns_200_with_quote(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/quotes/random")
    assert resp.status_code == 200
    body = resp.json()
    assert "quote" in body
    assert isinstance(body["quote"], str)
    assert len(body["quote"]) > 0


def test_quotes_random_quote_is_from_seed_file(tmp_path):
    client = _client(tmp_path)
    # Collect 20 samples; at least one must be from the known seed.
    samples = {client.get("/api/quotes/random").json()["quote"] for _ in range(20)}
    assert any("Buffett" in s or "Franklin" in s or "Kiyosaki" in s for s in samples)
