from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.cli import ingest as cli
from backend.app.cli.port import HttpIngestPort
from backend.app.core.paths import REPO_ROOT
from backend.app.server.main import create_app


HDFC = Path("tests/fixtures/statements/hdfc_bank.csv")
PAYLOAD = json.dumps(
    {
        "draft": {
            "institution": "HDFC",
            "header_row_index": 0,
            "date_column": "Date",
            "date_format": "%Y-%m-%d",
            "narration_column": "Narration",
            "debit_column": "Withdrawal Amt.",
            "credit_column": "Deposit Amt.",
            "default_account": "Assets:Checking:HDFC",
        },
        "claims": {"first_data_row": 1, "last_data_row": 2},
    }
)


@pytest.fixture
def port(tmp_path):
    app = create_app(storage_dir=tmp_path / "s", clock=lambda: date(2026, 10, 16))
    return HttpIngestPort(client=TestClient(app))


def run(port, *argv, stdin=""):
    out = io.StringIO()
    code = cli.main(list(argv), port=port, stdin=io.StringIO(stdin), stdout=out)
    return code, out.getvalue()


def test_happy_path_and_reject_by_number(port):
    code, out = run(port, "inspect", str(HDFC), "--kind", "bank")
    assert code == 0
    pid = json.loads(out)["prove_id"]
    assert run(port, "peek", pid)[0] == 0
    code, out = run(port, "dry-run", pid, "--draft", "-", stdin=PAYLOAD)
    assert code == 0 and json.loads(out)["summary"]["passed"] is True
    code, out = run(port, "stage", pid, "--use", "proof")
    batch = json.loads(out)["batch_id"]
    assert run(port, "reject", batch, "1")[0] == 0
    code, out = run(port, "show", batch)
    assert json.loads(out)["counts"]["total"] == 1
    assert run(port, "commit", batch)[0] == 0


def test_failing_dry_run_exits_2(port):
    pid = json.loads(run(port, "inspect", str(HDFC))[1])["prove_id"]
    bad = PAYLOAD.replace("%Y-%m-%d", "%d/%m/%Y")
    assert run(port, "dry-run", pid, "--draft", "-", stdin=bad)[0] == 2


def test_unreachable_app_exits_3():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    port = HttpIngestPort(
        client=httpx.Client(
            transport=httpx.MockTransport(boom), base_url="http://127.0.0.1:8765"
        )
    )
    code, out = run(port, "accounts")
    assert code == 3 and "not running" in out


def test_lost_batch_after_restart_exits_4_with_guidance(port):
    code, out = run(port, "commit", "batch-from-before-restart")
    assert code == 4
    assert "/ingest-stmt" in out


def test_every_subcommand_output_is_capped(port):
    pid = json.loads(run(port, "inspect", str(HDFC))[1])["prove_id"]
    for argv in (("peek", pid), ("accounts",)):
        assert len(run(port, *argv)[1]) <= 20_000


def _staged(port) -> str:
    pid = json.loads(run(port, "inspect", str(HDFC), "--kind", "bank")[1])["prove_id"]
    run(port, "dry-run", pid, "--draft", "-", stdin=PAYLOAD)
    return json.loads(run(port, "stage", pid, "--use", "proof")[1])["batch_id"]


FP = "0123456789abcdef"


def _cand(i: int, narration: str, **extra) -> dict:
    return {
        "id": f"c{i}",
        "date": "2026-01-0%d" % i,
        "amount": "10.00",
        "direction": "debit",
        "raw_narration": narration,
        "source_account": "Assets:Checking:HDFC",
        "dedup_status": "new",
        "flags": [],
        "suggested_category": "X",
        **extra,
    }


