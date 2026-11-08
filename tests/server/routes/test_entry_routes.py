from __future__ import annotations
from fastapi.testclient import TestClient
from backend.app.server.main import create_app


def _client(tmp_path):
    return TestClient(create_app(storage_dir=tmp_path / "storage"))


def test_post_entry_commits_balanced_transaction(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/api/entries",
        json={
            "date": "2026-06-30",
            "narration": "Swiggy dinner",
            "postings": [
                {"account": "Expenses:Food", "amount": "412"},
                {"account": "Assets:Bank:HDFC", "amount": "-412"},
            ],
            "tags": [],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "txn_id" in body
    assert isinstance(body["txn_id"], str)


def test_post_entry_appears_in_transactions(tmp_path):
    client = _client(tmp_path)
    client.post(
        "/api/entries",
        json={
            "date": "2026-06-30",
            "narration": "Chai",
            "postings": [
                {"account": "Expenses:Food", "amount": "20"},
                {"account": "Assets:Bank:HDFC", "amount": "-20"},
            ],
            "tags": [],
        },
    )
    resp = client.get("/api/transactions")
    narrations = [t["narration"] for t in resp.json()["transactions"]]
    assert "Chai" in narrations


def test_post_entry_rejects_imbalanced(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/api/entries",
        json={
            "date": "2026-06-30",
            "narration": "Bad entry",
            "postings": [
                {"account": "Expenses:Food", "amount": "100"},
                {"account": "Assets:Bank:HDFC", "amount": "-50"},  # doesn't balance
            ],
            "tags": [],
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "E1023"  # LEDGER_IMBALANCE


def test_post_entry_with_tags(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/api/entries",
        json={
            "date": "2026-06-30",
            "narration": "Tagged spend",
            "postings": [
                {"account": "Expenses:Misc", "amount": "500"},
                {"account": "Assets:Bank:HDFC", "amount": "-500"},
            ],
            "tags": ["#fy2026-27"],
        },
    )
    assert resp.status_code == 200


def test_post_entry_missing_narration_returns_422(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/api/entries",
        json={
            "date": "2026-06-30",
            "narration": "",
            "postings": [
                {"account": "Expenses:Food", "amount": "100"},
                {"account": "Assets:Bank:HDFC", "amount": "-100"},
            ],
        },
    )
    assert resp.status_code == 422


def test_post_entry_fewer_than_two_postings_returns_422(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/api/entries",
        json={
            "date": "2026-06-30",
            "narration": "One-legged",
            "postings": [{"account": "Expenses:Food", "amount": "100"}],
        },
    )
    assert resp.status_code == 422
