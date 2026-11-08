"""Premise 1, end to end through the real app and the agent CLI.

A user sets a category and a tag at review. Committing must (1) write the tag
into the journal, (2) show it in the grouped tags report, (3) learn exactly the
rules the review promised, and (4) make the next import of that merchant arrive
already categorised, from the learned rule. Fictional data only.
"""

from __future__ import annotations

import importlib.util
import io
import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.app.cli.port import HttpIngestPort
from backend.app.server.main import create_app

_spec = importlib.util.spec_from_file_location(
    "ingest_cli", Path("scripts/ingest_cli.py")
)
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

ACCOUNT = "Assets:Checking:HDFC"
BIKES = "Expenses:Travel:Bike"
EMI = "Expenses:Loans:Emi"
DRAFT = json.dumps(
    {
        "draft": {
            "institution": "HDFC",
            "header_row_index": 0,
            "date_column": "Date",
            "date_format": "%Y-%m-%d",
            "narration_column": "Narration",
            "debit_column": "Withdrawal Amt.",
            "credit_column": "Deposit Amt.",
            "default_account": ACCOUNT,
        },
        "claims": {"first_data_row": 1, "last_data_row": 3},
    }
)
FIRST_IMPORT = (
    "Date,Narration,Withdrawal Amt.,Deposit Amt.,Chq./Ref.No.\n"
    "2026-03-05,FICTIVEBIKES BANGALORE,420.50,,REF-1\n"
    "2026-03-06,FICTIVEBIKES BENGALURU,310.00,,REF-2\n"
    "2026-03-07,OFFUS EMI 4455,1200.00,,REF-3\n"
)
SECOND_IMPORT = (
    "Date,Narration,Withdrawal Amt.,Deposit Amt.,Chq./Ref.No.\n"
    "2026-04-09,FICTIVEBIKES MYSURU,77.00,,REF-4\n"
)


def _app(storage: Path):
    return create_app(storage_dir=storage, clock=lambda: date(2026, 10, 16))


def _run(port, *argv, stdin=""):
    out = io.StringIO()
    code = cli.main(list(argv), port=port, stdin=io.StringIO(stdin), stdout=out)
    return code, out.getvalue()


def _stage(port, csv_path: Path, last_row: int, *, known: bool = False) -> str:
    """Stage a CSV. `known`: the layout was saved by an earlier import."""
    code, out = _run(port, "inspect", str(csv_path), "--kind", "bank")
    assert code == 0, out
    pid = json.loads(out)["prove_id"]
    if known:
        code, out = _run(port, "stage", pid, "--use", "template_match")
        assert code == 0, out
        return json.loads(out)["batch_id"]
    draft = json.loads(DRAFT)
    draft["claims"]["last_data_row"] = last_row
    code, out = _run(port, "dry-run", pid, "--draft", "-", stdin=json.dumps(draft))
    assert code == 0 and json.loads(out)["summary"]["passed"] is True, out
    code, out = _run(port, "stage", pid, "--use", "proof")
    assert code == 0, out
    return json.loads(out)["batch_id"]


def _rows(port, batch: str) -> dict[str, dict]:
    """The review rows keyed by a narration fragment's row, via `show`."""
    return {r["n"]: r for r in json.loads(_run(port, "show", batch)[1])["rows"]}


def _n(port, batch: str, narration: str) -> int:
    rows = _rows(port, batch)
    (n,) = [n for n, r in rows.items() if narration in r["narration"]]
    return n


def _journal(storage: Path) -> str:
    return "\n".join(
        p.read_text() for p in sorted((storage / "journal").glob("*.beancount"))
    )


def _learned(storage: Path) -> list[dict]:
    path = storage / "rules" / "learned.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else []


def _candidates(port, batch: str) -> list[dict]:
    """The raw review candidates in staging order (row n is index n - 1)."""
    return port.review(batch)["candidates"]


