from __future__ import annotations

import io
import json
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app.core.paths import REPO_ROOT
from backend.app.server.main import create_app
from tests.llm.flows.prove_template.reference_drafts import REFERENCE

FIX = REPO_ROOT / "tests" / "fixtures" / "statements"
EXPECTED = REPO_ROOT / "tests" / "fixtures" / "agent"


def _essentials(cands):
    return [
        {
            k: c[k]
            for k in ("date", "amount", "direction", "raw_narration", "source_account")
        }
        for c in cands
    ]


@pytest.mark.parametrize("name", sorted(REFERENCE))
def test_reference_draft_passes_and_matches_expected(tmp_path, name):
    ref = REFERENCE[name]
    client = TestClient(
        create_app(storage_dir=tmp_path / "s", clock=lambda: date(2026, 10, 16))
    )
    if ref["open"]:
        client.post("/api/accounts", json={"accounts": ref["open"]})
    raw = (FIX / f"{name}.csv").read_bytes()
    run = client.post(
        "/api/agent/inspect",
        files={"file": (f"{name}.csv", io.BytesIO(raw), "text/csv")},
    ).json()
    dry = client.post(
        "/api/agent/dry-run",
        json={
            "prove_id": run["prove_id"],
            "draft": ref["draft"],
            "claims": ref["claims"],
        },
    ).json()
    assert dry["report"]["passed"], dry["report"]
    got = _essentials(dry["candidates"])
    path = EXPECTED / f"{name}.expected.json"
    if os.environ.get("UPDATE_AGENT_EXPECTED"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(got, indent=2) + "\n", encoding="utf-8")
    assert got == json.loads(path.read_text(encoding="utf-8"))


def test_running_balance_fixture_verifies_the_sign(tmp_path):
    ref = REFERENCE["sample_bank_running_balance"]
    client = TestClient(
        create_app(storage_dir=tmp_path / "s", clock=lambda: date(2026, 10, 16))
    )
    raw = (FIX / "sample_bank_running_balance.csv").read_bytes()
    run = client.post(
        "/api/agent/inspect", files={"file": ("r.csv", io.BytesIO(raw), "text/csv")}
    ).json()
    dry = client.post(
        "/api/agent/dry-run",
        json={
            "prove_id": run["prove_id"],
            "draft": ref["draft"],
            "claims": ref["claims"],
        },
    ).json()
    checks = {c["name"]: c["status"] for c in dry["report"]["checks"]}
    assert checks["running_balance"] == "pass" and checks["opening_closing"] == "pass"
    flipped = dict(ref["draft"], amount_sign="indicator", sign_column="Ref")
    bad = client.post(
        "/api/agent/dry-run",
        json={"prove_id": run["prove_id"], "draft": flipped, "claims": ref["claims"]},
    ).json()
    assert not bad["report"]["passed"]
    flipped_checks = {c["name"]: c["status"] for c in bad["report"]["checks"]}
    assert flipped_checks["running_balance"] == "fail"


def test_groww_goes_dedicated_and_kite_is_refused(tmp_path):
    client = TestClient(
        create_app(storage_dir=tmp_path / "s", clock=lambda: date(2026, 10, 16))
    )
    g = client.post(
        "/api/agent/inspect",
        files={
            "file": (
                "g.csv",
                io.BytesIO((FIX / "groww_orders.csv").read_bytes()),
                "text/csv",
            )
        },
    ).json()
    assert g["dedicated"] == "groww_mf"
    k = client.post(
        "/api/agent/inspect",
        data={"kind": "stocks"},
        files={
            "file": (
                "k.csv",
                io.BytesIO((FIX / "kite_orders.csv").read_bytes()),
                "text/csv",
            )
        },
    )
    assert k.status_code == 422 and "E1080" in k.text
