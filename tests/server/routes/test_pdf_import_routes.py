"""PDF card statements through the browser routes: `/api/import/detect` on a
PDF, `/api/import/unlock`, and the unlock route's 422 bodies that never echo
the submitted values."""

from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fpdf import FPDF
from pypdf import PdfReader

from backend.app.server.main import create_app
from backend.app.core.errors.errors import CodedError
from tests.service.ingest.pdf_fixtures import Row, make_statement_pdf
from tests.service.ingest.pdf_reference import hdfc_template

TODAY = date(2026, 10, 24)
CARD = "Liabilities:CreditCard:HDFC"
FIXTURE_KEY = "Zq9-secret"
# Previous 8,200.00 - payments 8,200.00 + purchases 15,345.00 = total 15,345.00.
ROWS = [
    Row("21/09/2026", "14:22", "ACME STORE BENGALURU", "10,000.00"),
    Row("22/09/2026", "10:00", "PAYMENT RECEIVED THANK YOU", "8,200.00", credit=True),
    Row("23/09/2026", "11:30", "INITECH FOODS", "5,000.00", bar_apart=True),
    Row("24/09/2026", "11:30", "UMBRELLA PHARMA", "345.00", rewards=3),
]
E1070_RESOLUTION = (
    "Nothing was saved. Run /ingest-stmt on this file in Claude Code once to"
    " update the saved layout for this card."
)
UNKNOWN_LAYOUT = {
    "matched": False,
    "kind": "pdf_unknown",
    "message": "New card layout. Run /ingest-stmt on this file in Claude Code once.",
}


@pytest.fixture
def app(tmp_path):
    app = create_app(storage_dir=tmp_path / "s", clock=lambda: TODAY)
    TestClient(app)  # runs startup
    assert CARD in {
        e.account for e in app.state.ledger.load().entries if hasattr(e, "account")
    }
    return app


@pytest.fixture
def known(app):
    """The HDFC layout saved as a PDF template."""
    app.state.pdf_template_store.save(hdfc_template())
    return app


def _pdf(tmp_path: Path, name: str = "src/s.pdf", **kw) -> bytes:
    kw.setdefault("domestic", ROWS)
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return make_statement_pdf(path, **kw).read_bytes()


def _detect(client: TestClient, filename: str, raw: bytes):
    return client.post(
        "/api/import/detect",
        files={"file": (filename, io.BytesIO(raw), "application/pdf")},
    )


def _unlock(client: TestClient, filename: str, raw: bytes, password: str):
    return client.post(
        "/api/import/unlock",
        files={"file": (filename, io.BytesIO(raw), "application/pdf")},
        data={"password": password},
    )


def _imports(app) -> Path:
    return app.state.ledger.storage_dir / "imports"


def _assert_password_nowhere(text: str, caplog, tmp_path: Path) -> None:
    assert FIXTURE_KEY not in text
    assert FIXTURE_KEY not in caplog.text
    secret = FIXTURE_KEY.encode()
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret not in path.read_bytes(), path


def test_detect_known_pdf_stages_batch_with_statement(known, tmp_path):
    client = TestClient(known)
    resp = _detect(client, "sep.pdf", _pdf(tmp_path))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "warnings",
        "matched",
        "batch_id",
        "fingerprint",
        "candidates",
        "skipped_rows",
        "statement",
    }
    assert body["matched"] is True
    assert body["fingerprint"] == hdfc_template().fingerprint
    assert len(body["candidates"]) == len(ROWS)
    assert body["statement"]["period_start"] == "2026-09-21"
    assert body["statement"]["period_end"] == "2026-10-20"
    assert body["statement"]["roles"]["total_due"] == {
        "label": "TOTAL AMOUNT DUE",
        "value": "15345.00",
    }
    statement = known.state.review_store.statement(body["batch_id"])
    assert statement is not None and statement.account == CARD
    review = client.get(f"/api/review/{body['batch_id']}").json()
    assert review["statement"]["period_end"] == "2026-10-20"