class FakePort:
    """Records mutations; review() always reflects the current order."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def review(self, batch_id):
        self.calls.append(("review", batch_id))
        return {"candidates": list(self.rows)}

    def reject(self, batch_id, cid):
        self.calls.append(("reject", cid))
        self.rows = [c for c in self.rows if c["id"] != cid]
        return {"ok": True, "dropped": 1}

    def accounts(self):
        return {"accounts": ["Assets:Cash", "Assets:Checking:HDFC", "Expenses:Food"]}

    def edit(self, batch_id, cid, patch):
        self.calls.append(("edit", cid, patch))
        for i, c in enumerate(self.rows):
            if c["id"] == cid:  # the route edits the first row with the id
                self.rows[i] = {**c, **patch}
                break
        return {"ok": True}

    def learning(self, batch_id):
        return {"will_learn": [], "not_learned": [], "would_open": []}


def test_reject_two_numbers_drops_first_two_of_original_order():
    fp = FakePort([_cand(1, "A"), _cand(2, "B"), _cand(3, "C")])
    code, out = run(fp, "reject", "b", "1", "2")
    assert code == 0
    assert [c["id"] for c in fp.rows] == ["c3"]
    assert [c[0] for c in fp.calls].count("review") == 1
    body = json.loads(out)
    assert [r["narration"] for r in body["rejected"]] == ["A", "B"]
    assert [r["n"] for r in body["rejected"]] == [1, 2]
    assert body["renumbered"] is True and "show again" in body["hint"]


def test_reject_out_of_range_drops_nothing_and_exits_4():
    fp = FakePort([_cand(1, "A"), _cand(2, "B")])
    code, out = run(fp, "reject", "b", "1", "9")
    assert code == 4
    assert not [c for c in fp.calls if c[0] == "reject"]
    assert len(fp.rows) == 2 and "no row 9" in out


def test_reject_and_account_output_is_masked():
    fp = FakePort([_cand(1, "UPI 123456789012 a@b.com")])
    code, out = run(fp, "account", "b", "1", "Assets:Cash")
    assert code == 0
    assert "123456789012" not in out and "a@b.com" not in out
    assert fp.calls[-1] == (
        "edit",
        "c1",
        {"source_account": "Assets:Cash", "needs_account": False},
    )
    assert json.loads(out)["updated"][0]["account"] == "Assets:Cash"
    fp2 = FakePort([_cand(1, "UPI 123456789012 a@b.com")])
    code, out = run(fp2, "reject", "b", "1")
    assert "123456789012" not in out and "a@b.com" not in out


def test_reject_on_real_app_drops_first_two(port):
    batch = _staged(port)
    code, out = run(port, "reject", batch, "1", "2")
    assert code == 0
    assert json.loads(run(port, "show", batch)[1])["counts"]["total"] == 0


class _Err:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def accounts(self):
        from backend.app.cli.port import ApiError

        raise ApiError(self.status, self.body)


def test_validation_failed_code_exits_2_other_422_exits_1():
    assert run(_Err(422, {"detail": "x", "code": "E1081"}), "accounts")[0] == 2
    assert run(_Err(422, {"detail": "x", "code": "E1077"}), "accounts")[0] == 1


def test_unreachable_names_real_base_url():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    p = HttpIngestPort(
        client=httpx.Client(
            transport=httpx.MockTransport(boom), base_url="http://10.1.2.3:9999"
        )
    )
    code, out = run(p, "accounts")
    assert code == 3 and "http://10.1.2.3:9999" in out and "run_app.sh" in out


def test_argparse_error_exits_1_not_2():
    code, out = run(FakePort([]), "dry-run", "p", "--bogus")
    assert code == 1 and "error" in json.loads(out)


def test_help_still_exits_0():
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"], port=FakePort([]), stdout=io.StringIO())
    assert e.value.code == 0


def test_the_script_path_the_skill_names_still_runs():
    # .claude/settings.json and the ingest-stmt SKILL name this exact path.
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "scripts/ingest_cli.py", "accounts", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={"PATH": "", "PYTHONPATH": ""},
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("usage: ingest_cli accounts")


@pytest.mark.parametrize("stdin", ["[]", json.dumps({"draft": {}})])
def test_bad_draft_input_exits_1_without_traceback(stdin):
    code, out = run(FakePort([]), "dry-run", "p", "--draft", "-", stdin=stdin)
    assert code == 1 and "Traceback" not in out
    assert "draft JSON must be" in out


def test_malformed_draft_json_names_the_decode_error_not_the_text():
    stdin = '{"pdf_draft": {"institution": "SECRETNAME",\n  "date_format" "x"}}'
    code, out = run(FakePort([]), "dry-run", "p", "--draft", "-", stdin=stdin)
    message = json.loads(out)["error"]
    assert code == 1 and "Traceback" not in out
    assert "not valid JSON" in message
    assert "Expecting ':' delimiter" in message and "line 2 column" in message
    assert "SECRETNAME" not in out


def test_draft_missing_claims_names_the_missing_key():
    code, out = run(FakePort([]), "dry-run", "p", "--draft", "-", stdin='{"draft": {}}')
    assert code == 1 and "missing claims" in json.loads(out)["error"]


def test_missing_draft_file_exits_1_without_traceback(tmp_path):
    code, out = run(
        FakePort([]), "dry-run", "p", "--draft", str(tmp_path / "nope.json")
    )
    assert code == 1 and "Traceback" not in out and "cannot read" in out


def test_unexpected_exception_is_contained():
    class Boom(FakePort):
        def accounts(self):
            raise RuntimeError("kaput 123456789012")

    code, out = run(Boom([]), "accounts")
    assert code == 1 and "Traceback" not in out
    assert "RuntimeError" in out and "123456789012" not in out


def test_account_refuses_unopened_account_and_edits_nothing():
    fp = FakePort([_cand(1, "A")])
    code, out = run(fp, "account", "b", "1", "Assets:Nope")
    assert code == 1
    body = json.loads(out)
    assert "not open" in body["error"]
    assert "Assets:Cash" in body["open_like"]
    assert not [c for c in fp.calls if c[0] == "edit"]


def test_out_of_range_has_row_hint_not_restart_hint():
    code, out = run(FakePort([_cand(1, "A")]), "reject", "b", "5")
    assert code == 4 and "1..1" in out and "/ingest-stmt" not in out


def test_timeout_is_not_reported_as_not_running():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    p = HttpIngestPort(
        client=httpx.Client(
            transport=httpx.MockTransport(slow), base_url="http://127.0.0.1:8765"
        )
    )
    code, out = run(p, "accounts")
    assert code == 3 and "not running" not in out and "did not answer" in out


def test_commit_and_resolve_marker_are_masked():
    class P(FakePort):
        def commit(self, b):
            return {"note": "acct 123456789012"}

        def resolve_marker(self, *a):
            return {"note": "x@y.com"}

    assert "123456789012" not in run(P([]), "commit", "b")[1]
    marked = _cand(1, "A", needs_account=True, unresolved_marker_key="k")
    out = run(P([marked]), "resolve-marker", FP, "b", "1", "Assets:Cash")[1]
    assert "x@y.com" not in out


def test_partial_reject_failure_reports_already_dropped():
    class P(FakePort):
        def reject(self, batch_id, cid):
            if cid == "c2":
                raise cli.ApiError(409, {"detail": "changed"})
            return super().reject(batch_id, cid)

    fp = P([_cand(1, "A"), _cand(2, "B")])
    code, out = run(fp, "reject", "b", "1", "2")
    assert code == 4
    dropped = json.loads(out)["detail"]["already_dropped"]
    assert [r["n"] for r in dropped] == [1]


# --- B1: resolve-marker by row number ---------------------------------------


class MarkerPort(FakePort):
    def __init__(self, rows):
        super().__init__(rows)

    def resolve_marker(self, fp, batch_id, key, account):
        self.calls.append(("resolve", fp, batch_id, key, account))
        n = 0
        for c in self.rows:
            if c.get("needs_account") and c.get("unresolved_marker_key") == key:
                c["needs_account"], c["source_account"] = False, account
                n += 1
        return {"updated": n}


def _marked(i, narration="A", key="4399XXXXXXXX0001"):
    return _cand(i, narration, needs_account=True, unresolved_marker_key=key)


def test_resolve_marker_by_row_sends_the_raw_key_and_lists_masked_rows():
    key = "4399123456780001"  # a raw card number: masked in output, raw on the wire
    p = MarkerPort(
        [_marked(1, "SHOP 123456789012", key), _cand(2, "B"), _marked(3, "C", key)]
    )
    code, out = run(p, "resolve-marker", FP, "b", "1", "Assets:Cash")
    assert code == 0
    assert ("resolve", FP, "b", key, "Assets:Cash") in p.calls
    body = json.loads(out)
    assert [r["n"] for r in body["updated"]] == [1, 3] and body["count"] == 2
    assert "123456789012" not in out and key not in out


def test_resolve_marker_refuses_a_row_without_an_unresolved_marker():
    p = MarkerPort([_cand(1, "A"), _marked(2)])
    code, out = run(p, "resolve-marker", FP, "b", "1", "Assets:Cash")
    assert code == 1 and "no unresolved account marker" in out
    assert not [c for c in p.calls if c[0] == "resolve"]


def test_resolve_marker_refuses_an_account_that_is_not_open():
    p = MarkerPort([_marked(1)])
    code, out = run(p, "resolve-marker", FP, "b", "1", "Assets:Nope")
    assert code == 1 and "not open" in out
    assert not [c for c in p.calls if c[0] == "resolve"]


def test_resolve_marker_row_out_of_range_exits_4():
    code, out = run(MarkerPort([_marked(1)]), "resolve-marker", FP, "b", "7", "A")
    assert code == 4 and "no row 7" in out


def test_old_marker_key_form_is_a_usage_error():
    code, out = run(MarkerPort([_marked(1)]), "resolve-marker", FP, "b", "KEY", "A")
    assert code == 1 and "usage" in out


# --- B2: account and identical rows ------------------------------------------


def test_account_refuses_when_rows_share_an_id_and_changes_nothing():
    twin = _cand(1, "A")
    fp = FakePort([twin, dict(twin), _cand(3, "C")])
    code, out = run(fp, "account", "b", "1", "Assets:Cash")
    assert code == 1
    assert "2 identical rows" in out and "rows 1, 2" in out
    assert not [c for c in fp.calls if c[0] == "edit"]


# --- B3: E1065 shows the changed rows ----------------------------------------


class DedupPort(FakePort):
    def __init__(self, rows):
        super().__init__(rows)
        self.committed = False

    def commit(self, batch_id):
        from backend.app.cli.port import ApiError

        for c in self.rows:
            if c["id"] == "c2":
                c["dedup_status"] = "exact_dup"
        raise ApiError(409, {"code": "E1065", "detail": "changed 1 row(s): c2"})


def test_commit_e1065_prints_the_changed_rows():
    p = DedupPort([_cand(1, "A"), _cand(2, "UPI 123456789012")])
    code, out = run(p, "commit", "b")
    assert code == 4
    body = json.loads(out)
    assert body["detail"]["detail"]["code"] == "E1065"
    (row,) = body["detail"]["changed_rows"]
    assert row["n"] == 2 and row["before"] == "new" and row["after"] == "exact_dup"
    assert set(row) == {"n", "date", "amount", "dir", "narration", "before", "after"}
    assert "123456789012" not in out


def test_commit_other_409_has_no_changed_rows():
    class P(FakePort):
        def commit(self, b):
            raise cli.ApiError(409, {"code": "E1062", "detail": "x"})

    code, out = run(P([_cand(1, "A")]), "commit", "b")
    assert code == 4 and "changed_rows" not in out


# --- B4: fingerprint arguments -------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["", "abc", "0123456789ABCDEF", "0123456789abcdeg", "0123456789abcdef0"]
)
def test_malformed_fingerprints_exit_1(bad):
    p = MarkerPort([_marked(1)])
    for argv in (
        ("inspect", "f.csv", "--upgrade", bad),
        ("resolve-marker", bad, "b", "1", "Assets:Cash"),
    ):
        code, out = run(p, *argv)
        assert code == 1 and "fingerprint" in out
    assert not p.calls


def test_a_well_formed_upgrade_fingerprint_reaches_the_port():
    seen = {}

    class P(FakePort):
        def inspect(self, path, kind, upgrade):
            seen["upgrade"] = upgrade
            return {"stored_name": "x", "prove_id": "p"}

    run(P([]), "inspect", "f.csv", "--upgrade", FP)
    assert seen["upgrade"] == FP


class PdfPort:
    def __init__(self):
        self.calls = []

    def pdf_dry_run(self, prove_id, draft):
        self.calls.append(("pdf_dry_run", prove_id, draft))
        return {
            "prove_id": prove_id,
            "attempt": 1,
            "attempts_left": 4,
            "fingerprint": FP,
            "kind": "card",
            "report": {
                "passed": True,
                "checks": [],
                "low_confidence": [],
                "warnings": [],
                "sign_verified": True,
            },
            "previous_report": None,
            "candidates": [],
            "candidate_rows": [],
            "skipped_rows": [],
            "statement": {
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
                "roles": {"total_due": {"label": "TOTAL AMOUNT DUE", "value": "1.00"}},
            },
        }

    def dry_run(self, *a):
        self.calls.append(("dry_run", *a))
        raise AssertionError("CSV endpoint used for a pdf_draft")


def test_dry_run_routes_pdf_draft_to_pdf_endpoint():
    port = PdfPort()
    payload = json.dumps({"pdf_draft": {"institution": "HDFC"}})
    code, out = run(port, "dry-run", "pid1", "--draft", "-", stdin=payload)
    assert code == 0, out
    assert port.calls == [("pdf_dry_run", "pid1", {"institution": "HDFC"})]
    assert json.loads(out)["statement"]["period_end"] == "2026-01-31"


@pytest.mark.parametrize(
    "payload", ['{"pdf_draft": 1}', '{"draft": {}}', "[]", '{"other": {}}']
)
def test_bad_draft_shape_message_mentions_pdf_draft(payload):
    code, out = run(PdfPort(), "dry-run", "pid1", "--draft", "-", stdin=payload)
    assert code == 1
    assert "pdf_draft" in json.loads(out)["error"]


_REPO = REPO_ROOT
_CLI_DOCS = (
    _REPO / ".claude/skills/ingest-stmt/SKILL.md",
    _REPO / ".claude/agents/ingest-prover.md",
)


# `upgrade "<fp>"` is the SKILL telling the prover what to do, not a CLI command.
_NOT_CLI = {"upgrade"}


def _doc_problems(text: str) -> tuple[list[str], int]:
    """Commands and flags in a markdown file that the CLI parser does not have."""
    import argparse
    import re

    (sub_action,) = [
        a for a in cli._parser()._actions if isinstance(a, argparse._SubParsersAction)
    ]
    subs = sub_action.choices
    all_opts = {s for p in subs.values() for a in p._actions for s in a.option_strings}
    problems: list[str] = []
    mentioned = 0
    for m in re.finditer(r"ingest_cli\.py\s+([a-z][a-z-]*)", text):
        mentioned += 1
        if m.group(1) not in subs:
            problems.append(f"unknown subcommand {m.group(1)}")
    for snippet in re.findall(r"`([^`\n]+)`", text):
        words = snippet.split()
        if not words:
            continue
        looks_like_command = re.fullmatch(r"[a-z][a-z-]*", words[0]) and (
            len(words) > 1 and (words[1].startswith(('"', "--")))
        )
        if words[0] not in subs:
            if words[0] not in _NOT_CLI and looks_like_command:
                problems.append(f"unknown subcommand {words[0]} in `{snippet}`")
            continue
        opts = {s for a in subs[words[0]]._actions for s in a.option_strings}
        for flag in re.findall(r"(?<!\S)(--[a-z][a-z-]*)", snippet):
            mentioned += 1
            if flag not in opts:
                problems.append(f"{words[0]} has no {flag}")
    for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", text):
        if flag not in all_opts:
            problems.append(f"unknown flag {flag}")
    return problems, mentioned


def test_skill_and_prover_name_only_real_cli_commands():
    total = 0
    for doc in _CLI_DOCS:
        problems, mentioned = _doc_problems(doc.read_text())
        assert not problems, f"{doc.name}: {problems}"
        total += mentioned
    assert total > 5


@pytest.mark.parametrize(
    "line",
    [
        'Run `foo "<id>" --rows a:b`.',
        "Run `frobnicate --nope`.",
        'Run `peek "<id>" --nope`.',
        "Run .venv/bin/python scripts/ingest_cli.py frobnicate x.",
    ],
)
def test_doc_checker_flags_invented_commands(line):
    assert _doc_problems(line)[0]


PDF_ADVICE = (
    "the layout needs another /ingest-stmt run (upgrade), or import this statement"
    " by hand"
)


def _paragraphs(text: str) -> list[str]:
    """Bullets and paragraphs, each joined onto one line."""
    import re

    blocks = re.split(r"\n(?=\s*(?:- |\d+\. )|\s*\n)", text)
    return [" ".join(b.split()) for b in blocks if b.strip()]


def test_docs_never_send_a_pdf_to_the_browser_wizard():
    """The browser cannot read a new PDF layout, so every mention of the
    wizard is for CSV/XLS files, and each doc gives the PDF advice instead."""
    for doc in _CLI_DOCS:
        text = doc.read_text()
        assert PDF_ADVICE in " ".join(text.split()), doc.name
        for para in _paragraphs(text):
            if "wizard" in para:
                assert "CSV" in para, f"{doc.name}: {para}"


def test_prover_names_every_pdf_template_role_and_fx_group():
    """M6: the prover is told every summary role the template accepts and the
    optional fx groups, so A6's fx metadata can be captured live."""
    from backend.app.models.storage.pdf_template import DATE_ROLES, MONEY_ROLES

    text = (_REPO / ".claude/agents/ingest-prover.md").read_text()
    for name in [*MONEY_ROLES, *DATE_ROLES, "fx_amount", "fx_currency"]:
        assert name in text, name


