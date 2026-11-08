"""Period-overview routes (Phase 1-3).

The contract (params, models, E1087) is locked by T1; these tests exercise the
implemented read models behind the overview, range-Sankey, tags and
in-transit (T5b) routes. Synthetic data only.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app.models.api.reports import ExpenseOverviewResponse, FlowOverviewResponse
from backend.app.server.main import create_app
from tests.conftest import make_txn

CARD = "Liabilities:CreditCard:SynthA1111"
BANK = "Assets:Bank:Synth"
CLEARING = "Assets:Transfer:CreditCard"


def _client(tmp_path) -> TestClient:
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 11, 1))
    led = app.state.ledger
    led.open_named_accounts([CARD, BANK, CLEARING, "Expenses:Food", "Income:Salary"])
    entries = [
        make_txn(date(2026, 8, 3), "Synth Diner", "Expenses:Food", CARD, "300"),
        make_txn(date(2026, 8, 1), "Synth Payroll", BANK, "Income:Salary", "90000"),
        make_txn(date(2026, 10, 20), "Card bill", CLEARING, BANK, "300"),
    ]
    for i, txn in enumerate(entries):
        txn.meta["txn-id"] = f"t{i}"
    led.append(entries)
    return TestClient(app, raise_server_exceptions=False)


def test_overview_routes_require_from_and_to(tmp_path):
    client = _client(tmp_path)
    for path in ("expense", "income", "cashflow"):
        assert client.get(f"/api/reports/{path}/overview").status_code == 422
        assert (
            client.get(
                f"/api/reports/{path}/overview", params={"from": "2026-08-01"}
            ).status_code
            == 422
        )


def test_overview_route_bad_funding_is_422(tmp_path):
    r = _client(tmp_path).get(
        "/api/reports/expense/overview",
        params={"from": "2026-08-01", "to": "2026-08-31", "funding": "upi"},
    )
    assert r.status_code == 422


def test_tags_route_bad_kind_is_422(tmp_path):
    r = _client(tmp_path).get("/api/reports/expense/tags", params={"kind": "asset"})
    assert r.status_code == 422


def test_sankey_route_fy_draws_the_account_graph(tmp_path):
    r = _client(tmp_path).get("/api/reports/cashflow/sankey", params={"fy": 2026})
    assert r.status_code == 200
    j = r.json()
    # The bill has no card receipt and one card owes it: it goes to the card,
    # and the card's spend is drawn card -> category.
    assert {(lk["source"], lk["target"], lk["back"]) for lk in j["links"]} == {
        ("Income:Salary (direct)", "Income:Salary", False),
        ("Income:Salary", BANK, False),
        (BANK, CARD, False),
        (CARD, "Expenses:Food", False),
    }
    assert (j["depth"], j["totals"]) == (
        "detailed",
        {"income": "90000.00", "expenses": "300.00"},
    )
    nodes = {n["id"]: (n["kind"], n["role"], n["amount"]) for n in j["nodes"]}
    assert nodes["Income:Salary"] == ("income", "income", "90000.00")
    assert nodes[CARD] == ("liability", "account", "300.00")
    summary = _client(tmp_path).get(
        "/api/reports/cashflow/sankey", params={"fy": 2026, "depth": "summary"}
    )
    assert "Income:Salary (direct)" not in {n["id"] for n in summary.json()["nodes"]}


def test_overview_route_from_after_to_is_e1087(tmp_path):
    r = _client(tmp_path).get(
        "/api/reports/expense/overview",
        params={"from": "2026-08-31", "to": "2026-08-01"},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "E1087"


def test_expense_overview_route_shape(tmp_path):
    r = _client(tmp_path).get(
        "/api/reports/expense/overview",
        params={"from": "2026-08-01", "to": "2026-08-31"},
    )
    assert r.status_code == 200
    assert set(r.json()) == set(ExpenseOverviewResponse.model_fields)


def test_income_overview_route_shape(tmp_path):
    r = _client(tmp_path).get(
        "/api/reports/income/overview",
        params={"from": "2026-08-01", "to": "2026-08-31"},
    )
    assert r.status_code == 200
    assert r.json()["side"] == "income"
    assert set(r.json()) == set(FlowOverviewResponse.model_fields)


def test_expense_overview_route_values_and_funding_filter(tmp_path):
    client = _client(tmp_path)
    aug = {"from": "2026-08-01", "to": "2026-08-31"}
    j = client.get("/api/reports/expense/overview", params=aug).json()
    assert j["headline"]["net"] == "300.00"
    assert j["headline"]["funding_split"]["card"] == "300.00"
    (line,) = j["lines"]
    assert (line["txn_id"], line["via"]["account"]) == ("t0", CARD)
    bank = client.get(
        "/api/reports/expense/overview", params={**aug, "funding": "bank"}
    ).json()
    assert (bank["headline"]["count"], bank["lines"]) == (0, [])
    other_card = client.get(
        "/api/reports/expense/overview",
        params={**aug, "account": "Liabilities:CreditCard:Other"},
    ).json()
    assert other_card["lines"] == []


def test_income_overview_route_values(tmp_path):
    j = (
        _client(tmp_path)
        .get(
            "/api/reports/income/overview",
            params={"from": "2026-08-01", "to": "2026-08-31"},
        )
        .json()
    )
    assert j["headline"]["net"] == "90000.00"
    assert j["headline"]["funding_split"]["bank"] == "90000.00"
    assert [g["group"] for g in j["groups"]] == ["Income:Salary"]


def test_cashflow_overview_route_shape(tmp_path):
    r = _client(tmp_path).get(
        "/api/reports/cashflow/overview",
        params={"from": "2026-08-01", "to": "2026-08-31"},
    )
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {
        "start",
        "end",
        "totals",
        "granularity",
        "series",
        "by_month",
        "statement",
    }
    assert j["granularity"] == "day" and len(j["series"]) == 31
    assert j["series"][0] == {
        "key": "2026-08-01",
        "label": "1 Aug",
        "income": "90000.00",
        "expenses": "0.00",
        "invested": "0.00",
        "net": "90000.00",
    }


def test_sankey_route_accepts_range(tmp_path):
    r = _client(tmp_path).get(
        "/api/reports/cashflow/sankey",
        params={"from": "2026-08-01", "to": "2026-08-31"},
    )
    assert r.status_code == 200


def test_sankey_route_fy_plus_range_is_e1087(tmp_path):
    r = _client(tmp_path).get(
        "/api/reports/cashflow/sankey",
        params={"fy": 2026, "from": "2026-08-01", "to": "2026-08-31"},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "E1087"


def test_expense_tags_route_accepts_range(tmp_path):
    r = _client(tmp_path).get(
        "/api/reports/expense/tags",
        params={"from": "2026-08-01", "to": "2026-08-31"},
    )
    assert r.status_code == 200
    assert (r.json()["start"], r.json()["end"]) == ("2026-08-01", "2026-08-31")


def test_income_tags_route(tmp_path):
    r = _client(tmp_path).get("/api/reports/expense/tags", params={"kind": "income"})
    assert r.status_code == 200
    assert r.json()["kind"] == "income"


def test_flow_overview_counts_hand_authored_entries(tmp_path):
    # A transaction with no txn-id still moves money: the income overview must
    # agree with the posting-level cash-flow overview.
    client = _client(tmp_path)
    led = client.app.state.ledger  # type: ignore[attr-defined]
    led.open_named_accounts(["Income:Refund"])
    led.append(
        [make_txn(date(2026, 8, 9), "Synth Refund", BANK, "Income:Refund", "29.05")]
    )
    aug = {"from": "2026-08-01", "to": "2026-08-31"}
    income = client.get("/api/reports/income/overview", params=aug).json()
    cash = client.get("/api/reports/cashflow/overview", params=aug).json()
    assert income["headline"]["net"] == cash["totals"]["income"] == "90029.05"
    assert income["headline"]["count"] == 2
    editable = {line["name"]: line["editable"] for line in income["lines"]}
    assert editable == {"Synth Payroll": True, "Synth Refund": False}
    hand = next(line for line in income["lines"] if line["name"] == "Synth Refund")
    assert hand["txn_id"].startswith("untracked-")
    # Stable across requests and unrelated writes.
    led.append([make_txn(date(2026, 8, 2), "Synth Later", "Expenses:Food", CARD, "1")])
    again = client.get("/api/reports/income/overview", params=aug).json()
    assert hand["txn_id"] in {line["txn_id"] for line in again["lines"]}


def test_sankey_contributions_include_hand_authored_entries(tmp_path):
    client = _client(tmp_path)
    led = client.app.state.ledger  # type: ignore[attr-defined]
    led.append(
        [
            make_txn(date(2026, 8, 9), "Synth Hand Meal", "Expenses:Food", BANK, "5"),
            make_txn(date(2026, 8, 10), "Synth Bank Meal", "Expenses:Food", BANK, "7"),
        ]
    )
    link = {"source": BANK, "target": "Expenses:Food"}
    for window in ({"fy": 2026}, {"from": "2026-08-01", "to": "2026-08-31"}):
        j = client.get(
            "/api/reports/cashflow/sankey/contributions", params={**link, **window}
        ).json()
        # The card-funded "Synth Diner" is behind card -> Food, not bank -> Food.
        assert {t["narration"] for t in j["transactions"]} == {
            "Synth Hand Meal",
            "Synth Bank Meal",
        }


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("expense/overview", {"from": "0001-01-01", "to": "0001-03-01"}),
        ("income/overview", {"from": "2026-01-01", "to": "9999-12-31"}),
        ("cashflow/overview", {"from": "1899-12-31", "to": "2026-01-01"}),
        ("cashflow/sankey", {"fy": 99999}),
        ("cashflow/rows", {"fy": 99999}),
        ("expense/tags", {"from": "0001-01-01", "to": "0001-01-02"}),
    ],
)
def test_out_of_bounds_years_are_e1087(tmp_path, path, params):
    r = _client(tmp_path).get(f"/api/reports/{path}", params=params)
    assert (r.status_code, r.json()["code"]) == (422, "E1087")


def test_cashflow_overview_route_values(tmp_path):
    j = (
        _client(tmp_path)
        .get(
            "/api/reports/cashflow/overview",
            params={"from": "2026-08-01", "to": "2026-10-31"},
        )
        .json()
    )
    assert j["totals"] == {
        "income": "90000.00",
        "expenses": "300.00",
        "invested": "0.00",
        "saved": "89700.00",
        "savings_rate": "99.7",
    }
    assert [m["label"] for m in j["by_month"]] == ["Aug", "Sep", "Oct"]
    assert j["statement"]["net"] == "89700.00"


def test_cashflow_overview_route_from_after_to_is_e1087(tmp_path):
    r = _client(tmp_path).get(
        "/api/reports/cashflow/overview",
        params={"from": "2026-08-31", "to": "2026-08-01"},
    )
    assert (r.status_code, r.json()["code"]) == (422, "E1087")


def test_sankey_route_range_windows_links(tmp_path):
    client = _client(tmp_path)
    r = client.get(
        "/api/reports/cashflow/sankey",
        params={"from": "2026-08-02", "to": "2026-10-31", "depth": "summary"},
    )
    # The payroll (1 Aug) is outside; the meal (Aug) and bill (Oct) are in.
    assert r.json()["links"] == [
        {"source": BANK, "target": CARD, "value": "300.00", "back": False},
        {"source": CARD, "target": "Expenses:Food", "value": "300.00", "back": False},
    ]
    fy = client.get("/api/reports/cashflow/sankey", params={"fy": 2026}).json()
    bounds = client.get(
        "/api/reports/cashflow/sankey",
        params={"from": "2026-04-01", "to": "2027-03-31"},
    ).json()
    assert fy == bounds


@pytest.mark.parametrize(
    "params",
    [{}, {"from": "2026-08-01"}, {"fy": 2026, "to": "2026-08-31"}],
)
def test_sankey_route_invalid_window_is_e1087(tmp_path, params):
    r = _client(tmp_path).get("/api/reports/cashflow/sankey", params=params)
    assert (r.status_code, r.json()["code"]) == (422, "E1087")


def test_sankey_contributions_route_accepts_range(tmp_path):
    client = _client(tmp_path)
    link = {"source": "Income:Salary", "target": BANK}
    inside = client.get(
        "/api/reports/cashflow/sankey/contributions",
        params={**link, "from": "2026-08-01", "to": "2026-08-31"},
    ).json()
    assert [t["narration"] for t in inside["transactions"]] == ["Synth Payroll"]
    outside = client.get(
        "/api/reports/cashflow/sankey/contributions",
        params={**link, "from": "2026-09-01", "to": "2026-09-30"},
    ).json()
    assert outside["transactions"] == []
    both = client.get(
        "/api/reports/cashflow/sankey/contributions",
        params={**link, "fy": 2026, "from": "2026-08-01", "to": "2026-08-31"},
    )
    assert (both.status_code, both.json()["code"]) == (422, "E1087")


def _tagged_client(tmp_path) -> TestClient:
    client = _client(tmp_path)
    led = client.app.state.ledger  # type: ignore[attr-defined]
    entries = [
        make_txn(date(2026, 8, 5), "Synth Hotel", "Expenses:Food", CARD, "700"),
        make_txn(date(2026, 6, 5), "Synth Cab", "Expenses:Food", CARD, "40"),
        make_txn(date(2026, 8, 6), "Synth Bonus", BANK, "Income:Salary", "5000"),
    ]
    tags = ("trip/goa", "trip/old", "bonus")
    for i, (txn, tag) in enumerate(zip(entries, tags, strict=True)):
        txn.meta["txn-id"] = f"tag{i}"
        entries[i] = txn._replace(tags=frozenset({tag}))
    led.append(entries)
    return client


def test_expense_tags_route_filters_by_range(tmp_path):
    j = (
        _tagged_client(tmp_path)
        .get(
            "/api/reports/expense/tags",
            params={"from": "2026-08-01", "to": "2026-08-31"},
        )
        .json()
    )
    assert [(t["tag"], t["amount"]) for t in j["by_tag"]] == [("trip/goa", "700")]
    assert [g["prefix"] for g in j["groups"]] == ["trip"]


def test_expense_tags_route_no_params_is_all_time(tmp_path):
    j = _tagged_client(tmp_path).get("/api/reports/expense/tags").json()
    assert (j["kind"], j["start"], j["end"]) == ("expense", None, None)
    assert {t["tag"] for t in j["by_tag"]} == {"trip/goa", "trip/old"}


def test_expense_tags_route_half_range_is_e1087(tmp_path):
    r = _client(tmp_path).get("/api/reports/expense/tags", params={"to": "2026-08-31"})
    assert (r.status_code, r.json()["code"]) == (422, "E1087")


def test_income_tags_route_totals_only_income(tmp_path):
    j = (
        _tagged_client(tmp_path)
        .get(
            "/api/reports/expense/tags",
            params={"kind": "income", "from": "2026-08-01", "to": "2026-08-31"},
        )
        .json()
    )
    assert j["kind"] == "income"
    assert [(t["tag"], t["amount"]) for t in j["by_tag"]] == [("bonus", "5000")]


def test_credit_cards_route_reports_in_transit(tmp_path):
    j = _client(tmp_path).get("/api/reports/liabilities/credit-cards").json()
    (tile,) = j["cards"]
    assert tile["in_transit"] == {"amount": "300.00", "date": "2026-10-20", "count": 1}
    assert tile["net_owed"] == "0.00"
    assert j["in_transit"]["amount"] == "300.00"


def test_liabilities_balance_route_reports_in_transit(tmp_path):
    j = _client(tmp_path).get("/api/reports/liabilities/balance").json()
    assert j["in_transit"]["amount"] == "300.00"
    assert (j["cards_outstanding"], j["cards_net_owed"]) == ("300.00", "0.00")


def test_card_receipt_clears_the_in_transit_payment(tmp_path):
    client = _client(tmp_path)
    receipt = make_txn(date(2026, 10, 22), "Payment received", CARD, CLEARING, "300")
    receipt.meta["txn-id"] = "t-receipt"
    client.app.state.ledger.append([receipt])
    j = client.get("/api/reports/liabilities/credit-cards").json()
    assert j["cards"][0]["in_transit"] is None
    assert j["in_transit"]["payments"] == []
    b = client.get("/api/reports/liabilities/balance").json()
    assert (b["in_transit"]["amount"], b["cards_net_owed"]) == ("0.00", "0.00")


def test_net_worth_is_unchanged_by_a_payment_in_transit(tmp_path):
    # The in-transit read model never writes: the payment already sits in the
    # clearing account (an asset), so net worth is the same before and after
    # the card-side receipt is imported.
    client = _client(tmp_path)
    before = client.get("/api/reports/networth").json()["current"]
    assert (
        client.get("/api/reports/liabilities/balance").json()["in_transit"]["amount"]
        == "300.00"
    )
    assert client.get("/api/reports/networth").json()["current"] == before
    # 90000 salary - 300 card spend; the paid bill moved bank -> clearing.
    assert before["net"] == "89700.00"
    receipt = make_txn(date(2026, 10, 22), "Payment received", CARD, CLEARING, "300")
    receipt.meta["txn-id"] = "t-receipt"
    client.app.state.ledger.append([receipt])
    assert client.get("/api/reports/networth").json()["current"]["net"] == before["net"]


def _client_with_opening(tmp_path) -> TestClient:
    """A card opened with a balance booked without a txn-id, a cashback
    top-up row in the clearing account, and a bill payment in transit."""
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 11, 1))
    led = app.state.ledger
    refund = "Income:Refund:CreditCard:Synth"
    led.open_named_accounts(
        [CARD, BANK, CLEARING, "Expenses:Food", "Equity:Opening-Balances", refund]
    )
    opening = make_txn(
        date(2026, 7, 1), "Opening", "Equity:Opening-Balances", CARD, "4000"
    )
    entries = [
        make_txn(date(2026, 8, 3), "Synth Diner", "Expenses:Food", CARD, "300"),
        make_txn(date(2026, 10, 20), "Card bill", CLEARING, BANK, "4300"),
        make_txn(date(2026, 10, 20), "Cashback", CLEARING, refund, "15"),
    ]
    for i, txn in enumerate(entries):
        txn.meta["txn-id"] = f"t{i}"
    led.append([opening, *entries])
    return TestClient(app, raise_server_exceptions=False)


def test_tile_outstanding_and_attribution_count_the_opening_balance(tmp_path):
    client = _client_with_opening(tmp_path)
    j = client.get("/api/reports/liabilities/credit-cards").json()
    (tile,) = j["cards"]
    assert tile["outstanding"] == "4300.00"
    assert tile["in_transit"] == {"amount": "4300.00", "date": "2026-10-20", "count": 1}
    assert tile["net_owed"] == "0.00"
    # The cashback top-up is not a bill payment.
    assert [p["txn_id"] for p in j["in_transit"]["payments"]] == ["t1"]
    b = client.get("/api/reports/liabilities/balance").json()
    assert b["in_transit"]["attributed"] == "4300.00"
    assert (b["cards_outstanding"], b["cards_net_owed"]) == ("4300.00", "0.00")


def test_receipt_with_cashback_clears_the_payment(tmp_path):
    client = _client_with_opening(tmp_path)
    receipt = make_txn(date(2026, 10, 22), "Payment received", CARD, CLEARING, "4315")
    receipt.meta["txn-id"] = "t-receipt"
    client.app.state.ledger.append([receipt])
    j = client.get("/api/reports/liabilities/credit-cards").json()
    assert j["in_transit"]["payments"] == []
    assert j["cards"][0]["in_transit"] is None


def test_stale_payment_is_left_out_of_in_transit(tmp_path):
    # Clock is 2026-11-01; a bill payment from June never got its receipt.
    client = _client(tmp_path)
    old = make_txn(date(2026, 6, 1), "Old card bill", CLEARING, BANK, "999")
    old.meta["txn-id"] = "t-old"
    client.app.state.ledger.append([old])
    j = client.get("/api/reports/liabilities/credit-cards").json()
    assert [p["txn_id"] for p in j["in_transit"]["payments"]] == ["t2"]
    b = client.get("/api/reports/liabilities/balance").json()
    assert (b["in_transit"]["amount"], b["cards_net_owed"]) == ("300.00", "0.00")


def test_hand_written_receipt_clears_the_payment(tmp_path):
    # A card-side "payment received" row typed into the journal by hand has
    # no txn-id; it must still clear the bank payment it matches.
    client = _client(tmp_path)
    receipt = make_txn(date(2026, 10, 22), "Payment received", CARD, CLEARING, "300")
    assert "txn-id" not in receipt.meta
    client.app.state.ledger.append([receipt])
    j = client.get("/api/reports/liabilities/credit-cards").json()
    assert j["in_transit"]["payments"] == []
    assert j["cards"][0]["in_transit"] is None
    b = client.get("/api/reports/liabilities/balance").json()
    assert (b["in_transit"]["amount"], b["cards_net_owed"]) == ("0.00", "0.00")


def test_hand_written_bank_payment_is_in_transit_under_the_payment_rule(tmp_path):
    # A hand-written bill payment (bank -> clearing) meets the payment rule and
    # is in transit with a read-only untracked id; a hand-written cashback
    # top-up (Income -> clearing) never is a payment.
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 11, 1))
    led = app.state.ledger
    cashback = "Income:Cashback:CreditCard"
    led.open_named_accounts([CARD, BANK, CLEARING, "Expenses:Food", cashback])
    spend = make_txn(date(2026, 10, 3), "Synth Diner", "Expenses:Food", CARD, "300")
    spend.meta["txn-id"] = "t-spend"
    led.append(
        [
            spend,
            make_txn(date(2026, 10, 20), "Card bill by hand", CLEARING, BANK, "300"),
            make_txn(date(2026, 10, 21), "Cashback by hand", CLEARING, cashback, "15"),
        ]
    )
    client = TestClient(app, raise_server_exceptions=False)
    j = client.get("/api/reports/liabilities/credit-cards").json()
    (payment,) = j["in_transit"]["payments"]
    assert payment["txn_id"].startswith("untracked-")
    assert (payment["amount"], payment["account"]) == ("300.00", CARD)
    assert j["cards"][0]["net_owed"] == "0.00"
    b = client.get("/api/reports/liabilities/balance").json()
    assert b["in_transit"]["amount"] == "300.00"


def test_rows_route_for_a_link_a_category_and_a_day(tmp_path):
    client = _client(tmp_path)
    led = client.app.state.ledger  # type: ignore[attr-defined]
    led.append(
        [make_txn(date(2026, 8, 9), "Synth Hand Meal", "Expenses:Food", BANK, "5")]
    )
    link = client.get(
        "/api/reports/cashflow/rows",
        params={"fy": 2026, "source": BANK, "target": CARD},
    )
    assert link.status_code == 200
    j = link.json()
    assert [(r["name"], r["amount"], r["direction"]) for r in j["rows"]] == [
        ("Card bill", "300.00", "move")
    ]
    assert j["totals"] == {
        "received": "0.00",
        "spent": "0.00",
        "moved": "300.00",
        "count": 1,
    }
    food = client.get(
        "/api/reports/cashflow/rows",
        params={"from": "2026-08-01", "to": "2026-08-31", "category": "Expenses:Food"},
    ).json()
    # Hand-authored entries count, marked read-only.
    assert {(r["name"], r["editable"]) for r in food["rows"]} == {
        ("Synth Diner", True),
        ("Synth Hand Meal", False),
    }
    assert food["by_category"] == [
        {
            "category": "Expenses:Food",
            "label": "Food",
            "received": "0.00",
            "spent": "305.00",
            "moved": "0.00",
            "count": 2,
        }
    ]
    day = client.get(
        "/api/reports/cashflow/rows",
        params={"from": "2026-08-01", "to": "2026-08-01", "side": "income"},
    ).json()
    assert [(r["name"], r["account"], r["direction"]) for r in day["rows"]] == [
        ("Synth Payroll", BANK, "in")
    ]


@pytest.mark.parametrize(
    "params",
    [
        {"node": BANK, "category": "Expenses:Food"},
        {"source": BANK},
        {"node": BANK, "side": "income"},
    ],
)
def test_rows_route_bad_selector_is_e1098(tmp_path, params):
    r = _client(tmp_path).get(
        "/api/reports/cashflow/rows", params={"fy": 2026, **params}
    )
    assert (r.status_code, r.json()["code"]) == (422, "E1098")


def test_rows_route_bad_side_is_422(tmp_path):
    r = _client(tmp_path).get(
        "/api/reports/cashflow/rows", params={"fy": 2026, "side": "both"}
    )
    assert r.status_code == 422
