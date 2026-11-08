"""Dashboard v2 route: net worth and months of cover from the Net Worth
overview, needs-action items with their routes.

One synthetic persona ("Acme"), clock 2026-11-15.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import yaml
from beancount.core import data
from beancount.core.amount import Amount
from fastapi.testclient import TestClient

from backend.app.models.domain.nav import NavRow
from backend.app.persistence.nav_store import NavStore
from backend.app.server.main import create_app
from tests.conftest import make_txn

TODAY = date(2026, 11, 15)
URL = "/api/reports/dashboard/v2"
CHK = "Assets:Checking:Acme"
OLD = "Assets:Checking:Old"
EMPTY = "Assets:Checking:Empty"
CARD = "Liabilities:CreditCard:Acme1234"
CARD2 = "Liabilities:CreditCard:Beta5678"
OPENING = "Equity:Opening-Balances"
FD = "Assets:Investment:FD:Acme"
LOAN = "Liabilities:Loan:Bike"
CLEARING = "Assets:Transfer:CreditCard"
STOCK = "Assets:Investment:Stock:ACME"


def _buy_stock(ledger, when: date) -> None:
    """10 ACME at 100 each, priced at 150 on 14 Nov 2026."""
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
                        STOCK,
                        Amount(Decimal("10"), "ACME"),
                        data.Cost(Decimal("100"), "INR", when, None),
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
    NavStore(ledger.storage_dir / "prices" / "equity_history.parquet").append(
        [NavRow("ACME", Decimal("150"), date(2026, 11, 14))]
    )


@pytest.fixture
def client(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: TODAY)
    led = app.state.ledger
    led.open_named_accounts(
        [
            CHK,
            OLD,
            EMPTY,
            CARD,
            CARD2,
            FD,
            LOAN,
            CLEARING,
            "Assets:House:Home",
            "Assets:Wallet:Pay",
            "Expenses:Food",
            "Expenses:Home",
            "Expenses:Shopping",
            "Expenses:Uncategorized",
            "Expenses:Investment:Charges",
            "Income:Salary",
            "Income:Gift",
        ]
    )
    led.main_file.write_text(
        led.main_file.read_text(encoding="utf-8").rstrip()
        + '\n2000-01-01 commodity ACME\n  name: "Acme Ltd"'
        + f'\n2000-01-01 open {STOCK} ACME "FIFO"\n',
        encoding="utf-8",
    )
    t = make_txn
    led.append(
        [
            t(date(2025, 4, 1), "opening", CHK, OPENING, "200000"),
            t(date(2025, 4, 1), "house", "Assets:House:Home", OPENING, "500000"),
            t(date(2025, 4, 1), "wallet", "Assets:Wallet:Pay", OPENING, "3000"),
            t(date(2026, 7, 1), "salary", CHK, "Income:Salary", "300000"),
            # A one-off in May, then food Aug 20k, Sep 10k, Oct 30k; October
            # also has 999 uncategorized and 4,000 on Beta. The six months
            # May..Oct average 26,249.75 (months with spending only); the
            # three months Aug..Oct 21,666.33.
            t(date(2026, 5, 12), "sofa", "Expenses:Home", CHK, "40000"),
            t(date(2026, 8, 20), "food", "Expenses:Food", CHK, "20000"),
            t(date(2026, 9, 20), "food", "Expenses:Food", CHK, "10000"),
            t(date(2026, 10, 20), "food", "Expenses:Food", CHK, "30000"),
            t(date(2026, 8, 1), "fd", FD, CHK, "100000"),
            # Investment cost, not spending: Invested, never Spent.
            t(date(2026, 8, 1), "stamp", "Expenses:Investment:Charges", CHK, "50"),
            # A 12,000 loan at 0% over 12 months.
            t(date(2026, 9, 1), "bike loan", OPENING, LOAN, "12000"),
            # Last activity 2026-09-01, 75 days before the clock: stale.
            t(date(2026, 9, 1), "gift", OLD, "Income:Gift", "2000"),
            # Stale too, but empty: not an action.
            t(date(2026, 8, 1), "in", EMPTY, OPENING, "1000"),
            t(date(2026, 8, 2), "out", OPENING, EMPTY, "1000"),
            # November: an 8,000 card spend, two uncategorized rows (750), one
            # uncategorized in October (not this month).
            t(date(2026, 11, 2), "shop", "Expenses:Shopping", CARD, "8000"),
            t(date(2026, 11, 3), "mystery", "Expenses:Uncategorized", CHK, "500"),
            t(date(2026, 11, 4), "mystery2", "Expenses:Uncategorized", CHK, "250"),
            t(date(2026, 10, 4), "older", "Expenses:Uncategorized", CHK, "999"),
            # On Beta before its 20 Oct statement, never paid: due 10 Nov,
            # overdue on 15 Nov.
            t(date(2026, 10, 15), "beta", "Expenses:Shopping", CARD2, "4000"),
            # Acme's 8,000 paid from the bank on 10 Nov, not on a statement yet.
            t(date(2026, 11, 10), "acme bill", CLEARING, CHK, "8000"),
            # After the clock: never counted.
            t(date(2026, 11, 20), "future", "Expenses:Food", CHK, "5555"),
        ]
    )
    _buy_stock(led, date(2025, 11, 15))
    (led.storage_dir / "rules").mkdir(exist_ok=True)
    (led.storage_dir / "rules" / "loan-terms.yaml").write_text(
        yaml.safe_dump({LOAN: {"annual_rate_pct": 0, "term_months": 12}}),
        encoding="utf-8",
    )
    c = TestClient(app)
    for account, statement_day, due_day in ((CARD, 1, 20), (CARD2, 20, 10)):
        resp = c.put(
            "/api/accounts/card-properties",
            json={
                "account": account,
                "statement_day": statement_day,
                "due_day": due_day,
                "last4": account[-4:],
            },
        )
        assert resp.status_code == 200, resp.text
    return c


def _body(client, query: str = "") -> dict:
    resp = client.get(URL + query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _overview(client, as_of: str) -> dict:
    resp = client.get(f"/api/reports/networth/overview?as_of={as_of}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _facts(overview: dict, gid: str) -> dict:
    (group,) = [
        g for g in overview["groups"] + overview["liability_groups"] if g["id"] == gid
    ]
    return group["facts"]


def test_net_worth_and_its_breakdown_add_up(client):
    nw = _body(client)["net_worth"]
    overview = _overview(client, TODAY.isoformat())
    assert nw["current"] == overview["totals"]["net_worth"]
    b = nw["breakdown"]
    total = (
        Decimal(b["investments"]["total"])
        + Decimal(b["bank"]["total"])
        + Decimal(b["other"]["total"])
        - Decimal(b["liabilities"]["total"])
    )
    assert total == Decimal(nw["current"])
    assert b["other"]["groups"] == ["property", "other"]
    # FD 100,000 and the stock at cost 1,000.
    assert b["investments"]["total"] == "101000.00"
    # Acme 8,000 + Beta 4,000 + the loan 12,000.
    assert b["liabilities"]["total"] == "24000.00"
    assert [b[k]["route"] for k in ("investments", "bank", "other", "liabilities")] == [
        "networth?section=investments",
        "networth?section=bank",
        "networth?section=other",
        "networth?section=liabilities",
    ]


def test_breakdown_adds_up_to_the_paisa_with_sub_paisa_balances(tmp_path):
    """Three groups each holding .004: rounded on their own they make
    2,100.00, the net rounds once to 2,100.01; the breakdown still adds up."""
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: TODAY)
    led = app.state.ledger
    led.open_named_accounts([CHK, FD, "Assets:Cash", OPENING])
    legs = [
        (CHK, "1000.004"),
        (FD, "1000.004"),
        ("Assets:Cash", "100.004"),
        (OPENING, "-2100.012"),
    ]
    led.append(
        [
            data.Transaction(
                data.new_metadata("<test>", 0),
                date(2026, 5, 1),
                "*",
                None,
                "opening",
                data.EMPTY_SET,
                data.EMPTY_SET,
                [
                    data.Posting(a, Amount(Decimal(v), "INR"), None, None, None, None)
                    for a, v in legs
                ],
            )
        ]
    )
    nw = _body(TestClient(app))["net_worth"]
    assert nw["current"] == "2100.01"
    b = nw["breakdown"]
    assert (
        Decimal(b["investments"]["total"])
        + Decimal(b["bank"]["total"])
        + Decimal(b["other"]["total"])
        - Decimal(b["liabilities"]["total"])
    ) == Decimal(nw["current"])


def test_both_screens_default_to_today_and_agree(client):
    """Neither screen sends `as_of`; both mean today, so a card spend dated
    after today counts on neither (R35)."""
    client.app.state.ledger.append(
        [make_txn(date(2026, 11, 20), "future card", "Expenses:Food", CARD, "30000")]
    )
    dash = _body(client)
    resp = client.get("/api/reports/networth/overview")
    assert resp.status_code == 200, resp.text
    overview = resp.json()
    assert overview["as_of"] == dash["as_of"] == TODAY.isoformat()
    nw = dash["net_worth"]
    assert overview["totals"]["net_worth"] == nw["current"]
    assert overview["totals"]["liabilities"] == nw["breakdown"]["liabilities"]["total"]
    assert nw["breakdown"]["liabilities"]["total"] == "24000.00"


def test_months_of_cover_is_the_net_worth_overview_figure(client):
    bank = _body(client)["net_worth"]["breakdown"]["bank"]
    assert bank["cover_basis_months"] == 3
    assert bank["total"] == "292201.00"
    # 292,201 / 21,666.33 (Aug..Oct) = 13.49; over six months (26,249.75) it
    # would be 11.1.
    assert bank["months_of_cover"] == "13.5"
    facts = _facts(_overview(client, TODAY.isoformat()), "bank")
    assert bank["months_of_cover"] == facts["months_of_cover"]


def test_breakdown_facts_are_the_net_worth_overview_facts(client):
    body = _body(client)
    b = body["net_worth"]["breakdown"]
    overview = _overview(client, TODAY.isoformat())

    inv = _facts(overview, "investments")
    assert b["investments"]["xirr_pct"] == inv["xirr_pct"] is not None
    assert b["investments"]["gain"] == inv["gain"] == "500.00"  # 10 x (150 - 100)
    vehicles = overview["investments"]["vehicles"]
    assert Decimal(b["investments"]["value"]) == sum(
        Decimal(v["value"]) for v in vehicles
    )
    assert b["investments"]["value"] == "101500.00"
    assert b["investments"]["vehicles"] == len(vehicles) == 2
    assert b["investments"]["xirr_basis"] == overview["investments"]["xirr_basis"]

    loans = _facts(overview, "loans")
    assert b["liabilities"]["emi_per_month"] == loans["emi_per_month"] == "1000.00"
    assert b["liabilities"]["emi_last_ends"] == loans["last_ends"] is not None

    cards = _facts(overview, "cards")
    # 12,000 owed on the cards less Acme's 8,000 in transit.
    assert (
        b["liabilities"]["cards_net_of_in_transit"]
        == cards["owed_net_of_in_transit"]
        == "4000.00"
    )

    stale = [i for i in body["needs_action"] if i["kind"] == "stale_import"]
    assert b["bank"]["stale_count"] == _facts(overview, "bank")["stale_count"] == 1
    assert b["bank"]["stale_count"] == len(stale)


def _stale_on_both(client) -> list[str]:
    body = _body(client)
    overview = client.get("/api/reports/networth/overview").json()
    stale = [i["account"] for i in body["needs_action"] if i["kind"] == "stale_import"]
    count = body["net_worth"]["breakdown"]["bank"]["stale_count"]
    assert count == _facts(overview, "bank")["stale_count"] == len(stale)
    return stale


def test_a_closed_bank_account_is_stale_on_neither_screen(client):
    """A closed account with a balance left on it gets no new imports."""
    led = client.app.state.ledger
    shut = "Assets:Checking:Shut"
    led.open_named_accounts([shut])
    led.append([make_txn(date(2026, 6, 1), "left", shut, OPENING, "700")])
    led.close_account(shut, date(2026, 7, 1))
    assert _stale_on_both(client) == [OLD]


def test_half_a_paisa_is_stale_on_both_screens(client):
    """0.005 shows as 0.01 (half up), so the account has a balance."""
    led = client.app.state.ledger
    dust = "Assets:Checking:Dust"
    led.open_named_accounts([dust])
    led.append([make_txn(date(2026, 6, 1), "dust", dust, OPENING, "0.005")])
    assert _stale_on_both(client) == [dust, OLD]


def test_cash_flow_matches_the_cash_flow_overview(client):
    for period in _body(client)["cash_flow"]:
        resp = client.get(
            f"/api/reports/cashflow/overview?from={period['start']}&to={period['end']}"
        )
        assert resp.status_code == 200, resp.text
        totals = resp.json()["totals"]
        assert (period["income"], period["spent"], period["invested"]) == (
            totals["income"],
            totals["expenses"],
            totals["invested"],
        ), period["key"]
    fy = _body(client)["cash_flow"][1]
    # FD 100,000 + stamp duty 50; the stamp duty is never Spent.
    assert fy["invested"] == "100050.00"


def test_series_is_eleven_month_ends_then_today(client):
    nw = _body(client)["net_worth"]
    series = nw["series"]
    assert len(series) == 12
    assert series[0]["date"] == "2025-12-31"
    assert series[-2]["date"] == "2026-10-31"
    assert series[-1]["date"] == "2026-11-15"
    assert series[-1]["net_worth"] == nw["current"]
    assert (
        series[-2]["net_worth"]
        == _overview(client, "2026-10-31")["totals"]["net_worth"]
    )
    base = Decimal(_overview(client, "2025-11-30")["totals"]["net_worth"])
    assert Decimal(nw["change_12_months"]) == Decimal(nw["current"]) - base


def test_needs_action_items_and_routes(client):
    items = _body(client)["needs_action"]
    by_kind: dict[str, list[dict]] = {}
    for item in items:
        by_kind.setdefault(item["kind"], []).append(item)

    # Beta's 20 Oct statement (4,000) was due 10 Nov and is unpaid: overdue,
    # listed first. Acme: statement 1 Nov, due 20 Nov (5 days), already paid.
    overdue, due = by_kind["card_due"]
    assert (
        overdue["account"],
        overdue["overdue"],
        overdue["date"],
        overdue["days"],
        overdue["amount"],
    ) == (CARD2, True, "2026-11-10", -5, "4000.00")
    assert (due["account"], due["overdue"], due["date"], due["days"]) == (
        CARD,
        False,
        "2026-11-20",
        5,
    )
    assert due["amount"] == "8000.00"
    assert (due["paid_awaiting_statement"], due["paid_on"]) == ("8000.00", "2026-11-10")
    assert {i["route"] for i in by_kind["card_due"]} == {"networth/liabilities"}
    assert items[0] is overdue

    (unc,) = by_kind["uncategorized"]
    assert (unc["count"], unc["amount"], unc["route"]) == (
        2,
        "750.00",
        "expense/uncategorized",
    )

    # Beta's statement on 20 Nov (5 days) and Acme's on 1 Dec (16 days).
    (stmts,) = by_kind["statements_expected"]
    assert [(s["account"], s["date"]) for s in stmts["statements"]] == [
        (CARD2, "2026-11-20"),
        (CARD, "2026-12-01"),
    ]
    assert stmts["route"] == "ledger/import"

    stale = by_kind["stale_import"]
    assert [(s["account"], s["days"]) for s in stale] == [(OLD, 75)]
    assert stale[0]["route"] == "ledger/import"


def test_as_of_bounds_everything(client):
    body = _body(client, "?as_of=2026-10-31")
    assert body["as_of"] == "2026-10-31"
    assert (
        body["net_worth"]["current"]
        == _overview(client, "2026-10-31")["totals"]["net_worth"]
    )
    # October's uncategorized row counts for an October report.
    (unc,) = [i for i in body["needs_action"] if i["kind"] == "uncategorized"]
    assert (unc["count"], unc["amount"]) == (1, "999.00")
    # On 31 Oct, Acme (owing nothing yet) is due 20 Nov and Beta 10 Nov: both
    # more than 7 days away.
    assert "card_due" not in {i["kind"] for i in body["needs_action"]}
    # The October cash flow ends on the report date.
    month = body["cash_flow"][0]
    assert (month["start"], month["end"]) == ("2026-10-01", "2026-10-31")


def test_bad_benchmarks_file_does_not_fail_the_dashboard(client, tmp_path):
    cfg = tmp_path / "storage" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "benchmarks.yaml").write_text("benchmarks: [unclosed", encoding="utf-8")
    assert client.get("/api/reports/networth/overview").json()["benchmarks_error"]
    assert (
        _body(client)["net_worth"]["breakdown"]["investments"]["total"] == "101000.00"
    )


@pytest.mark.parametrize(
    ("debit", "label"),
    [(CLEARING, "paid from the bank, in transit"), (CARD2, "paid on the card")],
)
def test_a_paid_statement_is_not_overdue(client, debit, label):
    # Beta's 4,000 statement, paid on 12 Nov (after its 10 Nov due date).
    client.app.state.ledger.append(
        [make_txn(date(2026, 11, 12), "beta bill", debit, CHK, "4000")]
    )
    cards = [i for i in _body(client)["needs_action"] if i["kind"] == "card_due"]
    assert [i["account"] for i in cards] == [CARD], label