_PROVER = _REPO / ".claude/agents/ingest-prover.md"
_SKILL = _REPO / ".claude/skills/ingest-stmt/SKILL.md"


def _code_words(text: str) -> set[str]:
    """Every word inside a backtick span of the PROSE. Fenced blocks (the worked
    examples) do not count: a field in an example is not explained by it, and
    `name` or `start` cannot be satisfied by ordinary words."""
    import re

    prose = re.sub(r"```.*?```", " ", text, flags=re.S)
    spans = re.findall(r"`[^`\n]+`", prose)
    return set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", " ".join(spans)))


def test_prover_doc_names_every_pdf_template_field_and_role():
    """The prover is told every field of the PDF template, its nested models,
    every summary role, and the two reading options."""
    from backend.app.models.storage.pdf_template import (
        DATE_ROLES,
        MONEY_ROLES,
        ManualSection,
        PdfTemplateFields,
        SummaryField,
        SummaryRow,
    )

    words = _code_words(_PROVER.read_text())
    expected = {
        *PdfTemplateFields.model_fields,
        *SummaryRow.model_fields,
        *SummaryField.model_fields,
        *ManualSection.model_fields,
        *MONEY_ROLES,
        *DATE_ROLES,
        "total_due_rounding",
        "description_around",
        "fx_amount",
        "fx_currency",
    }
    missing = sorted(expected - words)
    assert not missing, f"ingest-prover.md does not name {missing}"
    # Each substantive field has a bullet of its own, headed by the field, so
    # a mention in the field list or in a passing sentence is not enough.
    import re

    prose = re.sub(r"```.*?```", " ", _PROVER.read_text(), flags=re.S)
    heads = set(re.findall(r"^\s*- `(\w+)`", prose, flags=re.M))
    own_bullet = set(PdfTemplateFields.model_fields) - {
        "institution",
        "default_account",
        "date_format",
    }
    missing = sorted(own_bullet - heads)
    assert not missing, f"ingest-prover.md has no bullet headed by {missing}"


