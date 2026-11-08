from fastapi.testclient import TestClient
from backend.app.server.main import create_app

LEDGER = """\
2024/01/05 Buy units
    Assets:Investment:MutualFund:Groww:AcmeELSS   100.00 AcmeELSS @ 80.00 INR
    Assets:Checking:HDFC                         -8000.00 INR
"""

STATEMENT = "Date,Narration,Debit,Credit\n01/05/2024,Coffee,120.00,\n"


def test_classify_routes_ledger_file(tmp_path):
    client = TestClient(create_app(storage_dir=tmp_path))
    res = client.post(
        "/api/import/classify",
        files={"file": ("acme.ledger", LEDGER.encode(), "text/plain")},
    ).json()
    assert res["kind"] == "ledger"
    assert "ledger" in res and "batch_id" in res["ledger"]


def test_classify_routes_statement_file(tmp_path):
    client = TestClient(create_app(storage_dir=tmp_path))
    res = client.post(
        "/api/import/classify",
        files={"file": ("hdfc.csv", STATEMENT.encode(), "text/csv")},
    ).json()
    assert res["kind"] == "statement"
    assert "statement" in res


def test_classify_force_statement_on_a_ledger(tmp_path):
    """C1: forcing a ledger file as a statement must surface a coded HTTP error,
    not a silent 200 with an embedded error body."""
    client = TestClient(create_app(storage_dir=tmp_path))
    resp = client.post(
        "/api/import/classify",
        files={"file": ("acme.ledger", LEDGER.encode(), "text/plain")},
        data={"force_kind": "statement"},
    )
    # The registered CodedError handler renders E1002 as 422.
    assert resp.status_code == 422
    body = resp.json()
    assert "code" in body
    assert body["code"].startswith("E")


def test_classify_force_ledger_on_a_statement(tmp_path):
    """Forcing force_kind='ledger' on a bank CSV runs the ledger preview path."""
    client = TestClient(create_app(storage_dir=tmp_path))
    resp = client.post(
        "/api/import/classify",
        files={"file": ("hdfc.csv", STATEMENT.encode(), "text/csv")},
        data={"force_kind": "ledger"},
    )
    # Even if preview fails (bad ledger content), the kind path is ledger.
    # For a minimal CSV that parse_ledger sees as empty, preview may raise —
    # we only assert the route enters the ledger branch (kind == "ledger") when
    # it succeeds, or that it does NOT return kind == "statement".
    # A zero-transaction preview still returns a valid preview dict with batch_id.
    body = resp.json()
    assert body.get("kind") == "ledger"


def test_classify_invalid_force_kind(tmp_path):
    """I3: an unrecognised force_kind value must be rejected with 422."""
    client = TestClient(create_app(storage_dir=tmp_path))
    resp = client.post(
        "/api/import/classify",
        files={"file": ("acme.ledger", LEDGER.encode(), "text/plain")},
        data={"force_kind": "xlsx"},
    )
    assert resp.status_code == 422
