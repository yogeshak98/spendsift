from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.server.main import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(storage_dir=tmp_path))


def test_post_accounts_buckets(tmp_path):
    c = _client(tmp_path)
    existing = c.get("/api/accounts").json()["accounts"][0]
    resp = c.post(
        "/api/accounts",
        json={"accounts": [existing, "Assets:Checking:Axis", "bogus name"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["opened"] == ["Assets:Checking:Axis"]
    assert body["skipped_existing"] == [existing]
    assert body["rejected"] == [
        {"name": "bogus name", "reason": "INVALID_ACCOUNT_NAME"}
    ]
    assert "Assets:Checking:Axis" in c.get("/api/accounts").json()["accounts"]


def test_rename_happy(tmp_path):
    c = _client(tmp_path)
    c.post("/api/accounts", json={"accounts": ["Assets:Checking:HDFC"]})
    resp = c.post(
        "/api/accounts/rename",
        json={
            "account": "Assets:Checking:HDFC",
            "new_name": "Assets:Checking:HDFC-Salary",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["to"] == "Assets:Checking:HDFC-Salary"


def test_rename_updates_learned_rules_and_reports_counts(tmp_path):
    app = create_app(storage_dir=tmp_path)
    c = TestClient(app)
    c.post("/api/accounts", json={"accounts": ["Expenses:Synth"]})
    rules = app.state.categorizer.rules
    rules.add("SYNTHMART", "Expenses:Synth")
    resp = c.post(
        "/api/accounts/rename",
        json={"account": "Expenses:Synth", "new_name": "Expenses:SynthShop"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rules_updated"] == 1
    assert body["references_updated"] == {
        "templates": 0,
        "pdf_templates": 0,
        "loan_terms": 0,
        "merge_booking": 0,
        "budget": 0,
        "aliases": 1,
        "review_batches": 0,
    }
    # the app's categorizer (used by the next import) sees the new name
    assert app.state.categorizer.categorize("SYNTHMART 42")[0] == "Expenses:SynthShop"


def test_rename_breaking_a_pdf_template_409_e1094(tmp_path):
    from backend.app.persistence.templates import PdfTemplateStore
    from tests.service.ingest.pdf_reference import hdfc_template

    c = _client(tmp_path)
    card = "Liabilities:CreditCard:HDFC"
    c.post("/api/accounts", json={"accounts": [card]})
    PdfTemplateStore(tmp_path / "templates" / "pdf").save(hdfc_template())
    resp = c.post(
        "/api/accounts/rename",
        json={"account": card, "new_name": "Liabilities:Loan:HDFC"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "E1094"
    assert "PDF import template for HDFC" in body["description"]
    assert "must be a card account under Liabilities:CreditCard:" in body["description"]
    assert c.get("/api/accounts").json()["accounts"].count(card) == 1


LEDGER_FOOD = """\
2024/01/05 Synth lunch
    Expenses:Synth-Food    100.00 INR
    Assets:Checking:Synth  -100.00 INR
"""


def test_rename_blocked_by_ledger_preview_until_discarded(tmp_path):
    c = _client(tmp_path)
    c.post(
        "/api/accounts",
        json={"accounts": ["Expenses:Synth-Food", "Assets:Checking:Synth"]},
    )
    preview = c.post(
        "/api/import/ledger/preview",
        files={"file": ("synth.ledger", LEDGER_FOOD.encode(), "text/plain")},
    ).json()
    rename = {"account": "Expenses:Synth-Food", "new_name": "Expenses:Synth-Meals"}

    blocked = c.post("/api/accounts/rename", json=rename)
    assert blocked.status_code == 409
    body = blocked.json()
    assert body["code"] == "E1095"
    assert body["data"]["batches"] == [
        {"batch_id": preview["batch_id"], "filename": "synth.ledger"}
    ]
    assert "synth.ledger" in body["description"]
    # tells the user where the Discard button is
    assert "Open Import" in body["resolution"]
    assert "press Discard" in body["resolution"]

    discard = c.delete(f"/api/import/ledger/{preview['batch_id']}")
    assert discard.status_code == 200
    assert discard.json() == {"discarded": preview["batch_id"]}
    again = c.delete(f"/api/import/ledger/{preview['batch_id']}")
    assert again.status_code == 404
    assert again.json()["code"] == "E1006"

    assert c.post("/api/accounts/rename", json=rename).status_code == 200


def test_rename_invalid_name_422(tmp_path):
    c = _client(tmp_path)
    c.post("/api/accounts", json={"accounts": ["Assets:Checking:HDFC"]})
    resp = c.post(
        "/api/accounts/rename",
        json={"account": "Assets:Checking:HDFC", "new_name": "bad name"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "E1017"


def test_rename_missing_404(tmp_path):
    c = _client(tmp_path)
    resp = c.post(
        "/api/accounts/rename",
        json={"account": "Assets:Checking:Nope", "new_name": "Assets:Checking:X"},
    )
    assert resp.status_code == 404


def test_rename_collision_409(tmp_path):
    c = _client(tmp_path)
    c.post(
        "/api/accounts",
        json={"accounts": ["Assets:Checking:HDFC", "Assets:Checking:Axis"]},
    )
    resp = c.post(
        "/api/accounts/rename",
        json={"account": "Assets:Checking:HDFC", "new_name": "Assets:Checking:Axis"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "E1018"


def test_close_settled_200_and_missing_404(tmp_path):
    c = _client(tmp_path)
    c.post("/api/accounts", json={"accounts": ["Assets:Checking:Old"]})
    ok = c.post("/api/accounts/close", json={"account": "Assets:Checking:Old"})
    assert ok.status_code == 200
    assert ok.json()["closed"] == "Assets:Checking:Old"
    missing = c.post("/api/accounts/close", json={"account": "Assets:Checking:Nope"})
    assert missing.status_code == 404


def _save_template(c) -> str:
    from backend.app.models.storage.template import Template

    t = Template(
        fingerprint="fp1",
        institution="HDFC",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        default_account="Assets:Checking:HDFC",
    )
    r = c.put("/api/templates/fp1", json=t.model_dump())
    assert r.status_code == 200
    return "fp1"


def test_template_crud(tmp_path):
    c = _client(tmp_path)
    fp = _save_template(c)
    assert c.get("/api/templates").json()["templates"][0]["fingerprint"] == fp
    assert c.get(f"/api/templates/{fp}").json()["institution"] == "HDFC"
    assert c.get("/api/templates/nope").status_code == 404
    assert c.delete(f"/api/templates/{fp}").status_code == 204
    assert c.get("/api/templates").json()["templates"] == []
    assert c.delete(f"/api/templates/{fp}").status_code == 404


def test_template_put_fingerprint_mismatch_422(tmp_path):
    from backend.app.models.storage.template import Template

    c = _client(tmp_path)
    t = Template(
        fingerprint="other",
        institution="HDFC",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
    )
    r = c.put("/api/templates/fp1", json=t.model_dump())
    assert r.status_code == 422


def test_card_properties_routes_follow_contract(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    card = "Liabilities:CreditCard:Acme1234"
    app.state.ledger.open_named_accounts([card, "Assets:Checking:Bank"])
    client = TestClient(app)

    put = client.put(
        "/api/accounts/card-properties",
        json={"account": card, "statement_day": 15, "due_day": 3, "last4": "1234"},
    )
    assert put.status_code == 200
    assert put.json() == {
        "account": card,
        "statement_day": 15,
        "due_day": 3,
        "last4": "1234",
        "expiry": None,
        "suggested": {
            "statement_day": None,
            "due_day": None,
            "last4": "1234",
            "statement_count": 0,
            "last4_source": "account_name",
        },
    }
    # The app bootstraps with its seed card accounts too; only ours is checked.
    cards = client.get("/api/accounts/card-properties").json()["cards"]
    assert [c for c in cards if c["account"] == card] == [put.json()]

    bad = client.put(
        "/api/accounts/card-properties",
        json={"account": card, "statement_day": 32, "due_day": 3, "last4": "1234"},
    )
    assert (bad.status_code, bad.json()["code"]) == (422, "E1082")
    assert bad.json()["data"]["field"] == "statement_day"

    other = client.put(
        "/api/accounts/card-properties",
        json={
            "account": "Assets:Checking:Bank",
            "statement_day": 1,
            "due_day": 2,
            "last4": None,
        },
    )
    assert (other.status_code, other.json()["code"]) == (422, "E1083")

    extra = client.put(
        "/api/accounts/card-properties",
        json={"account": card, "statement_day": 1, "due_day": 2, "last4": None, "x": 1},
    )
    assert extra.status_code == 422

    missing = client.put(
        "/api/accounts/card-properties",
        json={
            "account": "Liabilities:CreditCard:Nope",
            "statement_day": 1,
            "due_day": 2,
            "last4": None,
        },
    )
    assert (missing.status_code, missing.json()["code"]) == (404, "E1039")


def test_card_properties_get_put_roundtrip(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    card = "Liabilities:CreditCard:Zeta"
    ledger = app.state.ledger
    ledger.open_named_accounts([card])
    client = TestClient(app)
    snaps_before = (
        len(list(ledger.snapshots_dir.iterdir()))
        if ledger.snapshots_dir.exists()
        else 0
    )

    put = client.put(
        "/api/accounts/card-properties",
        json={"account": card, "statement_day": 15, "due_day": None, "last4": "4321"},
    )

    assert put.status_code == 200
    assert put.json()["statement_day"] == 15
    assert put.json()["suggested"]["last4"] is None
    cards = client.get("/api/accounts/card-properties").json()["cards"]
    assert [c for c in cards if c["account"] == card] == [put.json()]
    text = ledger.main_file.read_text(encoding="utf-8")
    assert '  statement-day: 15\n  last4: "4321"\n' in text
    assert len(list(ledger.snapshots_dir.iterdir())) == snaps_before + 1


def test_card_properties_put_unknown_account_404(tmp_path):
    resp = _client(tmp_path).put(
        "/api/accounts/card-properties",
        json={
            "account": "Liabilities:CreditCard:Nope",
            "statement_day": None,
            "due_day": None,
            "last4": None,
        },
    )
    assert (resp.status_code, resp.json()["code"]) == (404, "E1039")


def test_card_properties_put_extra_key_422(tmp_path):
    resp = _client(tmp_path).put(
        "/api/accounts/card-properties",
        json={
            "account": "Liabilities:CreditCard:Nope",
            "statement_day": None,
            "due_day": None,
            "last4": None,
            "label": "x",
        },
    )
    assert resp.status_code == 422


# --- expiry on the single PUT ---------------------------------------------------


def test_card_properties_put_expiry_is_optional_and_kept_when_omitted(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    card = "Liabilities:CreditCard:Zeta"
    app.state.ledger.open_named_accounts([card])
    client = TestClient(app)
    body = {"account": card, "statement_day": 15, "due_day": 3, "last4": None}

    first = client.put(
        "/api/accounts/card-properties", json={**body, "expiry": "2029-08"}
    )
    assert (first.status_code, first.json()["expiry"]) == (200, "2029-08")
    kept = client.put("/api/accounts/card-properties", json={**body, "due_day": 4})
    assert (kept.json()["due_day"], kept.json()["expiry"]) == (4, "2029-08")
    cleared = client.put("/api/accounts/card-properties", json={**body, "expiry": None})
    assert cleared.json()["expiry"] is None

    for bad in ("2029-13", "abc"):
        resp = client.put("/api/accounts/card-properties", json={**body, "expiry": bad})
        assert (resp.status_code, resp.json()["code"]) == (422, "E1082")
        assert resp.json()["data"] == {
            "field": "expiry",
            "value": bad,
            "reason": "must be a month written YYYY-MM, with a month from 01 to 12",
        }


# --- PUT /api/accounts/card-properties/batch -------------------------------------

BATCH = "/api/accounts/card-properties/batch"
ACME = "Liabilities:CreditCard:Acme1234"
ZETA = "Liabilities:CreditCard:Zeta"
NOVA = "Liabilities:CreditCard:Nova"


def _card(account, statement_day=None, due_day=None, last4=None, expiry=None):
    return {
        "account": account,
        "statement_day": statement_day,
        "due_day": due_day,
        "last4": last4,
        "expiry": expiry,
    }


def _batch_app(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    app.state.ledger.open_named_accounts([ACME, ZETA, NOVA, "Assets:Checking:Bank"])
    return app


def _snapshot_count(ledger) -> int:
    snaps = ledger.snapshots_dir
    return len(list(snaps.iterdir())) if snaps.exists() else 0


def test_card_properties_batch_saves_three_cards_in_one_write(tmp_path):
    app = _batch_app(tmp_path)
    ledger = app.state.ledger
    client = TestClient(app)
    before = _snapshot_count(ledger)

    resp = client.put(
        BATCH,
        json={
            "cards": [
                _card(ACME, 15, 3, "1234", "2029-08"),
                _card(ZETA, 20, 8),
                _card(NOVA, last4="9999"),
            ]
        },
    )

    assert resp.status_code == 200
    assert _snapshot_count(ledger) == before + 1
    # The whole list after the write: the GET's shape and content.
    assert resp.json() == client.get("/api/accounts/card-properties").json()
    cards = {c["account"]: c for c in resp.json()["cards"]}
    assert (cards[ACME]["statement_day"], cards[ACME]["expiry"]) == (15, "2029-08")
    assert (cards[ZETA]["statement_day"], cards[ZETA]["due_day"]) == (20, 8)
    assert cards[NOVA]["last4"] == "9999"
    text = ledger.main_file.read_text(encoding="utf-8")
    assert '  expiry: "2029-08"\n' in text


def test_card_properties_batch_one_invalid_card_writes_nothing(tmp_path):
    app = _batch_app(tmp_path)
    ledger = app.state.ledger
    before, text = _snapshot_count(ledger), ledger.main_file.read_bytes()

    resp = TestClient(app).put(
        BATCH, json={"cards": [_card(ACME, 15, 3), _card(ZETA, 20, 32)]}
    )

    assert (resp.status_code, resp.json()["code"]) == (422, "E1082")
    assert resp.json()["data"] == {
        "account": ZETA,
        "field": "due_day",
        "value": "32",
        "reason": "must be a whole number from 1 to 31",
    }
    assert _snapshot_count(ledger) == before
    assert ledger.main_file.read_bytes() == text


def test_card_properties_batch_all_unchanged_takes_no_snapshot(tmp_path):
    app = _batch_app(tmp_path)
    ledger = app.state.ledger
    client = TestClient(app)
    body = {"cards": [_card(ACME, 15, 3), _card(ZETA, 20, expiry="2030-01")]}
    assert client.put(BATCH, json=body).status_code == 200
    before = _snapshot_count(ledger)

    again = client.put(BATCH, json=body)

    assert again.status_code == 200
    assert _snapshot_count(ledger) == before


def test_card_properties_batch_errors_name_the_card(tmp_path):
    client = TestClient(_batch_app(tmp_path))
    nope = "Liabilities:CreditCard:Nope"

    missing = client.put(BATCH, json={"cards": [_card(ACME, 1), _card(nope, 1)]})
    assert (missing.status_code, missing.json()["code"]) == (404, "E1039")
    assert missing.json()["data"] == {"account": nope}

    wrong = client.put(
        BATCH, json={"cards": [_card(ACME, 1), _card("Assets:Checking:Bank", 1)]}
    )
    assert (wrong.status_code, wrong.json()["code"]) == (422, "E1083")
    assert wrong.json()["data"]["account"] == "Assets:Checking:Bank"

    expiry = client.put(BATCH, json={"cards": [_card(ZETA, expiry="2029-13")]})
    assert (expiry.status_code, expiry.json()["code"]) == (422, "E1082")
    assert (expiry.json()["data"]["account"], expiry.json()["data"]["field"]) == (
        ZETA,
        "expiry",
    )

    twice = client.put(BATCH, json={"cards": [_card(ACME, 1), _card(ACME, 2)]})
    assert (twice.status_code, twice.json()["code"]) == (422, "E1082")
    assert twice.json()["data"]["field"] == "account"


def test_card_properties_batch_requires_every_key(tmp_path):
    client = TestClient(_batch_app(tmp_path))
    no_expiry = {k: v for k, v in _card(ACME, 1).items() if k != "expiry"}
    assert client.put(BATCH, json={"cards": [no_expiry]}).status_code == 422
    extra = {**_card(ACME, 1), "label": "x"}
    assert client.put(BATCH, json={"cards": [extra]}).status_code == 422


def test_card_properties_batch_empty_is_a_no_op(tmp_path):
    app = _batch_app(tmp_path)
    before = _snapshot_count(app.state.ledger)
    resp = TestClient(app).put(BATCH, json={"cards": []})
    assert resp.status_code == 200
    assert [c["account"] for c in resp.json()["cards"]][:1]
    assert _snapshot_count(app.state.ledger) == before


def test_single_card_put_still_works_beside_the_batch(tmp_path):
    client = TestClient(_batch_app(tmp_path))
    put = client.put(
        "/api/accounts/card-properties",
        json={"account": ACME, "statement_day": 9, "due_day": None, "last4": None},
    )
    assert (put.status_code, put.json()["statement_day"]) == (200, 9)