def _example(draft) -> str:
    return json.dumps(
        draft.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
        indent=2,
        ensure_ascii=False,
    )


def test_prover_doc_worked_examples_are_the_reference_templates():
    """One worked example per issuer, byte for byte the reference template the
    tests prove against, so the guidance cannot drift from a layout that works."""
    from tests.service.ingest.pdf_reference import (
        AXIS_PDF_DRAFT,
        HDFC_PDF_DRAFT,
        ICICI_PDF_DRAFT,
    )

    text = _PROVER.read_text()
    for draft in (HDFC_PDF_DRAFT, ICICI_PDF_DRAFT, AXIS_PDF_DRAFT):
        assert _example(draft) in text, draft.institution


def test_prover_doc_states_the_limits_the_code_enforces():
    from backend.app.service.ingest.parsers.pdf_parser import MAX_CONTINUATION_LINES
    from backend.app.models.storage.pdf_template import MAX_LINE_OFFSET, ManualSection
    from backend.app.service.ingest.parsers.pdf_words import AMOUNT_WORD

    text = " ".join(_PROVER.read_text().split())
    assert f"`line_offset` {0}-{MAX_LINE_OFFSET}" in text
    assert f"at most {MAX_CONTINUATION_LINES} lines" in text
    assert AMOUNT_WORD.pattern in text
    assert ManualSection.model_fields["name"].metadata[0].pattern in text


