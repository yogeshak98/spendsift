from __future__ import annotations

from datetime import date
from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.position import Cost
from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from backend.app.models.domain.nav import NavRow


class StubNavStore:
    def __init__(self, navs, as_of):
        self._navs = navs
        self._as_of = as_of

    def latest(self, codes):
        return {c: self._navs[c] for c in codes if c in self._navs}

    def as_of(self):
        return self._as_of


def _seed_buy(ledger):
    ledger.ensure_commodity_accounts([("MF111", "Held Priced Fund")])
    meta = data.new_metadata("<t>", 0)
    postings = [
        data.Posting(
            "Assets:Investment:MutualFund:Groww:MF111",
            Amount(Decimal("100"), "MF111"),
            Cost(Decimal("100"), "INR", date(2024, 6, 29), None),
            None,
            None,
            None,
        ),
        data.Posting(
            "Assets:Investment:MutualFund:Groww:Cash",
            Amount(Decimal("-10000"), "INR"),
            None,
            None,
            None,
            None,
        ),
    ]
    ledger.append(
        [
            data.Transaction(
                meta,
                date(2024, 6, 29),
                "*",
                None,
                "buy",
                data.EMPTY_SET,
                data.EMPTY_SET,
                postings,
            )
        ]
    )


def test_gains_route_returns_typed_contract(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed_buy(app.state.investment_ledger)
    # Replace the wired NavStore with a stub (no parquet / no network in tests).
    app.state.investment_service._nav_store = StubNavStore(
        {"111": NavRow(scheme_code="111", nav=Decimal("130"), date=date(2026, 6, 29))},
        as_of=date(2026, 6, 29),
    )
    client = TestClient(app)
    r = client.get("/api/reports/investments/gains")
    assert r.status_code == 200
    j = r.json()
    assert j["as_of"] == "2026-06-29"
    assert j["totals"]["current_value"] == "13000.00"
    assert j["totals"]["cost_basis"] == "10000.00"
    assert j["totals"]["unrealized_gain"] == "3000.00"
    assert j["holdings"][0]["symbol"] == "MF111"
    assert j["holdings"][0]["name"] == "Held Priced Fund"
    assert isinstance(j["totals"]["current_value"], str)
    assert j["unpriced"] == []
    assert j["unpriced_count"] == 0
