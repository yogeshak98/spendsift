"""Phase 6, end to end: the first import into an empty account asks for its
opening balance, the answer is written once with a restore point, the import
commits, and the checklist, Doctor and the agent CLI all agree. Synthetic
data only; the app clock is fixed so the real date never leaks in."""

from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import data
from beancount.core.amount import Amount
from fastapi.testclient import TestClient

from backend.app.cli import ingest as cli
from backend.app.cli.port import HttpIngestPort
from backend.app.server.main import create_app

TODAY = date(2026, 6, 15)
BANK = "Assets:Checking:HDFC"


def _statement(opening: str, first_day: str = "01/04/2025") -> bytes:
    return (
        "Account Statement\n"
        f"Opening Balance,{opening}\n"
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        f"{first_day},ACME SALARY APR,,50000.00\n"
        "05/04/2025,FICTIVE GROCER BANGALORE,1200.50,\n"
    ).encode()


def _second_statement() -> bytes:
    return (
        "Account Statement\n"
        "Opening Balance,58799.50\n"
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "03/05/2025,FICTIVE GROCER MYSURU,300.00,\n"
    ).encode()


@pytest.fixture
def app(tmp_path: Path):
    return create_app(storage_dir=tmp_path / "storage", clock=lambda: TODAY)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def _snapshots(app) -> int:
    return len(list(app.state.ledger.snapshots_dir.iterdir()))