def test_detect_unknown_pdf_says_run_ingest_stmt(app, tmp_path):
    resp = _detect(TestClient(app), "new.pdf", _pdf(tmp_path))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {**UNKNOWN_LAYOUT, "filename": "new.pdf"}
    assert app.state.review_store._batches == {}


def test_detect_locked_pdf_asks_for_password(known, tmp_path):
    raw = _pdf(tmp_path, password=FIXTURE_KEY)
    resp = _detect(TestClient(known), "locked.pdf", raw)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "matched": False,
        "kind": "pdf_locked",
        "filename": "locked.pdf",
    }
    # Only the upload itself is stored, still encrypted; nothing decrypted.
    assert sorted(p.name for p in _imports(known).iterdir()) == ["locked.pdf"]
    assert PdfReader(_imports(known) / "locked.pdf").is_encrypted
    assert known.state.review_store._batches == {}


def test_detect_textless_pdf_is_unreadable(app):
    pdf = FPDF(unit="pt", format="A4")
    pdf.add_page()
    resp = _detect(TestClient(app), "scan.pdf", bytes(pdf.output()))
    assert resp.status_code == 422
    assert resp.json()["code"] == "E1069"
    assert "it has no selectable text" in resp.json()["description"]


def test_unlock_right_password_detects_unlocked_copy_only(known, tmp_path):
    raw = _pdf(tmp_path, password=FIXTURE_KEY)
    resp = _unlock(TestClient(known), "locked.pdf", raw, FIXTURE_KEY)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matched"] is True and len(body["candidates"]) == len(ROWS)
    assert body["statement"]["period_end"] == "2026-10-20"
    # The unlock route writes only the decrypted copy, under its own name.
    assert sorted(p.name for p in _imports(known).iterdir()) == ["locked-unlocked.pdf"]
    unlocked = _imports(known) / "locked-unlocked.pdf"
    assert not PdfReader(unlocked).is_encrypted


def test_unlock_right_password_unknown_layout_names_unlocked_copy(app, tmp_path):
    raw = _pdf(tmp_path, password=FIXTURE_KEY)
    resp = _unlock(TestClient(app), "locked.pdf", raw, FIXTURE_KEY)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {**UNKNOWN_LAYOUT, "filename": "locked-unlocked.pdf"}


