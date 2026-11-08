from __future__ import annotations

import io
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.cli import ingest as cli
from backend.app.cli.port import HttpIngestPort
from backend.app.cli.render import OUTPUT_CAP
from backend.app.server.main import create_app


STDOUT_BUDGET = 90_000
# Per-command bounds: a dry run must stay small enough to iterate on, and a
# stage view is one capped page.
COMMAND_BUDGET = {
    "dry-run": 8_000,
    "stage": 20_000,
    "opening-check": 4_000,
    "openings": 12_000,
}


def _synthetic(path: Path, n: int = 3000) -> None:
    lines = ["Date,Narration,Debit,Credit,Balance"]
    bal = Decimal("100000.00")
    start = date(2020, 4, 1)
    for i in range(n):
        d = (start + timedelta(days=i // 2)).strftime("%d/%m/%Y")
        if i % 7 == 0:
            bal += Decimal("2500.00")
            lines.append(
                f"{d},UPI/CR/{900000000000 + i}/REFUND MERCHANT {i},,2500.00,{bal}"
            )
        else:
            bal -= Decimal("123.45")
            lines.append(
                f"{d},UPI/DR/{800000000000 + i}/SOME LONG MERCHANT NAME {i},123.45,,{bal}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_full_run_stays_inside_the_stdout_budget(tmp_path):
    src = tmp_path / "big.csv"
    _synthetic(src)
    app = create_app(storage_dir=tmp_path / "s", clock=lambda: date(2026, 10, 16))
    port = HttpIngestPort(client=TestClient(app))
    printed: list[str] = []

    def run(*argv, stdin=""):
        out = io.StringIO()
        code = cli.main(list(argv), port=port, stdin=io.StringIO(stdin), stdout=out)
        printed.append(out.getvalue())
        assert len(out.getvalue()) <= OUTPUT_CAP + 1
        bound = COMMAND_BUDGET.get(argv[0])
        assert bound is None or len(out.getvalue()) <= bound, (
            argv[0],
            len(out.getvalue()),
        )
        return code, out.getvalue()

    pid = json.loads(run("inspect", str(src))[1])["prove_id"]
    run("peek", pid)
    draft = {
        "institution": "Synthetic",
        "header_row_index": 0,
        "date_column": "Date",
        "date_format": "%m/%d/%Y",
        "narration_column": "Narration",
        "debit_column": "Debit",
        "credit_column": "Credit",
        "balance_column": "Balance",
        "default_account": "Assets:Checking:HDFC",
    }
    claims = {"first_data_row": 1, "last_data_row": 3000}
    assert (
        run(
            "dry-run",
            pid,
            "--draft",
            "-",
            stdin=json.dumps({"draft": draft, "claims": claims}),
        )[0]
        == 2
    )
    run("dry-run", pid, "--rows", "1:5")
    draft["date_format"] = "%d/%m/%Y"
    assert (
        run(
            "dry-run",
            pid,
            "--draft",
            "-",
            stdin=json.dumps({"draft": draft, "claims": claims}),
        )[0]
        == 0
    )
    batch = json.loads(run("stage", pid, "--use", "proof")[1])["batch_id"]
    run("show", batch, "--page", "2")
    code, out = run("opening-check", batch)
    assert code == 0 and json.loads(out)["prompts"]
    assert run("openings")[0] == 0
    total = sum(len(p) for p in printed)
    assert total <= STDOUT_BUDGET, total
