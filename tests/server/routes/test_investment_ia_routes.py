from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount
from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.persistence.journal import Ledger
from backend.app.persistence.nav_store import NavStore
from backend.app.models.domain.nav import NavRow


def _open_accounts(ledger: Ledger, lines: list[str]) -> None:
    """Write open/commodity directives directly into the main journal file --
    mirrors tests/service/reports/investments/test_investment_hub.py's helper, needed because
    `ensure_commodity_accounts` only knows how to open MutualFund accounts."""
    main = ledger.main_file.read_text(encoding="utf-8")
    ledger.main_file.write_text(
        main.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )


def _buy_commodity(
    ledger: Ledger, account: str, symbol: str, qty: str, price: str, when: date
) -> None:
    cost = data.Cost(Decimal(price), "INR", when, None)
    txn = data.Transaction(
        data.new_metadata("<test>", 0),
        when,
        "*",
        None,
        f"buy {symbol}",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(account, Amount(Decimal(qty), symbol), cost, None, None, None),
            data.Posting(
                "Assets:Checking:HDFC",
                Amount(-Decimal(qty) * Decimal(price), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )
    ledger.append([txn])


def _deposit(ledger: Ledger, account: str, amount: str, when: date) -> None:
    txn = data.Transaction(
        data.new_metadata("<test>", 0),
        when,
        "*",
        None,
        "FD deposit",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(
                account, Amount(Decimal(amount), "INR"), None, None, None, None
            ),
            data.Posting(
                "Assets:Checking:HDFC",
                Amount(-Decimal(amount), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )
    ledger.append([txn])


# Fixed price dates, read against a FIXED clock.
#
# `source_state` is derived in backend/app/service/reports/health/freshness.py against the app's
# clock with a 7-day staleness boundary. These fixtures were once pinned to
# 2026-09-04/03 while the clock was wall-clock: they passed until 2026-09-11
# and then failed every run afterwards with "stale", which was the CORRECT
# answer for a 36-day-old close. The product was right; the tests had rotted.
#
# `create_app(clock=...)` pins the other side of that comparison, so literal
# dates are safe again and a test can say exactly which freshness boundary it
# is standing on. The one-day gap is load-bearing: the MF NAV must be older
# than the equity close so `stalest_as_of` and `as_of` are distinguishable,
# which is what the mixed-vehicle test below exists to pin. _TODAY sits two
# days after the close, inside the 7-day window, so the classification under
# test is "cached".
_EQUITY_CLOSE = date(2026, 9, 4)
_MF_NAV = date(2026, 9, 3)
_TODAY = date(2026, 9, 6)


def _seeded_app(tmp_path):
    """One stock (SBIN, priced), one FD (unvalued), one mutual fund purchased
    through the real ingestion path so its lots land in investment_ledger and
    the coarse cash leg lands in main.beancount -- the real production split
    this unit exists to wire correctly."""
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: _TODAY)
    _open_accounts(
        app.state.ledger,
        [
            '2000-01-01 commodity SBIN\n  name: "State Bank of India"',
            '2000-01-01 open Assets:Investment:Stock:SBIN SBIN "FIFO"',
            "2000-01-01 open Assets:Investment:FD:HDFC INR",
        ],
    )
    _buy_commodity(
        app.state.ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 6, 18),
    )
    _deposit(
        app.state.ledger, "Assets:Investment:FD:HDFC", "750000.00", date(2024, 1, 1)
    )
    equity_store = NavStore(
        app.state.ledger.storage_dir / "prices" / "equity_history.parquet"
    )
    equity_store.append([NavRow("SBIN", Decimal("662.50"), _EQUITY_CLOSE)])

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
        ],
    )
    mf_store = NavStore(app.state.ledger.storage_dir / "prices" / "nav_history.parquet")
    mf_store.append([NavRow("122639", Decimal("220.00"), _MF_NAV)])
    return app


def test_investment_summary_full_body_on_empty_ledger(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)
    resp = client.get("/api/reports/investments/summary")
    assert resp.status_code == 200
    assert resp.json() == {
        "as_of": None,
        "stalest_as_of": None,
        "source_state": "stale",
        "totals": {
            "cost_basis": "0.00",
            "current_value": "0.00",
            "unrealized_gain": "0.00",
            "unvalued_count": 0,
            "is_partial": False,
        },
        "vehicles": [],
    }


