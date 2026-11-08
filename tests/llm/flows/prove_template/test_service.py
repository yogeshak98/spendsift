from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.models.api.agent import Claims
from backend.app.llm.flows.prove_template.service import AgentIngestService, derive_kind
from backend.app.server.main import create_app
from backend.app.core.errors.errors import CodedError
from backend.app.models.storage.template_draft import TemplateFields
from tests.llm.flows.prove_template.reference_drafts import REFERENCE

FIX = Path("tests/fixtures/statements")
HDFC = (FIX / "hdfc_bank.csv").read_bytes()
GOOD = TemplateFields(
    institution="HDFC",
    header_row_index=0,
    date_column="Date",
    date_format="%Y-%m-%d",
    narration_column="Narration",
    debit_column="Withdrawal Amt.",
    credit_column="Deposit Amt.",
    default_account="Assets:Checking:HDFC",
)
CLAIMS = Claims(first_data_row=1, last_data_row=2)


@pytest.fixture
def svc(tmp_path) -> AgentIngestService:
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 10, 16))
    TestClient(app)  # runs startup
    return AgentIngestService(
        import_service=app.state.import_service,
        template_store=app.state.template_store,
        review_store=app.state.review_store,
        ledger=app.state.ledger,
        clock=lambda: date(2026, 10, 16),
    )


def _code(fn):
    with pytest.raises(CodedError) as err:
        fn()
    return err.value.code


def test_derive_kind():
    assert derive_kind(["Assets:Checking:HDFC"]) == "bank"
    assert derive_kind(["Liabilities:CreditCard:HDFC"]) == "card"
    with pytest.raises(CodedError):
        derive_kind(["Assets:Checking:HDFC", "Liabilities:CreditCard:HDFC"])
    with pytest.raises(CodedError):
        derive_kind([])


def test_inspect_never_raises_on_an_unrecognisable_header(svc):
    out = svc.inspect("odd.csv", b"Txn Dt,Remarks,Amt\n01/04/2026,X,1.00\n")
    assert out["template_match"] is None and out["dedicated"] is None
    assert out["guess"] is None
    assert out["stored_name"].endswith("-odd.csv")


def test_inspect_is_idempotent_on_storage_but_opens_fresh_runs(svc):
    a = svc.inspect("h.csv", HDFC)
    b = svc.inspect("h.csv", HDFC)
    assert a["stored_name"] == b["stored_name"] and a["prove_id"] != b["prove_id"]


def test_dry_run_passes_and_stage_by_proof_saves_an_agent_template(svc):
    run = svc.inspect("h.csv", HDFC)
    out = svc.dry_run(run["prove_id"], GOOD, CLAIMS)
    assert out["report"]["passed"] and out["attempts_left"] == 4
    staged = svc.stage(run["prove_id"], "proof")
    assert staged["template_saved"] is True
    saved = svc._templates.get_by_fingerprint(staged["fingerprint"])
    assert saved.origin == "agent"
    assert len(staged["candidates"]) == 2


def test_attempt_budget_is_per_run_and_windows_are_free(svc):
    run = svc.inspect("h.csv", HDFC)
    for _ in range(5):
        svc.dry_run(run["prove_id"], GOOD, CLAIMS)
    window = svc.dry_run_window(run["prove_id"], 1, 1)
    assert [c["row"] for c in window["candidates"]] == [1]
    assert _code(lambda: svc.dry_run(run["prove_id"], GOOD, CLAIMS)) == "E1076"
    fresh = svc.inspect("h.csv", HDFC)
    assert svc.dry_run(fresh["prove_id"], GOOD, CLAIMS)["attempt"] == 1
    assert _code(lambda: svc.stage(fresh["prove_id"], "dedicated")) == "E1063"


def test_new_run_does_not_inherit_the_old_runs_proof(svc):
    old = svc.inspect("h.csv", HDFC)
    svc.dry_run(old["prove_id"], GOOD, CLAIMS)
    new = svc.inspect("h.csv", HDFC)
    assert _code(lambda: svc.stage(new["prove_id"], "proof")) == "E1062"


def test_previous_report_is_returned_for_diffing(svc):
    run = svc.inspect("h.csv", HDFC)
    first = svc.dry_run(
        run["prove_id"], GOOD.model_copy(update={"date_format": "%d/%m/%Y"}), CLAIMS
    )
    assert first["previous_report"] is None and not first["report"]["passed"]
    second = svc.dry_run(run["prove_id"], GOOD, CLAIMS)
    assert second["previous_report"]["passed"] is False


