"""Staging records the batch's opening evidence, on every path that stages a
CSV/XLS batch, and a failure to record it never fails the staging."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.app.llm.flows.prove_template.service import AgentIngestService
from backend.app.models.api.agent import Claims
from backend.app.models.storage.template import Template
from backend.app.models.storage.template_draft import TemplateDraft, TemplateFields
from backend.app.server.main import create_app
from backend.app.service.ingest import service as ingest_service
from backend.app.service.ingest.templates.fingerprint import fingerprint_headers

TODAY = date(2026, 10, 16)
ACCOUNT = "Assets:Checking:HDFC"
HEADER = "Date,Narration,Amount,Balance"
STATEMENT = (
    "Statement of account,,,\n"
    "Opening Balance,,,10000.00\n"
    f"{HEADER}\n"
    "01/04/2026,SWIGGY,-500.00,9500.00\n"
    "03/04/2026,SALARY,50000.00,59500.00\n"
    "05/04/2026,RENT,-20000.00,39500.00\n"
).encode()
FIELDS = TemplateFields(
    institution="Synthetic Bank",
    header_row_index=2,
    date_column="Date",
    date_format="%d/%m/%Y",
    narration_column="Narration",
    amount_column="Amount",
    amount_sign="signed",
    balance_column="Balance",
    default_account=ACCOUNT,
    opening_balance={"label": "^Opening Balance$"},
)
CLAIMS = Claims(
    first_data_row=3, last_data_row=5, opening_balance_printed=Decimal("10000.00")
)
FINGERPRINT = fingerprint_headers(HEADER.split(","))


@pytest.fixture
def app(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: TODAY)
    TestClient(app)  # runs startup
    return app


@pytest.fixture
def svc(app) -> AgentIngestService:
    return AgentIngestService(
        import_service=app.state.import_service,
        template_store=app.state.template_store,
        review_store=app.state.review_store,
        ledger=app.state.ledger,
        clock=lambda: TODAY,
    )


def _proof(svc) -> dict:
    run = svc.inspect("s.csv", STATEMENT)
    dry = svc.dry_run(run["prove_id"], FIELDS, CLAIMS)
    assert dry["report"]["passed"], dry["report"]
    return svc.stage(run["prove_id"], "proof")


def _assert_evidence(app, batch_id: str) -> None:
    ev = app.state.review_store.opening_evidence(batch_id)
    assert ev is not None
    assert ev.account == ACCOUNT
    assert ev.printed is not None and ev.printed.value == Decimal("10000.00")
    assert (ev.printed.row, ev.printed.column) == (1, 3)
    assert ev.first_row is not None and ev.first_row.derived == Decimal("10000.00")
    assert ev.printed_error is None


def test_agent_stage_records_evidence(app, svc):
    staged = _proof(svc)
    _assert_evidence(app, staged["batch_id"])
    checks = {c["id"]: c["status"] for c in staged["report"]["checks"]}
    assert checks[18] == "pass" and checks[11] == "pass"


def test_agent_template_match_records_evidence(app, svc):
    _proof(svc)
    run = svc.inspect("s.csv", STATEMENT)
    staged = svc.stage(run["prove_id"], "template_match")
    _assert_evidence(app, staged["batch_id"])


def _recorded(app, monkeypatch) -> list[str]:
    """Batch ids the store is asked to hold evidence for, via its public
    setter (the real setter still runs)."""
    store = app.state.review_store
    real = store.set_opening_evidence
    seen: list[str] = []

    def spy(batch_id, ev):
        seen.append(batch_id)
        real(batch_id, ev)

    monkeypatch.setattr(store, "set_opening_evidence", spy)
    return seen


def test_dry_run_records_nothing(app, svc, monkeypatch):
    seen = _recorded(app, monkeypatch)
    run = svc.inspect("s.csv", STATEMENT)
    svc.dry_run(run["prove_id"], FIELDS, CLAIMS)
    assert seen == []
    staged = svc.stage(run["prove_id"], "proof")
    assert seen == [staged["batch_id"]]


def test_reused_template_without_the_opening_line_still_stages(app, svc):
    """A saved template whose cell this month's file does not print: check
    18 warns, template_match stages, and the evidence records why."""
    _proof(svc)
    lines = STATEMENT.decode().splitlines(keepends=True)
    no_opening = "".join(
        ",,,\n" if line.startswith("Opening Balance") else line for line in lines
    ).encode()
    run = svc.inspect("no-opening.csv", no_opening)
    staged = svc.stage(run["prove_id"], "template_match")
    (check,) = [c for c in staged["report"]["checks"] if c["id"] == 18]
    assert check["status"] == "warn" and "matched no cell" in check["message"]
    ev = app.state.review_store.opening_evidence(staged["batch_id"])
    assert ev is not None and ev.printed is None
    assert "matched no cell" in (ev.printed_error or "")
    assert ev.first_row is not None and ev.first_row.derived == Decimal("10000.00")


def test_dry_run_two_matches_is_check18_fail_not_a_crash(svc):
    two = STATEMENT + b"Opening Balance,,,39500.00\n"
    run = svc.inspect("two.csv", two)
    dry = svc.dry_run(run["prove_id"], FIELDS, CLAIMS)
    assert not dry["report"]["passed"]
    (check,) = [c for c in dry["report"]["checks"] if c["id"] == 18]
    assert check["status"] == "fail"
    assert "row 1 column 0" in check["message"]
    assert "row 6 column 0" in check["message"]


def _save_template(app) -> None:
    app.state.template_store.save(FIELDS.to_template(FINGERPRINT, origin="agent"))


def test_browser_detect_records_evidence(app):
    _save_template(app)
    out = app.state.import_service.detect("s.csv", STATEMENT)
    assert out["matched"] is True
    _assert_evidence(app, out["batch_id"])


def test_create_template_records_evidence_only_when_saved(app, monkeypatch):
    seen = _recorded(app, monkeypatch)
    imp = app.state.import_service
    imp.detect("s.csv", STATEMENT)  # unmatched: stores the upload
    draft = dict(FIELDS.model_dump(), filename="s.csv", fingerprint=FINGERPRINT)
    preview = imp.create_template(TemplateDraft(**draft, save=False))
    assert "batch_id" not in preview
    assert seen == []
    saved = imp.create_template(TemplateDraft(**draft, save=True))
    assert seen == [saved["batch_id"]]
    _assert_evidence(app, saved["batch_id"])


def test_template_without_the_field_records_derivation_only(app):
    legacy = Template.model_validate(
        {**FIELDS.to_template(FINGERPRINT).model_dump(), "opening_balance": None}
    )
    app.state.template_store.save(legacy)
    out = app.state.import_service.detect("s.csv", STATEMENT)
    ev = app.state.review_store.opening_evidence(out["batch_id"])
    assert ev is not None and ev.printed is None and ev.printed_error is None
    assert ev.first_row is not None and ev.first_row.derived == Decimal("10000.00")


def _boom(*_args, **_kwargs):
    raise RuntimeError("evidence exploded")


def test_evidence_failure_never_fails_detect(app, monkeypatch, caplog):
    _save_template(app)
    monkeypatch.setattr(ingest_service, "csv_evidence", _boom)
    out = app.state.import_service.detect("s.csv", STATEMENT)
    assert out["matched"] is True and len(out["candidates"]) == 3
    assert app.state.review_store.get_batch(out["batch_id"])
    assert app.state.review_store.opening_evidence(out["batch_id"]) is None
    assert "opening evidence not recorded" in caplog.text


def test_evidence_failure_never_fails_agent_stage(app, svc, monkeypatch):
    monkeypatch.setattr(ingest_service, "csv_evidence", _boom)
    staged = _proof(svc)
    assert staged["template_saved"] is True
    assert app.state.review_store.opening_evidence(staged["batch_id"]) is None
