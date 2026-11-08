"""Integration test for GET /api/reports/investments/changes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from backend.app.models.domain.nav import NavRow


def _client(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    return TestClient(app)


def test_investment_changes_route_exists(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/reports/investments/changes")
    # Empty ledger → gains() returns holdings=[] → 200 with empty funds list
    assert resp.status_code == 200


def test_investment_changes_shape_with_one_priced_fund(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")

    mock_gains = {
        "holdings": [
            {
                "symbol": "MF122639",
                "name": "PPFC",
                "units": "100",
                "nav": "11.00",
                "nav_date": "2026-06-30",
                "current_value": "1100",
                "cost_basis": "1000",
                "unrealized_gain": "100",
                "abs_return_pct": "10.00",
                "current_xirr": None,
                "lifetime_xirr": "12.00",
            }
        ],
        "unpriced": [],
        "unpriced_count": 0,
        "totals": {
            "current_value": "1100",
            "cost_basis": "1000",
            "unrealized_gain": "100",
            "abs_return_pct": "10.00",
            "current_xirr": None,
            "lifetime_xirr": "12.00",
        },
        "as_of": "2026-06-30",
    }

    mock_store = MagicMock()
    mock_store.as_of.return_value = date(2026, 6, 30)
    mock_store.date_range.return_value = (date(2024, 1, 1), date(2026, 6, 30))
    mock_store.nav_as_of.return_value = {
        "122639": NavRow("122639", Decimal("10.00"), date(2026, 5, 30))
    }

    app.state.investment_service = MagicMock()
    app.state.investment_service.gains.return_value = mock_gains
    app.state.price_service._nav_store = mock_store

    resp = TestClient(app).get("/api/reports/investments/changes")
    assert resp.status_code == 200
    body = resp.json()

    assert "portfolio_windows" in body
    assert "funds" in body
    assert isinstance(body["backfill_needed"], bool)
    assert len(body["funds"]) == 1
    assert body["funds"][0]["symbol"] == "MF122639"
    assert len(body["funds"][0]["windows"]) == 4  # 1D, 1M, 1Y, SI

    # At least one window should have a return_pct (the 1M window has mocked prior data)
    window_labels = [w["label"] for w in body["funds"][0]["windows"]]
    assert window_labels == ["1D", "1M", "1Y", "SI"]


def test_day_change_anchors_on_each_funds_own_nav_date(tmp_path):
    """A fund whose newest NAV (Mon 11-02) lags the store's newest date
    (Tue 11-03, another fund's) must report 11-02 vs Fri 10-30 -- not be
    stamped 11-03 and compared with its own 11-02 NAV as a flat 0.00%."""
    from backend.app.persistence.nav_store import NavStore

    app = create_app(storage_dir=tmp_path / "storage")
    store = NavStore(tmp_path / "nav.parquet")
    store.append(
        [
            NavRow("122639", Decimal("88.00"), date(2026, 10, 30)),
            NavRow("122639", Decimal("89.7397"), date(2026, 11, 2)),
            NavRow("135781", Decimal("55.00"), date(2026, 11, 2)),
            NavRow("135781", Decimal("55.352"), date(2026, 11, 3)),
        ]
    )

    def _h(sym: str, nav: str, nav_date: str) -> dict:
        return {
            "symbol": sym,
            "name": sym,
            "units": "100",
            "nav": nav,
            "nav_date": nav_date,
            "current_value": "1",
            "cost_basis": "1",
            "unrealized_gain": "0",
            "abs_return_pct": None,
            "current_xirr": None,
            "lifetime_xirr": None,
        }

    app.state.investment_service = MagicMock()
    app.state.investment_service.gains.return_value = {
        "holdings": [
            _h("MF122639", "89.7397", "2026-11-02"),
            _h("MF135781", "55.352", "2026-11-03"),
        ],
        "unpriced": [],
        "unpriced_count": 0,
        "totals": {},
        "as_of": "2026-11-03",
    }
    app.state.price_service._nav_store = store

    body = TestClient(app).get("/api/reports/investments/changes").json()
    one_day = {f["symbol"]: f["windows"][0] for f in body["funds"]}
    assert one_day["MF122639"]["date_then"] == "2026-10-30"
    assert one_day["MF122639"]["return_pct"] == "1.98"
    assert one_day["MF122639"]["value_change"] == "173.97"
    assert one_day["MF135781"]["date_then"] == "2026-11-02"
    assert one_day["MF135781"]["return_pct"] == "0.64"
