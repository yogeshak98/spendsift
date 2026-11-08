import io

import pytest
from fastapi.testclient import TestClient

from backend.app.server.main import create_app


def _client(tmp_path):
    return TestClient(create_app(storage_dir=tmp_path / "storage"))


def test_detect_returns_unmatched_with_raw_rows_for_unknown_format(tmp_path):
    client = _client(tmp_path)
    csv = (
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    )
    resp = client.post(
        "/api/import/detect",
        files={"file": ("hdfc.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is False
    assert body["suggested_header_row"] == 0
    assert body["suggested_roles"]["date"] == "Date"
    assert body["fingerprint"]
    assert body["filename"] == "hdfc.csv"
    assert len(body["raw_rows"]) == 2
    assert body["suggested_date_format"] == "%d/%m/%Y"


def test_detect_suggests_iso_date_format_when_that_is_what_the_file_uses(tmp_path):
    client = _client(tmp_path)
    csv = (
        "Date,Narration,Withdrawal Amt.,Deposit Amt.\n"
        "2026-03-05,SWIGGY BANGALORE,420.50,\n"
    )
    resp = client.post(
        "/api/import/detect",
        files={"file": ("hdfc.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    assert resp.json()["suggested_date_format"] == "%Y-%m-%d"


def test_create_template_saves_and_scans_immediately(tmp_path):
    client = _client(tmp_path)
    csv = (
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    )
    detected = client.post(
        "/api/import/detect",
        files={"file": ("hdfc.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    assert detected["matched"] is False

    resp = client.post(
        "/api/templates",
        json={
            "filename": detected["filename"],
            "fingerprint": detected["fingerprint"],
            "institution": "HDFC",
            "header_row_index": detected["suggested_header_row"],
            "delimiter": detected["delimiter"],
            "date_column": detected["suggested_roles"]["date"],
            "date_format": "%d/%m/%Y",
            "narration_column": detected["suggested_roles"]["narration"],
            "debit_column": detected["suggested_roles"]["debit"],
            "credit_column": detected["suggested_roles"]["credit"],
            "default_account": "Assets:Bank:HDFC",
            "save": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["saved"] is True
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["source_account"] == "Assets:Bank:HDFC"

    # Re-uploading the same format now auto-matches without re-creating a template.
    again = client.post(
        "/api/import/detect",
        files={"file": ("hdfc2.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    assert again["matched"] is True
    assert again["candidates"][0]["source_account"] == "Assets:Bank:HDFC"


def test_create_template_dry_run_does_not_save_or_open_a_batch(tmp_path):
    client = _client(tmp_path)
    csv = (
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    )
    detected = client.post(
        "/api/import/detect",
        files={"file": ("hdfc.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()

    resp = client.post(
        "/api/templates",
        json={
            "filename": detected["filename"],
            "fingerprint": detected["fingerprint"],
            "institution": "HDFC",
            "header_row_index": detected["suggested_header_row"],
            "delimiter": detected["delimiter"],
            "date_column": detected["suggested_roles"]["date"],
            "date_format": "%d/%m/%Y",
            "narration_column": detected["suggested_roles"]["narration"],
            "debit_column": detected["suggested_roles"]["debit"],
            "credit_column": detected["suggested_roles"]["credit"],
            "default_account": "Assets:Bank:HDFC",
            "save": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["saved"] is False
    assert "batch_id" not in body
    assert len(body["candidates"]) == 1

    # Previewing again with the same format still reports unmatched.
    again = client.post(
        "/api/import/detect",
        files={"file": ("hdfc2.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    assert again["matched"] is False


def test_resolve_marker_assigns_account_and_unblocks_commit(tmp_path):
    client = _client(tmp_path)
    csv = (
        '"Accountno:","1234567890"\n'
        '"Transaction Details:"\n'
        '"Date","Details","Amount","Sign"\n'
        '"4399XXXXXXXX0001"\n'
        '"17/03/2025","BBPS Payment received","8123.45","CR"\n'
    )
    detected = client.post(
        "/api/import/detect",
        files={"file": ("icici.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    assert detected["matched"] is False
    assert detected["suggested_header_row"] == 2

    created = client.post(
        "/api/templates",
        json={
            "filename": detected["filename"],
            "fingerprint": detected["fingerprint"],
            "institution": "ICICI",
            "header_row_index": 2,
            "delimiter": detected["delimiter"],
            "date_column": "Date",
            "date_format": "%d/%m/%Y",
            "narration_column": "Details",
            "amount_column": "Amount",
            "sign_column": "Sign",
            "account_marker_pattern": r"(\d{4})XXXXXXXX(\d{4})",
            "save": True,
        },
    ).json()
    batch_id = created["batch_id"]
    candidate = created["candidates"][0]
    assert candidate["needs_account"] is True
    marker_key = candidate["unresolved_marker_key"]
    assert marker_key

    blocked = client.post(f"/api/commit/{batch_id}")
    assert blocked.status_code == 422

    resolve = client.post(
        f"/api/templates/{detected['fingerprint']}/resolve-marker",
        json={
            "batch_id": batch_id,
            "marker_key": marker_key,
            "account": "Liabilities:CreditCard:ICICI",
        },
    )
    assert resolve.status_code == 200
    assert resolve.json()["updated"] == 1

    committed = client.post(f"/api/commit/{batch_id}")
    assert committed.status_code == 200
    assert committed.json()["committed"] == 1


def _create_template(client, csv_bytes, filename, **overrides):
    detected = client.post(
        "/api/import/detect",
        files={"file": (filename, io.BytesIO(csv_bytes), "text/csv")},
    ).json()
    assert detected["matched"] is False, detected
    payload = {
        "filename": detected["filename"],
        "fingerprint": detected["fingerprint"],
        "institution": "HDFC",
        "header_row_index": detected["suggested_header_row"],
        "delimiter": detected["delimiter"],
        "date_column": detected["suggested_roles"]["date"],
        "date_format": detected["suggested_date_format"],
        "narration_column": detected["suggested_roles"]["narration"],
        "debit_column": detected["suggested_roles"]["debit"],
        "credit_column": detected["suggested_roles"]["credit"],
        "save": True,
    }
    payload.update(overrides)
    return client.post("/api/templates", json=payload).json()


def test_preview_auto_categorizes_known_merchant(tmp_path):
    client = _client(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    created = _create_template(
        client, csv, "hdfc.csv", default_account="Assets:Bank:HDFC"
    )
    assert created["candidates"][0]["suggested_category"] == "Expenses:Food"


def test_export_then_import_categorizes_and_learns(tmp_path):
    client = _client(tmp_path)
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"06/04/2026,UPI/DR/412345678901/SUNRISE TIFFINS/YESB/sunrise.tiffins@ybl/180,"
        b"180.00,\n"
    )
    created = _create_template(
        client, csv, "hdfc.csv", default_account="Assets:Bank:HDFC"
    )
    batch_id = created["batch_id"]
    assert created["candidates"][0]["suggested_category"] is None  # unknown

    export = client.get(f"/api/categorize/export/{batch_id}").json()
    assert "SUNRISE.TIFFINS" in export["text"]

    number = list(export["index"].keys())[0]
    result_text = f"{number}\tExpenses:Food\n"
    resp = client.post(
        f"/api/categorize/import/{batch_id}",
        json={"text": result_text, "index": export["index"]},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 1

    review = client.get(f"/api/review/{batch_id}").json()
    assert review["candidates"][0]["suggested_category"] == "Expenses:Food"
    assert review["candidates"][0]["category_source"] == "user"

    # Learned at commit, not at import.
    rules_file = tmp_path / "storage" / "rules" / "learned.yaml"
    assert not rules_file.exists() or "SUNRISE TIFFINS" not in rules_file.read_text()
    committed = client.post(f"/api/commit/{batch_id}")
    assert committed.status_code == 200
    # A UPI row is learned under its payee identity, not its text.
    assert committed.json()["learned"] == [
        {
            "pattern": "UPI:SUNRISE TIFFINS|SUNRISE.TIFFINS@YBL",
            "category": "Expenses:Food",
            "replaces": None,
        }
    ]
    assert "SUNRISE TIFFINS" in rules_file.read_text()


def test_edit_with_unbalanced_splits_is_rejected(tmp_path):
    client = _client(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,CRED CARD BILL,8420.00,\n"
    created = _create_template(
        client, csv, "hdfc.csv", default_account="Assets:Bank:HDFC"
    )
    batch_id = created["batch_id"]
    cid = created["candidates"][0]["id"]

    resp = client.post(
        f"/api/review/{batch_id}/{cid}",
        json={
            "splits": [
                {"account": "Expenses:Food", "amount": "2100.00"},
                {"account": "Expenses:Fuel", "amount": "1520.00"},
            ]
        },
    )
    assert resp.status_code == 422


def test_edit_with_balanced_splits_then_commit_produces_multi_posting(tmp_path):
    client = _client(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,CRED CARD BILL,8420.00,\n"
    created = _create_template(
        client, csv, "hdfc.csv", default_account="Assets:Bank:HDFC"
    )
    batch_id = created["batch_id"]
    cid = created["candidates"][0]["id"]

    resp = client.post(
        f"/api/review/{batch_id}/{cid}",
        json={
            "splits": [
                {"account": "Expenses:Food", "amount": "2100.00"},
                {"account": "Expenses:Fuel", "amount": "6320.00"},
            ]
        },
    )
    assert resp.status_code == 200

    commit = client.post(f"/api/commit/{batch_id}")
    assert commit.status_code == 200
    assert commit.json()["committed"] == 1

    year_file_text = (tmp_path / "storage" / "journal" / "2026.beancount").read_text(
        encoding="utf-8"
    )
    assert "Expenses:Food" in year_file_text
    assert "Expenses:Fuel" in year_file_text


def test_preview_flags_a_payment_already_recorded_from_the_other_side(tmp_path):
    client = _client(tmp_path)

    # Card-side and bank-side fixtures use different narration-column headers
    # ("Narration" vs "Description") on purpose: a template's fingerprint is
    # the header row's text, with no account baked in, so two real accounts
    # that happened to export byte-identical headers would otherwise collide
    # on the same template/default_account. Differing headers here is what
    # real HDFC bank vs. card exports also do.
    card_csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"01/04/2026,PAYMENT RECEIVED - THANK YOU,,5000.00\n"
    )
    created1 = _create_template(
        client, card_csv, "card.csv", default_account="Liabilities:CreditCard:HDFC"
    )
    batch1 = created1["batch_id"]
    cid1 = created1["candidates"][0]["id"]
    assert created1["candidates"][0]["dedup_status"] == "transfer_match_candidate"
    # Booked straight to the bank (the model before the card clearing
    # account): the bank row, which now suggests the clearing account, must
    # still see this payment as already recorded.
    client.post(
        f"/api/review/{batch1}/{cid1}",
        json={"suggested_other_leg": "Assets:Bank:HDFC"},
    )
    commit1 = client.post(f"/api/commit/{batch1}")
    assert commit1.json()["committed"] == 1

    bank_csv = (
        b"Date,Description,Withdrawal Amt,Deposit Amt\n"
        b"02/04/2026,HDFC CARD PAYMENT,5000.00,\n"
    )
    detected2 = client.post(
        "/api/import/detect",
        files={"file": ("bank.csv", io.BytesIO(bank_csv), "text/csv")},
    ).json()
    assert detected2["matched"] is False
    created2 = client.post(
        "/api/templates",
        json={
            "filename": detected2["filename"],
            "fingerprint": detected2["fingerprint"],
            "institution": "HDFC",
            "header_row_index": detected2["suggested_header_row"],
            "delimiter": detected2["delimiter"],
            "date_column": detected2["suggested_roles"]["date"],
            "date_format": detected2["suggested_date_format"],
            "narration_column": detected2["suggested_roles"]["narration"],
            "debit_column": detected2["suggested_roles"]["debit"],
            "credit_column": detected2["suggested_roles"]["credit"],
            "default_account": "Assets:Bank:HDFC",
            "save": True,
        },
    ).json()
    batch2 = created2["batch_id"]
    candidate2 = created2["candidates"][0]
    assert candidate2["dedup_status"] == "transfer_already_recorded"
    assert candidate2["matched_txn_id"]
    # The flag names the entry it matched, so the user can check it.
    assert any(
        f.startswith("already recorded by transaction ")
        and "2026-04-01" in f
        and "PAYMENT RECEIVED - THANK YOU" in f
        for f in candidate2["flags"]
    ), candidate2["flags"]

    commit2 = client.post(f"/api/commit/{batch2}")
    assert commit2.json()["committed"] == 0


def test_both_sides_of_a_card_bill_payment_book_through_the_clearing_account(
    tmp_path,
):
    # Each side of the payment is its own entry against the clearing account,
    # so the second import is a transfer to book, not one already recorded.
    client = _client(tmp_path)
    card_csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"01/04/2026,PAYMENT RECEIVED - THANK YOU,,5000.00\n"
    )
    card = _create_template(
        client, card_csv, "card.csv", default_account="Liabilities:CreditCard:HDFC"
    )
    card_row = card["candidates"][0]
    assert card_row["dedup_status"] == "transfer_match_candidate"
    assert card_row["suggested_other_leg"] == "Assets:Transfer:CreditCard"
    assert client.post(f"/api/commit/{card['batch_id']}").json()["committed"] == 1

    bank_csv = (
        b"Date,Description,Withdrawal Amt,Deposit Amt\n"
        b"02/04/2026,HDFC CARD PAYMENT,5000.00,\n"
    )
    bank = _create_template(
        client, bank_csv, "bank.csv", default_account="Assets:Bank:HDFC"
    )
    bank_row = bank["candidates"][0]
    assert bank_row["dedup_status"] == "transfer_match_candidate"
    assert bank_row["suggested_other_leg"] == "Assets:Transfer:CreditCard"
    assert client.post(f"/api/commit/{bank['batch_id']}").json()["committed"] == 1


def test_a_second_card_payment_of_the_same_amount_is_not_swallowed(tmp_path):
    # Both card payments book bank -> clearing account. An earlier one must
    # not read as "already recorded" for a new one of the same amount a few
    # days later: they are two payments, not one seen from two sides.
    client = _client(tmp_path)
    first = _create_template(
        client,
        b"Date,Description,Withdrawal Amt,Deposit Amt\n"
        b"01/04/2026,HDFC CARD PAYMENT,5000.00,\n",
        "bank.csv",
        default_account="Assets:Bank:HDFC",
    )
    assert first["candidates"][0]["suggested_other_leg"] == "Assets:Transfer:CreditCard"
    assert client.post(f"/api/commit/{first['batch_id']}").json()["committed"] == 1

    second = client.post(
        "/api/import/detect",
        files={
            "file": (
                "bank2.csv",
                io.BytesIO(
                    b"Date,Description,Withdrawal Amt,Deposit Amt\n"
                    b"03/04/2026,ICICI CREDIT CARD PAYMENT,5000.00,\n"
                ),
                "text/csv",
            )
        },
    ).json()
    assert second["matched"] is True
    row = second["candidates"][0]
    assert row["dedup_status"] == "transfer_match_candidate"
    assert row["matched_txn_id"] is None
    assert client.post(f"/api/commit/{second['batch_id']}").json()["committed"] == 1


def test_one_direct_card_entry_stands_for_one_bank_payment_only(tmp_path):
    client = _client(tmp_path)
    card = _create_template(
        client,
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"01/04/2026,PAYMENT RECEIVED - THANK YOU,,5000.00\n",
        "card.csv",
        default_account="Liabilities:CreditCard:HDFC",
    )
    cid = card["candidates"][0]["id"]
    client.post(
        f"/api/review/{card['batch_id']}/{cid}",
        json={"suggested_other_leg": "Assets:Bank:HDFC"},
    )
    assert client.post(f"/api/commit/{card['batch_id']}").json()["committed"] == 1

    bank = _create_template(
        client,
        b"Date,Description,Withdrawal Amt,Deposit Amt\n"
        b"02/04/2026,HDFC CARD PAYMENT,5000.00,\n"
        b"03/04/2026,AXIS CARD PAYMENT,5000.00,\n",
        "bank.csv",
        default_account="Assets:Bank:HDFC",
    )
    first, second = bank["candidates"]
    assert first["dedup_status"] == "transfer_already_recorded"
    assert second["dedup_status"] == "transfer_match_candidate"
    assert second["matched_txn_id"] is None


def test_create_template_dry_run_reports_skipped_rows(tmp_path):
    client = _client(tmp_path)
    csv = (
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
        "Total,,412.00,\n"
    )
    client.post(
        "/api/import/detect",
        files={"file": ("hdfc.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    resp = client.post(
        "/api/templates",
        json={
            "filename": "hdfc.csv",
            "fingerprint": "fp",
            "institution": "HDFC",
            "header_row_index": 0,
            "date_column": "Date",
            "date_format": "%d/%m/%Y",
            "narration_column": "Narration",
            "debit_column": "Withdrawal Amt",
            "credit_column": "Deposit Amt",
            "default_account": "Assets:Checking:HDFC",
            "save": False,
        },
    )
    body = resp.json()
    assert len(body["candidates"]) == 1
    assert body["skipped_rows"] == [
        {"row_index": 2, "reason": "bad_date", "cells": ["Total", "", "412.00", ""]}
    ]


def test_create_template_saved_path_reports_skipped_rows(tmp_path):
    client = _client(tmp_path)
    csv = (
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
        "Total,,412.00,\n"
    )
    client.post(
        "/api/import/detect",
        files={"file": ("hdfc.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    body = client.post(
        "/api/templates",
        json={
            "filename": "hdfc.csv",
            "fingerprint": "fp",
            "institution": "HDFC",
            "header_row_index": 0,
            "date_column": "Date",
            "date_format": "%d/%m/%Y",
            "narration_column": "Narration",
            "debit_column": "Withdrawal Amt",
            "credit_column": "Deposit Amt",
            "default_account": "Assets:Checking:HDFC",
            "save": True,
        },
    ).json()
    assert body["saved"] is True and body["batch_id"]
    assert body["skipped_rows"] == [
        {"row_index": 2, "reason": "bad_date", "cells": ["Total", "", "412.00", ""]}
    ]


# --- lane A2: the edit contract, GET /learning and the commit fields ---------
#
# The shapes below were copied from the API contract fixtures when this lane
# was built (learning, edit requests, edit ok, commit response and the three
# errors), and each live response is compared key for key against its copy.

FIXTURE_LEARNING = {
    "will_learn": [
        {
            "pattern": "FICTIVEAI",
            "category": "Expenses:Subscriptions",
            "candidate_ids": ["90d7a45998445c0ca69b8e11cba509ad"],
            "replaces": None,
            "conflicting": {},
            "warning": None,
        },
        {
            "pattern": "FICTIVEBIKES",
            "category": "Expenses:Travel",
            "candidate_ids": ["6c85233928a05f76b2fe24511e75d722"],
            "replaces": "Expenses:Travel:Bike",
            "conflicting": {},
            "warning": None,
        },
    ],
    "not_learned": [
        {
            "candidate_id": "67566ddefa185e28a9d69d3410b06494",
            "pattern": "OFFUS EMI",
            "reason": "only generic words",
        },
        {
            "candidate_id": "295609f0a6cb54a0a8c1453db2727748",
            "pattern": "FICTIVE CAFE",
            "reason": "you said don't learn this",
        },
    ],
    "would_open": [
        "Expenses:Food:Cafe",
        "Expenses:Household:Appliances",
        "Expenses:Subscriptions",
    ],
}
FIXTURE_LEARNING_EMPTY = {"will_learn": [], "not_learned": [], "would_open": []}

FIXTURE_EDIT_REQUESTS = {
    "set_category": {"suggested_category": "Expenses:Travel"},
    "set_tags": {"tags": ["travel/Madikeri Oct-2026", "#Trip"]},
    "set_tags_normalised_result": ["travel/madikeri-oct-2026", "trip"],
    "learn_off": {"learn": False},
    "learn_on": {"learn": True},
    "unlink_fee": {"follows": None},
    "browser_resave_same_split": {
        "splits": [{"account": "Expenses:Food", "amount": "412.00"}],
        "date": "2026-10-06",
        "raw_narration": "FICTIVEFOODS ORDER BANGALORE",
        "source_account": "Liabilities:CreditCard:HDFC",
        "tags": [],
    },
    "browser_uncategorised_row": {
        "date": "2026-10-09",
        "raw_narration": "FICTIVE GYM 21/10/2026",
        "source_account": "Liabilities:CreditCard:HDFC",
        "tags": ["fitness"],
    },
    "remember_is_ignored": {"suggested_category": "Expenses:Travel", "remember": True},
}
FIXTURE_EDIT_OK = {"ok": True}

FIXTURE_COMMIT = {
    "committed": 11,
    "uncategorized": 2,
    "unexplained": 0,
    "statement_recorded": True,
    "learned": [
        {
            "pattern": "FICTIVEAI",
            "category": "Expenses:Subscriptions",
            "replaces": None,
        },
        {
            "pattern": "FICTIVEBIKES",
            "category": "Expenses:Travel",
            "replaces": "Expenses:Travel:Bike",
        },
    ],
    "opened": [
        "Expenses:Food:Cafe",
        "Expenses:Household:Appliances",
        "Expenses:Subscriptions",
    ],
}

FIXTURE_E1037_TAGS = {
    "status": 422,
    "body": {
        "detail": "The edit could not be applied: tags must be a list of strings",
        "code": "E1037",
        "description": "The edit could not be applied: tags must be a list of strings",
        "resolution": "Check the values you're submitting for this candidate and try again.",
        "resolvedByRefresh": False,
    },
}
FIXTURE_E1017 = {
    "status": 422,
    "body": {
        "detail": "expenses travel is not a valid account name.",
        "code": "E1017",
        "description": "expenses travel is not a valid account name.",
        "resolution": "Use Type:Component:... where Type is one of Assets, Liabilities, Equity, Income, Expenses and each component starts with an uppercase letter or digit (letters, digits, hyphens only; no spaces).",
        "resolvedByRefresh": False,
    },
}
FIXTURE_E1072 = {
    "status": 422,
    "body": {
        "detail": "'café' is not a valid tag. A tag may hold only letters, digits and - _ / . (spaces become -).",
        "code": "E1072",
        "description": "'café' is not a valid tag. A tag may hold only letters, digits and - _ / . (spaces become -).",
        "resolution": "Rename the tag, e.g. travel/madikeri-oct-2026.",
        "resolvedByRefresh": False,
    },
}

# The fixture's story on a fictional card, staged through the real routes.
STORY = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"03/10/2026,FICTIVEAI SAN FRANCISCO US,1700.00,\n"
    b"03/10/2026,IGST @18% ON FX MARKUP REF 1234567890,61.20,\n"
    b"04/10/2026,FICTIVEBIKES BANGALORE,1500.00,\n"
    b"05/10/2026,OFFUS EMI 03/06,2500.00,\n"
    b"06/10/2026,FICTIVE CAFE,240.00,\n"
    b"07/10/2026,UPI/DR/SWIGGY/412,412.00,\n"
)


def _story(tmp_path):
    (tmp_path / "storage" / "rules").mkdir(parents=True, exist_ok=True)
    (tmp_path / "storage" / "rules" / "learned.yaml").write_text(
        "- pattern: FICTIVEBIKES\n  category: Expenses:Travel:Bike\n",
        encoding="utf-8",
    )
    client = _client(tmp_path)  # created after the rule, so it is loaded
    created = _create_template(
        client, STORY, "hdfc.csv", default_account="Liabilities:CreditCard:HDFC"
    )
    bid = created["batch_id"]
    ids = [c["id"] for c in created["candidates"]]
    for cid, patch in (
        (ids[0], {"suggested_category": "Expenses:Subscriptions"}),
        (ids[2], {"suggested_category": "Expenses:Travel"}),
        (ids[3], {"suggested_category": "Expenses:Household:Appliances"}),
        (ids[4], {"suggested_category": "Expenses:Food:Cafe"}),
        (ids[4], {"learn": False}),
    ):
        resp = client.post(f"/api/review/{bid}/{cid}", json=patch)
        assert resp.status_code == 200, resp.text
        assert resp.json() == FIXTURE_EDIT_OK
    return client, bid, ids


def _same_shape(live, fixture, path="$"):
    """Key for key: the same keys at every level and the same JSON types."""
    if isinstance(fixture, dict):
        assert isinstance(live, dict), path
        assert list(live) == list(fixture), f"{path}: {list(live)} != {list(fixture)}"
        for key in fixture:
            _same_shape(live[key], fixture[key], f"{path}.{key}")
    elif isinstance(fixture, list):
        assert isinstance(live, list), path
        assert fixture, f"{path}: the fixture list is empty"
        for item in live:
            _same_shape(item, fixture[0], f"{path}[]")
    elif fixture is None:
        assert live is None or isinstance(live, str), path  # replaces: str | null
    else:
        assert type(live) is type(fixture), f"{path}: {live!r} vs {fixture!r}"


def test_review_learning_route_returns_the_preview_in_the_fixture_shape(tmp_path):
    client, bid, ids = _story(tmp_path)
    resp = client.get(f"/api/review/{bid}/learning")
    assert resp.status_code == 200
    body = resp.json()
    _same_shape(body, FIXTURE_LEARNING)
    # The fixture's story, value for value apart from the content-derived ids.
    assert [
        (w["pattern"], w["category"], w["candidate_ids"], w["replaces"])
        for w in body["will_learn"]
    ] == [
        ("FICTIVEAI", "Expenses:Subscriptions", [ids[0]], None),
        ("FICTIVEBIKES", "Expenses:Travel", [ids[2]], "Expenses:Travel:Bike"),
    ]
    assert body["not_learned"] == [
        {
            "candidate_id": ids[3],
            "pattern": "OFFUS EMI",
            "reason": "only generic words",
        },
        {
            "candidate_id": ids[4],
            "pattern": "FICTIVE CAFE",
            "reason": "you said don't learn this",
        },
    ]
    assert body["would_open"] == FIXTURE_LEARNING["would_open"]


def test_a_learning_conflict_reaches_the_route_and_the_cli_over_http(tmp_path):
    # One merchant, two rows, set in review to two categories: the rule can
    # hold one. The warning must survive the route's response model and the
    # CLI's HTTP port, not only the service dict.
    import json as _json

    from backend.app.cli import ingest as cli
    from backend.app.cli.port import HttpIngestPort

    client = _client(tmp_path)
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"01/04/2026,FICTIVEBIKES BANGALORE,500.00,\n"
        b"02/04/2026,FICTIVEBIKES BENGALURU,700.00,\n"
    )
    created = _create_template(
        client, csv, "hdfc.csv", default_account="Assets:Checking:HDFC"
    )
    bid = created["batch_id"]
    first, second = (c["id"] for c in created["candidates"])
    for cid, category in ((first, "Expenses:Travel:Bike"), (second, "Expenses:Travel")):
        resp = client.post(
            f"/api/review/{bid}/{cid}", json={"suggested_category": category}
        )
        assert resp.status_code == 200, resp.text
    warning = (
        "conflicting categories for pattern FICTIVEBIKES: Expenses:Travel:Bike "
        "(1 row), Expenses:Travel (1 row); the rule will use Expenses:Travel"
    )

    (rule,) = client.get(f"/api/review/{bid}/learning").json()["will_learn"]
    assert rule["conflicting"] == {"Expenses:Travel:Bike": 1, "Expenses:Travel": 1}
    assert rule["warning"] == warning

    out = io.StringIO()
    code = cli.main(["show", bid], port=HttpIngestPort(client=client), stdout=out)
    assert code == 0, out.getvalue()
    (shown,) = _json.loads(out.getvalue())["will_learn"]
    assert shown["conflicting"] == {"Expenses:Travel:Bike": 1, "Expenses:Travel": 1}
    assert shown["warning"] == warning


def test_review_learning_route_on_a_batch_with_nothing_to_learn(tmp_path):
    client = _client(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    created = _create_template(
        client, csv, "hdfc.csv", default_account="Assets:Checking:HDFC"
    )
    resp = client.get(f"/api/review/{created['batch_id']}/learning")
    assert resp.status_code == 200
    assert resp.json() == FIXTURE_LEARNING_EMPTY


def test_commit_response_matches_the_fixture_shape(tmp_path):
    client, bid, _ = _story(tmp_path)
    resp = client.post(f"/api/commit/{bid}")
    assert resp.status_code == 200
    body = resp.json()
    _same_shape(body, FIXTURE_COMMIT)
    assert body["learned"] == FIXTURE_COMMIT["learned"]
    assert body["opened"] == FIXTURE_COMMIT["opened"]
    assert (body["committed"], body["uncategorized"]) == (6, 0)


def test_agent_commit_returns_learned_and_opened_too(tmp_path):
    client, bid, _ = _story(tmp_path)
    resp = client.post(f"/api/agent/commit/{bid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _same_shape(body, FIXTURE_COMMIT)
    assert body["learned"] == FIXTURE_COMMIT["learned"]
    assert body["opened"] == FIXTURE_COMMIT["opened"]


def test_every_fixture_edit_request_is_accepted_as_the_contract_says(tmp_path):
    client = _client(tmp_path)
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"06/10/2026,UPI/DR/SWIGGY/412,412.00,\n"
        b"03/10/2026,FICTIVEAI SAN FRANCISCO US,1700.00,\n"
        b"03/10/2026,IGST @18% ON FX MARKUP REF 1234567890,61.20,\n"
        b"09/10/2026,FICTIVE GYM 21/10/2026,999.00,\n"
    )
    created = _create_template(
        client, csv, "hdfc.csv", default_account="Liabilities:CreditCard:HDFC"
    )
    bid = created["batch_id"]
    swiggy, ai, igst, gym = (c["id"] for c in created["candidates"])

    def post(cid, name):
        resp = client.post(f"/api/review/{bid}/{cid}", json=FIXTURE_EDIT_REQUESTS[name])
        assert resp.status_code == 200, (name, resp.text)
        assert resp.json() == FIXTURE_EDIT_OK
        rows = client.get(f"/api/review/{bid}").json()["candidates"]
        return {r["id"]: r for r in rows}

    rows = post(swiggy, "browser_resave_same_split")
    assert rows[swiggy]["category_source"] == "dictionary"
    rows = post(gym, "browser_uncategorised_row")
    assert rows[gym]["category_source"] == "none"
    assert rows[gym]["tags"] == ["fitness"]
    rows = post(gym, "set_tags")
    assert rows[gym]["tags"] == FIXTURE_EDIT_REQUESTS["set_tags_normalised_result"]
    rows = post(gym, "learn_off")
    assert rows[gym]["learn"] is False
    rows = post(gym, "learn_on")
    assert rows[gym]["learn"] is True
    assert rows[igst]["follows"] == ai
    rows = post(igst, "unlink_fee")
    assert rows[igst]["follows"] is None and rows[igst]["category_source"] == "none"
    rows = post(gym, "set_category")
    assert rows[gym]["category_source"] == "user"
    rows = post(ai, "remember_is_ignored")
    assert rows[ai]["suggested_category"] == "Expenses:Travel"
    assert not (tmp_path / "storage" / "rules" / "learned.yaml").exists()


def _one_row(client):
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,FICTIVEBIKES BANGALORE,1500.00,\n"
    created = _create_template(
        client, csv, "hdfc.csv", default_account="Assets:Checking:HDFC"
    )
    return created["batch_id"], created["candidates"][0]["id"]


@pytest.mark.parametrize(
    "patch, fixture",
    [
        ({"tags": "travel"}, FIXTURE_E1037_TAGS),
        ({"tags": ["trip", 3]}, FIXTURE_E1037_TAGS),
        ({"suggested_category": "expenses travel"}, FIXTURE_E1017),
        ({"tags": ["café"]}, FIXTURE_E1072),
    ],
    ids=["tags-a-string", "tags-a-number", "bad-account", "bad-tag"],
)
def test_refused_edits_answer_the_fixture_envelope(tmp_path, patch, fixture):
    client = _client(tmp_path)
    bid, cid = _one_row(client)
    before = client.get(f"/api/review/{bid}").json()
    resp = client.post(f"/api/review/{bid}/{cid}", json=patch)
    assert resp.status_code == fixture["status"]
    assert resp.json() == fixture["body"]
    assert client.get(f"/api/review/{bid}").json() == before


@pytest.mark.parametrize(
    "patch, detail",
    [
        (
            {"category_source": "user"},
            "category_source is set by the app, not by an edit",
        ),
        (
            {"follows": "abc"},
            "follows can only be cleared (null); set a category to override the link",
        ),
        ({"learn": "false"}, "learn must be true or false"),
        (
            {"suggested_category": 12},
            "suggested_category must be an account name or null",
        ),
    ],
    ids=["category_source", "follows", "learn", "category-type"],
)
def test_refused_contract_keys_answer_E1037_with_the_exact_detail(
    tmp_path, patch, detail
):
    client = _client(tmp_path)
    bid, cid = _one_row(client)
    resp = client.post(f"/api/review/{bid}/{cid}", json=patch)
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "E1037"
    assert body["description"] == f"The edit could not be applied: {detail}"


def test_unknown_batch_or_candidate_is_404_on_edit_learning_and_commit(tmp_path):
    client = _client(tmp_path)
    bid, _ = _one_row(client)
    cases = [
        (client.post("/api/review/nope/x", json={"tags": ["trip"]}), "E1006"),
        (client.post(f"/api/review/{bid}/nope", json={"learn": False}), "E1007"),
        (client.get("/api/review/nope/learning"), "E1006"),
        (client.post("/api/commit/nope"), "E1006"),
    ]
    for resp, code in cases:
        assert resp.status_code == 404, resp.text
        assert resp.json()["code"] == code


def test_a_rules_file_failure_tells_the_client_the_rows_were_committed(tmp_path):
    # Q29, over HTTP: the envelope says the commit went through.
    client = _client(tmp_path)
    bid, cid = _one_row(client)
    client.post(
        f"/api/review/{bid}/{cid}", json={"suggested_category": "Expenses:Travel"}
    )

    def add_many(pairs, identity=()):
        raise OSError("fictional: rules file not writable")

    client.app.state.categorizer.rules.add_many = add_many
    resp = client.post(f"/api/commit/{bid}")
    body = resp.json()
    assert resp.status_code == 500 and body["code"] == "E1073"
    assert body["detail"] == (
        "the rows were committed; the learned rules were not saved:"
        " fictional: rules file not writable"
    )
    assert "Do not commit this batch again" in body["resolution"]
    assert client.post(f"/api/commit/{bid}").json()["code"] == "E1006"
    year = (tmp_path / "storage" / "journal" / "2026.beancount").read_text()
    assert year.count("FICTIVEBIKES BANGALORE") == 1


def test_the_learning_route_answers_exactly_the_contract_keys(tmp_path):
    # M4: exact keys at every level, and the route is bound to the contract's
    # response model.
    client, bid, _ = _story(tmp_path)
    body = client.get(f"/api/review/{bid}/learning").json()
    assert set(body) == {"will_learn", "not_learned", "would_open"}
    assert {frozenset(w) for w in body["will_learn"]} == {
        frozenset(
            {
                "pattern",
                "category",
                "candidate_ids",
                "replaces",
                "conflicting",
                "warning",
            }
        )
    }
    assert {frozenset(n) for n in body["not_learned"]} == {
        frozenset({"candidate_id", "pattern", "reason"})
    }
    assert all(isinstance(a, str) for a in body["would_open"])
    schema = client.get("/openapi.json").json()
    ok = schema["paths"]["/api/review/{batch_id}/learning"]["get"]["responses"]["200"]
    assert ok["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ReviewLearningResponse"
    )
