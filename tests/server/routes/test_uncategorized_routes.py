from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from tests.service.reports.conftest import _spend


def test_uncategorized_route_returns_merchant_rows(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _spend(
        app.state.ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9912@ybl-YESB-1111"
    )
    _spend(
        app.state.ledger, date(2026, 3, 9), "210.00", "UPI-SWIGGY-9912@ybl-YESB-2222"
    )

    client = TestClient(app)
    body = client.get("/api/reports/uncategorized").json()

    assert body["transactions"] == 2
    assert body["merchants"] == 1
    assert body["rows"][0]["count"] == 2


def test_uncategorized_route_is_empty_on_a_clean_journal(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)
    body = client.get("/api/reports/uncategorized").json()

    assert body == {"transactions": 0, "merchants": 0, "total": "0.00", "rows": []}


def test_classifying_a_merchant_fixes_history_and_future(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _spend(
        app.state.ledger,
        date(2026, 3, 5),
        "420.50",
        "UPI-SWIGGY-9912@ybl-YESB-1111",
        txn_id="t1",
    )
    _spend(
        app.state.ledger,
        date(2026, 3, 9),
        "210.00",
        "UPI-SWIGGY-9912@ybl-YESB-2222",
        txn_id="t2",
    )
    _spend(
        app.state.ledger,
        date(2026, 3, 12),
        "99.00",
        "UPI-SWIGGY-9912@ybl-YESB-3333",
        txn_id="t3",
    )

    client = TestClient(app)
    r = client.post(
        "/api/reports/uncategorized/classify",
        json={"merchant": "SWIGGY", "account": "Expenses:Food"},
    )
    assert r.status_code == 200
    assert r.json() == {"rules_added": 1, "postings_updated": 3}

    # the backlog is now empty ...
    assert client.get("/api/reports/uncategorized").json()["transactions"] == 0

    # ... and the rule persists, so the NEXT import is already categorized
    assert app.state.categorizer.categorize("SWIGGY")[0] == "Expenses:Food"


def test_classify_only_rewrites_the_named_merchant(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _spend(
        app.state.ledger,
        date(2026, 3, 5),
        "420.50",
        "UPI-SWIGGY-9912@ybl-YESB-1111",
        txn_id="t1",
    )
    _spend(
        app.state.ledger,
        date(2026, 3, 6),
        "150.00",
        "UPI-ZOMATO-4411@ybl-YESB-9999",
        txn_id="t2",
    )

    client = TestClient(app)
    r = client.post(
        "/api/reports/uncategorized/classify",
        json={"merchant": "SWIGGY", "account": "Expenses:Food"},
    )
    assert r.json()["postings_updated"] == 1

    body = client.get("/api/reports/uncategorized").json()
    assert body["transactions"] == 1
    assert body["rows"][0]["merchant"] == "ZOMATO"


def test_classify_rejects_a_malformed_target_account(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _spend(
        app.state.ledger,
        date(2026, 3, 5),
        "420.50",
        "UPI-SWIGGY-9912@ybl-YESB-1111",
        txn_id="t1",
    )

    client = TestClient(app)
    r = client.post(
        "/api/reports/uncategorized/classify",
        json={"merchant": "SWIGGY", "account": "not a valid account"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "E1017"

    # the backlog is untouched
    assert client.get("/api/reports/uncategorized").json()["transactions"] == 1


def test_classify_with_no_matching_rows_is_a_noop_but_still_adds_the_rule(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)
    r = client.post(
        "/api/reports/uncategorized/classify",
        json={"merchant": "SWIGGY", "account": "Expenses:Food"},
    )
    assert r.json() == {"rules_added": 1, "postings_updated": 0}


def test_classify_missing_merchant_is_a_422_not_a_500(tmp_path):
    # FastAPI's own request-validation 422 (from the typed ClassifyMerchantRequest
    # model), not the generic catch-all UNEXPECTED_ERROR/E0000 a bare KeyError on
    # payload["merchant"] used to surface as -- see error_handlers.py's comment
    # that request-validation 422s are deliberately left in FastAPI's own
    # {"detail": [...]} shape rather than the CodedError envelope.
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)
    r = client.post(
        "/api/reports/uncategorized/classify",
        json={"account": "Expenses:Food"},
    )
    assert r.status_code == 422
    body = r.json()
    assert "code" not in body  # not the E0000 catch-all envelope
    assert any(err["loc"][-1] == "merchant" for err in body["detail"])


def test_classify_missing_account_is_a_422_not_a_500(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)
    r = client.post(
        "/api/reports/uncategorized/classify",
        json={"merchant": "SWIGGY"},
    )
    assert r.status_code == 422
    body = r.json()
    assert "code" not in body
    assert any(err["loc"][-1] == "account" for err in body["detail"])


def test_classify_rejects_a_non_expense_target_account(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _spend(
        app.state.ledger,
        date(2026, 3, 5),
        "420.50",
        "UPI-SWIGGY-9912@ybl-YESB-1111",
        txn_id="t1",
    )

    client = TestClient(app)
    r = client.post(
        "/api/reports/uncategorized/classify",
        json={"merchant": "SWIGGY", "account": "Assets:Bank:HDFC"},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "E1048"

    # nothing moved and no rule was learned
    assert client.get("/api/reports/uncategorized").json()["transactions"] == 1
    assert app.state.categorizer.categorize("SWIGGY")[0] != "Assets:Bank:HDFC"


def test_classify_rejects_the_backlog_account_itself(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _spend(
        app.state.ledger,
        date(2026, 3, 5),
        "420.50",
        "UPI-SWIGGY-9912@ybl-YESB-1111",
        txn_id="t1",
    )

    client = TestClient(app)
    r = client.post(
        "/api/reports/uncategorized/classify",
        json={"merchant": "SWIGGY", "account": "Expenses:Uncategorized"},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "E1049"
    assert client.get("/api/reports/uncategorized").json()["transactions"] == 1


# ── Journal-wide export / bulk import ────────────────────────────────────────


def test_export_route_numbers_every_backlog_merchant(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _spend(app.state.ledger, date(2026, 3, 7), "240.00", "UPI-BLUEBIRD CAB-221")
    _spend(app.state.ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911")
    _spend(app.state.ledger, date(2026, 3, 6), "210.00", "UPI-SWIGGY-9912")

    client = TestClient(app)
    body = client.get("/api/reports/uncategorized/export").json()

    assert body["merchants"] == 2
    # count desc, then merchant asc -- the same order GET /uncategorized uses
    assert body["index"] == {"1": "SWIGGY", "2": "BLUEBIRD CAB"}
    assert "1\tSWIGGY" in body["text"]
    assert "2\tBLUEBIRD CAB" in body["text"]


def test_import_route_applies_the_whole_paste_in_one_call(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    for n, amount in enumerate(("420.50", "210.00", "99.00"), start=1):
        _spend(
            app.state.ledger,
            date(2026, 3, n),
            amount,
            f"UPI-SWIGGY-991{n}",
            txn_id=f"s{n}",
        )
    for n, amount in enumerate(("240.00", "260.00"), start=1):
        _spend(
            app.state.ledger,
            date(2026, 3, n + 10),
            amount,
            f"UPI-BLUEBIRD CAB-22{n}",
            txn_id=f"b{n}",
        )

    client = TestClient(app)
    export = client.get("/api/reports/uncategorized/export").json()
    r = client.post(
        "/api/reports/uncategorized/import",
        json={
            "text": "1\tExpenses:Food\n2\tExpenses:Travel\n",
            "index": export["index"],
        },
    )

    assert r.status_code == 200
    assert r.json() == {
        "rules_added": 2,
        "postings_updated": 5,
        "merchants_applied": 2,
        "ignored": 0,
        "rejected": [],
    }
    assert client.get("/api/reports/uncategorized").json()["transactions"] == 0
    # both rules persist, so the NEXT import of either is already categorized
    assert app.state.categorizer.categorize("UPI-SWIGGY-9999")[0] == "Expenses:Food"
    assert (
        app.state.categorizer.categorize("UPI-BLUEBIRD CAB-999")[0] == "Expenses:Travel"
    )


def test_import_route_reports_bad_lines_without_failing_the_paste(tmp_path):
    # One bad answer out of three must not 422 the whole paste: the point of
    # this route is that an LLM answering ~241 merchants gets a few wrong.
    app = create_app(storage_dir=tmp_path / "storage")
    # SWIGGY gets two rows so it sorts first (count desc) and the numbers in
    # the paste below are unambiguous.
    _spend(app.state.ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")
    _spend(app.state.ledger, date(2026, 3, 6), "210.00", "UPI-SWIGGY-9912", txn_id="s2")
    _spend(
        app.state.ledger,
        date(2026, 3, 7),
        "240.00",
        "UPI-BLUEBIRD CAB-221",
        txn_id="b1",
    )

    client = TestClient(app)
    export = client.get("/api/reports/uncategorized/export").json()
    assert export["index"] == {"1": "SWIGGY", "2": "BLUEBIRD CAB"}
    r = client.post(
        "/api/reports/uncategorized/import",
        json={
            "text": "1\tExpenses:Food\n2\tIncome:Salary\n7\tExpenses:Travel\n",
            "index": export["index"],
        },
    )

    assert r.status_code == 200
    body = r.json()
    # deliberately different numbers: 2 postings, 1 merchant, 1 rule
    assert body["rules_added"] == 1
    assert body["postings_updated"] == 2
    assert body["merchants_applied"] == 1
    assert len(body["rejected"]) == 2
    assert all(set(row) == {"line", "reason"} for row in body["rejected"])
    # the rejected merchant is still listed
    assert client.get("/api/reports/uncategorized").json()["rows"] == [
        {
            "merchant": "BLUEBIRD CAB",
            "count": 1,
            "total": "240.00",
            "first": "2026-03-07",
            "last": "2026-03-07",
            "sample": "UPI-BLUEBIRD CAB-221",
        }
    ]


def test_import_route_missing_text_is_a_422_not_a_500(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)
    r = client.post("/api/reports/uncategorized/import", json={"index": {}})
    assert r.status_code == 422
    body = r.json()
    assert "code" not in body  # FastAPI's own validation shape, not E0000
    assert any(err["loc"][-1] == "text" for err in body["detail"])


def test_import_route_missing_index_is_a_422_not_a_500(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)
    r = client.post("/api/reports/uncategorized/import", json={"text": "1\tX"})
    assert r.status_code == 422
    body = r.json()
    assert "code" not in body
    assert any(err["loc"][-1] == "index" for err in body["detail"])


def test_export_route_is_empty_on_a_clean_journal(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)
    body = client.get("/api/reports/uncategorized/export").json()
    assert body["merchants"] == 0
    assert body["index"] == {}


def test_import_route_maps_a_non_numeric_index_key_to_a_coded_422(tmp_path):
    # The index must be posted back exactly as exported. A non-numeric key
    # used to reach the int() coercion unclaimed and render as the generic
    # E0000 500 -- the same defect class as the dict-body 500 fixed in
    # 394a806. Same coded error the per-batch sibling route raises for the
    # identical coercion.
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)
    r = client.post(
        "/api/reports/uncategorized/import",
        json={"text": "1\tExpenses:Food", "index": {"abc": "SWIGGY"}},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "E1038"


def test_import_route_counts_a_misread_answer_line_as_ignored(tmp_path):
    # "1 Expenses:Food" (no tab) is unreadable, but it must not vanish: a
    # 241-merchant paste that was entirely misread has to be distinguishable
    # from one that succeeded.
    app = create_app(storage_dir=tmp_path / "storage")
    _spend(app.state.ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")
    client = TestClient(app)
    export = client.get("/api/reports/uncategorized/export").json()

    r = client.post(
        "/api/reports/uncategorized/import",
        json={"text": "1 Expenses:Food", "index": export["index"]},
    )
    assert r.status_code == 200
    assert r.json()["ignored"] == 1
    assert client.get("/api/reports/uncategorized").json()["transactions"] == 1

    # ... and the numbered-list form of the same answer now lands
    r = client.post(
        "/api/reports/uncategorized/import",
        json={"text": "1.\tExpenses:Food", "index": export["index"]},
    )
    assert r.json()["ignored"] == 0
    assert r.json()["postings_updated"] == 1


def test_export_route_lists_the_users_open_expense_accounts_in_the_prompt(tmp_path):
    # The prompt's suggested categories must be the journal's own, not a
    # shipped list of nine generic ones -- these totals feed the tax surfaces.
    app = create_app(storage_dir=tmp_path / "storage")
    app.state.ledger.open_named_accounts(["Expenses:Dining:Swiggy"])
    _spend(app.state.ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911")

    client = TestClient(app)
    text = client.get("/api/reports/uncategorized/export").json()["text"]

    assert "- Expenses:Dining:Swiggy" in text
    assert "- Expenses:Uncategorized" not in text
    assert "Income:" not in text


def test_export_route_with_a_deleted_prompt_file_is_a_coded_422_not_a_500(tmp_path):
    # The prompt is user-owned and hand-deletable; an unguarded read_text
    # surfaced as the generic E0000 500 with no remedy.
    app = create_app(storage_dir=tmp_path / "storage")
    (app.state.ledger.storage_dir / "prompts" / "categorize-merchants.md").unlink()

    client = TestClient(app)
    r = client.get("/api/reports/uncategorized/export")

    assert r.status_code == 422
    assert r.json()["code"] == "E1050"
