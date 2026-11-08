"""Route-level checks of the card EMI and loan-terms contract."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from tests.conftest import make_txn

PARENT = "Liabilities:Loan:CardEMI"
CARD = "Liabilities:CreditCard:Acme1234"
PLAN = f"{PARENT}:Acme1234-20260722"


def _txn(d, narration, debit, credit, amount, txn_id):
    txn = make_txn(d, narration, debit, credit, amount)
    txn.meta["txn-id"] = txn_id
    return txn


def _app(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 11, 1))
    led = app.state.ledger
    led.open_named_accounts([PARENT, CARD, "Expenses:Shopping"])
    led.append(
        [
            # Conversion: the card is credited, the EMI account debited (drawn).
            _txn(date(2026, 7, 22), "EMI CONVERSION", CARD, PARENT, "1200", "t1"),
            _txn(
                date(2026, 8, 21),
                "OFFUS EMI,PRIN NB:01,0000555000111",
                PARENT,
                CARD,
                "600",
                "t2",
            ),
            _txn(
                date(2026, 9, 21),
                "OFFUS EMI,PRIN NB:02,0000555000111",
                PARENT,
                CARD,
                "600",
                "t3",
            ),
        ]
    )
    return app


def test_split_closes_fully_repaid_plan_in_one_write(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)

    proposal = client.get("/api/liabilities/emi-plans/proposal")
    assert proposal.status_code == 200
    body = proposal.json()
    assert body["parent"] == PARENT
    assert body["unassigned"] == []
    (plan,) = body["plans"]
    assert (plan["account"], plan["fully_repaid"]) == (PLAN, True)

    snaps = len(list(app.state.ledger.snapshots_dir.iterdir()))
    split = client.post(
        "/api/liabilities/emi-plans/split",
        json={
            "plans": [
                {
                    "account": PLAN,
                    "card": CARD,
                    "ref": plan["ref"],
                    "label": "Test plan",
                    "txn_ids": ["t1", "t2", "t3"],
                    "close": True,
                }
            ]
        },
    )
    assert split.status_code == 200
    assert split.json() == {
        "moved": 3,
        "opened": [PLAN],
        "plans": [PLAN],
        "closed": [PLAN],
    }
    assert len(list(app.state.ledger.snapshots_dir.iterdir())) == snaps + 1

    repayment = client.get("/api/reports/liabilities/repayment").json()
    assert repayment["emi_pool"] == {
        "account": PARENT,
        "unassigned_count": 0,
        "unassigned_amount": "0",
    }


def test_split_stale_and_invalid_are_coded(tmp_path):
    client = TestClient(_app(tmp_path))
    base = {"account": PLAN, "card": CARD, "ref": None, "label": None}

    stale = client.post(
        "/api/liabilities/emi-plans/split",
        json={"plans": [{**base, "txn_ids": ["t1", "nope"]}]},
    )
    assert (stale.status_code, stale.json()["code"]) == (409, "E1085")
    assert stale.json()["data"] == {"txn_id": "nope"}

    grandchild = client.post(
        "/api/liabilities/emi-plans/split",
        json={"plans": [{**base, "account": f"{PLAN}:X", "txn_ids": ["t1"]}]},
    )
    assert (grandchild.status_code, grandchild.json()["code"]) == (422, "E1086")

    not_repaid = client.post(
        "/api/liabilities/emi-plans/split",
        json={"plans": [{**base, "txn_ids": ["t1", "t2"], "close": True}]},
    )
    assert (not_repaid.status_code, not_repaid.json()["code"]) == (422, "E1086")


def test_loan_terms_put_follows_contract(tmp_path):
    app = _app(tmp_path)
    loan = "Liabilities:Loan:Car"
    app.state.ledger.open_named_accounts([loan])
    client = TestClient(app)
    body = {
        "account": loan,
        "annual_rate_pct": "15.5",
        "term_months": 12,
        "original_principal": None,
        "label": "Car",
    }

    r = client.put("/api/accounts/loan-terms", json=body)
    assert (r.status_code, r.json()) == (200, body)

    half = client.put("/api/accounts/loan-terms", json={**body, "term_months": None})
    assert (half.status_code, half.json()["code"]) == (422, "E1082")
    assert half.json()["data"]["field"] == "term_months"

    card = client.put("/api/accounts/loan-terms", json={**body, "account": CARD})
    assert (card.status_code, card.json()["code"]) == (422, "E1083")


def test_repayment_route_declares_contract_model(tmp_path):
    app = _app(tmp_path)
    loans = TestClient(app).get("/api/reports/liabilities/repayment").json()
    schema = app.openapi()["paths"]["/api/reports/liabilities/repayment"]["get"]
    node = schema["responses"]["200"]["content"]["application/json"]["schema"]
    assert node["$ref"].endswith("/RepaymentResponse")
    assert loans["emi_pool"] == {
        "account": PARENT,
        "unassigned_count": 3,
        "unassigned_amount": "0",
    }


def test_proposal_then_split_then_repayment_lists_plans(tmp_path):
    """End to end over HTTP on a tmp_path app: a running synthetic plan is
    proposed, split into its own account, and repaid with inferred terms."""
    from decimal import Decimal

    from backend.app.service.reports.liabilities import amortize

    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 11, 1))
    led = app.state.ledger
    led.open_named_accounts([PARENT, CARD, "Expenses:Home"])
    sched = amortize.schedule(Decimal(12000), Decimal(15), 12)
    txns = [
        _txn(date(2026, 7, 20), "ACME STORE", "Expenses:Home", CARD, "12000", "p1"),
        _txn(date(2026, 7, 22), "EMI CONVERSION", CARD, PARENT, "12000", "c1"),
    ]
    for m in range(3):
        txns.append(
            _txn(
                date(2026, 8 + m, 21),
                f"OFFUS EMI,PRIN NB:{m + 1:02d},0000555000111",
                PARENT,
                CARD,
                str(sched[m]["principal"]),
                f"i{m + 1}",
            )
        )
    led.append(txns)
    client = TestClient(app)

    before = client.get("/api/reports/liabilities/repayment").json()
    assert before["emi_pool"]["unassigned_count"] == 4
    (pool_loan,) = before["loans"]
    assert (pool_loan["account"], pool_loan["terms_set"]) == (PARENT, False)

    (plan,) = client.get("/api/liabilities/emi-plans/proposal").json()["plans"]
    assert plan["account"] == PLAN
    assert (plan["label_hint"], plan["confident"], plan["fully_repaid"]) == (
        "ACME STORE",
        True,
        False,
    )
    txn_ids = [plan["conversion"]["txn_id"]] + [
        i["txn_id"] for i in plan["instalments"]
    ]
    split = client.post(
        "/api/liabilities/emi-plans/split",
        json={
            "plans": [
                {
                    "account": PLAN,
                    "card": CARD,
                    "ref": plan["ref"],
                    "label": "Washer",
                    "txn_ids": txn_ids,
                }
            ]
        },
    )
    assert split.status_code == 200
    assert split.json()["moved"] == 4 and split.json()["closed"] == []

    after = client.get("/api/reports/liabilities/repayment").json()
    assert after["emi_pool"] == {
        "account": PARENT,
        "unassigned_count": 0,
        "unassigned_amount": "0",
    }
    (loan,) = after["loans"]
    assert (loan["account"], loan["label"], loan["card"]) == (PLAN, "Washer", CARD)
    assert (loan["terms_source"], loan["term_months"], loan["paid_instalments"]) == (
        "inferred",
        12,
        3,
    )
    assert loan["payoff_month"] == "2027-07"


def test_loan_terms_put_then_repayment_source_account(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    split = client.post(
        "/api/liabilities/emi-plans/split",
        json={
            "plans": [
                {
                    "account": PLAN,
                    "card": CARD,
                    "ref": None,
                    "label": None,
                    "txn_ids": ["t1", "t2"],
                }
            ]
        },
    )
    assert split.status_code == 200
    r = client.put(
        "/api/accounts/loan-terms",
        json={
            "account": PLAN,
            "annual_rate_pct": "14",
            "term_months": 2,
            "original_principal": "1200",
            "label": "Mine",
        },
    )
    assert r.status_code == 200
    loans = {
        loan["account"]: loan
        for loan in client.get("/api/reports/liabilities/repayment").json()["loans"]
    }
    assert loans[PLAN]["terms_source"] == "account"
    assert (loans[PLAN]["label"], loans[PLAN]["original_principal"]) == ("Mine", "1200")
    balance = {
        row["account"]: row
        for row in client.get("/api/reports/liabilities/balance").json()["balances"]
    }
    assert (balance[PLAN]["apr"], balance[PLAN]["apr_source"]) == ("14", "account")


def test_loan_terms_put_unknown_account_and_extra_key(tmp_path):
    client = TestClient(_app(tmp_path))
    body = {
        "account": "Liabilities:Loan:Nope",
        "annual_rate_pct": None,
        "term_months": None,
        "original_principal": None,
        "label": None,
    }
    r = client.put("/api/accounts/loan-terms", json=body)
    assert (r.status_code, r.json()["code"]) == (404, "E1039")
    r = client.put("/api/accounts/loan-terms", json={**body, "extra": 1})
    assert r.status_code == 422
