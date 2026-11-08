import io
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.ingest.templates.fingerprint import fingerprint_headers
from backend.app.models.storage.template import Template


def _client(tmp_path):
    return TestClient(create_app(storage_dir=tmp_path / "storage"))


def _commit_one(client, tmp_path):
    headers = ["Date", "Narration", "Withdrawal Amt", "Deposit Amt"]
    client.app.state.template_store.save(
        Template(
            fingerprint=fingerprint_headers(headers),
            institution="HDFC",
            header_row_index=0,
            date_column="Date",
            date_format="%d/%m/%Y",
            narration_column="Narration",
            debit_column="Withdrawal Amt",
            credit_column="Deposit Amt",
            default_account="Assets:Bank:HDFC",
        )
    )
    csv = "Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    detect = client.post(
        "/api/import/detect",
        files={"file": ("hdfc.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    batch_id = detect["batch_id"]
    cid = detect["candidates"][0]["id"]
    client.post(
        f"/api/review/{batch_id}/{cid}", json={"suggested_category": "Expenses:Food"}
    )
    client.post(f"/api/commit/{batch_id}")
    return cid


def test_list_accounts_includes_seed_accounts(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    assert "Assets:Checking:HDFC" in resp.json()["accounts"]


def test_list_transactions_returns_committed_entry(tmp_path):
    client = _client(tmp_path)
    txn_id = _commit_one(client, tmp_path)

    resp = client.get("/api/transactions")
    assert resp.status_code == 200
    rows = resp.json()["transactions"]
    assert len(rows) == 1
    assert rows[0]["txn_id"] == txn_id
    accounts = {p["account"] for p in rows[0]["postings"]}
    assert accounts == {"Expenses:Food", "Assets:Bank:HDFC"}


def test_list_transactions_shows_vehicle_leg_for_mf_purchase(tmp_path):
    # Post the investment-ledger detach split (see 2026-09-05 plan Unit 3): the
    # per-scheme commodity lot now lives in investments.beancount, so
    # /api/transactions (main.beancount only) shows the coarse vehicle-level
    # leg (Assets:Investment:MutualFund) with no per-scheme commodity code to
    # resolve — the "MF122639" resolution scenario now applies to the
    # investment ledger's own view (Unit 5), not this main-ledger endpoint.
    client = _client(tmp_path)
    commit_candidates(
        client.app.state.ledger,
        client.app.state.investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 18),
                amount=Decimal("21500.00"),
                raw_narration="Purchase — Parag Parikh Flexi Cap",
                normalized_narration="Parag Parikh Flexi Cap",
                source_account="Assets:Investment:MutualFund:Groww:Cash",
                direction=Direction.DEBIT,
                units=Decimal("100.0"),
                commodity="MF122639",
                unit_cost=Decimal("215.0"),
            )
        ],
    )

    resp = client.get("/api/transactions")
    assert resp.status_code == 200
    rows = resp.json()["transactions"]
    postings = rows[0]["postings"]
    holding = next(
        p for p in postings if p["account"] == "Assets:Investment:MutualFund"
    )
    # No per-scheme commodity suffix on the vehicle account -> no resolution needed.
    assert holding["account_display"] == "Assets:Investment:MutualFund"


def test_get_transaction_detail_includes_year_file_hash(tmp_path):
    client = _client(tmp_path)
    txn_id = _commit_one(client, tmp_path)

    resp = client.get(f"/api/transactions/{txn_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["txn_id"] == txn_id
    assert len(body["year_file_hash"]) == 64  # sha256 hex digest length


def test_get_transaction_detail_404_for_unknown_id(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/transactions/does-not-exist")
    assert resp.status_code == 404


def test_annotate_posting_happy_path(tmp_path):
    client = _client(tmp_path)
    txn_id = _commit_one(client, tmp_path)
    detail = client.get(f"/api/transactions/{txn_id}").json()

    resp = client.post(
        f"/api/transactions/{txn_id}/postings/Expenses:Food/annotate",
        json={"units": "1.0", "expected_hash": detail["year_file_hash"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    food_posting = next(p for p in body["postings"] if p["account"] == "Expenses:Food")
    assert food_posting["meta"]["units"] == "1.0"


def test_annotate_posting_requires_expected_hash(tmp_path):
    client = _client(tmp_path)
    txn_id = _commit_one(client, tmp_path)
    resp = client.post(
        f"/api/transactions/{txn_id}/postings/Expenses:Food/annotate", json={}
    )
    assert resp.status_code == 422


def test_annotate_posting_404_for_unknown_txn(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/api/transactions/does-not-exist/postings/Expenses:Food/annotate",
        json={"expected_hash": "x"},
    )
    assert resp.status_code == 404


def test_annotate_posting_409_on_stale_hash(tmp_path):
    client = _client(tmp_path)
    txn_id = _commit_one(client, tmp_path)
    resp = client.post(
        f"/api/transactions/{txn_id}/postings/Expenses:Food/annotate",
        json={"units": "1.0", "expected_hash": "stale"},
    )
    assert resp.status_code == 409
