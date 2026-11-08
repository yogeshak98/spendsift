from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from tests.conftest import make_txn


def _seed(app):
    commit_candidates(
        app.state.ledger,
        app.state.investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 1),
                amount=Decimal("412"),
                raw_narration="Swiggy",
                normalized_narration="SWIGGY",
                source_account="Assets:Bank:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Food",
            ),
        ],
    )


def test_report_endpoints(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)
    client = TestClient(app)

    r = client.get("/api/reports/expense/monthly?year=2026&month=4")
    assert r.status_code == 200
    assert r.json()["by_category"][0]["category"] == "Expenses:Food"
    j = r.json()
    assert "postings" in j and "trailing" in j
    assert j["postings"][0]["account"].startswith("Expenses:")
    assert len(j["trailing"]) == 6
    assert j["trailing"][-1]["amount"] == "412"  # seed: Apr-2026 Swiggy 412

    r = client.get("/api/reports/dashboard?year=2026&month=4")
    assert r.json()["spent_this_month"] == "412"

    r = client.get("/api/reports/doctor")
    assert r.json()["journal_valid"] is True

    r = client.get("/api/reports/cashflow/monthly?fy=2026")
    assert r.status_code == 200
    j = r.json()
    assert len(j["series"]) == 12
    assert j["series"][0]["month"] == 4
    first = j["series"][0]
    for key in ("income", "expenses", "liabilities", "investment", "tax", "balance"):
        assert isinstance(first[key], str)

    # Contract change enforced: calendar `year` param is no longer accepted.
    r = client.get("/api/reports/cashflow/monthly?year=2026")
    assert r.status_code == 422

    r = client.get("/api/reports/cashflow/sankey?fy=2026")
    assert r.status_code == 200
    j = r.json()
    assert isinstance(j["links"], list)
    assert all(isinstance(link["value"], str) for link in j["links"])


def test_routes_folded_into_the_overviews_are_retired(tmp_path):
    # T13: Expense/Income/Cash Flow Overviews replaced these; /cashflow/pnl
    # (Compare FYs) and /liabilities/credit-cards/monthly stay.
    client = TestClient(create_app(storage_dir=tmp_path / "storage"))
    for path in (
        "/api/reports/expense/yearly?fy=2026",
        "/api/reports/expense/breakdown?year=2026&month=4",
        "/api/reports/cashflow/income-yearly?fy=2026",
        "/api/reports/cashflow/income-statement?year=2026",
    ):
        assert client.get(path).status_code == 404, path
    assert client.get("/api/reports/cashflow/pnl?fys=2026").status_code == 200


def test_dashboard_and_assets_balance_resolve_scheme_code_to_fund_name(tmp_path):
    # Post the investment-ledger detach split (2026-09-05 plan Unit 3): the
    # per-scheme commodity account now lives in investments.beancount, so
    # main.beancount only carries the coarse vehicle-level account
    # (Assets:Investment:MutualFund) with no per-scheme code to resolve. The
    # per-scheme resolution scenario moves to the investment ledger's own
    # view (Unit 5), not these main-ledger report routes.
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
            )
        ],
    )
    client = TestClient(app)

    r = client.get("/api/reports/dashboard?year=2026&month=4")
    assert r.status_code == 200
    account = next(
        a
        for a in r.json()["accounts"]
        if a["account"] == "Assets:Investment:MutualFund"
    )
    assert account["account_display"] == "Assets:Investment:MutualFund"

    r = client.get("/api/reports/assets/balance")
    assert r.status_code == 200
    balance = next(
        b
        for b in r.json()["balances"]
        if b["account"] == "Assets:Investment:MutualFund"
    )
    assert balance["account_display"] == "Assets:Investment:MutualFund"


def test_available_years_route(tmp_path):
    """GET /api/reports/available-years returns 200 with both year lists as ints."""
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)  # seeds Apr-2026 Swiggy -> calendar 2026, FY 2026
    client = TestClient(app)

    r = client.get("/api/reports/available-years")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["calendar_years"], list)
    assert isinstance(body["fiscal_years"], list)
    assert all(isinstance(y, int) for y in body["calendar_years"])
    assert all(isinstance(y, int) for y in body["fiscal_years"])
    assert body["calendar_years"] == [2026]
    assert body["fiscal_years"] == [2026]