def test_investment_summary_merges_mf_from_investment_ledger_without_double_count(
    tmp_path,
):
    """The regression this unit exists to guard: MutualFund's per-scheme lots
    live only in investment_ledger while Stock/FD live only in the main
    ledger. If the hub route naively summed both ledgers' own investment_hub()
    totals wholesale, or failed to drop the unresolved MutualFund line from
    the main ledger's call, the mutual-fund cost basis would be missing or
    doubled. Assert the exact figure, not an inequality, and cross-check it
    against the existing (already-correct) /api/reports/investments route."""
    app = _seeded_app(tmp_path)
    client = TestClient(app)

    summary = client.get("/api/reports/investments/summary").json()
    mf = next(v for v in summary["vehicles"] if v["key"] == "mutual_fund")
    assert mf["cost_basis"] == "21500.00"

    legacy = client.get("/api/reports/investments").json()
    by_vehicle = {v["vehicle"]: v["amount"] for v in legacy["by_vehicle"]}
    assert by_vehicle["MutualFund"] == "21500.00"
    assert mf["cost_basis"] == by_vehicle["MutualFund"]


def test_investment_summary_full_body_with_mixed_vehicles(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["as_of"] == _EQUITY_CLOSE.isoformat()
    # stalest_as_of is the MF's NAV date, one day older than the stock's
    # equity close -- the headline source_state below is derived from this
    # oldest date, not the newest.
    assert body["stalest_as_of"] == _MF_NAV.isoformat()
    assert body["source_state"] == "cached"
    # cost = 59000.00 (stock) + 750000.00 (FD, unvalued) + 21500.00 (MF)
    # value = 66250.00 (stock) + 22000.00 (MF, priced at 220.00/unit) --
    # FD is unvalued and excluded from current_value.
    assert body["totals"]["cost_basis"] == "830500.00"
    assert body["totals"]["current_value"] == "88250.00"
    assert body["totals"]["unrealized_gain"] == "7750.00"
    assert body["totals"]["unvalued_count"] == 1  # FD only -- MF is priced
    assert body["totals"]["is_partial"] is True

    assert isinstance(body["totals"]["cost_basis"], str)
    assert isinstance(body["totals"]["current_value"], str)
    assert isinstance(body["totals"]["unrealized_gain"], str)

    # Assert the list length too, not just the set of keys: a duplicated
    # "mutual_fund" vehicle (e.g. the double-count regression this route
    # exists to prevent) collapses to the same set but must never pass here.
    keys = [v["key"] for v in body["vehicles"]]
    assert len(keys) == 3
    assert set(keys) == {"stock", "fd", "mutual_fund"}

    fd = next(v for v in body["vehicles"] if v["key"] == "fd")
    assert fd == {
        "key": "fd",
        "label": "FD",
        "holdings": 1,
        "cost_basis": "750000.00",
        "current_value": None,
        "unrealized_gain": None,
        "mode": "unvalued",
        "as_of": None,
        # No price behind an at-cost vehicle means no date at EITHER end --
        # never a fabricated "oldest" just to keep the field shape uniform.
        "stalest_as_of": None,
        "is_partial": False,
    }

    stock = next(v for v in body["vehicles"] if v["key"] == "stock")
    assert stock["cost_basis"] == "59000.00"
    assert stock["current_value"] == "66250.00"
    assert isinstance(stock["cost_basis"], str)


def test_investment_summary_source_state_is_most_conservative_across_vehicles(
    tmp_path,
):
    """Regression for the fabricated-freshness defect: a fresh (today's) stock
    close must NOT carry a badly-stale MF NAV to a headline "live". The old
    `max(as_of)` rule would report `as_of`/`source_state` from the stock alone
    (today, "live"); the fix must report the headline `source_state` from the
    MF's stale NAV instead ("stale"), while `as_of` stays the newest date and
    `stalest_as_of` surfaces the MF's date explicitly."""
    today = date.today()
    stale_nav_date = today - timedelta(days=30)

    app = create_app(storage_dir=tmp_path / "storage")
    _open_accounts(
        app.state.ledger,
        [
            '2000-01-01 commodity SBIN\n  name: "State Bank of India"',
            '2000-01-01 open Assets:Investment:Stock:SBIN SBIN "FIFO"',
        ],
    )
    _buy_commodity(
        app.state.ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 6, 18),
    )
    equity_store = NavStore(
        app.state.ledger.storage_dir / "prices" / "equity_history.parquet"
    )
    equity_store.append([NavRow("SBIN", Decimal("662.50"), today)])

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
        ],
    )
    mf_store = NavStore(app.state.ledger.storage_dir / "prices" / "nav_history.parquet")
    mf_store.append([NavRow("122639", Decimal("220.00"), stale_nav_date)])

    client = TestClient(app)
    resp = client.get("/api/reports/investments/summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["as_of"] == today.isoformat()
    assert body["stalest_as_of"] == stale_nav_date.isoformat()
    assert body["source_state"] == "stale"


def test_freshness_is_conservative_WITHIN_a_vehicle_not_only_across_them(tmp_path):
    """The cross-screen contradiction this pair of fields exists to remove.

    ONE vehicle (Stock) holds two symbols: SBIN closed today, TCS closed 90
    days ago. Under the old contract the vehicle reported a single `as_of` =
    max(dates) = today, so the hub card badged the vehicle "live" while the
    Stock vehicle screen -- which derives its header from the STALEST holding
    -- called the same data "stale". Same data, two verdicts, one click apart.

    Both assertions below fail on the old behaviour:
      * the vehicle now carries its own `stalest_as_of` (the 90-day-old date),
        which is what the hub card classifies from, so the card and the screen
        reach the same verdict;
      * the response-level `stalest_as_of` is a MIN OVER THOSE per-vehicle
        minima, not a min-of-maxes -- it previously reported today's date under
        the UI's literal label "oldest", which was affirmatively false.
    """
    today = date.today()
    old_close = today - timedelta(days=90)

    app = create_app(storage_dir=tmp_path / "storage")
    _open_accounts(
        app.state.ledger,
        [
            '2000-01-01 commodity SBIN\n  name: "State Bank of India"',
            '2000-01-01 open Assets:Investment:Stock:SBIN SBIN "FIFO"',
            '2000-01-01 commodity TCS\n  name: "Tata Consultancy Services"',
            '2000-01-01 open Assets:Investment:Stock:TCS TCS "FIFO"',
        ],
    )
    _buy_commodity(
        app.state.ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 6, 18),
    )
    _buy_commodity(
        app.state.ledger,
        "Assets:Investment:Stock:TCS",
        "TCS",
        "10",
        "3500.00",
        date(2024, 6, 18),
    )
    equity_store = NavStore(
        app.state.ledger.storage_dir / "prices" / "equity_history.parquet"
    )
    equity_store.append(
        [
            NavRow("SBIN", Decimal("662.50"), today),
            NavRow("TCS", Decimal("3900.00"), old_close),
        ]
    )

    client = TestClient(app)
    summary = client.get("/api/reports/investments/summary").json()
    stock = next(v for v in summary["vehicles"] if v["key"] == "stock")

    # The vehicle's two ends, both real dates of real holdings.
    assert stock["as_of"] == today.isoformat()
    assert stock["stalest_as_of"] == old_close.isoformat()

    # The headline "oldest" is the genuinely oldest holding date -- NOT
    # min(per-vehicle max), which with a single vehicle would be today.
    assert summary["stalest_as_of"] == old_close.isoformat()
    assert summary["as_of"] == today.isoformat()
    assert summary["source_state"] == "stale"

    # And the vehicle screen behind that card reads the same two dates off
    # /investments/equity, so the two screens cannot disagree.
    equity = client.get("/api/reports/investments/equity").json()
    assert equity["as_of"] == today.isoformat()
    assert equity["stalest_as_of"] == old_close.isoformat()


