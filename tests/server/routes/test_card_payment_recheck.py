"""The direct bank<->card payment match: once per side, and against the
account the user picked in review rather than the deduper's suggestion."""

import io
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.server.main import create_app

CLEARING = "Assets:Transfer:CreditCard"


def _client(tmp_path):
    return TestClient(create_app(storage_dir=tmp_path / "storage"))


def _create_template(client, csv_bytes, filename, default_account):
    detected = client.post(
        "/api/import/detect",
        files={"file": (filename, io.BytesIO(csv_bytes), "text/csv")},
    ).json()
    assert detected["matched"] is False, detected
    return client.post(
        "/api/templates",
        json={
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
            "default_account": default_account,
            "save": True,
        },
    ).json()


def _commit_direct_card_payment(client) -> None:
    """A card bill payment booked straight bank -> card (before the clearing
    account existed)."""
    card = _create_template(
        client,
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"01/04/2026,PAYMENT RECEIVED - THANK YOU,,5000.00\n",
        "card.csv",
        "Liabilities:CreditCard:HDFC",
    )
    cid = card["candidates"][0]["id"]
    client.post(
        f"/api/review/{card['batch_id']}/{cid}",
        json={"suggested_other_leg": "Assets:Bank:HDFC"},
    )
    assert client.post(f"/api/commit/{card['batch_id']}").json()["committed"] == 1


def _stage_bank_row(client, narration: str) -> tuple[str, dict]:
    bank = _create_template(
        client,
        b"Date,Description,Withdrawal Amt,Deposit Amt\n"
        + f"02/04/2026,{narration},5000.00,\n".encode(),
        "bank.csv",
        "Assets:Bank:HDFC",
    )
    return bank["batch_id"], bank["candidates"][0]


def test_one_direct_entry_stands_for_its_bank_side_and_its_card_side(tmp_path):
    client = _client(tmp_path)
    _commit_direct_card_payment(client)

    def row(narration, source, direction):
        return CandidateTxn(
            date=date(2026, 4, 2),
            amount=Decimal("5000.00"),
            raw_narration=narration,
            normalized_narration=narration,
            source_account=source,
            direction=direction,
        )

    bank = row("HDFC CARD PAYMENT", "Assets:Bank:HDFC", Direction.DEBIT)
    card = row("AUTOPAY RECEIVED", "Liabilities:CreditCard:HDFC", Direction.CREDIT)
    client.app.state.import_service._mark_duplicates([bank, card])
    assert bank.dedup_status.value == "transfer_already_recorded"
    assert card.dedup_status.value == "transfer_already_recorded"
    assert bank.matched_txn_id is not None
    assert card.matched_txn_id == bank.matched_txn_id


def test_recheck_runs_the_direct_match_on_a_bill_pay_row_the_user_sent_to_clearing(
    tmp_path,
):
    client = _client(tmp_path)
    _commit_direct_card_payment(client)
    bid, row = _stage_bank_row(client, "BBPS BILL PAYMENT 7781")
    cid = row["id"]
    assert row["dedup_status"] == "new"
    assert row["suggested_other_leg"] is None
    client.post(
        f"/api/review/{bid}/{cid}",
        json={"splits": [{"account": CLEARING, "amount": "5000.00"}]},
    )
    changes = client.app.state.import_service.recheck_dedup(bid)
    assert changes == [
        {"candidate_id": cid, "before": "new", "after": "transfer_already_recorded"}
    ]
    after = client.get(f"/api/review/{bid}").json()["candidates"][0]
    assert after["matched_txn_id"]
    assert [s["account"] for s in after["splits"]] == [CLEARING]


def test_recheck_keeps_a_card_payment_the_user_moved_off_clearing(tmp_path):
    client = _client(tmp_path)
    _commit_direct_card_payment(client)
    bid, row = _stage_bank_row(client, "HDFC CARD PAYMENT")
    cid = row["id"]
    assert row["dedup_status"] == "transfer_already_recorded"
    # The user says this paid a different card, and commits it anyway.
    client.post(
        f"/api/review/{bid}/{cid}",
        json={
            "suggested_other_leg": "Liabilities:CreditCard:ICICI",
            "dedup_status": "transfer_match_candidate",
            "matched_txn_id": None,
        },
    )
    assert client.app.state.import_service.recheck_dedup(bid) == []
    after = client.get(f"/api/review/{bid}").json()["candidates"][0]
    assert after["dedup_status"] == "transfer_match_candidate"
    assert after["suggested_other_leg"] == "Liabilities:CreditCard:ICICI"
    assert client.post(f"/api/commit/{bid}").json()["committed"] == 1


def test_recheck_skips_transfer_checks_for_a_row_the_user_booked_as_spend(tmp_path):
    client = _client(tmp_path)
    _commit_direct_card_payment(client)
    bid, row = _stage_bank_row(client, "HDFC CARD PAYMENT")
    cid = row["id"]
    client.post(
        f"/api/review/{bid}/{cid}",
        json={
            "splits": [{"account": "Expenses:Shopping", "amount": "5000.00"}],
            "dedup_status": "transfer_match_candidate",
            "matched_txn_id": None,
        },
    )
    client.app.state.import_service.recheck_dedup(bid)
    after = client.get(f"/api/review/{bid}").json()["candidates"][0]
    assert after["dedup_status"] != "transfer_already_recorded"