def test_unknown_account_and_kind_mismatch(svc):
    run = svc.inspect("h.csv", HDFC, kind="card")
    bad = GOOD.model_copy(update={"default_account": "Assets:Checking:NOPE"})
    assert _code(lambda: svc.dry_run(run["prove_id"], bad, CLAIMS)) == "E1077"
    assert _code(lambda: svc.dry_run(run["prove_id"], GOOD, CLAIMS)) == "E1079"


def test_investment_kind_without_dedicated_parser_is_refused(svc):
    kite = (FIX / "kite_orders.csv").read_bytes()
    assert _code(lambda: svc.inspect("k.csv", kite, kind="stocks")) == "E1080"


def test_template_match_after_save(svc):
    run = svc.inspect("h.csv", HDFC)
    svc.dry_run(run["prove_id"], GOOD, CLAIMS)
    svc.stage(run["prove_id"], "proof")
    again = svc.inspect("h2.csv", HDFC)
    assert again["template_match"]["institution"] == "HDFC"
    staged = svc.stage(again["prove_id"], "template_match")
    assert staged["report"]["passed"] and staged["template_saved"] is False
    assert (
        _code(lambda: svc.stage(svc.inspect("h3.csv", HDFC)["prove_id"], "proof"))
        == "E1062"
    )


def test_proof_refuses_to_overwrite_an_existing_template_without_upgrade(svc):
    a = svc.inspect("h.csv", HDFC)
    svc.dry_run(a["prove_id"], GOOD, CLAIMS)
    svc.stage(a["prove_id"], "proof")
    b = svc.inspect("h.csv", HDFC)
    svc.dry_run(b["prove_id"], GOOD, CLAIMS)
    assert _code(lambda: svc.stage(b["prove_id"], "proof")) == "E1064"


def test_upgrade_carries_the_marker_map_over(svc):
    a = svc.inspect("h.csv", HDFC)
    svc.dry_run(
        a["prove_id"],
        GOOD.model_copy(update={"account_marker_map": {"x": "Assets:Checking:HDFC"}}),
        CLAIMS,
    )
    fp = svc.stage(a["prove_id"], "proof")["fingerprint"]
    up = svc.inspect("h.csv", HDFC, upgrade=fp)
    svc.dry_run(
        up["prove_id"], GOOD.model_copy(update={"stop_pattern": "^Total"}), CLAIMS
    )
    svc.stage(up["prove_id"], "proof")
    saved = svc._templates.get_by_fingerprint(fp)
    assert saved.stop_pattern == "^Total" and saved.account_marker_map == {
        "x": "Assets:Checking:HDFC"
    }


def test_dedicated_stage_for_groww(svc):
    raw = (FIX / "groww_stocks.csv").read_bytes()
    run = svc.inspect("g.csv", raw, kind="stocks")
    assert run["dedicated"] == "groww_stocks"
    staged = svc.stage(run["prove_id"], "dedicated")
    assert staged["candidates"] and staged["report"]["passed"]


def test_upload_tamper_is_detected(svc, tmp_path):
    run = svc.inspect("h.csv", HDFC)
    (tmp_path / "storage" / "imports" / run["stored_name"]).write_bytes(HDFC + b"x")
    assert _code(lambda: svc.dry_run(run["prove_id"], GOOD, CLAIMS)) == "E1074"


def test_commit_rechecks_duplicates_then_commits(svc):
    a = svc.inspect("h.csv", HDFC)
    svc.dry_run(a["prove_id"], GOOD, CLAIMS)
    first = svc.stage(a["prove_id"], "proof")
    b = svc.inspect("h2.csv", HDFC)
    second = svc.stage(b["prove_id"], "template_match")
    svc.commit(first["batch_id"])
    assert _code(lambda: svc.commit(second["batch_id"])) == "E1065"
    svc.commit(second["batch_id"])  # statuses written back: duplicates are skipped
    assert _code(lambda: svc.commit("gone")) == "E1006"
    assert (svc._ledger.storage_dir / "imports" / a["stored_name"]).exists()  # kept


def _template_check(out):
    assert out["report"]["passed"] is False
    assert [c["name"] for c in out["report"]["checks"]] == ["template"]
    assert (
        out["candidates"] == []
        and out["candidate_rows"] == []
        and out["skipped_rows"] == []
    )