def test_prover_never_runs_an_interpreter_and_skill_names_the_card_errors():
    prover = " ".join(_PROVER.read_text().split())
    assert "Never run an interpreter or any ad-hoc command" in prover
    skill = " ".join(_SKILL.read_text().split())
    for needle in ("E1071", "PDF_ACCOUNT_HAS_OTHER_CARD", "E1067"):
        assert needle in skill, needle


def test_skill_tells_the_agent_which_account_a_matched_template_staged_into():
    skill = " ".join(_SKILL.read_text().split())
    assert "matched template stages into" in skill
    assert "the named account was NOT used" in skill


def test_skill_gives_the_replaced_card_route_and_never_removes_files():
    # Ruling P14: a replaced card (new number, same account) goes forward by
    # the user removing the old card's layout; the agent never removes it and
    # never advises a second account for the same debt.
    skill = " ".join(_SKILL.read_text().split())
    assert "A replacement card for the same account" in skill
    assert "the user removes the old card's saved layout by hand" in skill
    assert "storage/templates/pdf/<fingerprint>.yaml" in skill
    assert "Never suggest a new account for a replacement card" in skill
    assert "You never remove, edit or overwrite a saved layout yourself" in skill
    assert "or whether to open a new one" not in skill


def test_prover_doc_warns_that_a_lookbehind_card_pattern_never_passes():
    # Final review M5: the whole number must be inside the match.
    prover = " ".join(_PROVER.read_text().split())
    assert "the `last4` group must end the match" in prover
    assert "Do not use a lookbehind for the number." in prover


def test_prover_doc_gives_the_cues_for_the_two_reading_options():
    prover = " ".join(_PROVER.read_text().split())
    # rounding: check 13 "compared to the paisa", off by < 1.00, total ends .00
    assert 'check 13 fails with "compared to the paisa"' in prover
    assert "rounded down OR rounded half up" in prover
    # description_around: check 1 lists the unread lines; the row pattern must
    # accept an empty description.
    assert "check 1 (coverage) lists the date line and the line(s) next to it" in prover
    assert "accept an EMPTY description" in prover
    # exit 1 is not always a stop
    assert 'Exit 1 whose message starts "draft is not valid JSON"' in prover
    assert "check the narration at review" in prover


