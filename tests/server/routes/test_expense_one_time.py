"""Expense · Overview one-offs: the `one_time` block and `exclude_one_time`.

One synthetic persona, clock 2026-11-15. One-time is the Dashboard's rule
(`dashboard_v2.spending`), built from the whole ledger up to the report date
(min(to, today)), so the Expense overview and the Dashboard agree.

- Bike: fuel 1,000-1,200 a month (ordinary rows, varied narration) and the
  Royal Enfield, 82,700 on 2026-10-22 by card: one-time (vs the Bike usual).
- Subscriptions: Claude 1,900 monthly; LeetCode 13,000 on 2025-10-10 and
  2026-10-10, a yearly pair: 6.8x the usual but recurring, never one-time.
- Food: three orders a month, each a new narration; NEW CAFE 650 on
  2026-10-22 (a new merchant, ordinary in size): never one-time.
- Sofa, 40,000 from the bank on 2026-09-14 (Home has < 3 rows, so judged
  against all spending): one-time.
- Laptop, 90,000 by card on 2026-06-18: one-time, before the Dashboard's
  3-month window.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.app.models.api.reports import FlowOverviewResponse
from backend.app.server.main import create_app
from backend.app.service.reports.cashflow import overview, period
from backend.app.service.reports.health.ledger_view import (
    commodity_names,
    flow_transactions_in_range,
)
from tests.conftest import make_txn

TODAY = date(2026, 11, 15)
CARD = "Liabilities:CreditCard:SynthA1111"
BANK = "Assets:Bank:Synth"
BIKE_ACC = "Expenses:Bike:Accessories"
FUEL = "Expenses:Bike:Fuel"
FOOD = "Expenses:Food"
SUBS = "Expenses:Subscriptions"
HOME = "Expenses:Home"
GADGETS = "Expenses:Gadgets"


def _months() -> list[tuple[int, int]]:
    """Nov 2025 .. Nov 2026."""
    return [(2025 + (10 + i) // 12, (10 + i) % 12 + 1) for i in range(13)]


def _entries() -> list[tuple[date, str, str, str, str]]:
    out: list[tuple[date, str, str, str, str]] = []
    for i, (y, m) in enumerate(_months()):
        for day, amt in ((8, "500"), (12, "600"), (16, "700")):
            out.append(
                (date(y, m, day), f"SWIGGY ORDER {y}-{m}-{day}", FOOD, CARD, amt)
            )
        out.append((date(y, m, 5), "CLAUDE.AI", SUBS, CARD, "1900"))
        out.append(
            (
                date(y, m, 22),
                f"FUEL STATION {y}-{m}",
                FUEL,
                CARD,
                ("1000", "1100", "1200")[i % 3],
            )
        )
    out += [
        (date(2025, 10, 10), "LEETCODE PREMIUM", SUBS, CARD, "13000"),
        (date(2026, 10, 10), "LEETCODE PREMIUM", SUBS, CARD, "13000"),
        (date(2026, 10, 22), "ROYAL ENFIELD", BIKE_ACC, CARD, "82700"),
        (date(2026, 10, 22), "NEW CAFE", FOOD, CARD, "650"),
        (date(2026, 9, 14), "SOFA WORLD", HOME, BANK, "40000"),
        (date(2026, 6, 18), "LAPTOP STORE", GADGETS, CARD, "90000"),
        (date(2026, 10, 1), "Synth Payroll", BANK, "Income:Salary", "90000"),
    ]
    return [e for e in out if e[0] <= TODAY]


def _client(tmp_path) -> TestClient:
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: TODAY)
    led = app.state.ledger
    led.open_named_accounts(
        [CARD, BANK, BIKE_ACC, FUEL, FOOD, SUBS, HOME, GADGETS, "Income:Salary"]
    )
    txns = [make_txn(*e) for e in _entries()]
    for i, txn in enumerate(txns):
        txn.meta["txn-id"] = f"t{i:03d}"
    led.append(txns)
    return TestClient(app, raise_server_exceptions=False)


OCT = {"from": "2026-10-01", "to": "2026-10-31"}
YEAR = {"from": "2026-01-01", "to": "2026-12-31"}

# Day and month granularity, a funding filter and an account scope.
QUERIES = {
    "oct_day": OCT,
    "year_month": YEAR,
    "sep_bank": {"from": "2026-09-01", "to": "2026-09-30", "funding": "bank"},
    "year_card_account": {**YEAR, "account": CARD},
}


def _get(client: TestClient, params: dict, path: str = "expense") -> dict:
    r = client.get(f"/api/reports/{path}/overview", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _names(block: dict) -> list[str]:
    return [i["name"] for i in block["items"]]


@pytest.fixture
def client(tmp_path) -> TestClient:
    return _client(tmp_path)


def _plain_flow_overview(client: TestClient, params: dict) -> dict:
    """The expense `flow_overview` (the read model before one-offs existed)
    for `params`, called directly on the same ledger, as JSON."""
    ledger = client.app.state.ledger
    r = period.validate_range(
        date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
    )
    body = overview.flow_overview(
        flow_transactions_in_range(ledger, r.start, r.end),
        "expense",
        r,
        params.get("funding", "all"),
        params.get("account"),
        names=commodity_names(ledger),
    )
    return FlowOverviewResponse.model_validate(body).model_dump(mode="json")


@pytest.mark.parametrize("explicit", [False, True], ids=["default", "false"])
@pytest.mark.parametrize("key", list(QUERIES))
def test_default_response_is_the_plain_flow_overview_plus_one_time(
    client, key, explicit
):
    params = QUERIES[key]
    if explicit:
        params = {**params, "exclude_one_time": "false"}
    body = _get(client, params)
    assert "one_time" in body
    del body["one_time"]
    assert body.pop("exclude_one_time") is False
    assert body == _plain_flow_overview(client, QUERIES[key])


@pytest.mark.parametrize("exclude", [False, True])
def test_exclude_one_time_is_echoed(client, exclude):
    body = _get(client, {**OCT, "exclude_one_time": str(exclude).lower()})
    assert body["exclude_one_time"] is exclude


def test_one_time_block_lists_the_period_one_offs_largest_first(client):
    block = _get(client, YEAR)["one_time"]
    assert block["report_date"] == "2026-11-15"
    assert _names(block) == ["LAPTOP STORE", "ROYAL ENFIELD", "SOFA WORLD"]
    assert block["count"] == 3
    assert block["total"] == "212700.00"
    bike = block["items"][1]
    assert bike["category"] == "Expenses:Bike"
    assert bike["account"] == BIKE_ACC
    assert bike["paid_from"] == CARD
    assert bike["amount"] == "82700.00"
    assert bike["usual_basis"] == "category"
    sofa = block["items"][2]
    assert sofa["usual_basis"] == "all_spending"
    assert sofa["paid_from"] == BANK


def test_report_date_bounds_to_the_period_end_before_today(client):
    block = _get(client, {"from": "2026-06-01", "to": "2026-06-30"})["one_time"]
    assert block["report_date"] == "2026-06-30"
    assert _names(block) == ["LAPTOP STORE"]


def test_recurring_yearly_and_new_merchant_are_never_one_time(client):
    for exclude in ("false", "true"):
        body = _get(client, {**OCT, "exclude_one_time": exclude})
        assert "LEETCODE PREMIUM" not in _names(body["one_time"])
        assert "NEW CAFE" not in _names(body["one_time"])
        line_names = {ln["name"] for ln in body["lines"]}
        assert {"LEETCODE PREMIUM", "NEW CAFE"} <= line_names
        subs = next(g for g in body["groups"] if g["group"] == SUBS)
        assert subs["amount"] == "14900.00"  # 1,900 + 13,000


def test_one_time_is_the_same_with_the_switch_on_or_off(client):
    for params in QUERIES.values():
        off = _get(client, params)["one_time"]
        on = _get(client, {**params, "exclude_one_time": "true"})["one_time"]
        assert on == off


def test_one_time_respects_funding_and_account(client):
    assert _names(_get(client, {**YEAR, "funding": "card"})["one_time"]) == [
        "LAPTOP STORE",
        "ROYAL ENFIELD",
    ]
    bank = _get(client, {**YEAR, "funding": "bank"})["one_time"]
    assert (_names(bank), bank["total"], bank["count"]) == (
        ["SOFA WORLD"],
        "40000.00",
        1,
    )
    assert _names(_get(client, {**YEAR, "account": BANK})["one_time"]) == ["SOFA WORLD"]
    assert _names(_get(client, {**YEAR, "account": CARD})["one_time"]) == [
        "LAPTOP STORE",
        "ROYAL ENFIELD",
    ]


def _dec(s: str) -> Decimal:
    return Decimal(s)


BIKE = Decimal("82700")
CARD_ONE_OFFS = Decimal("172700")  # laptop 90,000 + bike 82,700


def _both(client: TestClient, params: dict) -> tuple[dict, dict]:
    """(All, Without one-offs) for `params`."""
    return _get(client, params), _get(client, {**params, "exclude_one_time": "true"})


def _point(body: dict, key: str) -> str:
    return next(p for p in body["series"] if p["key"] == key)["amount"]


def test_exclude_one_time_removes_the_bike_from_the_headline(client):
    all_oct, ex_oct = _both(client, OCT)
    assert _dec(ex_oct["headline"]["net"]) == _dec(all_oct["headline"]["net"]) - BIKE
    assert (
        _dec(ex_oct["headline"]["gross"]) == _dec(all_oct["headline"]["gross"]) - BIKE
    )
    assert ex_oct["headline"]["count"] == all_oct["headline"]["count"] - 1


def test_exclude_one_time_removes_the_bike_from_its_day_cell(client):
    all_oct, ex_oct = _both(client, OCT)
    assert all_oct["granularity"] == "day"
    # 22 Oct keeps the fuel and the cafe, loses the bike.
    assert _point(all_oct, "2026-10-22") == "84550.00"  # 82,700 + 1,200 + 650
    assert _point(ex_oct, "2026-10-22") == "1850.00"


def test_exclude_one_time_removes_the_bike_from_its_category(client):
    all_oct, ex_oct = _both(client, OCT)

    def bike_group(body: dict) -> dict:
        return next(g for g in body["groups"] if g["group"] == "Expenses:Bike")

    # Bike keeps its fuel.
    assert bike_group(all_oct)["amount"] == "83900.00"
    assert bike_group(ex_oct)["amount"] == "1200.00"
    assert "ROYAL ENFIELD" not in {i["name"] for i in bike_group(ex_oct)["items"]}


def test_exclude_one_time_removes_the_bike_from_the_paid_via_split(client):
    all_oct, ex_oct = _both(client, OCT)
    split_all = all_oct["headline"]["funding_split"]
    split_ex = ex_oct["headline"]["funding_split"]
    assert _dec(split_ex["card"]) == _dec(split_all["card"]) - BIKE
    assert split_ex["bank"] == split_all["bank"]


def test_exclude_one_time_removes_every_one_off_from_the_year_split(client):
    # The bank-funded sofa is not the split's largest part, so a split taken
    # from unfiltered lines and only re-balanced to the net would keep it.
    all_year, ex_year = _both(client, YEAR)
    assert all_year["headline"]["funding_split"]["bank"] == "40000.00"
    assert ex_year["headline"]["funding_split"]["bank"] == "0.00"
    assert (
        _dec(ex_year["headline"]["funding_split"]["card"])
        == _dec(all_year["headline"]["funding_split"]["card"]) - CARD_ONE_OFFS
    )


def test_exclude_one_time_removes_the_bike_from_the_lines(client):
    all_oct, ex_oct = _both(client, OCT)
    assert "ROYAL ENFIELD" in {ln["name"] for ln in all_oct["lines"]}
    assert "ROYAL ENFIELD" not in {ln["name"] for ln in ex_oct["lines"]}
    assert len(ex_oct["lines"]) == len(all_oct["lines"]) - 1


@pytest.mark.parametrize(
    ("month", "all_amount", "without"),
    [
        ("2026-06", "94800.00", "4800.00"),  # laptop
        ("2026-08", "4700.00", "4700.00"),  # no one-off
        ("2026-09", "44800.00", "4800.00"),  # sofa
        ("2026-10", "101250.00", "18550.00"),  # bike (LeetCode and the cafe stay)
    ],
)
def test_exclude_one_time_removes_one_offs_from_the_month_bars(
    client, month, all_amount, without
):
    all_year, ex_year = _both(client, YEAR)
    assert all_year["granularity"] == "month"
    assert (_point(all_year, month), _point(ex_year, month)) == (all_amount, without)


@pytest.mark.parametrize("key", list(QUERIES))
@pytest.mark.parametrize("funding", ["all", "card", "bank", "other"])
def test_exclude_one_time_drops_exactly_the_listed_total(client, key, funding):
    params = {**QUERIES[key], "funding": funding}
    all_, ex = _both(client, params)
    assert _dec(ex["headline"]["net"]) == _dec(all_["headline"]["net"]) - _dec(
        all_["one_time"]["total"]
    )


def test_exclude_one_time_with_a_funding_filter_leaves_only_matching_one_offs(client):
    all_bank = _get(client, {**YEAR, "funding": "bank"})
    ex_bank = _get(client, {**YEAR, "funding": "bank", "exclude_one_time": "true"})
    assert _dec(ex_bank["headline"]["net"]) == _dec(
        all_bank["headline"]["net"]
    ) - Decimal("40000")


def test_expense_one_time_agrees_with_the_dashboard(client):
    dash = client.get("/api/reports/dashboard/v2").json()["one_time_spends"]
    assert (dash["start"], dash["end"]) == ("2026-09-01", "2026-11-15")
    assert [i["name"] for i in dash["items"]] == ["ROYAL ENFIELD", "SOFA WORLD"]
    for params in (
        {"from": dash["start"], "to": "2026-11-30"},
        YEAR,
    ):
        block = _get(client, params)["one_time"]
        assert block["report_date"] == dash["end"]
        within = [
            i for i in block["items"] if dash["start"] <= i["date"] <= dash["end"]
        ]
        assert within == dash["items"]


def test_expense_one_time_agrees_with_the_dashboard_for_a_past_date(client):
    # Report date = `to` (before today): both build the rule at 2026-06-30.
    r = client.get("/api/reports/dashboard/v2", params={"as_of": "2026-06-30"})
    dash = r.json()["one_time_spends"]
    assert (dash["start"], dash["end"]) == ("2026-04-01", "2026-06-30")
    assert [i["name"] for i in dash["items"]] == ["LAPTOP STORE"]
    block = _get(client, {"from": dash["start"], "to": dash["end"]})["one_time"]
    assert block["report_date"] == "2026-06-30"
    assert block["items"] == dash["items"]


def test_income_overview_has_no_one_time_block(client):
    body = _get(client, OCT, path="income")
    assert "one_time" not in body