def test_ledger_transactions_route_returns_only_fy(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)  # seeds a 2026-04-01 txn -> FY 2026
    client = TestClient(app)

    r = client.get("/api/reports/ledger/transactions?fy=2026")
    assert r.status_code == 200
    rows = r.json()["transactions"]
    assert len(rows) == 1
    assert rows[0]["txn_id"]
    posting = rows[0]["postings"][0]
    assert isinstance(posting["amount"], str)

    r = client.get("/api/reports/ledger/transactions?fy=2025")
    assert r.status_code == 200
    assert r.json()["transactions"] == []


def test_ledger_transactions_missing_fy_is_422(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)
    client = TestClient(app)

    assert client.get("/api/reports/ledger/transactions").status_code == 422


def test_ledger_transactions_shape_matches_legacy(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)
    client = TestClient(app)

    txn = client.get("/api/reports/ledger/transactions?fy=2026").json()["transactions"][
        0
    ]
    assert set(txn.keys()) == {
        "txn_id",
        "date",
        "narration",
        "year_file_hash",
        "postings",
    }
    posting = txn["postings"][0]
    assert set(posting.keys()) == {
        "account",
        "account_display",
        "amount",
        "tags",
        "meta",
    }
    assert "txn-id" not in posting["meta"]


def test_cashflow_pnl_smoke(tmp_path):
    """GET /api/reports/cashflow/pnl returns the P&L matrix for the requested FYs."""
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)  # seeds Apr-2026 Swiggy 412 (Expenses:Food)
    client = TestClient(app)
    r = client.get("/api/reports/cashflow/pnl?fys=2025,2026")
    assert r.status_code == 200
    body = r.json()
    # Typed response_model keys (mirror the read-model dict shape field-for-field).
    assert set(body.keys()) == {"fys", "columns", "sections", "net"}
    assert set(body["columns"][0].keys()) == {"fy", "label"}
    assert [c["fy"] for c in body["columns"]] == [2025, 2026]
    exp = next(s for s in body["sections"] if s["key"] == "expenses")
    assert set(exp.keys()) == {"key", "label", "line_items", "totals"}
    food = next(li for li in exp["line_items"] if li["category"] == "Expenses:Food")
    assert set(food.keys()) == {"category", "amounts"}
    assert food["amounts"] == ["0", "412"]
    assert body["net"] == ["0", "-412"]  # only an expense in the seed -> negative net
    assert len(body["net"]) == len(body["columns"]) == 2


def test_cashflow_pnl_rejects_non_integer_fys(tmp_path):
    """A malformed `fys` token must yield a clean 422, not a generic 500."""
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)
    client = TestClient(app)
    assert client.get("/api/reports/cashflow/pnl?fys=abc").status_code == 422
    assert client.get("/api/reports/cashflow/pnl?fys=2025.5").status_code == 422


def test_cashflow_recurring_smoke(tmp_path):
    """GET /api/reports/cashflow/recurring returns 200 with summary + items keys."""
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)
    client = TestClient(app)

    r = client.get("/api/reports/cashflow/recurring?year=2026&month=4")
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body
    assert "items" in body
    assert "stream_count" in body["summary"]
    assert "cleared" in body["summary"]
    assert "past_due" in body["summary"]
    assert "upcoming" in body["summary"]


def test_expense_budget_no_config(tmp_path):
    """GET /expense/budget with no budget.yaml returns 200, targets==[], total_budgeted=="0"."""
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)
    client = TestClient(app)

    r = client.get("/api/reports/expense/budget?year=2026&month=4")
    assert r.status_code == 200
    body = r.json()
    assert body["targets"] == []
    assert body["total_budgeted"] == "0"


def test_liabilities_credit_card_routes(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    commit_candidates(
        app.state.ledger,
        app.state.investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 2),
                amount=Decimal("4299"),
                raw_narration="Amazon",
                normalized_narration="AMAZON",
                source_account="Liabilities:CreditCard:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
        ],
    )
    client = TestClient(app)

    r = client.get("/api/reports/liabilities/credit-cards")
    assert r.status_code == 200
    cards = {c["account"]: c for c in r.json()["cards"]}
    assert cards["Liabilities:CreditCard:HDFC"]["outstanding"] == "4299.00"

    r = client.get("/api/reports/liabilities/credit-cards/Liabilities:CreditCard:HDFC")
    assert r.status_code == 200
    assert r.json()["transactions"][0]["narration"] == "Amazon"

    r = client.get("/api/reports/liabilities/interest")
    assert r.status_code == 200
    assert r.json()["rows"] == []


