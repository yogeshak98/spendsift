"""Upload routes that reach the journal write lock (through the review
store) run in the thread pool, never on the event loop, so an import commit
or rename holding the lock cannot freeze every other request. Synthetic
data only."""

from __future__ import annotations

import inspect
import io
import threading
from datetime import date

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.app.server.main import create_app

UPLOAD_ROUTES = {
    "/api/import/detect",
    "/api/import/unlock",
    "/api/import/classify",
    "/api/import/ledger/preview",
    "/api/agent/inspect",
}


def test_upload_routes_are_sync_so_they_run_in_the_thread_pool(tmp_path):
    app = create_app(storage_dir=tmp_path)
    endpoints = {
        r.path: r.endpoint
        for r in app.routes
        if isinstance(r, APIRoute) and r.path in UPLOAD_ROUTES
    }
    assert set(endpoints) == UPLOAD_ROUTES
    for path, endpoint in endpoints.items():
        assert not inspect.iscoroutinefunction(endpoint), path


TEMPLATE = {
    "fingerprint": "fp-synth",
    "institution": "Synth",
    "header_row_index": 0,
    "date_column": "Date",
    "date_format": "%Y-%m-%d",
    "narration_column": "Narration",
    "debit_column": "Debit",
    "credit_column": "Credit",
    "default_account": "Assets:Bank:Synth",
}
CSV = "Date,Narration,Debit,Credit\n2026-09-03,SYNTH SHOP,305.00,\n"


def test_detect_waiting_on_the_lock_does_not_block_other_requests(tmp_path):
    app = create_app(storage_dir=tmp_path, clock=lambda: date(2026, 10, 16))
    with TestClient(app) as client:  # one shared event loop
        client.post("/api/accounts", json={"accounts": ["Assets:Bank:Synth"]})
        first = client.post(
            "/api/import/detect",
            files={"file": ("a.csv", io.BytesIO(CSV.encode()), "text/csv")},
        ).json()
        template = {**TEMPLATE, "fingerprint": first["fingerprint"]}
        client.post("/api/templates", json={"filename": "a.csv", **template})

        detect_done = threading.Event()
        other_done = threading.Event()

        def detect():
            # a known layout: detect stages a batch, which takes the lock
            r = client.post(
                "/api/import/detect",
                files={"file": ("b.csv", io.BytesIO(CSV.encode()), "text/csv")},
            )
            assert r.json()["matched"] is True
            detect_done.set()

        def other():
            client.get("/api/accounts")
            other_done.set()

        with app.state.ledger.write_lock():
            t1 = threading.Thread(target=detect)
            t1.start()
            assert not detect_done.wait(0.5)  # waiting on the lock
            t2 = threading.Thread(target=other)
            t2.start()
            # the loop is free: another request completes meanwhile
            assert other_done.wait(10)
            assert not detect_done.is_set()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert detect_done.is_set()
