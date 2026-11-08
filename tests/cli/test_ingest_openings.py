from __future__ import annotations

import io
import json

import httpx
import pytest

from backend.app.cli import ingest as cli
from backend.app.cli import render
from backend.app.cli.port import ApiError, AppUnreachable, HttpIngestPort

ACCT = "Assets:Checking:Acme"


def _suggestion(**kw) -> dict:
    return {
        "source": "statement",
        "amount": "10000.00",
        "direction": "have",
        "ledger_amount": "10000.00",
        "arithmetic": "'Opening Balance' printed 10,000.00; recorded as 10000.00 INR "
        f"on {ACCT}",
        "label": "Opening Balance",
        **kw,
    }


def _prompt(account: str = ACCT, **kw) -> dict:
    return {
        "account": account,
        "account_display": account,
        "kind": "bank",
        "statement_start": "2026-05-01",
        "suggested_date": "2026-04-30",
        "april": False,
        "april_advice": "This statement starts on 01 May 2026. Import April first.",
        "suggestion": _suggestion(),
        "alternatives": [],
        "mismatch": False,
        "note": None,
        "directions": ["have", "owe"],
        **kw,
    }


def _status_row(account: str, warn: bool) -> dict:
    return {
        "account": account,
        "account_display": account,
        "kind": "bank",
        "status": "missing" if warn else "opening",
        "opening": None
        if warn
        else {
            "date": "2026-03-31",
            "amount": "12500.00",
            "direction": "have",
            "ledger_amount": "12500.00",
            "narration": "Opening balance",
            "txn_id": "a1b2c3d4e5f60718",
            "shared": False,
            "source": "statement",
            "balance_check": False,
        },
        "first_posting": "2026-04-01",
        "suggested_date": "2026-03-31",
        "april": True,
        "warn": warn,
        "directions": ["have", "owe"],
    }


class FakePort:
    def __init__(self, prompts=None, rows=None, error=None):
        self.prompts = prompts if prompts is not None else [_prompt()]
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple] = []

    def _go(self, *call):
        self.calls.append(call)
        if self.error:
            raise self.error
        return {"ok": True}

    def opening_prompt(self, batch_id):
        self._go("opening_prompt", batch_id)
        return {"batch_id": batch_id, "prompts": self.prompts, "missing": []}

    def openings(self):
        self._go("openings")
        return {
            "accounts": self.rows,
            "missing_count": sum(1 for r in self.rows if r["warn"]),
            "warning": None,
        }

    def commit_opening(self, body):
        self._go("commit_opening", body)
        return {
            "account": body["account"],
            "txn_id": "a1b2c3d4e5f60718",
            "date": body["date"],
            "ledger_amount": body["amount"],
            "source": "manual",
            "balance_check": body.get("balance_check"),
            "snapshot": "20261101T093000_123456",
        }

    def correct_opening(self, body):
        self._go("correct_opening", body)
        return {
            "account": body["account"],
            "txn_id": "a1b2c3d4e5f60718",
            "date": body["date"],
            "ledger_amount": body["amount"],
            "source": "manual",
            "balance_check": body.get("balance_check"),
            "snapshot": "20261101T093000_123456",
        }

    def mark_opening_zero(self, account, zero):
        self._go("mark_opening_zero", account, zero)
        return {"account": account, "status": "zero" if zero else "empty"}


def run(port, *argv):
    out = io.StringIO()
    code = cli.main(list(argv), port=port, stdin=io.StringIO(""), stdout=out)
    return code, out.getvalue()


def test_opening_check_prints_prompts():
    port = FakePort()
    code, out = run(port, "opening-check", "b1")
    assert code == 0
    assert port.calls == [("opening_prompt", "b1")]
    p = json.loads(out)["prompts"][0]
    assert p["account"] == ACCT
    assert p["suggestion"]["amount"] == "10000.00"
    assert p["suggestion"]["direction"] == "have"
    assert p["suggested_date"] == "2026-04-30"
    assert p["april"] is False
    assert p["april_advice"].startswith("This statement starts")
    assert "10,000.00" in p["suggestion"]["arithmetic"]


COMMIT = (
    "commit-opening", ACCT, "--amount", "10000.00", "--direction", "have",
    "--date", "2025-03-31",
)  # fmt: skip


def test_commit_opening_builds_request():
    port = FakePort()
    code, out = run(port, *COMMIT, "--batch", "b1", "--balance-check")
    assert code == 0
    assert port.calls == [
        (
            "commit_opening",
            {
                "account": ACCT,
                "amount": "10000.00",
                "direction": "have",
                "date": "2025-03-31",
                "batch_id": "b1",
                "balance_check": True,
            },
        )
    ]
    assert json.loads(out)["ledger_amount"] == "10000.00"