def test_liabilities_repayment_route(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    commit_candidates(
        app.state.ledger,
        app.state.investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 1),
                amount=Decimal("100000"),
                raw_narration="Car loan drawdown",
                normalized_narration="CAR LOAN",
                source_account="Liabilities:Loan:Car",
                direction=Direction.DEBIT,  # nets to a negative ledger balance
                suggested_other_leg="Assets:Bank:HDFC",
            ),
        ],
    )
    # No terms file yet -> loan listed, terms_set false
    client = TestClient(app)
    r = client.get("/api/reports/liabilities/repayment")
    assert r.status_code == 200
    loans = r.json()["loans"]
    car = next(loan for loan in loans if loan["account"] == "Liabilities:Loan:Car")
    assert car["terms_set"] is False
    assert car["outstanding"] == "100000"
    assert car["schedule"] == []

    # Add terms -> full schedule appears
    terms_path = app.state.ledger.storage_dir / "rules" / "loan-terms.yaml"
    terms_path.write_text(
        'Liabilities:Loan:Car:\n  annual_rate_pct: "12"\n  term_months: 12\n'
    )
    r = client.get("/api/reports/liabilities/repayment")
    car = next(
        loan for loan in r.json()["loans"] if loan["account"] == "Liabilities:Loan:Car"
    )
    assert car["terms_set"] is True
    assert len(car["schedule"]) == 12
    assert car["schedule"][-1]["balance"] == "0"
    assert isinstance(car["emi"], str)


def test_sankey_contributions_route_returns_flow_transactions(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    commit_candidates(
        app.state.ledger,
        app.state.investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 1),
                amount=Decimal("412"),
                raw_narration="Swiggy",
                normalized_narration="SWIGGY",
                source_account="Assets:Bank:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Food",
            ),
            CandidateTxn(
                date=date(2026, 4, 4),
                amount=Decimal("35000"),
                raw_narration="Rent",
                normalized_narration="RENT",
                source_account="Assets:Bank:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Rent",
            ),
        ],
    )
    client = TestClient(app)

    r = client.get(
        "/api/reports/cashflow/sankey/contributions",
        params={"fy": 2026, "source": "Assets:Bank:HDFC", "target": "Expenses:Rent"},
    )
    assert r.status_code == 200
    txns = r.json()["transactions"]
    # Only the Rent flow contributes; the Swiggy txn (Food, folded into
    # Other at 1.2% of expenses) is excluded.
    assert len(txns) == 1
    assert txns[0]["narration"] == "Rent"
    posting = txns[0]["postings"][0]
    assert set(posting.keys()) == {
        "account",
        "account_display",
        "amount",
        "tags",
        "meta",
    }
    assert "txn-id" not in posting["meta"]
    assert isinstance(posting["amount"], str)


def test_sankey_contributions_route_unknown_flow_is_empty_200(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)
    client = TestClient(app)
    r = client.get(
        "/api/reports/cashflow/sankey/contributions",
        params={"fy": 2026, "source": "Checking", "target": "Expenses:Travel"},
    )
    assert r.status_code == 200
    assert r.json()["transactions"] == []


def test_sankey_contributions_route_missing_params_is_422(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed(app)
    client = TestClient(app)
    # All three params are required (match the FY-route convention).
    assert client.get("/api/reports/cashflow/sankey/contributions").status_code == 422
    assert (
        client.get(
            "/api/reports/cashflow/sankey/contributions", params={"fy": 2026}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/reports/cashflow/sankey/contributions",
            params={"fy": 2026, "source": "Checking"},
        ).status_code
        == 422
    )


def _seed_cards(app):
    commit_candidates(
        app.state.ledger,
        app.state.investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 2),
                amount=Decimal("1000"),
                raw_narration="A",
                normalized_narration="A",
                source_account="Liabilities:CreditCard:Acme",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
            CandidateTxn(
                date=date(2026, 4, 10),
                amount=Decimal("400"),
                raw_narration="Pay",
                normalized_narration="PAY",
                source_account="Liabilities:CreditCard:Acme",
                direction=Direction.CREDIT,
                suggested_other_leg="Assets:Bank:HDFC",
            ),
        ],
    )


