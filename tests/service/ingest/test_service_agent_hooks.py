from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from backend.app.core.errors.errors import CodedError
from backend.app.models.domain.candidate import DedupStatus

FIX = Path("tests/fixtures/statements")


def _app(tmp_path):
    return create_app(
        storage_dir=tmp_path / "storage", clock=lambda: date(2026, 10, 16)
    )


def test_scan_dedicated_matches_detect_for_groww_files(tmp_path, monkeypatch, request):
    monkeypatch.setenv(
        "SPENDSIFT_SCHEME_MASTER_PATH",
        str((FIX.parent / "scheme_master.csv").resolve()),
    )
    from backend.app.core import settings

    settings.get_settings.cache_clear()
    request.addfinalizer(settings.get_settings.cache_clear)
    app = _app(tmp_path)
    client = TestClient(app)
    svc = app.state.import_service
    for name in ("groww_orders.csv", "groww_stocks.csv"):
        raw = (FIX / name).read_bytes()
        via_detect = client.post(
            "/api/import/detect", files={"file": (name, io.BytesIO(raw), "text/csv")}
        ).json()
        via_scan = svc.scan_dedicated(tmp_path / "storage" / "imports" / name)
        assert via_scan is not None
        assert via_scan["candidates"] == via_detect["candidates"]


def test_scan_dedicated_is_none_for_a_bank_file(tmp_path):
    app = _app(tmp_path)
    path = tmp_path / "hdfc.csv"
    path.write_bytes((FIX / "hdfc_bank.csv").read_bytes())
    assert app.state.import_service.scan_dedicated(path) is None


def _stage_hdfc(client, tmp_path):
    raw = (FIX / "hdfc_bank.csv").read_bytes()
    client.post(
        "/api/import/detect", files={"file": ("h.csv", io.BytesIO(raw), "text/csv")}
    )
    return client.post(
        "/api/templates",
        json={
            "filename": "h.csv",
            "fingerprint": "fp-h",
            "institution": "HDFC",
            "header_row_index": 0,
            "date_column": "Date",
            "date_format": "%Y-%m-%d",
            "narration_column": "Narration",
            "debit_column": "Withdrawal Amt.",
            "credit_column": "Deposit Amt.",
            "default_account": "Assets:Checking:HDFC",
        },
    ).json()