def test_unlock_wrong_password_is_coded_and_never_echoed(known, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    raw = _pdf(tmp_path, password="right-one")
    resp = _unlock(TestClient(known), "locked.pdf", raw, FIXTURE_KEY)
    assert resp.status_code == 422
    assert resp.json()["code"] == "E1068"
    assert not _imports(known).exists() or list(_imports(known).iterdir()) == []
    _assert_password_nowhere(resp.text, caplog, tmp_path)


def test_unlock_right_password_never_echoed(known, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    raw = _pdf(tmp_path, password=FIXTURE_KEY)
    resp = _unlock(TestClient(known), "locked.pdf", raw, FIXTURE_KEY)
    assert resp.status_code == 200, resp.text
    _assert_password_nowhere(resp.text, caplog, tmp_path)


def test_unlock_malformed_request_has_no_input_echo(app, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    resp = TestClient(app).post("/api/import/unlock", data={"password": FIXTURE_KEY})
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"], body
    for error in body["detail"]:
        assert "input" not in error and "ctx" not in error
        assert {"type", "loc", "msg"} <= set(error)
    assert any(e["loc"] == ["body", "file"] for e in body["detail"])
    _assert_password_nowhere(resp.text, caplog, tmp_path)


def test_unlock_password_sent_as_a_file_is_not_echoed(app, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    resp = TestClient(app).post(
        "/api/import/unlock",
        files={
            "file": ("s.pdf", io.BytesIO(b"%PDF-1.7"), "application/pdf"),
            "password": ("p.txt", io.BytesIO(FIXTURE_KEY.encode()), "text/plain"),
        },
    )
    assert resp.status_code == 422
    for error in resp.json()["detail"]:
        assert "input" not in error and "ctx" not in error
    _assert_password_nowhere(resp.text, caplog, tmp_path)


def test_unlock_corrupt_pdf_is_coded_without_library_text(app, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    resp = _unlock(
        TestClient(app), "bad.pdf", b"%PDF-1.7\nnot really a pdf", FIXTURE_KEY
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "E1069"
    assert body["description"] == (
        "bad.pdf could not be read as a PDF statement: the file is damaged."
    )
    lowered = resp.text.lower()
    for library_text in ("pypdf", "pdfreaderror", "traceback", "eof marker"):
        assert library_text not in lowered
    _assert_password_nowhere(resp.text, caplog, tmp_path)


def test_other_routes_keep_default_422_body(app):
    resp = TestClient(app).post("/api/templates", json={"filename": "x.csv"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail and all("input" in e for e in detail)
    assert set(resp.json()) == {"detail"}


def test_detect_csv_unchanged(app):
    csv = (
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    )
    resp = TestClient(app).post(
        "/api/import/detect",
        files={"file": ("hdfc.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is False and "kind" not in body
    assert body["filename"] == "hdfc.csv"
    assert body["suggested_roles"]["date"] == "Date"
    assert body["delimiter"] == ","


MISMATCH = {"total_due": "C 99,999.00"}


def test_detect_known_pdf_with_mismatch_is_statement_mismatch(known, tmp_path):
    wrong = _pdf(tmp_path, summary=MISMATCH)
    resp = _detect(TestClient(known), "wrong.pdf", wrong)
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "E1070"
    assert body["description"].startswith(
        "This statement did not reconcile with its own summary, so nothing was"
        " imported: summary_equation: "
    )
    assert body["resolution"] == E1070_RESOLUTION
    assert known.state.review_store._batches == {}


def test_unlock_known_pdf_with_mismatch_is_statement_mismatch(known, tmp_path):
    wrong = _pdf(tmp_path, summary=MISMATCH, password=FIXTURE_KEY)
    resp = _unlock(TestClient(known), "wrong.pdf", wrong, FIXTURE_KEY)
    assert resp.status_code == 422 and resp.json()["code"] == "E1070"
    assert known.state.review_store._batches == {}


def test_agent_path_mismatch_stays_e1061(known, tmp_path):
    wrong = _pdf(tmp_path, summary=MISMATCH)
    client = TestClient(known)
    run = client.post(
        "/api/agent/inspect",
        files={"file": ("wrong.pdf", wrong, "application/pdf")},
    ).json()
    resp = client.post(
        "/api/agent/stage", json={"prove_id": run["prove_id"], "use": "template_match"}
    )
    assert resp.status_code == 422 and resp.json()["code"] == "E1081"
    with pytest.raises(CodedError) as err:
        known.state.import_service.stage_pdf(tmp_path / "src/s.pdf", hdfc_template())
    assert err.value.code == "E1081"
    assert known.state.review_store._batches == {}


def _post_password_as_file(client: TestClient, url: str):
    return client.post(
        url,
        files={
            "file": ("s.pdf", io.BytesIO(b"%PDF-1.7"), "application/pdf"),
            "password": ("p.txt", io.BytesIO(FIXTURE_KEY.encode()), "text/plain"),
        },
    )


@pytest.mark.parametrize("prefix", ["root_path", "mount"])
def test_unlock_422_strips_input_under_a_prefix(app, tmp_path, caplog, prefix):
    """The guard matches the route, not the URL: a proxy root_path or a mount
    prefix must not turn the stripping off."""
    caplog.set_level(logging.DEBUG)
    if prefix == "root_path":
        client = TestClient(app, root_path="/x")
    else:
        outer = FastAPI()
        outer.mount("/x", app)
        client = TestClient(outer)
    resp = _post_password_as_file(client, "/x/api/import/unlock")
    assert resp.status_code == 422
    for error in resp.json()["detail"]:
        assert "input" not in error and "ctx" not in error
    _assert_password_nowhere(resp.text, caplog, tmp_path)


@pytest.mark.parametrize("data", [{"password": ""}, {}])
def test_unlock_empty_or_absent_password_is_coded_wrong_password(known, tmp_path, data):
    raw = _pdf(tmp_path, password=FIXTURE_KEY)
    resp = TestClient(known).post(
        "/api/import/unlock",
        files={"file": ("locked.pdf", io.BytesIO(raw), "application/pdf")},
        data=data,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "E1068"


# --- F4: commit re-checks the rows against the statement ---------------------


def _journal(app) -> str:
    journal = app.state.ledger.journal_dir
    return "".join(p.read_text() for p in sorted(journal.glob("*.beancount")))


def _staged(client: TestClient, tmp_path: Path, **kw) -> dict:
    resp = _detect(client, "sep.pdf", _pdf(tmp_path, **kw))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_narration(body: dict, narration: str) -> str:
    return next(c["id"] for c in body["candidates"] if c["raw_narration"] == narration)


@pytest.mark.parametrize(
    "change",
    [
        ("POST", "ACME STORE BENGALURU", {"amount": "9000.00"}),
        ("POST", "PAYMENT RECEIVED THANK YOU", {"direction": "debit"}),
        ("DELETE", "UMBRELLA PHARMA", None),
        ("POST", "INITECH FOODS", {"source_account": "Liabilities:CreditCard:ICICI"}),
    ],
    ids=["amount", "direction", "dropped", "re-accounted"],
)
def test_commit_refuses_a_batch_that_no_longer_reconciles(known, tmp_path, change):
    client = TestClient(known)
    body = _staged(client, tmp_path)
    method, narration, patch = change
    url = f"/api/review/{body['batch_id']}/{_by_narration(body, narration)}"
    edit = client.post(url, json=patch) if method == "POST" else client.delete(url)
    assert edit.status_code == 200, edit.text
    before = _journal(known)

    resp = client.post(f"/api/commit/{body['batch_id']}")
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "E1070"
    assert resp.json()["resolution"] == E1070_RESOLUTION
    assert "summary_totals" in resp.json()["description"]
    assert _journal(known) == before  # nothing written, not even the record
    assert 'custom "card-statement"' not in before


def test_commit_of_an_untouched_batch_writes_rows_and_record(known, tmp_path):
    client = TestClient(known)
    # Finance charges printed only in the summary: the generated row is not a
    # parsed row, so the re-check leaves it out of the totals.
    body = _staged(
        client,
        tmp_path,
        summary={"finance_charges": "C 100.00", "total_due": "C 15,445.00"},
    )
    assert len(body["candidates"]) == len(ROWS) + 1
    resp = client.post(f"/api/commit/{body['batch_id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] == len(ROWS) + 1
    assert resp.json()["statement_recorded"] is True


def test_plain_statement_has_no_warnings(known, tmp_path):
    body = _staged(TestClient(known), tmp_path)
    assert body["warnings"] == []


def test_emi_panel_stages_with_a_warning_and_commits_once(known, tmp_path):
    client = TestClient(known)
    body = _staged(client, tmp_path, emi_lines=["01/10/2026 EMI 3 OF 12 C 2,000.00"])
    assert body["matched"] is True and len(body["candidates"]) == len(ROWS)
    assert len(body["warnings"]) == 1
    assert body["warnings"][0].startswith(
        "this statement has a panel called EMI (lines "
    )
    assert body["warnings"][0].endswith("EMI instalments are imported as ordinary rows")
    resp = client.post(f"/api/commit/{body['batch_id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] == len(ROWS)
    assert resp.json()["statement_recorded"] is True


def test_browser_detect_second_card_is_pdf_unknown(known, tmp_path):
    raw = _pdf(tmp_path, name="src/b.pdf", card="XXXX XXXX XXXX 5678")
    resp = _detect(TestClient(known), "b.pdf", raw)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {**UNKNOWN_LAYOUT, "filename": "b.pdf"}
    assert known.state.review_store._batches == {}
