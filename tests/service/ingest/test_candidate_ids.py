"""A charge and its same-day reversal are two rows with two ids.

Synthetic card statement only: the same date, amount and narration, once as
a charge and once as its reversal, plus a true duplicate line (the same charge
printed twice), which only a per-statement ordinal can tell apart.
"""

from __future__ import annotations

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app.models.domain.candidate import DedupStatus
from backend.app.server.main import create_app

_CARD = "Liabilities:CreditCard:Test"
_STATEMENT = (
    "Date,Narration,Debit,Credit\n"
    "2026-09-03,IND*AMAZON.IN - GROCER,305.00,\n"
    "2026-09-03,IND*AMAZON.IN - GROCER,,305.00\n"
    "2026-09-04,CAFE COFFEE DAY,120.00,\n"
    "2026-09-04,CAFE COFFEE DAY,120.00,\n"
)


def _app(tmp_path):
    return create_app(
        storage_dir=tmp_path / "storage", clock=lambda: date(2026, 10, 16)
    )


def _stage(client) -> dict:
    client.post(
        "/api/import/detect",
        files={"file": ("card.csv", io.BytesIO(_STATEMENT.encode()), "text/csv")},
    )
    return client.post(
        "/api/templates",
        json={
            "filename": "card.csv",
            "fingerprint": "fp-card",
            "institution": "TestCard",
            "header_row_index": 0,
            "date_column": "Date",
            "date_format": "%Y-%m-%d",
            "narration_column": "Narration",
            "debit_column": "Debit",
            "credit_column": "Credit",
            "default_account": _CARD,
        },
    ).json()


def test_charge_and_reversal_get_distinct_ids_and_each_is_editable(tmp_path):
    client = TestClient(_app(tmp_path))
    staged = _stage(client)
    cands = staged["candidates"]
    assert len(cands) == 4
    ids = [c["id"] for c in cands]
    assert len(set(ids)) == 4, "every row needs its own id"

    charge, reversal = cands[0], cands[1]
    assert (charge["direction"], reversal["direction"]) == ("debit", "credit")
    batch = staged["batch_id"]
    for cid, category in (
        (charge["id"], "Expenses:Food:Groceries"),
        (reversal["id"], "Expenses:Shopping"),
    ):
        resp = client.post(
            f"/api/review/{batch}/{cid}", json={"suggested_category": category}
        )
        assert resp.status_code == 200, resp.text
    rows = {c["id"]: c for c in client.get(f"/api/review/{batch}").json()["candidates"]}
    assert rows[charge["id"]]["suggested_category"] == "Expenses:Food:Groceries"
    assert rows[reversal["id"]]["suggested_category"] == "Expenses:Shopping"


def test_ids_are_the_same_when_the_statement_is_staged_again(tmp_path):
    client = TestClient(_app(tmp_path))
    first = [c["id"] for c in _stage(client)["candidates"]]
    second = [c["id"] for c in _stage(client)["candidates"]]
    assert first == second


def test_a_reimport_of_the_statement_is_all_exact_duplicates(tmp_path):
    client = TestClient(_app(tmp_path))
    staged = _stage(client)
    batch = staged["batch_id"]
    for c in staged["candidates"]:
        client.post(
            f"/api/review/{batch}/{c['id']}",
            json={"suggested_category": "Expenses:Shopping"},
        )
    resp = client.post(f"/api/commit/{batch}")
    assert resp.status_code == 200, resp.text

    again = _stage(client)["candidates"]
    assert [c["dedup_status"] for c in again] == [DedupStatus.EXACT_DUP.value] * 4


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "f" * 32},
        {"matched_txn_id": "abc"},
        {"order_id": "ORD-9"},
        {"suggested_category": "Expenses:Food", "id": "f" * 32},
    ],
)
def test_an_edit_cannot_change_a_rows_identity(tmp_path, patch):
    client = TestClient(_app(tmp_path))
    staged = _stage(client)
    batch, row = staged["batch_id"], staged["candidates"][0]
    resp = client.post(f"/api/review/{batch}/{row['id']}", json=patch)
    assert resp.status_code == 422, resp.text
    assert "E1037" in resp.text
    after = client.get(f"/api/review/{batch}").json()["candidates"]
    assert after == staged["candidates"]  # a refused edit changes nothing


def test_an_edit_may_clear_the_matched_entry(tmp_path):
    client = TestClient(_app(tmp_path))
    staged = _stage(client)
    batch, row = staged["batch_id"], staged["candidates"][0]
    resp = client.post(
        f"/api/review/{batch}/{row['id']}", json={"matched_txn_id": None}
    )
    assert resp.status_code == 200, resp.text
