"""Net Worth · Overview: totals tie to Balance, groups, vehicles, benchmarks.

One synthetic persona ("Acme"), clock 2026-11-15. Hand-computed figures are
derived in the comments next to the fixture so a failure points at the rule.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import yaml
from beancount.core import data
from beancount.core.amount import Amount
from fastapi.testclient import TestClient

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.persistence.nav_store import NavStore
from backend.app.models.domain.nav import NavRow
from backend.app.server.main import create_app
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from tests.conftest import make_txn

TODAY = date(2026, 11, 15)
OVERVIEW = "/api/reports/networth/overview"
CHK = "Assets:Checking:Acme"
OLD = "Assets:Checking:Old"
CARD = "Liabilities:CreditCard:Acme1234"
LOAN = "Liabilities:Loan:Bike"
OPENING = "Equity:Opening-Balances"


def _t(d: date, narration: str, debit: str, credit: str, amount: str):
    return make_txn(d, narration, debit, credit, amount)


def _buy_stock(ledger, when: date) -> None:
    cost = data.Cost(Decimal("100"), "INR", when, None)
    ledger.append(
        [
            data.Transaction(
                data.new_metadata("<test>", 0),
                when,
                "*",
                None,
                "buy ACME",
                data.EMPTY_SET,
                data.EMPTY_SET,
                [
                    data.Posting(
                        "Assets:Investment:Stock:ACME",
                        Amount(Decimal("10"), "ACME"),
                        cost,
                        None,
                        None,
                        None,
                    ),
                    data.Posting(
                        OPENING, Amount(Decimal("-1000"), "INR"), None, None, None, None
                    ),
                ],
            )
        ]
    )


@pytest.fixture
def app(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: TODAY)
    led = app.state.ledger
    led.open_named_accounts(
        [
            CHK,
            OLD,
            CARD,
            LOAN,
            "Assets:Cash",
            "Assets:House:Home",
            "Assets:Investment:FD:Acme",
            "Assets:Investment:Insurance:Acme",
            "Assets:Debt:PPF",
            "Assets:NPS",
            "Assets:Receivable:TDS",
            "Liabilities:People:Sam",
            "Expenses:Food",
            "Expenses:Shopping",
            "Income:Salary",
            "Income:Gift",
        ]
    )
    led.main_file.write_text(
        led.main_file.read_text(encoding="utf-8").rstrip()
        + '\n2000-01-01 commodity ACME\n  name: "Acme Ltd"'
        + '\n2000-01-01 open Assets:Investment:Stock:ACME ACME "FIFO"\n',
        encoding="utf-8",
    )
    led.append(
        [
            _t(date(2026, 7, 1), "salary", CHK, "Income:Salary", "300000"),
            # Spending Aug 20k, Sep 10k, Oct 30k -> 3-month average 20,000.
            _t(date(2026, 8, 20), "food", "Expenses:Food", CHK, "20000"),
            _t(date(2026, 9, 20), "food", "Expenses:Food", CHK, "10000"),
            _t(date(2026, 10, 20), "food", "Expenses:Food", CHK, "30000"),
            _t(date(2026, 10, 30), "cash", "Assets:Cash", CHK, "5000"),
            # Last activity 2026-09-01: 75 days before the clock, so stale.
            _t(date(2026, 9, 1), "gift", OLD, "Income:Gift", "2000"),
            _t(date(2026, 8, 1), "fd", "Assets:Investment:FD:Acme", CHK, "100000"),
            # This month (Nov): food 1,000.
            _t(date(2026, 11, 5), "food", "Expenses:Food", CHK, "1000"),
            _t(date(2025, 4, 10), "house", "Assets:House:Home", OPENING, "900000"),
            _t(date(2025, 4, 10), "ppf", "Assets:Debt:PPF", OPENING, "50000"),
            _t(date(2025, 4, 10), "nps", "Assets:NPS", OPENING, "30000"),
            _t(
                date(2025, 4, 10),
                "lic",
                "Assets:Investment:Insurance:Acme",
                OPENING,
                "20000",
            ),
            _t(date(2026, 3, 1), "tds", "Assets:Receivable:TDS", OPENING, "7000"),
            # On the card, in Nov: outside the Aug-Oct spending window.
            _t(date(2026, 11, 2), "shop", "Expenses:Shopping", CARD, "8000"),
            _t(date(2026, 4, 1), "sam", "Liabilities:People:Sam", OPENING, "4000"),
            _t(date(2026, 9, 1), "bike", OPENING, LOAN, "12000"),
            # The mutual-fund purchase below books its cash leg against the
            # broker wallet; fund the wallet so it nets to zero.
            _t(date(2026, 4, 17), "wallet", "Assets:Broker:Groww", OPENING, "21500"),
        ]
    )
    _buy_stock(led, date(2025, 11, 15))
    NavStore(led.storage_dir / "prices" / "equity_history.parquet").append(
        [NavRow("ACME", Decimal("150"), date(2026, 11, 14))]
    )
    commit_candidates(
        led,
        app.state.investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 18),
                amount=Decimal("21500.00"),
                raw_narration="Purchase Acme Flexi",
                normalized_narration="Acme Flexi",
                source_account="Assets:Investment:MutualFund:Groww:Cash",
                direction=Direction.DEBIT,
                units=Decimal("100.0"),
                commodity="MF122639",
                unit_cost=Decimal("215.0"),
            )
        ],
    )
    NavStore(led.storage_dir / "prices" / "nav_history.parquet").append(
        [NavRow("122639", Decimal("220.00"), date(2026, 11, 14))]
    )
    (led.storage_dir / "rules").mkdir(exist_ok=True)
    (led.storage_dir / "rules" / "loan-terms.yaml").write_text(
        yaml.safe_dump({LOAN: {"annual_rate_pct": 0, "term_months": 12}}),
        encoding="utf-8",
    )
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _body(client, query: str = "") -> dict:
    resp = client.get(OVERVIEW + query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _group(body: dict, gid: str) -> dict:
    return next(g for g in body["groups"] + body["liability_groups"] if g["id"] == gid)


def test_totals_equal_the_balance_route_exactly(client):
    body = _body(client)
    current = client.get("/api/reports/networth").json()["current"]
    assert body["totals"]["net_worth"] == current["net"]
    assert body["totals"]["assets"] == current["assets"]
    # Overview reports owed as a positive number; Balance keeps it signed.
    assert body["totals"]["liabilities"] == str(
        -Decimal(current["liabilities"])
    ).replace("-0.00", "0.00")


def test_group_totals_add_up_to_the_headline(client):
    body = _body(client)
    assets = sum(Decimal(g["total"]) for g in body["groups"])
    owed = sum(Decimal(g["total"]) for g in body["liability_groups"])
    assert assets == Decimal(body["totals"]["assets"])
    assert owed == Decimal(body["totals"]["liabilities"])


def test_groups_in_the_ordering_the_brief_names_and_empty_ones_omitted(client):
    body = _body(client)
    assert [g["id"] for g in body["groups"]] == ["investments", "property", "bank"]
    assert [g["id"] for g in body["liability_groups"]] == ["loans", "cards", "people"]
    # No Retirement / Other group exists for a user without such accounts.
    assert "retirement" not in {g["id"] for g in body["groups"]}


def test_excluded_tds_carries_its_reason(client):
    (excluded,) = _body(client)["totals"]["excluded"]
    assert excluded["account"] == "Assets:Receivable:TDS"
    assert excluded["amount"] == "7000.00"
    assert "settled at filing" in excluded["reason"]


def test_changes_this_month_and_fy(client):
    totals = _body(client)["totals"]
    # Nov: a 1,000 food spend from the bank and 8,000 on the card.
    assert totals["change_this_month"] == "-9000.00"
    before_fy = _body(client, "?as_of=2026-03-31")["totals"]["net_worth"]
    assert Decimal(totals["change_this_fy"]) == Decimal(totals["net_worth"]) - Decimal(
        before_fy
    )


def test_bank_group_facts(client):
    bank = _group(_body(client), "bank")
    # Checking 300000-60000-5000-100000-1000 = 134,000; Old 2,000; Cash 5,000.
    assert bank["total"] == "141000.00"
    assert bank["account_count"] == 3
    # 141,000 / 20,000 = 7.05, half-up to 7.1.
    assert bank["facts"]["months_of_cover"] == "7.1"
    assert bank["facts"]["change_this_month"] == "-1000.00"
    assert bank["facts"]["stale_count"] == 1
    stale = {a["account"]: a["stale"] for a in bank["accounts"]}
    assert stale[OLD] is True
    assert stale[CHK] is False


def test_every_row_carries_a_display_counterpart(client):
    body = _body(client)
    rows = [a for g in body["groups"] + body["liability_groups"] for a in g["accounts"]]
    assert rows and all(r["account_display"] for r in rows)
    bank = _group(body, "bank")
    assert {a["account"]: a["account_display"] for a in bank["accounts"]}[CHK] == CHK


def test_property_is_at_cost(client):
    prop = _group(_body(client), "property")
    assert prop["total"] == "900000.00"
    assert prop["facts"]["valuation"] == "at_cost"
    assert prop["accounts"][0]["meta"] == "at cost · since 2025"


def test_people_and_loans_and_cards(client):
    body = _body(client)
    people = _group(body, "people")
    assert people["facts"]["owed_to_you"] == "4000.00"
    assert people["facts"]["you_owe"] == "0.00"
    assert people["accounts"][0]["direction"] == "owed_to_me"
    loans = _group(body, "loans")
    assert loans["total"] == "12000.00"
    assert loans["facts"]["emi_per_month"] == "1000.00"
    assert loans["facts"]["last_ends"] == "2027-11"
    cards = _group(body, "cards")
    assert cards["total"] == "8000.00"
    assert cards["facts"]["owed_net_of_in_transit"] == "8000.00"
    assert cards["facts"]["paid_awaiting_statement"] == "0.00"


def test_investment_vehicles_with_xirr_and_at_cost_flags(client):
    body = _body(client)
    vehicles = {v["key"]: v for v in body["investments"]["vehicles"]}
    assert set(vehicles) == {
        "mutual_fund",
        "stock",
        "fd",
        "ppf",
        "nps",
        "insurance",
    }
    stock = vehicles["stock"]
    # 10 units bought at 100 on 2025-11-15, worth 150 on 2026-11-15: exactly
    # one 365-day year for +50%.
    assert (stock["invested"], stock["value"], stock["gain"]) == (
        "1000.00",
        "1500.00",
        "500.00",
    )
    assert stock["xirr_pct"] == "50.00"
    assert stock["at_cost"] is False
    mf = vehicles["mutual_fund"]
    assert (mf["invested"], mf["value"], mf["gain"]) == (
        "21500.00",
        "22000.00",
        "500.00",
    )
    expected = ((22000 / 21500) ** (365 / 211) - 1) * 100
    assert float(mf["xirr_pct"]) == pytest.approx(expected, abs=0.01)
    for key in ("fd", "ppf", "nps", "insurance"):
        v = vehicles[key]
        assert v["at_cost"] is True
        assert v["xirr_pct"] is None and v["gain"] is None
        assert v["invested"] == v["value"]
    assert vehicles["insurance"]["value"] == "20000.00"
    shares = sum(Decimal(v["share_pct"]) for v in vehicles.values())
    assert abs(shares - 100) < Decimal("0.1")
    # Largest value first.
    values = [Decimal(v["value"]) for v in body["investments"]["vehicles"]]
    assert values == sorted(values, reverse=True)


def test_investments_group_facts_pool_priced_holdings(client):
    inv = _group(_body(client), "investments")
    assert inv["facts"]["gain"] == "1000.00"  # 500 stock + 500 mutual fund
    assert inv["facts"]["xirr_pct"] is not None
    assert Decimal(inv["facts"]["invested"]) == Decimal("1000") + Decimal(
        "21500"
    ) + Decimal("100000") + Decimal("50000") + Decimal("30000") + Decimal("20000")


def test_default_benchmark_is_nifty50_from_the_seeded_file(client, app):
    body = _body(client)
    block = body["benchmarks"]
    assert block["selected"] == "nifty50"
    assert block["selected_return_pct"] == "12.00"
    keys = [o["key"] for o in block["options"]]
    assert keys == [
        "nifty50",
        "nifty500",
        "sensex",
        "midcap150",
        "gold",
        "fd",
        "ppf",
        "cpi",
        "blended",
    ]
    stock = next(v for v in body["investments"]["vehicles"] if v["key"] == "stock")
    assert stock["benchmark_pct"] == "12.00"
    assert stock["vs_benchmark_pct"] == "38.00"  # 50.00 - 12.00
    assert (app.state.ledger.storage_dir / "config" / "benchmarks.yaml").exists()


def test_blended_weights_each_vehicle_by_share_of_value(client):
    body = _body(client, "?benchmark=blended&equity_index=nifty500")
    block = body["benchmarks"]
    values = {v["key"]: Decimal(v["value"]) for v in body["investments"]["vehicles"]}
    total = sum(values.values())
    rate = {
        "mutual_fund": Decimal("12.5"),  # equity -> the chosen index
        "stock": Decimal("12.5"),
        "fd": Decimal("7.0"),
        "insurance": Decimal("7.0"),
        "ppf": Decimal("7.1"),
        "nps": Decimal("7.1"),
    }
    expected = sum(values[k] / total * rate[k] for k in values)
    assert Decimal(block["blended_return_pct"]) == pytest.approx(
        expected, abs=Decimal("0.01")
    )
    assert block["selected_return_pct"] == block["blended_return_pct"]
    weights = {w["vehicle"]: w for w in block["blend_weights"]}
    assert weights["stock"]["benchmark"] == "nifty500"
    assert weights["fd"]["benchmark"] == "fd"
    stock = next(v for v in body["investments"]["vehicles"] if v["key"] == "stock")
    assert stock["benchmark"] == "nifty500"


def test_editing_the_benchmarks_file_changes_the_numbers(client, app):
    client.get(OVERVIEW)
    path = app.state.ledger.storage_dir / "config" / "benchmarks.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["benchmarks"]["nifty50"]["annual_return_pct"] = "10.5"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert _body(client)["benchmarks"]["selected_return_pct"] == "10.50"


def test_unknown_benchmark_is_rejected_E1096(client):
    for query in ("?benchmark=nope", "?equity_index=gold"):
        resp = client.get(OVERVIEW + query)
        assert resp.status_code == 422
        assert resp.json()["code"] == "E1096"


def _bench_path(app):
    return app.state.ledger.storage_dir / "config" / "benchmarks.yaml"


def test_unquoted_numbers_in_the_benchmarks_file_are_accepted(client, app):
    client.get(OVERVIEW)
    path = _bench_path(app)
    path.write_text(path.read_text().replace("'12.0'", "10.5", 1), encoding="utf-8")
    body = _body(client)
    assert body["benchmarks"]["selected_return_pct"] == "10.50"
    assert body["benchmarks_error"] is None


@pytest.mark.parametrize(
    "damage",
    [
        lambda p: p.write_text("benchmarks: [not, a, mapping]", encoding="utf-8"),
        lambda p: p.write_text(
            p.read_text().replace("'12.0'", "'NaN'", 1), encoding="utf-8"
        ),
        lambda p: p.write_text(
            p.read_text().replace("'12.0'", ".inf", 1), encoding="utf-8"
        ),
        lambda p: p.write_bytes(b"\xff\xfe\x00bad"),
    ],
    ids=["not-a-mapping", "nan", "inf", "not-utf8"],
)
def test_unusable_benchmarks_file_costs_only_the_benchmark_parts(client, app, damage):
    healthy = _body(client)
    damage(_bench_path(app))
    body = _body(client)  # still 200
    assert body["benchmarks"] is None
    assert body["benchmarks_error"]["code"] == "E1097"
    assert body["benchmarks_error"]["message"]
    assert body["totals"] == healthy["totals"]
    assert body["groups"] == healthy["groups"]
    assert all(v["benchmark"] is None for v in body["investments"]["vehicles"])


def test_a_file_without_nifty50_still_serves_the_default_request(client, app):
    client.get(OVERVIEW)
    path = _bench_path(app)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    del raw["benchmarks"]["nifty50"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    block = _body(client)["benchmarks"]
    assert (block["selected"], block["equity_index"]) == ("nifty500", "nifty500")
    # Naming the missing one explicitly is still a mistake.
    assert client.get(OVERVIEW + "?benchmark=nifty50").status_code == 422


def test_as_of_bounds_the_ledger(client):
    early = _body(client, "?as_of=2026-07-02")
    # Only the salary and the 2025-26 openings exist by then.
    bank = _group(early, "bank")
    assert bank["total"] == "300000.00"
    assert early["as_of"] == "2026-07-02"


def test_empty_ledger_gives_an_empty_overview(tmp_path):
    client = TestClient(create_app(storage_dir=tmp_path / "s", clock=lambda: TODAY))
    body = _body(client)
    assert body["groups"] == [] and body["liability_groups"] == []
    assert body["totals"]["net_worth"] == "0.00"
    assert body["investments"]["vehicles"] == []
    assert body["benchmarks"]["blended_return_pct"] is None


def test_card_payment_in_transit_is_labelled_and_netted(client, app):
    clearing = "Assets:Transfer:CreditCard"
    app.state.ledger.open_named_accounts([clearing])
    app.state.ledger.append(
        [_t(date(2026, 11, 10), "card bill", clearing, CHK, "3000")]
    )
    body = _body(client)
    other = _group(body, "other")
    assert other["total"] == "3000.00"
    assert other["facts"]["includes_in_transit"] == "3000.00"
    assert other["accounts"][0]["name"] == "Card payments in transit"
    cards = _group(body, "cards")
    # The ledger still owes 8,000; 3,000 of it is paid and awaiting a statement.
    assert cards["total"] == "8000.00"
    assert cards["facts"]["paid_awaiting_statement"] == "3000.00"
    assert cards["facts"]["owed_net_of_in_transit"] == "5000.00"
    # Nothing ties the payment to this card by amount, so the per-card row is
    # not netted (only the group fact is): an unattributed payment pays "some"
    # card and is never pinned on one.
    assert cards["accounts"][0]["balance"] == "8000.00"
    # Totals still tie to Balance with the clearing asset counted.
    current = client.get("/api/reports/networth").json()["current"]
    assert body["totals"]["net_worth"] == current["net"]


def test_as_of_bounds_mutual_fund_and_stock_lots(client, app):
    # Before both lots: only the at-cost vehicles opened in April 2025 remain,
    # and the Investments group agrees with its own invested figure.
    early = _body(client, "?as_of=2025-06-01")
    assert {v["key"] for v in early["investments"]["vehicles"]} == {
        "ppf",
        "nps",
        "insurance",
    }
    inv = _group(early, "investments")
    assert inv["total"] == inv["facts"]["invested"] == "100000.00"
    assert inv["facts"]["xirr_pct"] is None
    # Stock held, but its only close is after as_of: counted at cost, no XIRR,
    # never valued at the later price.
    mid = _body(client, "?as_of=2025-12-01")
    stock = next(v for v in mid["investments"]["vehicles"] if v["key"] == "stock")
    assert (stock["value"], stock["at_cost"], stock["xirr_pct"]) == (
        "1000.00",
        True,
        None,
    )
    assert "mutual_fund" not in {v["key"] for v in mid["investments"]["vehicles"]}


def test_as_of_values_at_the_latest_close_on_or_before_it(client, app):
    NavStore(app.state.ledger.storage_dir / "prices" / "equity_history.parquet").append(
        [NavRow("ACME", Decimal("120"), date(2026, 2, 1))]
    )
    stock = next(
        v
        for v in _body(client, "?as_of=2026-03-01")["investments"]["vehicles"]
        if v["key"] == "stock"
    )
    assert (stock["value"], stock["gain"], stock["at_cost"]) == (
        "1200.00",
        "200.00",
        False,
    )
    # 1,000 on 2025-11-15 growing to 1,200 by 2026-03-01 (106 days).
    assert float(stock["xirr_pct"]) == pytest.approx(
        ((1.2) ** (365 / 106) - 1) * 100, abs=0.01
    )


def test_group_totals_hit_the_headline_to_the_paisa_with_fractional_balances(
    client, app
):
    app.state.ledger.open_named_accounts(["Assets:Checking:Sub"])
    app.state.ledger.append(
        [
            _t(date(2026, 10, 1), "a", "Assets:Checking:Sub", "Income:Gift", "0.006"),
            _t(date(2026, 10, 1), "b", "Assets:Cash", "Income:Gift", "0.006"),
            _t(date(2026, 10, 1), "c", "Liabilities:Cash:Misc", "Income:Gift", "0.004"),
        ]
    )
    body = _body(client)
    assert sum(Decimal(g["total"]) for g in body["groups"]) == Decimal(
        body["totals"]["assets"]
    )
    assert sum(Decimal(g["total"]) for g in body["liability_groups"]) == Decimal(
        body["totals"]["liabilities"]
    )


def test_people_in_both_directions_and_other_liabilities(client, app):
    app.state.ledger.open_named_accounts(["Liabilities:People:Ann"])
    app.state.ledger.append(
        [
            _t(date(2026, 10, 1), "ann", OPENING, "Liabilities:People:Ann", "2500"),
            _t(date(2026, 10, 2), "misc", OPENING, "Liabilities:Cash:Misc", "300"),
        ]
    )
    body = _body(client)
    people = _group(body, "people")
    assert people["facts"]["you_owe"] == "2500.00"
    assert people["facts"]["owed_to_you"] == "4000.00"
    assert people["total"] == "-1500.00"  # net: they owe you more
    directions = {a["name"]: a["direction"] for a in people["accounts"]}
    assert directions == {"Sam": "owed_to_me", "Ann": "i_owe"}
    other = _group(body, "other_liabilities")
    assert other["total"] == "300.00"
    assert [g["id"] for g in body["liability_groups"]][-1] == "other_liabilities"
    assert sum(Decimal(g["total"]) for g in body["liability_groups"]) == Decimal(
        body["totals"]["liabilities"]
    )