def test_commit_opening_without_batch_or_check_omits_batch_and_sends_false():
    port = FakePort()
    run(port, *COMMIT)
    ((_, body),) = port.calls
    assert "batch_id" not in body and body.get("balance_check") is False


@pytest.mark.parametrize("amount", ["0", "0.00", "-5.00"])
def test_amount_is_passed_through_for_the_server_to_judge(amount):
    port = FakePort()
    argv = list(COMMIT)
    argv[3] = amount
    assert run(port, *argv)[0] == 0
    assert port.calls[0][1]["amount"] == amount


def _api(status, code):
    return ApiError(status, {"detail": "x", "code": code})


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [
        (_api(409, "E1088"), 4),
        (_api(404, "E1006"), 4),
        (_api(422, "E1089"), 1),
        (_api(422, "E1081"), 2),
        (AppUnreachable("refused"), 3),
    ],
)
def test_commit_opening_exit_codes(error, exit_code):
    assert run(FakePort(error=error), *COMMIT)[0] == exit_code


@pytest.mark.parametrize(
    ("code", "hinted"), [("E1006", True), ("E1039", False), (None, False)]
)
def test_404_restart_hint_only_when_the_batch_is_gone(code, hinted):
    """An account that is not open (E1039) is a 404 too, but re-running
    /ingest-stmt would not help: only a vanished batch or run gets the hint."""
    body = {"detail": "x"} if code is None else {"detail": "x", "code": code}
    exit_code, out = run(FakePort(error=ApiError(404, body)), *COMMIT)
    assert exit_code == 4
    assert ("Run /ingest-stmt again" in out) is hinted


def test_bad_direction_is_a_usage_error():
    port = FakePort()
    argv = list(COMMIT)
    argv[5] = "owes"
    code, out = run(port, *argv)
    assert code == 1 and "usage" in out
    assert port.calls == []


def test_unreachable_app_message_on_real_port():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    port = HttpIngestPort(
        client=httpx.Client(
            transport=httpx.MockTransport(boom), base_url="http://127.0.0.1:8765"
        )
    )
    code, out = run(port, "openings")
    assert code == 3 and "not running" in out


def test_opening_check_masks_label_digits():
    label = "Opening Bal 50100012345678"
    p = _prompt(
        suggestion=_suggestion(
            label=label, arithmetic=f"'{label}' printed 10,000.00; recorded as ..."
        ),
        note=f"cell {label} unreadable",
        april_advice=f"see {label}",
    )
    code, out = run(FakePort(prompts=[p]), "opening-check", "b1")
    assert code == 0
    assert "50100012345678" not in out
    assert "XXXXXXXXXX5678" in out  # last four kept, length kept
    got = json.loads(out)["prompts"][0]
    for text in (
        got["suggestion"]["label"],
        got["suggestion"]["arithmetic"],
        got["note"],
        got["april_advice"],
    ):
        assert "50100012345678" not in text


def test_opening_check_keeps_accounts_and_figures_readable():
    big = "1234567890123.45"  # 13 integer digits: the server's maximum
    p = _prompt(
        suggestion=_suggestion(amount=big, ledger_amount="-4210.37", direction="owe"),
        alternatives=[_suggestion(amount="0.00", ledger_amount="0.00")],
    )
    out = run(FakePort(prompts=[p]), "opening-check", "b1")[1]
    got = json.loads(out)["prompts"][0]
    assert got["account"] == ACCT
    assert got["suggestion"]["amount"] == big
    assert got["suggestion"]["ledger_amount"] == "-4210.37"
    assert got["alternatives"][0]["amount"] == "0.00"


def test_batch_id_is_not_masked():
    batch = "9f0c2b7e4d1a4c3b8e6f5a2d1c0b9a87"
    out = run(FakePort(), "opening-check", batch)[1]
    assert json.loads(out)["batch_id"] == batch


def test_commit_opening_correction_builds_expected():
    port = FakePort()
    code, _ = run(
        port, "commit-opening-correction", ACCT, "--amount", "4210.37",
        "--direction", "owe", "--date", "2025-03-31",
        "--expect-date", "2025-03-31", "--expect-amount", "-4210.37",
    )  # fmt: skip
    assert code == 0
    assert port.calls == [
        (
            "correct_opening",
            {
                "account": ACCT,
                "amount": "4210.37",
                "direction": "owe",
                "date": "2025-03-31",
                "balance_check": False,
                "expected": {"date": "2025-03-31", "ledger_amount": "-4210.37"},
            },
        )
    ]