def test_credit_cards_monthly_typed_shape(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed_cards(app)
    client = TestClient(app)

    r = client.get("/api/reports/liabilities/credit-cards/monthly?fy=2026")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"fy", "account", "cards", "opening", "months"}
    assert body["fy"] == 2026
    assert body["account"] is None
    assert body["cards"] == ["Liabilities:CreditCard:Acme"]
    assert len(body["months"]) == 12
    pt = body["months"][0]
    assert set(pt.keys()) == {
        "month",
        "purchases",
        "charges",
        "payments",
        "refunds",
        "emi",
        "adjustments",
        "outstanding",
    }
    assert pt["month"] == "2026-04"
    assert pt["purchases"] == "1000"
    assert pt["payments"] == "400"


def test_credit_cards_monthly_account_filter(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed_cards(app)
    client = TestClient(app)

    r = client.get(
        "/api/reports/liabilities/credit-cards/monthly"
        "?fy=2026&account=Liabilities:CreditCard:Acme"
    )
    assert r.status_code == 200
    assert r.json()["account"] == "Liabilities:CreditCard:Acme"


def test_credit_cards_monthly_rejects_non_integer_fy(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed_cards(app)
    client = TestClient(app)

    r = client.get("/api/reports/liabilities/credit-cards/monthly?fy=notayear")
    assert r.status_code == 422


def test_credit_cards_monthly_route_not_shadowed_by_account_param(tmp_path):
    # The literal `monthly` segment must resolve to the monthly route, NOT be
    # captured by `/credit-cards/{account}` as account="monthly".
    app = create_app(storage_dir=tmp_path / "storage")
    _seed_cards(app)
    client = TestClient(app)

    r = client.get("/api/reports/liabilities/credit-cards/monthly?fy=2026")
    body = r.json()
    assert "months" in body and "transactions" not in body


def test_expense_tags_route_returns_groups_and_by_tag(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    entry_cand = CandidateTxn(
        date=date(2026, 4, 3),
        amount=Decimal("8400"),
        raw_narration="Indigo",
        normalized_narration="INDIGO",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
        suggested_category="Expenses:Travel",
    )
    from backend.app.service.ingest.pipeline.entry_writer import candidate_to_entry

    entry = candidate_to_entry(entry_cand)._replace(
        tags=frozenset({"travel/goa", "travel/flight"})
    )
    app.state.ledger.append([entry])
    j = TestClient(app).get("/api/reports/expense/tags").json()
    assert set(j) == {"kind", "start", "end", "by_tag", "groups"}
    assert (j["kind"], j["start"], j["end"]) == ("expense", None, None)
    assert {t["tag"] for t in j["by_tag"]} == {"travel/goa", "travel/flight"}
    assert len(j["groups"]) == 1
    g = j["groups"][0]
    assert (g["prefix"], g["amount"], g["count"]) == ("travel", "8400", 1)
    assert [t["tag"] for t in g["tags"]] == [t["tag"] for t in j["by_tag"]]
    assert TestClient(create_app(storage_dir=tmp_path / "s2")).get(
        "/api/reports/expense/tags"
    ).json() == {
        "kind": "expense",
        "start": None,
        "end": None,
        "by_tag": [],
        "groups": [],
    }


def _route_response_ref(app, path: str) -> str | None:
    """The component name a GET route's 200 response is documented as."""
    schema = app.openapi()["paths"][path]["get"]["responses"]["200"]
    node = schema["content"]["application/json"]["schema"]
    return node.get("$ref", "").rsplit("/", 1)[-1] or None


def test_liabilities_balance_route_shape(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    led = app.state.ledger
    person, gone = "Liabilities:People:Asha", "Liabilities:Cash:Gone"
    led.open_named_accounts([person, gone, "Assets:Checking:Bank"])
    led.append(
        [
            make_txn(date(2026, 4, 1), "Lent", person, "Assets:Checking:Bank", "10000"),
            make_txn(date(2026, 4, 2), "Out", gone, "Assets:Checking:Bank", "500"),
            make_txn(date(2026, 4, 3), "Back", "Assets:Checking:Bank", gone, "500"),
        ]
    )
    led.close_account(gone, date(2026, 4, 30))

    r = TestClient(app).get("/api/reports/liabilities/balance")

    assert r.status_code == 200
    rows = {row["account"]: row for row in r.json()["balances"]}
    assert gone not in rows
    assert set(rows[person]) == {
        "account",
        "drawn",
        "repaid",
        "balance",
        "interest",
        "apr",
        "apr_source",
        "direction",
        "amount",
    }
    assert (rows[person]["direction"], rows[person]["amount"]) == (
        "owed_to_me",
        "10000",
    )
    assert rows[person]["balance"] == "-10000"
    assert rows[person]["apr_source"] is None
    assert (
        _route_response_ref(app, "/api/reports/liabilities/balance")
        == "LiabilitiesBalanceResponse"
    )


def test_credit_cards_route_shape(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 11, 1))
    card = "Liabilities:CreditCard:Acme1234"
    led = app.state.ledger
    led.open_named_accounts([card, "Expenses:Shopping"])
    led.set_open_meta(
        card,
        {"statement-day": 15, "due-day": 3, "last4": "1234", "expiry": "2026-12"},
    )
    txn = make_txn(date(2026, 10, 20), "Shop", "Expenses:Shopping", card, "2299")
    txn.meta["txn-id"] = "t-shop"
    led.append([txn])

    r = TestClient(app).get("/api/reports/liabilities/credit-cards")

    assert r.status_code == 200
    assert r.json()["cards"] == [
        {
            "account": card,
            "outstanding": "2299.00",
            "last_activity_date": "2026-10-20",
            "last4": "1234",
            "statement_day": 15,
            "due_day": 3,
            "properties_source": "account",
            "last_statement_date": "2026-10-15",
            "next_statement_date": "2026-11-15",
            "current_due_date": "2026-11-03",
            "next_due_date": "2026-12-03",
            "statement_expected": False,
            "expiry": "2026-12",
            "expiry_status": "expiring_soon",
            "in_transit": None,
            "net_owed": "2299.00",
        }
    ]
    assert (
        _route_response_ref(app, "/api/reports/liabilities/credit-cards")
        == "CreditCardSummaryResponse"
    )


def test_credit_cards_route_reports_stored_statement_day(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2027, 2, 10))
    card = "Liabilities:CreditCard:Zeta"
    led = app.state.ledger
    led.open_named_accounts([card, "Expenses:Shopping"])
    txn = make_txn(date(2027, 1, 20), "Shop", "Expenses:Shopping", card, "500")
    txn.meta["txn-id"] = "t-zeta"
    led.append([txn])
    client = TestClient(app)

    before = client.get("/api/reports/liabilities/credit-cards").json()["cards"]
    tile = next(c for c in before if c["account"] == card)
    assert (tile["statement_day"], tile["next_statement_date"]) == (None, None)

    put = client.put(
        "/api/accounts/card-properties",
        json={"account": card, "statement_day": 31, "due_day": 20, "last4": None},
    )
    assert put.status_code == 200

    cards = client.get("/api/reports/liabilities/credit-cards").json()["cards"]
    tile = next(c for c in cards if c["account"] == card)
    assert tile["properties_source"] == "account"
    assert tile["last_statement_date"] == "2027-01-31"
    assert tile["next_statement_date"] == "2027-02-28"
    assert tile["current_due_date"] == "2027-02-20"
    assert tile["next_due_date"] == "2027-03-20"
    # The latest line (20 Jan) is more than 7 days before the 31 Jan statement.
    assert tile["statement_expected"] is True


def test_credit_cards_route_omits_closed_cards(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 11, 1))
    card = "Liabilities:CreditCard:Zeta"
    led = app.state.ledger
    led.open_named_accounts([card, "Expenses:Shopping"])
    buy = make_txn(date(2026, 10, 1), "Shop", "Expenses:Shopping", card, "500")
    buy.meta["txn-id"] = "t-buy"
    pay = make_txn(date(2026, 10, 2), "Pay", card, "Assets:Checking:Bank", "500")
    pay.meta["txn-id"] = "t-pay"
    led.open_named_accounts(["Assets:Checking:Bank"])
    led.append([buy, pay])
    led.close_account(card, date(2026, 10, 3))

    cards = TestClient(app).get("/api/reports/liabilities/credit-cards").json()["cards"]
    assert card not in [c["account"] for c in cards]


def test_balance_rows_apr_from_inferred_terms_via_route(tmp_path):
    """A plan account's APR comes from its inferred terms (synthetic plan)."""
    from backend.app.service.reports.liabilities import amortize

    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 11, 1))
    led = app.state.ledger
    plan = "Liabilities:Loan:CardEMI:Acme1234-20260722"
    card = "Liabilities:CreditCard:Acme1234"
    led.open_named_accounts([plan, card])
    sched = amortize.schedule(Decimal(12000), Decimal(15), 12)
    txns = [make_txn(date(2026, 7, 22), "Conversion", card, plan, "12000")]
    for m in range(3):
        principal = str(sched[m]["principal"])
        txns.append(make_txn(date(2026, 8 + m, 21), "EMI", plan, card, principal))
    led.append(txns)

    client = TestClient(app)
    rows = {
        r["account"]: r
        for r in client.get("/api/reports/liabilities/balance").json()["balances"]
    }
    assert rows[plan]["apr_source"] == "inferred"
    assert abs(Decimal(rows[plan]["apr"]) - 15) <= Decimal("0.25")
    assert rows[card]["apr"] is None and rows[card]["apr_source"] is None

    (loan,) = client.get("/api/reports/liabilities/repayment").json()["loans"]
    assert (loan["terms_source"], loan["term_months"]) == ("inferred", 12)


