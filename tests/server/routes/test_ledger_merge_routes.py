from fastapi.testclient import TestClient
from backend.app.server.main import create_app

LEDGER = """\
2024/01/05 Buy units
    Assets:Investment:MutualFund:Groww:AcmeELSS   742.61 AcmeELSS @ 83.48 INR  ; Acme ELSS
    Assets:Checking:HDFC                        -61993.69 INR
    Equity:Opening-Balances
"""


def test_preview_then_commit(tmp_path):
    client = TestClient(create_app(storage_dir=tmp_path))
    pv = client.post(
        "/api/import/ledger/preview",
        files={"file": ("acme.ledger", LEDGER.encode(), "text/plain")},
    )
    assert pv.status_code == 200
    body = pv.json()
    assert body["summary"]["transactions"] == 1

    commit = client.post(
        "/api/import/ledger/commit",
        json={
            "batch_id": body["batch_id"],
            "account_map": {a["raw"]: a["suggested"] for a in body["accounts"]},
            "commodity_map": {c["raw"]: c["suggested"] for c in body["commodities"]},
            "include_duplicates": False,
        },
    )
    assert commit.status_code == 200
    assert commit.json()["committed"] == 1


def test_incomplete_map_422(tmp_path):
    client = TestClient(create_app(storage_dir=tmp_path))
    body = client.post(
        "/api/import/ledger/preview",
        files={"file": ("acme.ledger", LEDGER.encode(), "text/plain")},
    ).json()
    res = client.post(
        "/api/import/ledger/commit",
        json={
            "batch_id": body["batch_id"],
            "account_map": {},
            "commodity_map": {},
            "include_duplicates": False,
        },
    )
    assert res.status_code == 422
    assert res.json()["code"] == "E1020"


UNBALANCED_LEDGER = """\
2024/01/05 Short txn
    Assets:Checking:HDFC   -100.00 INR
    Expenses:Misc            90.00 INR
"""


def test_commit_imbalance_returns_422_not_500(tmp_path):
    client = TestClient(create_app(storage_dir=tmp_path))
    body = client.post(
        "/api/import/ledger/preview",
        files={"file": ("acme.ledger", UNBALANCED_LEDGER.encode(), "text/plain")},
    ).json()
    assert body["summary"]["imbalances"] == 1
    assert body["imbalances"][0]["line_no"] == 1
    res = client.post(
        "/api/import/ledger/commit",
        json={
            "batch_id": body["batch_id"],
            "account_map": {a["raw"]: a["suggested"] for a in body["accounts"]},
            "commodity_map": {c["raw"]: c["suggested"] for c in body["commodities"]},
            "include_duplicates": False,
        },
    )
    assert res.status_code == 422
    assert res.json()["code"] == "E1023"