def test_recheck_dedup_reports_and_writes_back_new_duplicates(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    first = _stage_hdfc(client, tmp_path)
    second = _stage_hdfc(client, tmp_path)  # same rows, staged before the first commits
    client.post(f"/api/commit/{first['batch_id']}")
    svc = app.state.import_service
    changes = svc.recheck_dedup(second["batch_id"])
    assert {c["after"] for c in changes} == {"exact_dup"}
    assert len(changes) == 2
    assert svc.recheck_dedup(second["batch_id"]) == []  # written back


def test_recheck_dedup_unknown_batch(tmp_path):
    with pytest.raises(CodedError) as err:
        _app(tmp_path).state.import_service.recheck_dedup("nope")
    assert err.value.code == "E1006"


def test_reject_drops_a_candidate(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    staged = _stage_hdfc(client, tmp_path)
    cid = staged["candidates"][0]["id"]
    resp = client.delete(f"/api/review/{staged['batch_id']}/{cid}")
    assert resp.json() == {"ok": True, "dropped": 1}
    left = client.get(f"/api/review/{staged['batch_id']}").json()["candidates"]
    assert [c["id"] for c in left] == [staged["candidates"][1]["id"]]
    assert client.delete(f"/api/review/{staged['batch_id']}/{cid}").status_code == 404
    assert client.delete(f"/api/review/nope/{cid}").status_code == 404


def test_recheck_dedup_marks_stock_orders_already_committed(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    raw = (FIX / "groww_stocks.csv").read_bytes()

    def stage():
        return client.post(
            "/api/import/detect",
            files={"file": ("groww_stocks.csv", io.BytesIO(raw), "text/csv")},
        ).json()

    first, second = stage(), stage()
    assert first["candidates"], "fixture must yield stock orders"
    svc = app.state.import_service
    for cand in svc._review_store.get_batch(first["batch_id"]):
        if cand.unresolved_scheme:  # new-symbol hold, acknowledged as the UI would
            svc._review_store.update(
                first["batch_id"], cand.id, {"unresolved_scheme": None}
            )
    assert client.post(f"/api/commit/{first['batch_id']}").status_code == 200
    changes = svc.recheck_dedup(second["batch_id"])
    assert len(changes) == len(second["candidates"])
    assert {c["after"] for c in changes} == {"exact_dup"}
    assert svc.recheck_dedup(second["batch_id"]) == []


def test_recheck_dedup_leaves_an_unchanged_transfer_row_alone(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    staged = _stage_hdfc(client, tmp_path)
    svc = app.state.import_service
    rows = app.state.import_service._review_store.get_batch(staged["batch_id"])
    transfer = next(c for c in rows if c.dedup_status == DedupStatus.TRANSFER_MATCH)
    flags_before = list(transfer.flags)
    assert svc.recheck_dedup(staged["batch_id"]) == []
    assert transfer.flags == flags_before


def test_recheck_dedup_flags_are_idempotent_and_never_stale(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    first = _stage_hdfc(client, tmp_path)
    second = _stage_hdfc(client, tmp_path)
    client.post(f"/api/commit/{first['batch_id']}")
    svc = app.state.import_service
    svc.recheck_dedup(second["batch_id"])
    for c in app.state.import_service._review_store.get_batch(second["batch_id"]):
        assert c.flags == ["exact duplicate of an existing transaction"]

    # A row marked EXACT_DUP whose journal match is absent (a fresh, uncommitted
    # batch) reverts, and its stale "exact duplicate" flag goes with it.
    third_app = _app(tmp_path / "other")
    third_client = TestClient(third_app)
    staged = _stage_hdfc(third_client, tmp_path)
    store = third_app.state.import_service._review_store
    row = store.get_batch(staged["batch_id"])[0]
    store.update(
        staged["batch_id"],
        row.id,
        {
            "dedup_status": DedupStatus.EXACT_DUP,
            "flags": [*row.flags, "exact duplicate of an existing transaction"],
        },
    )
    changes = third_app.state.import_service.recheck_dedup(staged["batch_id"])
    assert [c["after"] for c in changes] == ["new"]
    assert not any(
        "exact duplicate" in f for f in store.get_batch(staged["batch_id"])[0].flags
    )


_STOPPED = (
    b"Date,Narration,Withdrawal Amt.,Deposit Amt.\n"
    b"2026-03-05,SWIGGY BANGALORE,420.50,\n"
    b"Total,,420.50,\n"
    b"2026-03-07,LATE ROW AFTER TOTAL,99.00,\n"
    b"Skip me,,,\n"
)


def _save_hdfc_template(app, **extra):
    from backend.app.service.ingest.templates.fingerprint import fingerprint_headers
    from backend.app.models.storage.template import Template

    fp = fingerprint_headers(["Date", "Narration", "Withdrawal Amt.", "Deposit Amt."])
    app.state.template_store.save(
        Template(
            fingerprint=fp,
            institution="HDFC",
            header_row_index=0,
            date_column="Date",
            date_format="%Y-%m-%d",
            narration_column="Narration",
            debit_column="Withdrawal Amt.",
            credit_column="Deposit Amt.",
            default_account="Assets:Checking:HDFC",
            **extra,
        )
    )


def _detect(client, raw, name="s.csv"):
    return client.post(
        "/api/import/detect", files={"file": (name, io.BytesIO(raw), "text/csv")}
    ).json()


def test_detect_keeps_rows_after_the_stop_row_as_flagged_candidates(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    _save_hdfc_template(app, stop_pattern="^Total", origin="agent")
    out = _detect(client, _STOPPED)
    assert out["matched"] is True
    by_narration = {c["raw_narration"]: c for c in out["candidates"]}
    assert set(by_narration) == {"SWIGGY BANGALORE", "LATE ROW AFTER TOTAL"}
    late = by_narration["LATE ROW AFTER TOTAL"]
    assert "after the template's stop row — check this row" in late["flags"]
    assert not any("stop row" in f for f in by_narration["SWIGGY BANGALORE"]["flags"])
    assert [s["row_index"] for s in out["skipped_rows"]] == [2, 3, 4]
    assert {s["reason"] for s in out["skipped_rows"]} == {"after_stop"}
    staged = client.get(f"/api/review/{out['batch_id']}").json()["candidates"]
    assert len(staged) == 2


def test_detect_without_a_stop_pattern_is_unchanged_and_lists_skips(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    _save_hdfc_template(app, skip_exact_narrations=["late row after total"])
    out = _detect(client, _STOPPED.replace(b"Total,,420.50,\n", b""))
    assert [c["raw_narration"] for c in out["candidates"]] == ["SWIGGY BANGALORE"]
    assert all("stop row" not in " ".join(c["flags"]) for c in out["candidates"])
    assert [(s["row_index"], s["reason"]) for s in out["skipped_rows"]] == [
        (2, "skip_exact"),
        (3, "bad_date"),
    ]


def test_recheck_dedup_clears_a_stale_matched_txn_id_on_a_new_row(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    staged = _stage_hdfc(client, tmp_path)
    bid = staged["batch_id"]
    store = app.state.review_store
    row = store.get_batch(bid)[0]
    assert row.dedup_status == DedupStatus.NEW
    store.update(bid, row.id, {"matched_txn_id": "stale"})
    changes = app.state.import_service.recheck_dedup(bid)
    assert changes == [{"candidate_id": row.id, "before": "new", "after": "new"}]
    assert store.get_batch(bid)[0].matched_txn_id is None