def test_equal_instalment_home_loan_gets_no_inferred_terms_via_route(tmp_path):
    """Only card EMI plan accounts are inferred: a home loan whose full EMI is
    booked to it in equal amounts must not read as a no-cost 0% plan."""
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 5, 1))
    led = app.state.ledger
    loan = "Liabilities:Loan:HomeLoan"
    bank = "Assets:Checking:Bank"
    led.open_named_accounts([loan, bank])
    txns = [make_txn(date(2026, 1, 5), "Disbursal", bank, loan, "120000")]
    for m in range(3):
        txns.append(make_txn(date(2026, 2 + m, 5), "EMI", loan, bank, "10000"))
    led.append(txns)

    client = TestClient(app)
    balances = client.get("/api/reports/liabilities/balance").json()["balances"]
    row = next(r for r in balances if r["account"] == loan)
    assert (row["apr"], row["apr_source"]) == (None, None)
    (repay,) = client.get("/api/reports/liabilities/repayment").json()["loans"]
    assert repay["account"] == loan
    assert (repay["terms_source"], repay["terms_set"]) == (None, False)


def test_networth_route_returns_series_and_current(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    commit_candidates(
        app.state.ledger,
        app.state.investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 1, 10),
                amount=Decimal("100000"),
                raw_narration="OPEN",
                normalized_narration="OPEN",
                source_account="Equity:Opening-Balances",
                direction=Direction.DEBIT,
                suggested_other_leg="Assets:Checking:HDFC",
            ),
            CandidateTxn(
                date=date(2026, 2, 5),
                amount=Decimal("20000"),
                raw_narration="RENT",
                normalized_narration="RENT",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Rent",
            ),
        ],
    )
    client = TestClient(app)

    r = client.get("/api/reports/networth")
    assert r.status_code == 200
    j = r.json()
    assert j["current"]["by_group"] == [{"group": "Checking", "amount": "80000.00"}]
    assert j["current"]["as_of"] == "2026-02-05"
    assert Decimal(j["current"]["net"]) == Decimal(j["series"][-1]["net"])
    assert j["series"][-1]["month"] == "2026-02"

    dash = client.get("/api/reports/dashboard?year=2026&month=2").json()
    assert Decimal(j["current"]["net"]) == Decimal(dash["cash_net_worth"])


