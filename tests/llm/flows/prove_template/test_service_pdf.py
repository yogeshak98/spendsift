"""PDF card statements through the agent path: inspect -> peek -> dry-run ->
stage -> commit, and the shared `ImportService.stage_pdf`."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from beancount.core import data
from fastapi.testclient import TestClient
from fpdf import FPDF

from backend.app.llm.flows.prove_template.service import AgentIngestService
from backend.app.server.main import create_app
from backend.app.core.errors.errors import CodedError
from backend.app.models.storage.statement_record import STATEMENT_TYPE
from tests.service.ingest.pdf_fixtures import Row, make_statement_pdf
from tests.service.ingest.pdf_reference import HDFC_LAST4, HDFC_PDF_DRAFT, hdfc_template

TODAY = date(2026, 10, 24)
CARD = "Liabilities:CreditCard:HDFC"
# Previous 8,200.00 - payments 8,200.00 + purchases 15,345.00 = total 15,345.00.
ROWS = [
    Row("21/09/2026", "14:22", "ACME STORE BENGALURU", "10,000.00"),
    Row("22/09/2026", "10:00", "PAYMENT RECEIVED THANK YOU", "8,200.00", credit=True),
    Row("23/09/2026", "11:30", "INITECH FOODS", "5,000.00", bar_apart=True),
    Row("24/09/2026", "11:30", "UMBRELLA PHARMA", "345.00", rewards=3),
]
TWIN = Row("25/09/2026", "09:15", "GLOBEX CAFE", "120.00")


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
        pdf_template_store=app.state.pdf_template_store,
        review_store=app.state.review_store,
        ledger=app.state.ledger,
        clock=lambda: TODAY,
    )


def _code(fn) -> str:
    with pytest.raises(CodedError) as err:
        fn()
    return err.value.code


def _pdf(tmp_path: Path, name: str = "s.pdf", **kw) -> bytes:
    kw.setdefault("domestic", ROWS)
    return make_statement_pdf(tmp_path / name, **kw).read_bytes()


def _proven(svc, tmp_path, **kw) -> tuple[dict, dict]:
    run = svc.inspect("s.pdf", _pdf(tmp_path, **kw))
    out = svc.dry_run_pdf(run["prove_id"], HDFC_PDF_DRAFT)
    assert out["report"]["passed"], out["report"]
    return run, out


def _statement_records(ledger) -> int:
    return sum(
        1
        for e in ledger.load().entries
        if isinstance(e, data.Custom) and e.type == STATEMENT_TYPE
    )


def test_inspect_encrypted_pdf_returns_unlock_command_and_no_run_work(svc, tmp_path):
    raw = _pdf(tmp_path, password="Zq9-secret")
    out = svc.inspect("locked.pdf", raw)
    assert out["format"] == "pdf"
    assert out["encrypted"] is True
    assert out["error"] == (
        "the PDF is password-protected. Unlock it in your own terminal, not in"
        " Claude Code: uv run python scripts/unlock_pdf.py locked.pdf — then"
        " run /ingest-stmt on the unlocked copy it prints."
    )
    assert out["template_match"] is None and out["guess"] is None
    assert _code(lambda: svc.peek(out["prove_id"])) == "E1002"
    assert _code(lambda: svc.dry_run_pdf(out["prove_id"], HDFC_PDF_DRAFT)) == "E1002"


def test_inspect_textless_pdf_reports_error(svc, tmp_path):
    pdf = FPDF(unit="pt", format="A4")
    pdf.add_page()
    out = svc.inspect("scan.pdf", bytes(pdf.output()))
    assert out["format"] == "pdf" and out["encrypted"] is False
    assert (
        out["error"]
        == "the PDF has no selectable text (scanned PDFs are not supported)"
    )
    assert _code(lambda: svc.peek(out["prove_id"])) == "E1002"


def test_inspect_readable_pdf_fields_and_csv_gains_format(svc, tmp_path):
    out = svc.inspect("s.pdf", _pdf(tmp_path))
    assert out["format"] == "pdf" and out["encrypted"] is False
    assert out["error"] is None and out["guess"] is None and out["dedicated"] is None
    assert out["pages"] == 2 and out["total_rows"] > len(ROWS)
    assert out["template_match"] is None
    csv = svc.inspect(
        "h.csv", Path("tests/fixtures/statements/hdfc_bank.csv").read_bytes()
    )
    assert csv["format"] == "table" and csv["encrypted"] is False


def test_pdf_peek_dispatch(svc, tmp_path):
    run = svc.inspect("s.pdf", _pdf(tmp_path))
    out = svc.peek(run["prove_id"])
    assert out["format"] == "pdf" and out["pages"] == 2
    assert all({"row", "page", "text"} <= set(r) for r in out["head"])
    window = svc.peek(run["prove_id"], (0, 3))
    assert window["window"] is True and [r["row"] for r in window["head"]] == [
        0,
        1,
        2,
        3,
    ]


def test_dry_run_pdf_pass_then_stage_proof_saves_template_and_batch_statement(
    svc, app, tmp_path
):
    run, out = _proven(svc, tmp_path)
    assert out["attempt"] == 1 and out["attempts_left"] == 4 and out["kind"] == "card"
    assert out["fingerprint"] == hdfc_template().fingerprint
    assert out["statement"]["period_start"] == "2026-09-21"
    assert out["statement"]["period_end"] == "2026-10-20"
    total = out["statement"]["roles"]["total_due"]
    assert total == {"label": "TOTAL AMOUNT DUE", "value": "15345.00"}
    assert len(out["candidates"]) == len(ROWS)

    staged = svc.stage(run["prove_id"], "proof")
    assert staged["source"] == "pdf_template" and staged["template_saved"] is True
    assert staged["replaced"] is None
    assert staged["statement"] == out["statement"]
    saved = app.state.pdf_template_store.get_by_fingerprint(staged["fingerprint"])
    assert saved is not None and saved.origin == "agent"
    assert app.state.template_store.list() == []  # the CSV store is untouched
    statement = app.state.review_store.statement(staged["batch_id"])
    assert statement is not None and statement.account == CARD
    assert statement.period_end == date(2026, 10, 20)
    review = app.state.import_service.get_review(staged["batch_id"])
    assert review["statement"]["period_end"] == "2026-10-20"
    # A second proof of the same layout refuses to overwrite it.
    again, _ = _proven(svc, tmp_path, name="t.pdf")
    assert _code(lambda: svc.stage(again["prove_id"], "proof")) == "E1064"


@pytest.mark.parametrize(
    ("card_pattern", "where"),
    [
        # A year after a label is not a card number.
        # Lines 2 and 3: the card and the 16-digit customer number.
        (r"Billing Period.*?(?P<last4>20\d\d)", "card-shaped text on lines 2, 3"),
        (r"(?<=Account: )(?P<last4>\d{4})", "card-shaped text on lines 2, 3"),
    ],
)
def test_dry_run_pdf_without_one_card_number_fails_the_template_check(
    svc, tmp_path, card_pattern, where
):
    run = svc.inspect("s.pdf", _pdf(tmp_path))
    draft = HDFC_PDF_DRAFT.model_copy(update={"card_pattern": card_pattern})
    out = svc.dry_run_pdf(run["prove_id"], draft)
    assert out["report"]["passed"] is False
    failing = [c for c in out["report"]["checks"] if c["status"] == "fail"]
    assert [(c["id"], c["name"]) for c in failing] == [(17, "card_number")]
    message = failing[0]["message"]
    assert message == f"expected one card number; {where}"
    assert not any(ch.isdigit() for ch in message.split(";")[0])
    assert HDFC_LAST4 not in message
    assert out["fingerprint"] is None and run["prove_id"] == out["prove_id"]
    assert out["attempt"] == 1 and svc._runs[run["prove_id"]].passing is None


def test_dry_run_pdf_with_two_card_numbers_fails_check_17(svc, tmp_path):
    raw = _pdf(tmp_path, extra_lines={0: ["Card No: XXXX XXXX XXXX 5678"]})
    run = svc.inspect("s.pdf", raw)
    out = svc.dry_run_pdf(run["prove_id"], HDFC_PDF_DRAFT)
    failing = [c for c in out["report"]["checks"] if c["status"] == "fail"]
    assert [c["name"] for c in failing] == ["card_number"]
    message = failing[0]["message"]
    assert message.startswith("expected one card number; card-shaped text on lines")
    assert "1234" not in message and "5678" not in message
    assert out["fingerprint"] is None and out["attempt"] == 1


@pytest.mark.parametrize(
    ("mutate", "named"),
    [
        (lambda d: d.update(total_due_roundng="rupee"), "total_due_roundng"),
        (lambda d: d.update(description_arround=True), "description_arround"),
        (
            lambda d: d["summary"]["total_due"].update(offset=2),
            "summary.total_due.offset",
        ),
        (
            lambda d: d["manual_sections"][0].update(stop="x"),
            "manual_sections[0].stop",
        ),
    ],
    ids=["top", "option", "summary-role", "manual-section"],
)
def test_dry_run_pdf_refuses_an_unknown_field_by_name(svc, tmp_path, mutate, named):
    run = svc.inspect("s.pdf", _pdf(tmp_path))
    draft = HDFC_PDF_DRAFT.model_dump(mode="json")
    mutate(draft)
    out = svc.dry_run_pdf(run["prove_id"], draft)
    assert out["report"]["passed"] is False
    (check,) = out["report"]["checks"]
    assert (check["id"], check["name"]) == (0, "template")
    assert "unknown field" in check["message"] and named in check["message"]
    assert out["attempt"] == 1  # the attempt is spent: fix the draft and retry


def test_stored_template_with_a_stray_key_still_loads(app):
    """The unknown-field refusal is on the draft path only; a stored file
    keeps loading."""
    import yaml

    store = app.state.pdf_template_store
    template = hdfc_template()
    data = template.model_dump()
    data["written_by_a_later_version"] = True
    (store.dir / f"{template.fingerprint}.yaml").write_text(yaml.safe_dump(data))
    assert [t.fingerprint for t in store.list()] == [template.fingerprint]


def test_dry_run_pdf_template_error_is_a_failed_template_check(svc, app, tmp_path):
    run = svc.inspect("s.pdf", _pdf(tmp_path))
    bad = HDFC_PDF_DRAFT.model_dump()
    bad["row_pattern"] = "(?P<date>"
    out = svc.dry_run_pdf(run["prove_id"], bad)
    assert out["report"]["passed"] is False
    assert [(c["id"], c["name"]) for c in out["report"]["checks"]] == [(0, "template")]
    assert "row_pattern" in out["report"]["checks"][0]["message"]
    assert out["attempt"] == 1 and out["candidates"] == []
    # Over HTTP it is the same failed check, not a 422.
    client = TestClient(app)
    http_run = client.post(
        "/api/agent/inspect",
        files={"file": ("s.pdf", _pdf(tmp_path, name="h.pdf"), "application/pdf")},
    ).json()
    resp = client.post(
        "/api/agent/pdf/dry-run",
        json={"prove_id": http_run["prove_id"], "draft": bad},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["report"]["checks"][0]["name"] == "template"
    # A table run is not a PDF run.
    csv = svc.inspect(
        "h.csv", Path("tests/fixtures/statements/hdfc_bank.csv").read_bytes()
    )
    assert _code(lambda: svc.dry_run_pdf(csv["prove_id"], HDFC_PDF_DRAFT)) == "E1079"


def test_stage_template_match_on_second_inspect_needs_zero_dry_runs(svc, tmp_path):
    run, _ = _proven(svc, tmp_path)
    svc.stage(run["prove_id"], "proof")
    again = svc.inspect("next.pdf", _pdf(tmp_path, name="next.pdf"))
    fp = hdfc_template().fingerprint
    assert again["template_match"] == {
        "fingerprint": fp,
        "institution": "HDFC",
        "origin": "agent",
    }
    staged = svc.stage(again["prove_id"], "template_match")
    assert staged["report"]["passed"] and staged["template_saved"] is False
    assert staged["source"] == "pdf_template" and staged["fingerprint"] == fp
    assert svc._runs[again["prove_id"]].attempts == 0
    assert _code(lambda: svc.stage(again["prove_id"], "dedicated")) == "E1063"


def test_template_match_without_a_saved_template_is_not_matched(svc, tmp_path):
    run = svc.inspect("s.pdf", _pdf(tmp_path))
    assert _code(lambda: svc.stage(run["prove_id"], "template_match")) == "E1063"
    assert _code(lambda: svc.stage(run["prove_id"], "proof")) == "E1062"


def test_stage_refuses_on_summary_mismatch_with_e1061(svc, app, tmp_path):
    run, _ = _proven(svc, tmp_path)
    svc.stage(run["prove_id"], "proof")
    batches_before = dict(app.state.review_store._batches)
    wrong = _pdf(tmp_path, name="wrong.pdf", summary={"total_due": "C 99,999.00"})
    again = svc.inspect("wrong.pdf", wrong)
    assert again["template_match"] is not None
    with pytest.raises(CodedError) as err:
        svc.stage(again["prove_id"], "template_match")
    assert err.value.code == "E1081"
    assert "summary_equation: " in str(err.value)
    assert app.state.review_store._batches == batches_before
    # Over HTTP: a non-2xx status and still no batch.
    client = TestClient(app)
    http_run = client.post(
        "/api/agent/inspect",
        files={"file": ("wrong.pdf", wrong, "application/pdf")},
    ).json()
    resp = client.post(
        "/api/agent/stage",
        json={"prove_id": http_run["prove_id"], "use": "template_match"},
    )
    assert resp.status_code == 422 and resp.json()["code"] == "E1081"
    assert app.state.review_store._batches == batches_before


def test_emi_panel_stages_with_the_warning_and_commits_rows_and_record_once(
    svc, app, tmp_path
):
    run, out = _proven(svc, tmp_path, emi_lines=["EMI 3 OF 12 C 2,000.00"])
    assert out["report"]["passed"]
    warning = next(
        c["message"] for c in out["report"]["checks"] if c["name"] == "manual_sections"
    )
    assert warning.startswith("this statement has a panel called EMI (lines ")
    assert warning in out["report"]["warnings"]
    staged = svc.stage(run["prove_id"], "proof")
    assert len(staged["candidates"]) == len(ROWS)
    assert warning in staged["report"]["warnings"]
    result = svc.commit(staged["batch_id"])
    assert result["committed"] == len(ROWS)
    assert result["statement_recorded"] is True
    assert _statement_records(app.state.ledger) == 1


def test_emi_warning_is_in_the_http_stage_response_report(app, tmp_path):
    client = TestClient(app)
    raw = _pdf(tmp_path, emi_lines=["EMI 3 OF 12 C 2,000.00"])
    run = client.post(
        "/api/agent/inspect", files={"file": ("s.pdf", raw, "application/pdf")}
    ).json()
    out = client.post(
        "/api/agent/pdf/dry-run",
        json={
            "prove_id": run["prove_id"],
            "draft": HDFC_PDF_DRAFT.model_dump(mode="json"),
        },
    ).json()
    assert out["report"]["passed"], out["report"]
    warning = next(
        c["message"] for c in out["report"]["checks"] if c["name"] == "manual_sections"
    )
    resp = client.post(
        "/api/agent/stage", json={"prove_id": run["prove_id"], "use": "proof"}
    )
    assert resp.status_code == 200, resp.text
    assert warning in resp.json()["report"]["warnings"]


def test_commit_writes_rows_and_one_record_then_reimport_writes_nothing(
    svc, app, tmp_path
):
    rows = [*ROWS, TWIN, TWIN]  # two identical lines: both are real purchases
    run, _ = _proven(svc, tmp_path, domestic=rows)
    staged = svc.stage(run["prove_id"], "proof")
    assert len(staged["candidates"]) == len(rows)
    first = svc.commit(staged["batch_id"])
    assert first["committed"] == len(rows)
    assert first["statement_recorded"] is True

    again = svc.inspect("again.pdf", _pdf(tmp_path, name="again.pdf", domestic=rows))
    restaged = svc.stage(again["prove_id"], "template_match")
    second = svc.commit(restaged["batch_id"])
    assert second["committed"] == 0
    assert _statement_records(app.state.ledger) == 1
    assert second["statement_recorded"] is False


def test_bank_account_template_is_refused_kind_mismatch(app, tmp_path):
    imports = app.state.import_service
    path = make_statement_pdf(tmp_path / "s.pdf", domestic=ROWS)
    template = hdfc_template()
    bank = template.model_copy(update={"default_account": "Assets:Checking:HDFC"})
    assert _code(lambda: imports.stage_pdf(path, bank)) == "E1079"
    unknown = template.model_copy(
        update={"default_account": "Liabilities:CreditCard:NOPE"}
    )
    assert _code(lambda: imports.stage_pdf(path, unknown)) == "E1077"
    assert app.state.review_store._batches == {}
    staged = imports.stage_pdf(path, template)
    assert staged["source"] == "pdf_template" and staged["batch_id"]


def test_bank_kind_hint_on_a_pdf_is_a_kind_mismatch(svc, tmp_path):
    run = svc.inspect("s.pdf", _pdf(tmp_path), kind="bank")
    assert _code(lambda: svc.dry_run_pdf(run["prove_id"], HDFC_PDF_DRAFT)) == "E1079"


def test_upgrade_replaces_the_old_pdf_template(svc, app, tmp_path):
    run, _ = _proven(svc, tmp_path)
    old = svc.stage(run["prove_id"], "proof")["fingerprint"]
    store = app.state.pdf_template_store
    assert _code(lambda: svc.inspect("s.pdf", _pdf(tmp_path), upgrade="0" * 16)) == (
        "E1005"
    )
    up = svc.inspect("u.pdf", _pdf(tmp_path, name="u.pdf"), upgrade=old)
    changed = HDFC_PDF_DRAFT.model_copy(
        update={
            "section_start": [*HDFC_PDF_DRAFT.section_start, "^Domestic Transactions$"]
        }
    )
    out = svc.dry_run_pdf(up["prove_id"], changed)
    assert out["report"]["passed"], out["report"]
    assert out["fingerprint"] != old
    staged = svc.stage(up["prove_id"], "proof")
    assert staged["replaced"] == old and staged["template_saved"] is True
    assert store.get_by_fingerprint(old) is None
    assert store.get_by_fingerprint(staged["fingerprint"]) is not None


def test_table_dry_run_on_a_pdf_run_is_a_kind_mismatch_and_costs_nothing(svc, tmp_path):
    from backend.app.models.api.agent import Claims
    from tests.llm.flows.prove_template.test_service import GOOD

    run = svc.inspect("s.pdf", _pdf(tmp_path))
    claims = Claims(first_data_row=1, last_data_row=2)
    assert _code(lambda: svc.dry_run(run["prove_id"], GOOD, claims)) == "E1079"
    assert svc._runs[run["prove_id"]].attempts == 0


# --- F1: no raw holder text in dry-run windows or failing samples -----------

_HOLDER_WORDS = ("ASHA", "RAO", "LAKE", "BENGALURU", "560001")
# Rows with no holder word in them, so any hit below is holder text.
_PLAIN_ROWS = [
    Row("21/09/2026", "14:22", "ACME STORE", "10,000.00"),
    Row("22/09/2026", "10:00", "PAYMENT RECEIVED THANK YOU", "8,200.00", credit=True),
    Row("23/09/2026", "11:30", "INITECH FOODS", "5,345.00"),
]


def _holder_hits(text: str) -> list[str]:
    return [w for w in _HOLDER_WORDS if w in text]


def test_dry_run_window_and_stored_result_never_show_holder_text(svc, tmp_path):
    import json

    from backend.app.cli.render import render_window

    run, out = _proven(svc, tmp_path, domestic=_PLAIN_ROWS)
    window = svc.dry_run_window(run["prove_id"], 0, 5)
    reasons = [s["reason"] for s in window["skipped_rows"]]
    assert reasons[:2] == ["outside_section", "outside_section"]  # the holder lines
    assert _holder_hits(render_window(window)) == []
    assert _holder_hits(json.dumps(svc._runs[run["prove_id"]].last_result)) == []
    assert _holder_hits(json.dumps(out["skipped_rows"])) == []
    # A transaction-shaped line keeps its text (masked only), as in peek.
    assert any("ACME STORE" in c["raw_narration"] for c in out["candidates"])


def test_failing_check_samples_never_show_holder_text(svc, tmp_path):
    from backend.app.cli.render import render_dry_run

    run = svc.inspect("s.pdf", _pdf(tmp_path, domestic=_PLAIN_ROWS))
    # A section that opens on the holder's name line puts the address line
    # inside the table, so coverage fails and samples it.
    bad = HDFC_PDF_DRAFT.model_copy(update={"section_start": [r"^[A-Z]+ [A-Z]+$"]})
    out = svc.dry_run_pdf(run["prove_id"], bad)
    coverage = next(c for c in out["report"]["checks"] if c["name"] == "coverage")
    assert coverage["status"] == "fail" and 1 in coverage["rows"]
    rendered = render_dry_run(out)
    assert '"reason":"no_match"' in rendered
    assert _holder_hits(rendered) == []


# --- F2: the unlock command works from the repo root and is safe to paste ---

_UNLOCK = ["uv", "run", "python", "scripts/unlock_" + "pdf.py"]


def _unlock_argv(error: str) -> list[str]:
    import shlex

    return shlex.split(_unlock_command(error))


def _unlock_command(error: str) -> str:
    return error.split("not in Claude Code: ", 1)[1].rsplit(" — then run", 1)[0]


def _shell_argv(command: str, cwd: Path) -> list[str]:
    """The words a real POSIX shell makes of the pasted command, without
    running it: `printf` echoes each word. Any substitution would run here,
    in `cwd`, and leave a file behind."""
    import subprocess

    out = subprocess.run(
        ["/bin/sh", "-c", f"printf '%s\\0' {command}"],
        cwd=cwd,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return out.split("\0")[:-1]


@pytest.mark.parametrize(
    "source",
    [
        "/Users/me/My Statements/HDFC card oct.pdf",
        "/Users/me/Downloads/$(touch x).pdf",
        "/Users/me/Downloads/a`touch x`b.pdf",
        '/Users/me/Downloads/say "hi" now.pdf',
        "/Users/me/Downloads/it's; touch y.pdf",
    ],
)
def test_unlock_command_carries_the_original_path_shell_quoted(svc, tmp_path, source):
    raw = _pdf(tmp_path, password="Zq9-secret")
    out = svc.inspect(Path(source).name, raw, source_path=source)
    assert out["encrypted"] is True
    assert _unlock_argv(out["error"]) == [*_UNLOCK, source]
    sandbox = tmp_path / "shell"
    sandbox.mkdir()
    assert _shell_argv(_unlock_command(out["error"]), sandbox) == [*_UNLOCK, source]
    assert list(sandbox.iterdir()) == []  # nothing ran
    # The path is used for the message only: nothing on disk carries it.
    stored = [p.name for p in svc._imports_dir.iterdir()]
    assert stored == [out["stored_name"]] and source not in out["stored_name"]


def test_unlock_command_without_a_source_path_quotes_the_filename(svc, tmp_path):
    raw = _pdf(tmp_path, password="Zq9-secret")
    out = svc.inspect("card $(touch x).pdf", raw)
    assert _unlock_argv(out["error"]) == [*_UNLOCK, "card $(touch x).pdf"]


def test_cli_port_sends_the_full_path_and_the_route_uses_it(app, tmp_path):
    from backend.app.cli.port import HttpIngestPort

    folder = tmp_path / "My Statements"
    folder.mkdir()
    locked = _pdf(tmp_path, password="Zq9-secret")
    pdf = folder / "card `oct`.pdf"
    pdf.write_bytes(locked)
    out = HttpIngestPort(client=TestClient(app)).inspect(pdf, None, None)
    assert out["encrypted"] is True
    assert _unlock_argv(out["error"]) == [*_UNLOCK, str(pdf)]
    assert str(folder) not in out["stored_name"]


# --- F4: the agent's commit re-checks the rows against the statement ---------


def test_agent_commit_refuses_an_edited_batch_with_e1061(svc, app, tmp_path):
    run, _ = _proven(svc, tmp_path)
    staged = svc.stage(run["prove_id"], "proof")
    batch = staged["batch_id"]
    first = staged["candidates"][0]["id"]
    app.state.import_service.edit(batch, first, {"amount": "1.00"})
    journal = app.state.ledger.journal_dir
    before = {p.name: p.read_text() for p in journal.glob("*.beancount")}
    assert "main.beancount" in before  # the glob found the journal
    with pytest.raises(CodedError) as err:
        svc.commit(batch)
    assert err.value.code == "E1081"
    assert "debits" in str(err.value)
    assert {p.name: p.read_text() for p in journal.glob("*.beancount")} == before
    assert _statement_records(app.state.ledger) == 0


def test_agent_path_proves_and_stages_the_measured_layout(svc, tmp_path):
    """F6: glued `21/09/2026|`, a trailing PI `l` and 2pt jitter."""
    from tests.service.ingest.parsers.test_pdf_parser import MEASURED

    run, out = _proven(
        svc, tmp_path, domestic=MEASURED, jitter=2.0, whole_rupee_total=True
    )
    assert len(out["candidates"]) == len(MEASURED)
    staged = svc.stage(run["prove_id"], "proof")
    assert len(staged["candidates"]) == len(MEASURED)


def test_generated_finance_charge_row_stays_uncategorised(svc, app, tmp_path):
    """The row generated from the summary is left for the user to
    categorise, even though the seed dictionary would match its narration."""
    summary = {"finance_charges": "C 100.00", "total_due": "C 15,445.00"}
    run, _ = _proven(svc, tmp_path, summary=summary)
    staged = svc.stage(run["prove_id"], "proof")
    (generated,) = [
        c
        for c in staged["candidates"]
        if "generated from the statement summary" in c["flags"]
    ]
    assert generated["suggested_category"] is None
    stored = app.state.review_store.get_batch(staged["batch_id"])
    assert [c.suggested_category for c in stored if c.id == generated["id"]] == [None]


# --- F10/M2: summary labels inside check 13/14 messages are redacted ---------

# A label regex that matches a personal line: the holder's name printed beside
# the previous dues (fictional), on the first page outside the table.
_NAMED_DUES = "ASHA RAO DUES"


def _named_label_case(tmp_path, name: str):
    draft = HDFC_PDF_DRAFT.model_copy(
        update={
            "summary": {
                **HDFC_PDF_DRAFT.summary,
                "previous_balance": HDFC_PDF_DRAFT.summary[
                    "previous_balance"
                ].model_copy(update={"label": _NAMED_DUES, "line_offset": 0}),
            },
            # The extra line sits below the table; end the section before it.
            "section_end": "^(TRANSACTIONS TOTAL AMOUNT|ASHA)",
        }
    )
    raw = _pdf(
        tmp_path,
        name=name,
        summary={"total_due": "C 99,999.00"},  # the equation fails
        extra_lines={0: [f"{_NAMED_DUES} C 8,200.00"]},
    )
    return draft, raw


def test_dry_run_check_messages_redact_summary_labels(svc, tmp_path):
    from backend.app.cli.render import render_dry_run

    draft, raw = _named_label_case(tmp_path, "n.pdf")
    run = svc.inspect("n.pdf", raw)
    out = svc.dry_run_pdf(run["prove_id"], draft)
    equation = next(c for c in out["report"]["checks"] if c["id"] == 13)
    assert equation["status"] == "fail" and _NAMED_DUES in equation["message"]
    rendered = render_dry_run(out)
    assert '"summary_equation"' in rendered
    assert _holder_hits(rendered) == []


def test_agent_stage_refusal_redacts_summary_labels(svc, app, tmp_path):
    draft, raw = _named_label_case(tmp_path, "n.pdf")
    app.state.pdf_template_store.save(draft.to_template(card_last4=HDFC_LAST4))
    run = svc.inspect("n.pdf", raw)
    assert run["template_match"] is not None
    with pytest.raises(CodedError) as err:
        svc.stage(run["prove_id"], "template_match")
    assert err.value.code == "E1081" and "summary_equation" in str(err.value)
    assert _holder_hits(str(err.value)) == []
    # The browser shows the user's own document: its message keeps the label.
    with pytest.raises(CodedError) as browser:
        app.state.import_service.detect("n.pdf", raw)
    assert browser.value.code == "E1070" and _NAMED_DUES in str(browser.value)


# --- One template per card: derive, upgrade guard, shared-account guard -----

CARD2 = "Liabilities:CreditCard:HDFC2"
SECOND = "XXXX XXXX XXXX 5678"


def _open(app, *accounts: str) -> None:
    resp = TestClient(app).post("/api/accounts", json={"accounts": list(accounts)})
    assert resp.status_code < 300, resp.text


def _no_card_digits(text: str) -> None:
    assert "1234" not in text and "5678" not in text


def test_dry_run_and_stage_fingerprints_agree(svc, tmp_path):
    run, out = _proven(svc, tmp_path)
    staged = svc.stage(run["prove_id"], "proof")
    assert out["fingerprint"] == staged["fingerprint"] == hdfc_template().fingerprint
    other = svc.inspect("o.pdf", _pdf(tmp_path, name="o.pdf", card=SECOND))
    draft = HDFC_PDF_DRAFT.model_copy(update={"default_account": CARD})
    out2 = svc.dry_run_pdf(other["prove_id"], draft)
    assert out2["fingerprint"] == hdfc_template(card_last4="5678").fingerprint
    assert out2["fingerprint"] != out["fingerprint"]


def test_second_card_same_layout_gets_its_own_template(svc, app, tmp_path):
    _open(app, CARD2)
    first_raw = _pdf(tmp_path, name="a.pdf")
    second_raw = _pdf(tmp_path, name="b.pdf", card=SECOND)
    first = svc.inspect("a.pdf", first_raw)
    assert svc.dry_run_pdf(first["prove_id"], HDFC_PDF_DRAFT)["report"]["passed"]
    fp1 = svc.stage(first["prove_id"], "proof")["fingerprint"]

    second = svc.inspect("b.pdf", second_raw)
    assert second["template_match"] is None  # the first card's layout is not it
    draft2 = HDFC_PDF_DRAFT.model_copy(update={"default_account": CARD2})
    out = svc.dry_run_pdf(second["prove_id"], draft2)
    assert out["report"]["passed"], out["report"]
    staged = svc.stage(second["prove_id"], "proof")
    fp2 = staged["fingerprint"]
    assert fp2 == out["fingerprint"] and fp2 != fp1 and staged["replaced"] is None

    store = app.state.pdf_template_store
    assert {t.fingerprint for t in store.list()} == {fp1, fp2}
    assert store.get_by_fingerprint(fp2).default_account == CARD2
    assert svc.inspect("a2.pdf", first_raw)["template_match"]["fingerprint"] == fp1
    assert svc.inspect("b2.pdf", second_raw)["template_match"]["fingerprint"] == fp2


def test_upgrade_with_another_cards_statement_is_refused_and_deletes_nothing(
    svc, app, tmp_path
):
    _open(app, CARD2)
    run, _ = _proven(svc, tmp_path)
    old = svc.stage(run["prove_id"], "proof")["fingerprint"]
    store = app.state.pdf_template_store
    batches_before = dict(app.state.review_store._batches)

    # The second card's statement, on its own account, opened as an upgrade
    # of the first card's template.
    up = svc.inspect("u.pdf", _pdf(tmp_path, name="u.pdf", card=SECOND), upgrade=old)
    draft = HDFC_PDF_DRAFT.model_copy(update={"default_account": CARD2})
    out = svc.dry_run_pdf(up["prove_id"], draft)
    assert out["report"]["passed"], out["report"]
    with pytest.raises(CodedError) as err:
        svc.stage(up["prove_id"], "proof")
    assert err.value.code == "E1067"
    _no_card_digits(str(err.value))
    assert "different card" in str(err.value)
    # Ruling P14: the replaced-card route, not a new account.
    resolution = err.value.resolution
    assert "remove the old card's saved layout by hand" in resolution
    assert "<storage>/templates/pdf/" in resolution
    assert "with the account this card belongs to" in resolution
    assert "open a" not in resolution.lower()
    assert [t.fingerprint for t in store.list()] == [old]
    assert app.state.review_store._batches == batches_before


def test_shared_account_for_two_cards_is_refused(svc, app, tmp_path):
    run, _ = _proven(svc, tmp_path)
    old = svc.stage(run["prove_id"], "proof")["fingerprint"]
    store = app.state.pdf_template_store
    batches_before = dict(app.state.review_store._batches)

    second = svc.inspect("b.pdf", _pdf(tmp_path, name="b.pdf", card=SECOND))
    out = svc.dry_run_pdf(second["prove_id"], HDFC_PDF_DRAFT)  # the same account
    assert out["report"]["passed"], out["report"]
    with pytest.raises(CodedError) as err:
        svc.stage(second["prove_id"], "proof")
    assert err.value.code == "E1071" and err.value.status == 409
    assert str(err.value) == (
        f"E1071: {CARD} already belongs to another card's statement layout."
    )
    _no_card_digits(str(err.value))
    assert [t.fingerprint for t in store.list()] == [old]
    assert app.state.review_store._batches == batches_before
    # Over HTTP it is a 409 in the coded shape.
    client = TestClient(app)
    http_run = client.post(
        "/api/agent/inspect",
        files={
            "file": (
                "c.pdf",
                _pdf(tmp_path, name="c.pdf", card=SECOND),
                "application/pdf",
            )
        },
    ).json()
    dry = client.post(
        "/api/agent/pdf/dry-run",
        json={"prove_id": http_run["prove_id"], "draft": HDFC_PDF_DRAFT.model_dump()},
    )
    assert dry.status_code == 200 and dry.json()["report"]["passed"]
    resp = client.post(
        "/api/agent/stage", json={"prove_id": http_run["prove_id"], "use": "proof"}
    )
    assert resp.status_code == 409 and resp.json()["code"] == "E1071"
    # Ruling P14: a replaced card's route is to remove the old card's layout
    # by hand; a different card goes to its own account. Never a second
    # account for the same debt.
    assert resp.json()["resolution"] == (
        "If this card replaced an older card on the same account (a new card"
        " number), remove the old card's saved layout by hand: the file in"
        f" <storage>/templates/pdf/ whose default_account is {CARD} and whose"
        " card_last4 is the old card's last 4 digits. Then run /ingest-stmt again."
        " Otherwise run /ingest-stmt again with the account this card belongs to."
        " Never open a second account for the same card debt."
    )


def test_stage_refuses_when_the_card_differs_from_the_dry_run(
    svc, app, tmp_path, monkeypatch
):
    import backend.app.llm.flows.prove_template.service as service

    run, _ = _proven(svc, tmp_path)
    monkeypatch.setattr(service, "_derive_card_last4", lambda path, pattern: "5678")
    with pytest.raises(CodedError) as err:
        svc.stage(run["prove_id"], "proof")
    assert err.value.code == "E1081" and "card_number" in str(err.value)
    _no_card_digits(str(err.value))
    assert app.state.pdf_template_store.list() == []
    assert app.state.review_store._batches == {}