@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "not json",
        '{"pdf_draft": {"a": 1,}}',
        "[]",
        '"text"',
        '{"pdf_draft": 1}',
        '{"draft": {}}',
        '{"claims": {}}',
        '{"other": {}}',
    ],
)
def test_every_draft_shape_error_starts_with_a_prefix_the_prover_doc_lists(stdin):
    """Exit 1 for a mistake in the JSON costs no attempt, and the prover doc
    tells the prover to fix and retry only for the message prefixes it lists.
    A new shape message that is not listed would read as a stop."""
    import re

    doc = " ".join(_PROVER.read_text().split())
    (rule,) = re.findall(r"Exit 1 whose message starts (.*?) is a mistake", doc)
    prefixes = re.findall(r'"([^"]+)"', rule)
    assert prefixes, rule
    code, out = run(FakePort([]), "dry-run", "p", "--draft", "-", stdin=stdin)
    assert code == 1
    message = json.loads(out)["error"]
    assert message.startswith(tuple(prefixes)), (message, prefixes)


# ---- category, tag, untag, learn ------------------------------------------


def _edits(p):
    return [c for c in p.calls if c[0] == "edit"]


def test_category_sets_only_the_category_and_allows_an_unopened_account():
    p = FakePort([_cand(1, "FICTIVEBIKES BANGALORE"), _cand(2, "FICTIVE CAFE")])
    code, out = run(p, "category", "b", "1", "Expenses:Subscriptions")
    assert code == 0 and json.loads(out)["new_account"] is True
    assert _edits(p) == [
        ("edit", "c1", {"suggested_category": "Expenses:Subscriptions"})
    ]
    code, out = run(p, "category", "b", "2", "Expenses:Food")
    assert json.loads(out)["new_account"] is False


def test_category_matching_selects_by_narration_and_no_match_exits_4():
    p = FakePort(
        [
            _cand(1, "FICTIVEBIKES BANGALORE"),
            _cand(2, "FICTIVE CAFE"),
            _cand(3, "FICTIVEBIKES BENGALURU"),
        ]
    )
    assert (
        run(p, "category", "b", "--matching", "fictivebikes", "Expenses:Travel")[0] == 0
    )
    assert [c[1] for c in _edits(p)] == ["c1", "c3"]
    assert run(p, "category", "b", "--matching", "NOPE", "Expenses:Travel")[0] == 4
    assert (
        run(p, "category", "b", "Expenses:Travel", "--matching", "FICTIVE CAFE")[0] == 0
    )
    assert _edits(p)[-1][1] == "c2"


def test_category_needs_rows_or_matching_but_not_both():
    p = FakePort([_cand(1, "A")])
    assert run(p, "category", "b", "Expenses:Food")[0] == 1
    # positionals first, so the XOR check (not argparse) is what refuses (Q2)
    assert run(p, "category", "b", "1", "Expenses:Food", "--matching", "A")[0] == 1
    assert not _edits(p)


def test_category_refuses_a_proposed_transfer_row_and_a_lot_row():
    p = FakePort(
        [
            _cand(1, "CARD PAYMENT", suggested_other_leg="Liabilities:CreditCard:HDFC"),
            _cand(2, "SOME FUND", commodity="FUND"),
            _cand(3, "OK"),
        ]
    )
    for sel in ("1", "2"):
        code, out = run(p, "category", "b", "3", sel, "Expenses:Food")
        assert code == 1 and "no row was changed" in out
    assert not _edits(p)


def test_category_reports_rows_changed_elsewhere():
    class Spreading(FakePort):
        def edit(self, batch_id, cid, patch):
            super().edit(batch_id, cid, patch)
            # the app spreads the new category to a twin narration and a fee row
            self.rows[2] = {**self.rows[2], **patch, "category_source": "taught_batch"}
            self.rows[3] = {
                **self.rows[3],
                **patch,
                "category_source": "fee_follows",
                "follows": "c1",
            }
            return {"ok": True}

    p = Spreading(
        [_cand(1, "A"), _cand(2, "B"), _cand(3, "A2"), _cand(4, "FEE", flags=["fee"])]
    )
    out = json.loads(run(p, "category", "b", "1", "Expenses:Food")[1])
    assert out["changed_elsewhere"] == [
        {"n": 3, "category": "Expenses:Food", "source": "taught this batch"},
        {"n": 4, "category": "Expenses:Food", "source": "fee follows row 1"},
    ]
    assert [u["n"] for u in out["updated"]] == [1]


def test_category_and_tag_refuse_shared_ids_and_change_nothing():
    twin = [_cand(1, "A"), {**_cand(2, "A"), "id": "c1"}]
    for argv in (
        ("category", "b", "1", "Expenses:Food"),
        ("tag", "b", "1", "x"),
        ("untag", "b", "1", "x"),
        ("learn", "b", "1", "--off"),
    ):
        p = FakePort(twin)
        code, out = run(p, *argv)
        assert code == 1 and "no row was changed" in out and not _edits(p)