def test_instrument_detail_stock_full_body(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/stock/SBIN")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "vehicle": "stock",
        "symbol": "SBIN",
        "name": "State Bank of India",
        "units": "100",
        "avg_cost": "590.00",
        "cost_basis": "59000.00",
        "lots": [
            {
                "date": "2024-06-18",
                "units": "100",
                "cost": "590.00",
                "cost_basis": "59000.00",
                "long_term_on": "2025-06-18",
            }
        ],
        "mode": "priced",
        "source_state": "cached",
        "as_of": _EQUITY_CLOSE.isoformat(),
        "price": "662.50",
        "price_date": _EQUITY_CLOSE.isoformat(),
        "current_value": "66250.00",
        "unrealized_gain": "7250.00",
        "gain_pct": "12.29",
    }
    for key in ("units", "avg_cost", "cost_basis", "price", "current_value"):
        assert isinstance(body[key], str)


def test_instrument_detail_mutual_fund_reads_investment_ledger(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/mutual_fund/MF122639")
    assert resp.status_code == 200
    body = resp.json()
    assert body["units"] == "100.0"
    assert body["cost_basis"] == "21500.00"
    assert body["mode"] == "priced"
    assert body["price"] == "220.00"
    assert body["current_value"] == "22000.00"
    assert body["unrealized_gain"] == "500.00"


def test_instrument_detail_unknown_vehicle_404s(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/real_estate/HOME")
    assert resp.status_code == 404
    assert resp.json()["code"] == "E1034"


def test_instrument_detail_unknown_instrument_404s(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/stock/NOPE")
    assert resp.status_code == 404
    assert resp.json()["code"] == "E1034"


def test_value_history_stock_no_price_history_returns_honest_empty(tmp_path):
    # Stock buy predates the equity feed's own history accumulation start, and
    # no equity_history rows are appended in this test at all -- value_history
    # must degrade to an empty, explained series rather than fabricate one.
    app = create_app(storage_dir=tmp_path / "storage")
    _open_accounts(
        app.state.ledger,
        [
            '2000-01-01 commodity SBIN\n  name: "State Bank of India"',
            '2000-01-01 open Assets:Investment:Stock:SBIN SBIN "FIFO"',
        ],
    )
    _buy_commodity(
        app.state.ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 6, 18),
    )
    client = TestClient(app)
    resp = client.get("/api/reports/investments/stock/SBIN/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["invested_points"] == [{"date": "2024-06-18", "invested": "59000.00"}]
    assert body["value_points"] == []
    assert body["covers_from"] is None
    assert "no stored price history" in body["truncated_reason"].lower()
    assert body["source_state"] == "stale"
    assert body["as_of"] is None
    assert body["coverage_starts_late"] is False


def test_value_history_mutual_fund_reads_investment_ledger(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/mutual_fund/MF122639/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["invested_points"] == [{"date": "2026-04-18", "invested": "21500.00"}]
    # Stored NAV history for MF122639 begins at _MF_NAV, well after the
    # 2026-04-18 purchase -- value coverage starts late, and the response says
    # so rather than fabricating an earlier value.
    assert body["value_points"] == [{"date": _MF_NAV.isoformat(), "value": "22000.00"}]
    assert body["covers_from"] == _MF_NAV.isoformat()
    assert body["coverage_starts_late"] is True
    assert f"begins {_MF_NAV.isoformat()}" in body["truncated_reason"]


def test_value_history_unknown_vehicle_404s(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/real_estate/HOME/history")
    assert resp.status_code == 404
    assert resp.json()["code"] == "E1034"


def test_value_history_known_vehicle_unknown_instrument_404s(tmp_path):
    """Finding 4's actual scenario: a typo'd or stale instrument under a
    KNOWN vehicle (not an unknown vehicle -- that's the test above) must 404
    rather than drawing an empty chart. Distinguish this from the honest
    200-with-empty-series case in
    `test_value_history_stock_no_price_history_returns_honest_empty`: that
    test's SBIN instrument DOES exist (it has a buy) and merely lacks stored
    prices, while "NOPE" here has no lots/postings under stock at all."""
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/stock/NOPE/history")
    assert resp.status_code == 404
    assert resp.json()["code"] == "E1034"

    # The known-instrument-but-no-prices case must still be a 200 with an
    # honest empty series -- confirm the two remain distinguishable rather
    # than both having collapsed into "error".
    known_resp = client.get("/api/reports/investments/stock/SBIN/history")
    assert known_resp.status_code == 200


def test_vehicle_value_history_mutual_fund_reads_investment_ledger(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/vehicles/mutual_fund/value-history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["invested_points"] == [{"date": "2026-04-18", "invested": "21500.00"}]
    assert body["value_points"] == [{"date": _MF_NAV.isoformat(), "value": "22000.00"}]
    assert body["coverage_starts_late"] is True


def test_vehicle_value_history_unknown_vehicle_404s(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/vehicles/real_estate/value-history")
    assert resp.status_code == 404
    assert resp.json()["code"] == "E1034"


def test_new_routes_do_not_shadow_existing_single_segment_routes(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    assert client.get("/api/reports/investments/gains").status_code == 200
    assert client.get("/api/reports/investments/equity").status_code == 200
    assert client.get("/api/reports/investments/changes").status_code == 200


# ── at-cost vehicle holdings (FD, IndWealth) ────────────────────────────────


def test_at_cost_holdings_full_body(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/vehicles/fd/holdings")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "key": "fd",
        "label": "FD",
        "mode": "unvalued",
        "as_of": None,
        "totals": {"cost_basis": "750000.00"},
        "holdings": [
            {
                "account": "Assets:Investment:FD:HDFC",
                "account_display": "Assets:Investment:FD:HDFC",
                "name": "HDFC",
                "cost_basis": "750000.00",
            }
        ],
    }
    assert isinstance(body["totals"]["cost_basis"], str)
    assert isinstance(body["holdings"][0]["cost_basis"], str)
    # No valuation vocabulary may leak into an at-cost body: a consumer must
    # not be able to read a current value or a gain off it, nor a freshness
    # claim it has no price date to support.
    for banned in ("current_value", "unrealized_gain", "source_state", "gain_pct"):
        assert banned not in body


def test_at_cost_holdings_totals_match_the_hub_card(tmp_path):
    """The hub's FD card and this screen are two views of ONE walk
    (`_unvalued_balances`); the count and the total must agree exactly."""
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    fd_card = next(
        v
        for v in client.get("/api/reports/investments/summary").json()["vehicles"]
        if v["key"] == "fd"
    )
    detail = client.get("/api/reports/investments/vehicles/fd/holdings").json()
    assert detail["totals"]["cost_basis"] == fd_card["cost_basis"]
    assert len(detail["holdings"]) == fd_card["holdings"]


def test_at_cost_holdings_lists_one_row_per_account(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _open_accounts(
        app.state.ledger,
        [
            "2000-01-01 open Assets:Investment:IndWealth:Managed INR",
            "2000-01-01 open Assets:Investment:IndWealth:Cash INR",
            "2000-01-01 open Assets:Investment:IndWealth:Closed INR",
        ],
    )
    _deposit(
        app.state.ledger,
        "Assets:Investment:IndWealth:Managed",
        "1732.00",
        date(2025, 3, 1),
    )
    _deposit(
        app.state.ledger, "Assets:Investment:IndWealth:Cash", "268.00", date(2025, 3, 1)
    )
    # Opened, funded, then fully withdrawn -- a closed position is not a
    # holding and must not appear as a ₹0.00 row.
    _deposit(
        app.state.ledger,
        "Assets:Investment:IndWealth:Closed",
        "500.00",
        date(2025, 3, 1),
    )
    _deposit(
        app.state.ledger,
        "Assets:Investment:IndWealth:Closed",
        "-500.00",
        date(2025, 4, 1),
    )

    client = TestClient(app)
    body = client.get("/api/reports/investments/vehicles/ind_wealth/holdings").json()
    assert body["key"] == "ind_wealth"
    assert body["label"] == "Ind Wealth"
    assert [h["name"] for h in body["holdings"]] == ["Cash", "Managed"]
    assert body["totals"]["cost_basis"] == "2000.00"


def test_at_cost_holdings_route_is_not_shadowed_by_instrument_detail(tmp_path):
    """The chosen path shape (literal `vehicles` ... literal `holdings`) must
    resolve to THIS handler, never fall into `/investments/{vehicle_key}/
    {instrument}`, which would 404 on an unknown vehicle named "vehicles"."""
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/vehicles/fd/holdings")
    assert resp.status_code == 200
    # An instrument-detail body would carry these; an at-cost body must not.
    assert "lots" not in resp.json()
    assert "symbol" not in resp.json()
    # And the neighbouring routes still resolve as before.
    assert client.get("/api/reports/investments/stock/SBIN").status_code == 200
    assert client.get("/api/reports/investments/stock/SBIN/history").status_code == 200


def test_at_cost_holdings_unknown_vehicle_404s(tmp_path):
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/reports/investments/vehicles/real_estate/holdings")
    assert resp.status_code == 404
    assert resp.json()["code"] == "E1034"


def test_at_cost_holdings_rejects_a_valued_vehicle(tmp_path):
    """Stock has a valuation source. Answering it here at cost only would
    quietly strip its market value, so it 404s toward its own read model
    rather than returning a partial truth."""
    app = _seeded_app(tmp_path)
    client = TestClient(app)
    for key in ("stock", "mutual_fund"):
        resp = client.get(f"/api/reports/investments/vehicles/{key}/holdings")
        assert resp.status_code == 404, key
        assert resp.json()["code"] == "E1034"