def test_bad_regex_is_a_failed_check_and_counts_an_attempt(svc):
    run = svc.inspect("h.csv", HDFC)
    out = svc.dry_run(
        run["prove_id"], GOOD.model_copy(update={"stop_pattern": "(["}), CLAIMS
    )
    _template_check(out)
    assert out["attempt"] == 1 and out["attempts_left"] == 4
    assert out["fingerprint"] is not None
    assert (
        _code(lambda: svc.stage(run["prove_id"], "proof")) == "E1062"
    )  # no passing proof
    ok = svc.dry_run(run["prove_id"], GOOD, CLAIMS)
    assert (
        ok["report"]["passed"]
        and ok["previous_report"]["checks"][0]["name"] == "template"
    )


def test_invalid_template_model_is_a_failed_check(svc):
    run = svc.inspect("h.csv", HDFC)
    bad = GOOD.model_copy(
        update={
            "debit_column": None,
            "credit_column": None,
            "amount_column": "Withdrawal Amt.",
            "amount_sign": "indicator",
            "sign_column": None,
        }
    )
    out = svc.dry_run(run["prove_id"], bad, CLAIMS)
    _template_check(out)
    assert out["attempt"] == 1


def test_header_row_outside_the_file_is_a_failed_check(svc):
    run = svc.inspect("h.csv", HDFC)
    out = svc.dry_run(
        run["prove_id"], GOOD.model_copy(update={"header_row_index": 999}), CLAIMS
    )
    _template_check(out)
    assert out["fingerprint"] is None


def test_stage_template_match_with_unparseable_saved_template_is_coded(svc):
    run = svc.inspect("h.csv", HDFC)
    svc.dry_run(run["prove_id"], GOOD, CLAIMS)
    fp = svc.stage(run["prove_id"], "proof")["fingerprint"]
    saved = svc._templates.get_by_fingerprint(fp)
    svc._templates.save(saved.model_copy(update={"stop_pattern": "(["}))
    again = svc.inspect("h2.csv", HDFC)
    assert _code(lambda: svc.stage(again["prove_id"], "template_match")) == "E1081"


def test_marker_pattern_without_capture_group_is_a_failed_check(svc):
    run = svc.inspect("h.csv", HDFC)
    bad = GOOD.model_copy(
        update={"account_marker_pattern": "Date"}
    )  # matches, no group
    out = svc.dry_run(run["prove_id"], bad, CLAIMS)
    _template_check(out)
    assert out["attempt"] == 1


def test_stage_template_match_enforces_the_kind_hint(svc):
    a = svc.inspect("h.csv", HDFC)
    svc.dry_run(a["prove_id"], GOOD, CLAIMS)
    svc.stage(a["prove_id"], "proof")
    batches = len(svc._review._batches)
    card = svc.inspect("h2.csv", HDFC, kind="card")
    assert _code(lambda: svc.stage(card["prove_id"], "template_match")) == "E1079"
    assert len(svc._review._batches) == batches


def test_failed_dedicated_stage_leaves_no_batch(svc, monkeypatch):
    raw = (FIX / "groww_stocks.csv").read_bytes()
    run = svc.inspect("g.csv", raw, kind="stocks")
    real = svc._import.scan_dedicated
    seen: list[str] = []

    def with_unparsed(path):
        out = real(path)
        seen.append(out["batch_id"])
        return {**out, "unparsed": [{"row": 1}]}

    monkeypatch.setattr(svc._import, "scan_dedicated", with_unparsed)
    assert _code(lambda: svc.stage(run["prove_id"], "dedicated")) == "E1081"
    assert seen and seen[0] not in svc._review._batches


def test_unknown_account_still_consumes_an_attempt(svc):
    run = svc.inspect("h.csv", HDFC)
    bad = GOOD.model_copy(update={"default_account": "Assets:Checking:NOPE"})
    assert _code(lambda: svc.dry_run(run["prove_id"], bad, CLAIMS)) == "E1077"
    out = svc.dry_run(run["prove_id"], GOOD, CLAIMS)
    assert out["attempt"] == 2 and out["attempts_left"] == 3


def test_window_calls_do_not_consume_attempts(svc):
    run = svc.inspect("h.csv", HDFC)
    svc.dry_run(run["prove_id"], GOOD, CLAIMS)
    svc.dry_run_window(run["prove_id"], 1, 1)
    assert svc.dry_run(run["prove_id"], GOOD, CLAIMS)["attempt"] == 2