def test_tag_appends_and_untag_removes():
    p = FakePort(
        [_cand(1, "A", tags=["x"]), _cand(2, "B", tags=["travel/madikeri-oct-2026"])]
    )
    out = json.loads(run(p, "tag", "b", "1", "2", "Travel/Madikeri Oct-2026")[1])
    assert _edits(p) == [("edit", "c1", {"tags": ["x", "travel/madikeri-oct-2026"]})]
    assert out["unchanged"] == [2] and out["tag"] == "travel/madikeri-oct-2026"
    assert "prefix_hint" not in out
    p2 = FakePort(
        [_cand(1, "A", tags=["x", "travel/madikeri-oct-2026"]), _cand(2, "B")]
    )
    out = json.loads(run(p2, "untag", "b", "1", "2", "Travel/Madikeri Oct-2026")[1])
    assert _edits(p2) == [("edit", "c1", {"tags": ["x"]})]
    assert out["unchanged"] == [2]


def test_tag_hints_the_prefix_from_the_category():
    p = FakePort([_cand(1, "FICTIVE STAYS", suggested_category="Expenses:Travel")])
    out = json.loads(run(p, "tag", "b", "1", "Madikeri Oct-2026")[1])
    assert out["prefix_hint"] == "travel/"


def test_tag_refuses_a_lot_row():
    p = FakePort([_cand(1, "FUND", commodity="F")])
    assert run(p, "tag", "b", "1", "x")[0] == 1 and not _edits(p)


@pytest.mark.parametrize("cmd", ["tag", "untag"])
def test_invalid_tag_is_refused_locally_without_a_traceback(cmd):
    p = FakePort([_cand(1, "A")])
    code, out = run(p, cmd, "b", "1", "café")
    assert code == 1 and "not a valid tag" in out and "Traceback" not in out
    assert not _edits(p) and not p.calls


def test_learn_off_and_on_send_the_flag():
    p = FakePort([_cand(1, "A"), _cand(2, "B")])
    out = json.loads(run(p, "learn", "b", "1", "2", "--off")[1])
    assert [e[2] for e in _edits(p)] == [{"learn": False}, {"learn": False}]
    assert [u["learn"] for u in out["updated"]] == [False, False]
    run(p, "learn", "b", "1", "--on")
    assert _edits(p)[-1] == ("edit", "c1", {"learn": True})


def test_learn_needs_exactly_one_of_off_on():
    p = FakePort([_cand(1, "A")])
    assert run(p, "learn", "b", "1")[0] == 1
    assert run(p, "learn", "b", "1", "--off", "--on")[0] == 1


def test_a_failure_part_way_reports_already_updated():
    from backend.app.cli.port import ApiError

    class Flaky(FakePort):
        def edit(self, batch_id, cid, patch):
            if cid == "c2":
                raise ApiError(422, {"code": "E1017", "detail": "bad"})
            return super().edit(batch_id, cid, patch)

    code, out = run(
        Flaky([_cand(1, "A"), _cand(2, "B")]),
        "category",
        "b",
        "1",
        "2",
        "Expenses:Food",
    )
    assert code == 1 and json.loads(out)["detail"]["already_updated"] == [1]


def test_show_passes_the_learning_view_through():
    class L(FakePort):
        def learning(self, batch_id):
            return {
                "will_learn": [
                    {
                        "pattern": "A",
                        "category": "Expenses:Food",
                        "candidate_ids": ["c1"],
                        "replaces": None,
                    }
                ],
                "not_learned": [],
                "would_open": ["Expenses:Food"],
            }

    view = json.loads(run(L([_cand(1, "A")]), "show", "b")[1])
    assert view["will_learn"][0]["rows"] == [1] and view["would_open"] == [
        "Expenses:Food"
    ]


def test_show_prints_every_page_row_beside_a_large_learning_block():
    cands = [_cand(i, f"MERCHANT {i} " + "Z" * 40) for i in range(1, 61)]

    class L(FakePort):
        def learning(self, batch_id):
            return {
                "will_learn": [
                    {
                        "pattern": f"MERCHANT {i}",
                        "category": "Expenses:Food",
                        "candidate_ids": [c["id"] for c in cands],
                        "replaces": None,
                    }
                    for i in range(1, 61)
                ],
                "not_learned": [],
                "would_open": [],
            }

    port = L(cands)
    page1 = json.loads(run(port, "show", "b")[1])
    page2 = json.loads(run(port, "show", "b", "--page", "2")[1])
    assert len(page1["rows"]) == 50 and len(page2["rows"]) == 10
    assert page1["will_learn_more"] == 60 - len(page1["will_learn"])
    full = json.loads(run(port, "show", "b", "--learn-all")[1])
    assert len(full["rows"]) == 50
    assert len(full["will_learn"]) > len(page1["will_learn"])


def test_commit_masks_learned_and_opened():
    class C(FakePort):
        def commit(self, batch_id):
            return {
                "committed": 1,
                "learned": [
                    {
                        "pattern": "UPI 123456789012",
                        "category": "Expenses:Food",
                        "replaces": None,
                    }
                ],
                "opened": ["Expenses:Food"],
            }

    out = run(C([_cand(1, "A")]), "commit", "b")[1]
    assert "123456789012" not in out and '"opened"' in out


# ---- against the contract fixtures through a fake HTTP layer ---------------

_FIX = Path("tests/fixtures/agent/categorize")


def _fx(name):
    return json.loads((_FIX / name).read_text())


def _http_port(edit_response=None):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        path = request.url.path
        if path.endswith("/learning"):
            return httpx.Response(200, json=_fx("review-learning.json"))
        if path == "/api/accounts":
            return httpx.Response(200, json={"accounts": ["Expenses:Food"]})
        if path.startswith("/api/review/") and request.method == "GET":
            return httpx.Response(200, json=_fx("review-get.json"))
        if request.method == "POST":
            if edit_response:
                return httpx.Response(
                    edit_response["status"], json=edit_response["body"]
                )
            return httpx.Response(200, json=_fx("review-edit-ok.json"))
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    return HttpIngestPort(client=client), seen