def test_networth_route_empty_ledger(tmp_path):
    client = TestClient(create_app(storage_dir=tmp_path / "storage"))
    r = client.get("/api/reports/networth")
    assert r.status_code == 200
    assert r.json() == {
        "series": [],
        "current": {
            "as_of": None,
            "assets": "0.00",
            "liabilities": "0.00",
            "net": "0.00",
            "by_group": [],
            "excluded": [],
            "excluded_total": "0.00",
        },
    }


def test_credit_cards_route_passes_the_expiry_soon_setting(tmp_path, monkeypatch):
    from backend.app.core.settings import Settings
    from backend.app.server.routes import reports as reports_routes

    # 2026-12 ends 60 days after 1 Nov: "expiring_soon" at the default 60,
    # "ok" once the setting says 30.
    monkeypatch.setattr(
        reports_routes, "get_settings", lambda: Settings(card_expiry_soon_days=30)
    )
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 11, 1))
    card = "Liabilities:CreditCard:Zeta"
    led = app.state.ledger
    led.open_named_accounts([card, "Expenses:Shopping"])
    led.set_open_meta(card, {"expiry": "2026-12"})
    txn = make_txn(date(2026, 10, 20), "Shop", "Expenses:Shopping", card, "10")
    txn.meta["txn-id"] = "t-zeta"
    led.append([txn])

    cards = TestClient(app).get("/api/reports/liabilities/credit-cards").json()["cards"]
    tile = next(c for c in cards if c["account"] == card)
    assert (tile["expiry"], tile["expiry_status"]) == ("2026-12", "ok")
