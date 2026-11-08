from __future__ import annotations

import pathlib
import tempfile
from datetime import date

from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from tests.conftest import make_txn


def _client() -> TestClient:
    return TestClient(create_app(storage_dir=pathlib.Path(tempfile.mkdtemp())))


def test_cash_balance_zero_on_empty_journal():
    client = _client()
    r = client.get("/api/import/investment/cash-balance")
    assert r.status_code == 200
    body = r.json()
    assert body["account"] == "Assets:Broker:Groww"
    assert body["currency"] == "INR"
    # Empty journal → settled Groww:Cash → "0" (Decimal-string).
    assert body["balance"] in {"0", "0.00"}


def test_cash_balance_reflects_committed_funding():
    client = _client()
    # Seed a funding posting into Groww:Cash via the ledger directly, the same
    # account the import/commit path writes.
    ledger = client.app.state.ledger

    # Open the two accounts involved in the funding transaction (they are not
    # in SEED_ACCOUNTS so beancount validation would reject the posting otherwise).
    ledger.open_named_accounts(
        [
            "Assets:Bank:HDFC",
            "Assets:Broker:Groww",
        ]
    )

    txn = make_txn(
        date(2024, 1, 5),
        "Add money",
        "Assets:Broker:Groww",
        "Assets:Bank:HDFC",
        "5000",
    )
    ledger.append([txn])

    r = client.get("/api/import/investment/cash-balance")
    assert r.status_code == 200
    assert r.json()["balance"] in {"5000", "5000.00"}
