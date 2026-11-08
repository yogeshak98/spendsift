"""Two concurrent commits of one batch write its rows once (review I-A).

ImportService.commit holds both journals' write lock from reading the batch
through clearing it, so a second commit of the same batch -- a double click, a
retried request -- waits, then finds the batch gone instead of writing again.
"""

from __future__ import annotations

import io
import threading

from beancount.core import data
from fastapi.testclient import TestClient

from backend.app.core.errors.errors import CodedError
from backend.app.server.main import create_app
from backend.app.service.ingest import service as service_mod


def _batch(client: TestClient) -> str:
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"01/04/2026,FICTIVEBIKES BANGALORE,1500.00,\n"
    )
    detected = client.post(
        "/api/import/detect",
        files={"file": ("hdfc.csv", io.BytesIO(csv), "text/csv")},
    ).json()
    created = client.post(
        "/api/templates",
        json={
            "filename": detected["filename"],
            "fingerprint": detected["fingerprint"],
            "institution": "HDFC",
            "header_row_index": detected["suggested_header_row"],
            "delimiter": detected["delimiter"],
            "date_column": detected["suggested_roles"]["date"],
            "date_format": detected["suggested_date_format"],
            "narration_column": detected["suggested_roles"]["narration"],
            "debit_column": detected["suggested_roles"]["debit"],
            "credit_column": detected["suggested_roles"]["credit"],
            "default_account": "Assets:Checking:HDFC",
            "save": True,
        },
    ).json()
    return created["batch_id"]


def test_concurrent_commits_of_one_batch_write_its_rows_once(tmp_path, monkeypatch):
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)
    batch_id = _batch(client)
    service = app.state.import_service

    first_inside = threading.Event()
    second_started = threading.Event()
    real_commit_candidates = service_mod.commit_candidates

    def slow_commit_candidates(*args, **kwargs):
        # Hold the first commit mid-write until the second has had its chance
        # to run (bounded: with the lock, the second is blocked meanwhile).
        if not first_inside.is_set():
            first_inside.set()
            second_started.wait(timeout=5)
            threading.Event().wait(0.5)
        return real_commit_candidates(*args, **kwargs)

    monkeypatch.setattr(service_mod, "commit_candidates", slow_commit_candidates)

    results: dict[str, object] = {}

    def run(name: str) -> None:
        if name == "second":
            first_inside.wait(timeout=5)
            second_started.set()
        try:
            results[name] = service.commit(batch_id)
        except CodedError as exc:
            results[name] = exc

    threads = [threading.Thread(target=run, args=(n,)) for n in ("first", "second")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert isinstance(results["first"], dict)
    assert results["first"]["committed"] == 1
    second = results["second"]
    assert isinstance(second, CodedError), second
    assert second.code == "E1006"  # the existing unknown/gone-batch error

    entries = app.state.ledger.load(fresh=True).entries
    rows = [
        e
        for e in entries
        if isinstance(e, data.Transaction) and "FICTIVEBIKES" in (e.narration or "")
    ]
    assert len(rows) == 1