def test_show_against_the_fixtures_orders_names_and_lists_what_it_will_learn():
    port, _ = _http_port()
    view = json.loads(run(port, "show", "b")[1])
    assert [r["n"] for r in view["rows"]] == [9, 10, 1, 2, 3, 4, 5, 6, 7, 8, 11]
    assert view["will_learn"][1] == {
        "rows": [4],
        "pattern": "FICTIVEBIKES",
        "category": "Expenses:Travel",
        "replaces": "Expenses:Travel:Bike",
    }
    assert view["not_learned"][1]["reason"] == "you said don't learn this"
    assert len(view["would_open"]) == 3


@pytest.mark.parametrize(
    ("name", "argv"),
    [
        ("error-e1072-invalid-tag.json", ("tag", "b", "9", "x")),
        ("error-e1037-tags-not-a-list.json", ("tag", "b", "9", "x")),
        ("error-e1017-bad-category.json", ("category", "b", "9", "Expenses:Food")),
    ],
)
def test_app_422_for_tags_and_accounts_exits_1(name, argv):
    port, _ = _http_port(_fx(name))
    assert run(port, *argv)[0] == 1


def test_app_404_on_an_edit_exits_4_with_the_stale_hint():
    port, _ = _http_port({"status": 404, "body": {"code": "E1007", "detail": "gone"}})
    code, out = run(port, "learn", "b", "9", "--off")
    assert code == 4 and "/ingest-stmt" in out


def test_skill_states_the_categorise_flow():
    skill = " ".join(_SKILL.read_text().split())
    for phrase in (
        "uncategorised rows first",
        "only the merchant words",
        "with the reason",
        "will learn",
        "don't learn that one",
        "never touches the card side",
        "never applies a tag unasked",
    ):
        assert phrase in skill


@pytest.mark.parametrize(
    "argv",
    [
        ("category", "b", "9", "Expenses:Food"),
        ("tag", "b", "9", "x"),
        ("untag", "b", "9", "x"),
        ("learn", "b", "9", "--off"),
    ],
)
def test_out_of_range_row_exits_4_for_every_new_command(argv):
    p = FakePort([_cand(1, "A")])
    code, out = run(p, *argv)
    assert code == 4 and "no row 9" in out and not _edits(p)


@pytest.mark.parametrize("text", ["", "   "])
@pytest.mark.parametrize("cmd", [("category", "Expenses:Food"), ("tag", "x")])
def test_empty_matching_is_refused_and_selects_nothing(cmd, text):
    p = FakePort([_cand(1, "A"), _cand(2, "B", dedup_status="exact_dup")])
    code, out = run(p, cmd[0], "b", "--matching", text, cmd[1])
    assert code == 1 and "--matching needs some text" in out and not _edits(p)


@pytest.mark.parametrize("cmd", ["tag", "untag"])
def test_an_all_digit_tag_is_refused_as_a_forgotten_tag(cmd):
    p = FakePort([_cand(1, "A"), _cand(2, "B")])
    code, out = run(p, cmd, "b", "3", "7")
    assert code == 1 and "can't be only digits" in out and "another row number" in out
    assert not _edits(p)


@pytest.mark.parametrize("status", [404, 405])
def test_show_without_a_learning_route_says_to_restart_the_app(status):
    from backend.app.cli.port import ApiError

    class Old(FakePort):
        def learning(self, batch_id):
            raise ApiError(status, {"detail": "x"})

    code, out = run(Old([_cand(1, "A")]), "show", "b")
    assert code == 1
    assert "this server has no learning preview" in out and "restart the app" in out


# ---- against the real app (Q7: membership, rows picked by narration) -------


def _row_of(port, batch: str, narration: str) -> int:
    page = json.loads(run(port, "show", batch)[1])
    return next(r["n"] for r in page["rows"] if narration in r["narration"])


def _shown(port, batch: str, n: int) -> dict:
    # `show` lists uncategorised rows first, so the row is found by its number.
    page = json.loads(run(port, "show", batch)[1])
    return next(r for r in page["rows"] if r["n"] == n)


def test_real_app_category_then_show_lists_will_learn(port):
    batch = _staged(port)
    n = _row_of(port, batch, "SWIGGY")
    # SWIGGY is in the seed dictionary as Food; a different category is a
    # user decision the app will learn from.
    assert run(port, "category", batch, str(n), "Expenses:Subscriptions")[0] == 0
    view = json.loads(run(port, "show", batch)[1])
    assert "Expenses:Subscriptions" in view["would_open"]
    learned = [
        w for w in view["will_learn"] if w["category"] == "Expenses:Subscriptions"
    ]
    assert learned and n in learned[0]["rows"]
    assert "SWIGGY" in learned[0]["pattern"]


def test_real_app_tag_is_normalised_and_shown_on_the_row(port):
    batch = _staged(port)
    n = _row_of(port, batch, "SWIGGY")
    assert run(port, "tag", batch, str(n), "Madikeri Oct-2026")[0] == 0
    assert _shown(port, batch, n)["tags"] == ["madikeri-oct-2026"]
    assert run(port, "untag", batch, str(n), "Madikeri Oct-2026")[0] == 0
    assert _shown(port, batch, n)["tags"] == []