def test_commit_opening_correction_requires_expectation():
    code, out = run(
        FakePort(), "commit-opening-correction", ACCT, "--amount", "1",
        "--direction", "have", "--date", "2025-03-31",
    )  # fmt: skip
    assert code == 1 and "usage" in out


def test_commit_opening_zero_and_undo():
    port = FakePort()
    assert run(port, "commit-opening-zero", ACCT)[0] == 0
    code, out = run(port, "commit-opening-zero", ACCT, "--undo")
    assert code == 0 and json.loads(out)["status"] == "empty"
    assert port.calls == [
        ("mark_opening_zero", ACCT, True),
        ("mark_opening_zero", ACCT, False),
    ]


def test_openings_missing_filter():
    rows = [
        _status_row("Assets:Checking:A", warn=False),
        _status_row("Assets:Checking:B", warn=True),
        _status_row("Assets:Checking:C", warn=False),
        _status_row("Assets:Checking:D", warn=True),
    ]
    everything = json.loads(run(FakePort(rows=rows), "openings")[1])
    assert [r["account"][-1] for r in everything["accounts"]] == list("ABCD")
    only = json.loads(run(FakePort(rows=rows), "openings", "--missing")[1])
    assert [r["account"] for r in only["accounts"]] == [
        "Assets:Checking:B",
        "Assets:Checking:D",
    ]
    assert all(r["warn"] for r in only["accounts"])
    assert only["missing_count"] == 2


def test_large_responses_stay_inside_their_budgets():
    prompts = [_prompt(account=f"Assets:Checking:Bank{i}") for i in range(3)]
    missing = [f"Assets:Checking:Missing{i}" for i in range(500)]
    p = render.render_opening_prompt(
        {"batch_id": "b1", "prompts": prompts, "missing": missing}
    )
    assert len(p) <= 4_000
    got = json.loads(p)
    assert [x["account"] for x in got["prompts"]] == [
        f"Assets:Checking:Bank{i}" for i in range(3)
    ]
    assert got["prompts_truncated"] is False
    assert got["missing_truncated"] is True
    assert 0 < len(got["missing"]) < 500
    assert got["missing_omitted"] == 500 - len(got["missing"])
    for x in got["prompts"]:  # what the skill reads survives
        assert x["suggestion"]["amount"] and x["suggested_date"]
        assert x["april_advice"] and "arithmetic" in x["suggestion"]
    rows = [
        _status_row(f"Assets:Checking:Bank{i}", warn=i % 2 == 0) for i in range(600)
    ]
    o = render.render_openings({"accounts": rows, "missing_count": 300, "warning": "w"})
    assert len(o) <= 12_000
    got = json.loads(o)
    assert got["accounts_truncated"] and got["accounts_total"] == 600


def test_many_prompts_keep_at_least_one_and_stay_in_budget():
    prompts = [_prompt(account=f"Assets:Checking:Bank{i}") for i in range(200)]
    out = render.render_opening_prompt(
        {"batch_id": "b1", "prompts": prompts, "missing": ["Assets:X"]}
    )
    got = json.loads(out)
    assert len(out) <= 4_000
    assert got["prompts"] and got["prompts_truncated"] is True


def test_arithmetic_keeps_amounts_readable_but_masks_account_numbers():
    arith = (
        "'Opening Bal 50100012345678' printed 123456789.00 and -4,210.37 and "
        "1,23,45,678.00; recorded on Assets:Checking:Acme"
    )
    p = _prompt(suggestion=_suggestion(arithmetic=arith))
    out = run(FakePort(prompts=[p]), "opening-check", "b1")[1]
    text = json.loads(out)["prompts"][0]["suggestion"]["arithmetic"]
    assert "50100012345678" not in text and "XXXXXXXXXX5678" in text
    for amount in ("123456789.00", "-4,210.37", "1,23,45,678.00"):
        assert amount in text
    assert "Assets:Checking:Acme" in text


def test_long_texts_cannot_blow_the_budget():
    long = "x " * 5000
    p = _prompt(
        note=long,
        april_advice=long,
        suggestion=_suggestion(arithmetic=long, label=long),
    )
    out = render.render_opening_prompt({"batch_id": "b", "prompts": [p], "missing": []})
    assert len(out) <= 4_000
    json.loads(out)