def test_upgrade_with_a_different_header_row_is_a_repairable_failed_check(svc):
    a = svc.inspect("h.csv", HDFC)
    svc.dry_run(a["prove_id"], GOOD, CLAIMS)
    fp = svc.stage(a["prove_id"], "proof")["fingerprint"]
    up = svc.inspect("h.csv", HDFC, upgrade=fp)
    moved = GOOD.model_copy(update={"header_row_index": 1})
    out = svc.dry_run(up["prove_id"], moved, CLAIMS)
    _template_check(out)
    message = out["report"]["checks"][0]["message"]
    assert message.startswith("E1067: ") and "E1067: E1067" not in message
    assert out["attempt"] == 1 and out["attempts_left"] == 4
    ok = svc.dry_run(up["prove_id"], GOOD, CLAIMS)
    assert ok["report"]["passed"] and ok["attempt"] == 2


SEPARATED = (
    b"Date,Narration,Withdrawal Amt.,Deposit Amt.\n"
    b"********\n"
    b"2026-03-05,SWIGGY BANGALORE,420.50,\n"
    b"2026-03-06,SALARY,,5000.00\n"
)
SEP_DRAFT = GOOD.model_copy(update={"header_row_index": 0})
SEP_CLAIMS = Claims(first_data_row=2, last_data_row=3)


def _check(out, name):
    return next(c for c in out["report"]["checks"] if c["name"] == name)


def test_a_proof_that_would_fail_reuse_fails_check_11(svc):
    run = svc.inspect("sep.csv", SEPARATED)
    out = svc.dry_run(run["prove_id"], SEP_DRAFT, SEP_CLAIMS)
    assert out["report"]["passed"] is False
    reuse = _check(out, "reuse")
    assert reuse["id"] == 11 and reuse["status"] == "fail"
    assert reuse["rows"] == [1]
    assert reuse["message"].startswith("a saved template would fail next time: ")
    assert "coverage: rows 1 did not parse" in reuse["message"]
    assert _code(lambda: svc.stage(run["prove_id"], "proof")) == "E1062"
    # The row is in the trailing-space-joined line, so the pattern allows it.
    fixed = SEP_DRAFT.model_copy(update={"skip_row_patterns": [r"^\*+\s*$"]})
    ok = svc.dry_run(run["prove_id"], fixed, SEP_CLAIMS)
    assert ok["report"]["passed"], ok["report"]
    assert _check(ok, "reuse")["status"] == "pass"
    svc.stage(run["prove_id"], "proof")
    again = svc.inspect("sep2.csv", SEPARATED)
    assert again["template_match"] is not None
    assert svc.stage(again["prove_id"], "template_match")["report"]["passed"]


def test_stage_proof_reapplies_the_reuse_check(svc):
    run = svc.inspect("sep.csv", SEPARATED)
    fixed = SEP_DRAFT.model_copy(update={"skip_row_patterns": [r"^\*+\s*$"]})
    assert svc.dry_run(run["prove_id"], fixed, SEP_CLAIMS)["report"]["passed"]
    run_state = svc._runs[run["prove_id"]]
    run_state.passing = (SEP_DRAFT, SEP_CLAIMS)  # a proof that no longer holds
    with pytest.raises(CodedError) as err:
        svc.stage(run["prove_id"], "proof")
    assert err.value.code == "E1081" and "reuse" in str(err.value)


@pytest.mark.parametrize("name", sorted(REFERENCE))
def test_every_reference_proof_survives_reuse(tmp_path, name):
    ref = REFERENCE[name]
    app = create_app(storage_dir=tmp_path / "s", clock=lambda: date(2026, 10, 16))
    client = TestClient(app)
    if ref["open"]:
        client.post("/api/accounts", json={"accounts": ref["open"]})
    svc = AgentIngestService(
        import_service=app.state.import_service,
        template_store=app.state.template_store,
        review_store=app.state.review_store,
        ledger=app.state.ledger,
        clock=lambda: date(2026, 10, 16),
    )
    raw = (FIX / f"{name}.csv").read_bytes()
    run = svc.inspect(f"{name}.csv", raw)
    out = svc.dry_run(
        run["prove_id"],
        TemplateFields(**ref["draft"]),
        Claims(**ref["claims"]),
    )
    assert out["report"]["passed"], out["report"]
    assert _check(out, "reuse")["status"] == "pass"
    svc.stage(run["prove_id"], "proof")
    again = svc.inspect(f"{name}-2.csv", raw)
    assert again["template_match"] is not None
    assert svc.stage(again["prove_id"], "template_match")["report"]["passed"]


TWINS = (
    b"Date,Narration,Withdrawal Amt.,Deposit Amt.\n"
    b"2026-03-05,TEA STALL,20.00,\n"
    b"2026-03-05,TEA STALL,20.00,\n"
)


