from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates


def _client(tmp_path):
    return TestClient(create_app(storage_dir=tmp_path / "storage"))


def test_investments_route_returns_holdings_with_decimal_strings(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    commit_candidates(
        app.state.ledger,
        app.state.investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 1, 10),
                amount=Decimal("15000"),
                raw_narration="MF",
                normalized_narration="MF",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                suggested_other_leg="Assets:Investment:MutualFund:Groww:ParagParikh",
            ),
            CandidateTxn(
                date=date(2026, 1, 12),
                amount=Decimal("5000000"),
                raw_narration="HOME",
                normalized_narration="HOME",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                suggested_other_leg="Assets:Investment:RealEstate:Home",
            ),
        ],
    )
    client = TestClient(app)
    resp = client.get("/api/reports/investments")
    assert resp.status_code == 200
    j = resp.json()
    assert j["total"] == "5015000"
    # amounts are strings, sorted desc (RealEstate first)
    assert isinstance(j["total"], str)
    assert j["by_vehicle"][0] == {"vehicle": "RealEstate", "amount": "5000000"}
    mf = next(h for h in j["holdings"] if h["vehicle"] == "MutualFund")
    assert mf["instrument"] == "ParagParikh"
    assert isinstance(mf["amount"], str)


def test_investments_route_merges_mutual_fund_detail_from_investment_ledger(
    tmp_path,
):
    """Post-detach: MutualFund's per-scheme granularity lives in
    investment_ledger, while other vehicles (RealEstate, FD, ...) still live
    only in the main ledger. `/api/reports/investments` must surface both
    without double-counting the coarse MutualFund vehicle line that also
    exists in main.beancount."""
    app = create_app(storage_dir=tmp_path / "storage")
    commit_candidates(
        app.state.ledger,
        app.state.investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 18),
                amount=Decimal("21500.00"),
                raw_narration="Purchase — Parag Parikh Flexi Cap",
                normalized_narration="Parag Parikh Flexi Cap",
                source_account="Assets:Investment:MutualFund:Groww:Cash",
                direction=Direction.DEBIT,
                units=Decimal("100.0"),
                commodity="MF122639",
                unit_cost=Decimal("215.0"),
            ),
            CandidateTxn(
                date=date(2026, 1, 12),
                amount=Decimal("5000000"),
                raw_narration="HOME",
                normalized_narration="HOME",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                suggested_other_leg="Assets:Investment:RealEstate:Home",
            ),
        ],
    )
    client = TestClient(app)
    resp = client.get("/api/reports/investments")
    assert resp.status_code == 200
    j = resp.json()
    assert j["total"] == "5021500.00"
    by_vehicle = {v["vehicle"]: v["amount"] for v in j["by_vehicle"]}
    assert by_vehicle["RealEstate"] == "5000000"
    assert by_vehicle["MutualFund"] == "21500.00"
    mf = next(h for h in j["holdings"] if h["vehicle"] == "MutualFund")
    assert mf["account"] == "Assets:Investment:MutualFund:Groww:MF122639"


def test_investments_route_empty_ledger(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/reports/investments")
    assert resp.status_code == 200
    assert resp.json() == {
        "total": "0",
        "holdings": [],
        "by_vehicle": [],
        "by_issuer": [],
    }