@pytest.fixture
def storage(tmp_path: Path) -> Path:
    return tmp_path / "storage"


def test_category_and_tag_at_review_reach_the_journal_the_report_and_the_next_import(
    tmp_path, storage
):
    first = tmp_path / "first.csv"
    first.write_text(FIRST_IMPORT)
    second = tmp_path / "second.csv"
    second.write_text(SECOND_IMPORT)

    client = TestClient(_app(storage))
    port = HttpIngestPort(client=client)
    assert _learned(storage) == []

    # --- review: a category, two tags, and a generic row that must not be learned
    batch = _stage(port, first, 3)
    bangalore = _n(port, batch, "FICTIVEBIKES BANGALORE")
    bengaluru = _n(port, batch, "FICTIVEBIKES BENGALURU")
    emi = _n(port, batch, "OFFUS EMI")
    assert _run(port, "category", batch, str(bangalore), BIKES)[0] == 0
    assert _run(port, "category", batch, str(emi), EMI)[0] == 0
    code, out = _run(port, "tag", batch, str(bangalore), "Madikeri Oct-2026")
    assert code == 0 and json.loads(out)["tag"] == "madikeri-oct-2026"
    assert _run(port, "tag", batch, str(bengaluru), "travel/Madikeri Oct-2026")[0] == 0

    # --- show: the category spread to the same-merchant row, and the review
    # promises exactly one rule (the generic EMI row is declined with a reason)
    view = json.loads(_run(port, "show", batch)[1])
    by_n = {r["n"]: r for r in view["rows"]}
    assert by_n[bengaluru]["category"] == BIKES
    assert by_n[bengaluru]["source"] == "taught this batch"
    promised = view["will_learn"]
    assert [(w["pattern"], w["category"]) for w in promised] == [
        ("FICTIVEBIKES", BIKES)
    ]
    # only the row the user set teaches; the spread row is a consequence
    assert promised[0]["rows"] == [bangalore]
    declined = [x for x in view["not_learned"] if emi in x["rows"]]
    assert declined and declined[0]["reason"]
    assert {BIKES, EMI} <= set(view["would_open"])

    # --- commit
    code, out = _run(port, "commit", batch)
    assert code == 0, out
    result = json.loads(out)
    assert result["committed"] == 3
    assert [(w["pattern"], w["category"]) for w in result["learned"]] == [
        ("FICTIVEBIKES", BIKES)
    ]

    # 1. the journal holds the normalised tags
    journal = _journal(storage)
    assert "#madikeri-oct-2026" in journal
    assert "#travel/madikeri-oct-2026" in journal

    # 2. the tags report shows them, grouped by the part before the first "/"
    report = client.get("/api/reports/expense/tags").json()
    assert {t["tag"] for t in report["by_tag"]} == {
        "madikeri-oct-2026",
        "travel/madikeri-oct-2026",
    }
    groups = {g["prefix"]: g for g in report["groups"]}
    assert [t["tag"] for t in groups["travel"]["tags"]] == ["travel/madikeri-oct-2026"]
    assert groups["travel"]["amount"] == "310.00" and groups["travel"]["count"] == 1
    assert [t["tag"] for t in groups["madikeri-oct-2026"]["tags"]] == [
        "madikeri-oct-2026"
    ]

    # 3. learned.yaml gained exactly the promised rule, nothing for the EMI row
    assert _learned(storage) == [{"pattern": "FICTIVEBIKES", "category": BIKES}]

    # 4. a later import of the same merchant, on a fresh app that reads the
    # rules back from disk, arrives categorised by the learned rule
    port2 = HttpIngestPort(client=TestClient(_app(storage)))
    batch2 = _stage(port2, second, 1, known=True)
    mysuru = _n(port2, batch2, "FICTIVEBIKES MYSURU")
    rows = _candidates(port2, batch2)
    row = rows[mysuru - 1]
    assert row["suggested_category"] == BIKES
    assert row["category_source"] == "rule"