def test_identical_rows_settle_after_one_dedup_change(svc):
    from backend.app.service.reports.health.ledger_view import all_transactions

    claims = Claims(first_data_row=1, last_data_row=2)
    a = svc.inspect("t.csv", TWINS)
    assert svc.dry_run(a["prove_id"], GOOD, claims)["report"]["passed"]
    first = svc.stage(a["prove_id"], "proof")
    b = svc.inspect("t2.csv", TWINS)
    second = svc.stage(b["prove_id"], "template_match")
    svc.commit(first["batch_id"])
    booked = len(all_transactions(svc._ledger))
    assert _code(lambda: svc.commit(second["batch_id"])) == "E1065"
    twins = svc._review.get_batch(second["batch_id"])
    assert [c.dedup_status.value for c in twins] == ["exact_dup", "exact_dup"]
    svc.commit(second["batch_id"])
    assert len(all_transactions(svc._ledger)) == booked


def test_a_header_that_moved_down_still_matches_its_template(svc):
    a = svc.inspect("h.csv", HDFC)
    svc.dry_run(a["prove_id"], GOOD, CLAIMS)
    fp = svc.stage(a["prove_id"], "proof")["fingerprint"]
    shifted = b"Statement of account\n" + HDFC
    run = svc.inspect("shifted.csv", shifted)
    match = run["template_match"]
    assert match is not None and match["fingerprint"] == fp
    assert match["header_row_index"] == 1
    staged = svc.stage(run["prove_id"], "template_match")
    assert staged["report"]["passed"] and len(staged["candidates"]) == 2
    assert svc._templates.get_by_fingerprint(fp).header_row_index == 0


@pytest.mark.parametrize("pattern", ["", ".*"])
def test_catch_all_skip_pattern_is_a_failed_template_check(svc, pattern):
    run = svc.inspect("h.csv", HDFC)
    out = svc.dry_run(
        run["prove_id"],
        GOOD.model_copy(update={"skip_row_patterns": [pattern]}),
        CLAIMS,
    )
    _template_check(out)
    assert "skip_row_patterns" in out["report"]["checks"][0]["message"]
    assert out["attempt"] == 1


@pytest.mark.parametrize(
    "name, raw",
    [
        ("empty.csv", b""),
        ("corrupt.xlsx", b"not a workbook"),
        ("truncated.xlsx", b"PK\x03\x04garbage"),
    ],
)
def test_inspect_reports_an_unreadable_file_instead_of_failing(svc, name, raw):
    out = svc.inspect(name, raw)
    assert out["total_rows"] == 0 and out["guess"] is None
    assert out["template_match"] is None and out["dedicated"] is None
    assert isinstance(out["error"], str) and out["error"]
    pid = out["prove_id"]
    assert _code(lambda: svc.peek(pid)) == "E1002"
    assert _code(lambda: svc.dry_run(pid, GOOD, CLAIMS)) == "E1002"
    assert _code(lambda: svc.stage(pid, "template_match")) == "E1002"


def test_inspect_of_a_readable_file_has_no_error(svc):
    assert svc.inspect("h.csv", HDFC)["error"] is None


def test_inspect_route_does_not_500_on_an_empty_file(tmp_path):
    import io

    client = TestClient(
        create_app(storage_dir=tmp_path / "s", clock=lambda: date(2026, 10, 16)),
        raise_server_exceptions=False,
    )
    resp = client.post(
        "/api/agent/inspect", files={"file": ("e.csv", io.BytesIO(b""), "text/csv")}
    )
    assert resp.status_code == 200 and resp.json()["error"]
    peek = client.get(f"/api/agent/peek/{resp.json()['prove_id']}")
    assert peek.status_code == 422 and peek.json()["code"] == "E1002"


def test_sign_column_absent_from_the_header_is_a_failed_template_check(svc):
    # feat/import's E1052 direction-column assertion now runs inside
    # parse_statement_report; on the agent path it must surface as a
    # repairable failed "template" check, not a raise.
    run = svc.inspect("h.csv", HDFC)
    bad = GOOD.model_copy(
        update={
            "debit_column": None,
            "credit_column": None,
            "amount_column": "Withdrawal Amt.",
            "amount_sign": "indicator",
            "sign_column": "Dr/Cr",  # not in the HDFC header
        }
    )
    out = svc.dry_run(run["prove_id"], bad, CLAIMS)
    _template_check(out)
    assert "E1052" in out["report"]["checks"][0]["message"]
    assert out["attempt"] == 1 and out["fingerprint"] is not None