def _detect(client: TestClient, data: bytes, name: str) -> dict:
    resp = client.post(
        "/api/import/detect",
        files={"file": (name, io.BytesIO(data), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _save_template(client: TestClient, data: bytes) -> dict:
    """First sight of the layout: save a template that maps the opening cell."""
    detected = _detect(client, data, "apr.csv")
    assert detected["matched"] is False, detected
    resp = client.post(
        "/api/templates",
        json={
            "filename": detected["filename"],
            "fingerprint": detected["fingerprint"],
            "institution": "HDFC",
            "header_row_index": detected["suggested_header_row"],
            "delimiter": detected["delimiter"],
            "date_column": detected["suggested_roles"]["date"],
            "date_format": "%d/%m/%Y",
            "narration_column": detected["suggested_roles"]["narration"],
            "debit_column": detected["suggested_roles"]["debit"],
            "credit_column": detected["suggested_roles"]["credit"],
            "default_account": BANK,
            "opening_balance": {"label": "^Opening Balance$", "row_offset": 0},
            "save": True,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row(client: TestClient, account: str = BANK) -> dict:
    (row,) = [
        r
        for r in client.get("/api/openings").json()["accounts"]
        if r["account"] == account
    ]
    return row


def _validate(app) -> list:
    return app.state.ledger.validate()


def _post_as_shown(client: TestClient, batch: str, prompt: dict) -> dict:
    s = prompt["suggestion"]
    resp = client.post(
        "/api/openings",
        json={
            "account": prompt["account"],
            "amount": s["amount"],
            "direction": s["direction"],
            "date": prompt["suggested_date"],
            "batch_id": batch,
        },
    )
    return resp


def _first_prompt(client: TestClient, batch: str) -> dict:
    body = client.get(f"/api/review/{batch}/opening").json()
    (prompt,) = body["prompts"]
    return prompt


def test_first_csv_import_prompts_records_and_commits(app, client):
    staged = _save_template(client, _statement("10000.00"))
    batch = staged["batch_id"]

    prompt = _first_prompt(client, batch)
    assert prompt["suggestion"]["source"] == "statement"
    assert prompt["suggested_date"] == "2025-03-31"
    assert prompt["april"] is True and prompt["april_advice"] is None
    assert prompt["suggestion"]["ledger_amount"] == "10000.00"

    before = _snapshots(app)
    written = _post_as_shown(client, batch, prompt)
    assert written.status_code == 200, written.text
    assert written.json()["source"] == "statement"
    committed = client.post(f"/api/commit/{batch}")
    assert committed.status_code == 200, committed.text

    row = _row(client)
    assert row["status"] == "opening"
    assert row["opening"]["ledger_amount"] == "10000.00"
    assert row["opening"]["date"] == "2025-03-31"
    assert _validate(app) == []
    # One snapshot for the opening write, one for the commit; nothing else
    # (staging, detecting and the reads) writes.
    assert _snapshots(app) - before == 2


def test_second_import_same_account_no_prompt(app, client):
    batch = _save_template(client, _statement("10000.00"))["batch_id"]
    assert (
        _post_as_shown(client, batch, _first_prompt(client, batch)).status_code == 200
    )
    assert client.post(f"/api/commit/{batch}").status_code == 200

    second = _detect(client, _second_statement(), "may.csv")
    assert second["matched"] is True, second
    body = client.get(f"/api/review/{second['batch_id']}/opening").json()
    assert body == {"batch_id": second["batch_id"], "prompts": [], "missing": []}


def test_skipping_leaves_missing_warning(app, client):
    batch = _save_template(client, _statement("10000.00"))["batch_id"]
    assert client.post(f"/api/commit/{batch}").status_code == 200

    listing = client.get("/api/openings").json()
    assert listing["missing_count"] == 1
    assert listing["warning"]
    assert _row(client)["status"] == "missing"
    doctor = client.get("/api/reports/doctor").json()
    assert doctor["openings"]["missing_count"] == 1


def _june_statement() -> bytes:
    """A mid-June export: the opening is dated the day before its first row,
    and neither the figure nor the date is one a test would type."""
    return (
        "Account Statement\n"
        "Opening Balance,10437.25\n"
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "12/06/2025,ACME SALARY JUN,,50000.00\n"
        "18/06/2025,FICTIVE GROCER BANGALORE,1200.50,\n"
    ).encode()


def test_cli_round_trip_against_app(app, client):
    """The chat path accepts the opening exactly as `opening-check` printed
    it: the values fed to `commit-opening` are parsed from its JSON."""
    batch = _save_template(client, _june_statement())["batch_id"]
    port = HttpIngestPort(client=client)

    def run(*argv):
        out = io.StringIO()
        code = cli.main(list(argv), port=port, stdin=io.StringIO(""), stdout=out)
        return code, out.getvalue()

    code, out = run("opening-check", batch)
    assert code == 0, out
    (prompt,) = json.loads(out)["prompts"]
    shown = prompt["suggestion"]
    code, out = run(
        "commit-opening", prompt["account"],
        "--amount", shown["amount"], "--direction", shown["direction"],
        "--date", prompt["suggested_date"], "--batch", batch,
    )  # fmt: skip
    assert code == 0, out

    row = _row(client)
    assert row["status"] == "opening" and row["warn"] is False
    opening = row["opening"]
    # The statement's own figure and date, recorded as from the statement.
    assert (opening["amount"], opening["date"], opening["source"]) == (
        "10437.25",
        "2025-06-11",
        "statement",
    )
    assert _validate(app) == []


def test_zero_previous_balance_round_trip(app, client):
    batch = _save_template(client, _statement("0.00"))["batch_id"]
    prompt = _first_prompt(client, batch)
    assert prompt["suggestion"]["amount"] == "0.00"
    assert prompt["suggestion"]["ledger_amount"] == "0.00"

    written = _post_as_shown(client, batch, prompt)
    assert written.status_code == 200, written.text
    assert written.json()["ledger_amount"] == "0.00"
    assert client.post(f"/api/commit/{batch}").status_code == 200

    row = _row(client)
    assert row["status"] == "opening"
    assert row["opening"]["ledger_amount"] == "0.00"
    assert _validate(app) == []


def test_correct_after_import(app, client):
    batch = _save_template(client, _statement("10000.00"))["batch_id"]
    assert (
        _post_as_shown(client, batch, _first_prompt(client, batch)).status_code == 200
    )
    assert client.post(f"/api/commit/{batch}").status_code == 200

    opening = _row(client)["opening"]
    resp = client.put(
        "/api/openings",
        json={
            "account": BANK,
            "amount": "12500.00",
            "direction": "have",
            "date": opening["date"],
            "balance_check": False,
            "expected": {
                "date": opening["date"],
                "ledger_amount": opening["ledger_amount"],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "manual"

    corrected = _row(client)["opening"]
    assert corrected["ledger_amount"] == "12500.00"
    assert corrected["source"] == "manual"
    assert _validate(app) == []


# ── Settings checklist: the shown values are accepted as shown ──────────────
def _txn(when: date, *legs: tuple[str, str], narration: str = "Groceries"):
    return data.Transaction(
        data.new_metadata("<test>", 0, {"txn-id": f"t-{when}-{legs[0][0]}"}),
        when,
        "*",
        None,
        narration,
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(a, Amount(Decimal(n), "INR"), None, None, None, None)
            for a, n in legs
        ],
    )


@pytest.mark.parametrize(
    ("account", "first", "direction", "status", "suggested"),
    [
        ("Assets:Checking:Acme", date(2025, 4, 9), "have", "missing", "2025-03-31"),
        ("Assets:Checking:Acme", date(2025, 6, 12), "have", "missing", "2025-06-11"),
        ("Liabilities:CreditCard:Zeta", date(2025, 4, 20), "owe", "missing", "2025-04-19"),
        ("Liabilities:People:Ravi", date(2025, 7, 2), "owe", "missing", "2025-07-01"),
        ("Assets:Checking:Quiet", None, "have", "empty", "2026-03-31"),
    ],
    ids=["bank-april", "bank-mid-month", "card", "person", "empty"],
)  # fmt: skip
def test_checklist_add_accepts_the_suggested_date(
    app, client, account, first, direction, status, suggested
):
    """GET /api/openings suggested_date, posted back unchanged with a typed
    amount (no batch, as the Settings "Add opening" form does), is accepted."""
    ledger = app.state.ledger
    if first is None:
        ledger.open_named_accounts([account])
    else:
        ledger.append([_txn(first, (account, "-25.00"), ("Expenses:Food", "25.00"))])
    row = _row(client, account)
    assert (row["status"], row["suggested_date"]) == (status, suggested)

    resp = client.post(
        "/api/openings",
        json={
            "account": account,
            "amount": "1500.00",
            "direction": direction,
            "date": row["suggested_date"],
        },
    )

    assert resp.status_code == 200, resp.text
    after = _row(client, account)
    assert (after["status"], after["warn"]) == ("opening", False)
    assert after["opening"]["date"] == suggested
    assert _validate(app) == []


def test_checklist_correct_of_a_shared_late_opening_as_shown(app, client):
    """A mid-year "Liability Reset" opening two people at once, after one of
    them already had activity: the late row's suggested_date and expected,
    PUT unchanged, split the account out before its first posting."""
    asha, bala = "Liabilities:People:Asha", "Liabilities:People:Bala"
    ledger = app.state.ledger
    ledger.append(
        [
            _txn(date(2025, 4, 1), (asha, "-300.00"), ("Expenses:Food", "300.00")),
            _txn(
                date(2025, 12, 1),
                (asha, "-685.00"),
                (bala, "-120.00"),
                ("Equity:Opening-Balances", "805.00"),
                narration="Liability Reset",
            ),
        ]
    )
    row = _row(client, asha)
    assert (row["status"], row["warn"]) == ("opening", True)
    assert row["opening"]["shared"] is True
    assert row["suggested_date"] == "2025-03-31"
    o = row["opening"]

    resp = client.put(
        "/api/openings",
        json={
            "account": asha,
            "amount": o["amount"],
            "direction": o["direction"],
            "date": row["suggested_date"],
            "balance_check": False,
            "expected": {"date": o["date"], "ledger_amount": o["ledger_amount"]},
        },
    )

    assert resp.status_code == 200, resp.text
    after = _row(client, asha)
    assert (after["status"], after["warn"]) == ("opening", False)
    assert after["opening"]["date"] == "2025-03-31"
    assert after["opening"]["shared"] is False
    # Bala's part of the reset is untouched.
    assert _row(client, bala)["opening"]["date"] == "2025-12-01"
    assert _validate(app) == []


# ── Q29: an earlier export imported after an April-rule opening ─────────────
def _mid_april_statement() -> bytes:
    return (
        "Account Statement\n"
        "Opening Balance,10000.00\n"
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "20/04/2025,ACME SALARY APR,,50000.00\n"
        "25/04/2025,FICTIVE GROCER BANGALORE,1200.50,\n"
    ).encode()


def _early_april_statement() -> bytes:
    return (
        "Account Statement\n"
        "Opening Balance,9000.00\n"
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "03/04/2025,FICTIVE CAFE MYSURU,250.00,\n"
        "15/04/2025,FICTIVE BOOKS,750.00,\n"
    ).encode()


def test_earlier_april_export_after_an_april_rule_opening_warns(app, client):
    batch = _save_template(client, _mid_april_statement())["batch_id"]
    prompt = _first_prompt(client, batch)
    assert (prompt["statement_start"], prompt["suggested_date"]) == (
        "2025-04-20",
        "2025-03-31",
    )
    assert _post_as_shown(client, batch, prompt).status_code == 200
    assert client.post(f"/api/commit/{batch}").status_code == 200
    assert _row(client)["warn"] is False

    early = _detect(client, _early_april_statement(), "apr-early.csv")
    assert early["matched"] is True, early
    assert client.post(f"/api/commit/{early['batch_id']}").status_code == 200

    row = _row(client)
    assert (row["status"], row["warn"]) == ("opening", True)
    listing = client.get("/api/openings").json()
    assert listing["missing_count"] == 1 and listing["warning"]
    doctor = client.get("/api/reports/doctor").json()["openings"]
    assert [m["account"] for m in doctor["missing"]] == [BANK]
