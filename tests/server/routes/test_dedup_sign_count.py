"""Import dedup is sign-aware and count-aware (synthetic data only).

A charge and its same-day refund of the same amount and wording are two
transactions; two identical same-day charges are two transactions. Dedup used
to key on the unsigned amount and check a set, so a later statement's refund
(or second identical charge) was marked EXACT_DUP and never reached the
journal. Re-importing what is already committed must still be all duplicates.
"""

from __future__ import annotations

import io
from datetime import date

from fastapi.testclient import TestClient

from backend.app.server.main import create_app

CARD = "Liabilities:CreditCard:Test"
BANK = "Assets:Bank:Test"
POSSIBLE_DUP = "possible duplicate:"


def _template(fingerprint: str, account: str) -> dict:
    return {
        "fingerprint": fingerprint,
        "institution": "Test",
        "header_row_index": 0,
        "date_column": "Date",
        "date_format": "%Y-%m-%d",
        "narration_column": "Narration",
        "debit_column": "Debit",
        "credit_column": "Credit",
        "default_account": account,
    }


def _client(tmp_path) -> TestClient:
    return TestClient(
        create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 10, 16))
    )


def _stage(client: TestClient, name: str, text: str, account: str = CARD) -> dict:
    client.post(
        "/api/import/detect",
        files={"file": (name, io.BytesIO(text.encode()), "text/csv")},
    )
    tpl = _template(f"fp-{account}", account)
    return client.post("/api/templates", json={"filename": name, **tpl}).json()


def _commit(client: TestClient, staged: dict) -> dict:
    batch = staged["batch_id"]
    for c in staged["candidates"]:
        if c["dedup_status"] == "new":
            client.post(
                f"/api/review/{batch}/{c['id']}",
                json={"suggested_category": "Expenses:Shopping"},
            )
    r = client.post(f"/api/commit/{batch}")
    assert r.status_code == 200, r.text
    return r.json()


def _statuses(staged: dict) -> list[tuple[str, str]]:
    return [(c["direction"], c["dedup_status"]) for c in staged["candidates"]]


HEADER = "Date,Narration,Debit,Credit\n"


def test_refund_after_a_committed_charge_is_new(tmp_path):
    client = _client(tmp_path)
    _commit(client, _stage(client, "a.csv", HEADER + "2026-09-03,IND*SHOP,305.00,\n"))
    again = _stage(
        client,
        "b.csv",
        HEADER + "2026-09-03,IND*SHOP,305.00,\n2026-09-03,IND*SHOP,,305.00\n",
    )
    assert _statuses(again) == [("debit", "exact_dup"), ("credit", "new")]
    # The refund is not doubted: no possible-duplicate flag.
    assert not any(
        f.startswith(POSSIBLE_DUP) for c in again["candidates"] for f in c["flags"]
    )
    out = _commit(client, again)
    assert out["committed"] == 1


def test_charge_and_reversal_committed_together_reimport_as_two_dups(tmp_path):
    client = _client(tmp_path)
    text = HEADER + "2026-09-03,IND*SHOP,305.00,\n2026-09-03,IND*SHOP,,305.00\n"
    first = _stage(client, "a.csv", text)
    assert _statuses(first) == [("debit", "new"), ("credit", "new")]
    _commit(client, first)
    again = _stage(client, "b.csv", text)
    assert _statuses(again) == [("debit", "exact_dup"), ("credit", "exact_dup")]


def test_twin_charges_one_committed_gives_one_dup_and_one_flagged_new(tmp_path):
    client = _client(tmp_path)
    _commit(client, _stage(client, "a.csv", HEADER + "2026-09-04,CAFE DAY,120.00,\n"))
    twins = HEADER + "2026-09-04,CAFE DAY,120.00,\n2026-09-04,CAFE DAY,120.00,\n"
    again = _stage(client, "b.csv", twins)
    dup, new = again["candidates"]
    assert (dup["dedup_status"], new["dedup_status"]) == ("exact_dup", "new")
    assert not any(f.startswith(POSSIBLE_DUP) for f in dup["flags"])
    (flag,) = [f for f in new["flags"] if f.startswith(POSSIBLE_DUP)]
    assert "2 identical rows in this import, 1 already recorded" in flag

    # The review store and the commit-time re-check agree with staging: the
    # flagged twin is written, the duplicate is not.
    batch = again["batch_id"]
    review = client.get(f"/api/review/{batch}").json()["candidates"]
    assert [c["dedup_status"] for c in review] == ["exact_dup", "new"]
    assert _commit(client, again)["committed"] == 1

    # Now both are recorded: the same file is all duplicates, none doubted.
    third = _stage(client, "c.csv", twins)
    assert [c["dedup_status"] for c in third["candidates"]] == ["exact_dup"] * 2
    assert not any(
        f.startswith(POSSIBLE_DUP) for c in third["candidates"] for f in c["flags"]
    )


def test_reimporting_a_committed_card_statement_is_all_dups(tmp_path):
    client = _client(tmp_path)
    text = (
        HEADER
        + "2026-09-01,GROCER ONE,450.00,\n"
        + "2026-09-02,FUEL STATION,2000.00,\n"
        + "2026-09-02,FUEL STATION,2000.00,\n"
        + "2026-09-05,GROCER ONE,,450.00\n"
        + "2026-09-06,BOOK SHOP,99.00,\n"
    )
    first = _stage(client, "card.csv", text)
    assert {c["dedup_status"] for c in first["candidates"]} == {"new"}
    assert _commit(client, first)["committed"] == 5
    again = _stage(client, "card2.csv", text)
    assert [c["dedup_status"] for c in again["candidates"]] == ["exact_dup"] * 5


def test_reimporting_a_committed_bank_statement_is_all_dups(tmp_path):
    client = _client(tmp_path)
    text = (
        HEADER
        + "2026-09-01,SALARY EXAMPLE CO,,85000.00\n"
        + "2026-09-03,UPI/RENT/OWNER,25000.00,\n"
        + "2026-09-03,UPI/GROCER,740.00,\n"
        + "2026-09-04,UPI/GROCER REFUND,,740.00\n"
    )
    first = _stage(client, "bank.csv", text, account=BANK)
    assert _commit(client, first)["committed"] == 4
    again = _stage(client, "bank2.csv", text, account=BANK)
    assert [c["dedup_status"] for c in again["candidates"]] == ["exact_dup"] * 4


def test_a_negative_amount_debit_reimports_as_a_dup(tmp_path):
    """Dedup keys a row by the amount the writer posts, sign included. A Debit
    cell of -500.00 reaches the candidate as -500 and is posted as +500 on the
    source account; the key must say +500 too, or a re-import double-books."""
    client = _client(tmp_path)
    text = HEADER + "2026-09-03,UPI/ODD SIGN,-500.00,\n"
    first = _stage(client, "neg.csv", text, account=BANK)
    assert [c["amount"] for c in first["candidates"]] == ["-500.00"]
    assert _commit(client, first)["committed"] == 1
    again = _stage(client, "neg2.csv", text, account=BANK)
    assert [c["dedup_status"] for c in again["candidates"]] == ["exact_dup"]


# --- with candidate id disambiguation (fix/ingest-bugs) ----------------------


def _journal_txn_ids(client: TestClient) -> list[str]:
    from beancount.core import data

    return [
        e.meta["txn-id"]
        for e in client.app.state.ledger.load(fresh=True).entries
        if isinstance(e, data.Transaction) and "txn-id" in e.meta
    ]


def test_a_refund_marked_new_gets_its_own_id(tmp_path):
    # The refund shares date, amount, wording and account with the committed
    # charge. Dedup now books it as new; its id must not be the charge's, or
    # a review edit (or the committed txn-id) would address both.
    client = _client(tmp_path)
    _commit(client, _stage(client, "a.csv", HEADER + "2026-09-03,IND*SHOP,305.00,\n"))
    (charge_id,) = _journal_txn_ids(client)
    again = _stage(
        client,
        "b.csv",
        HEADER + "2026-09-03,IND*SHOP,305.00,\n2026-09-03,IND*SHOP,,305.00\n",
    )
    dup, refund = again["candidates"]
    assert (dup["dedup_status"], refund["dedup_status"]) == ("exact_dup", "new")
    assert dup["id"] == charge_id
    assert refund["id"] != charge_id

    assert _commit(client, again)["committed"] == 1
    ids = _journal_txn_ids(client)
    assert sorted(ids) == sorted([charge_id, refund["id"]])
    assert len(set(ids)) == 2


def test_rejecting_one_flagged_twin_keeps_the_other(tmp_path):
    # One charge recorded; the next statement prints it three times. One row
    # claims the recorded charge, the other two are new and flagged as
    # possible duplicates. They are identical, but each has its own id, so
    # rejecting one drops only that row: the other stays and is committed.
    client = _client(tmp_path)
    row = "2026-09-04,CAFE DAY,120.00,\n"
    _commit(client, _stage(client, "a.csv", HEADER + row))
    again = _stage(client, "b.csv", HEADER + row * 3)
    batch = again["batch_id"]
    dup, first, second = again["candidates"]
    assert [c["dedup_status"] for c in again["candidates"]] == [
        "exact_dup",
        "new",
        "new",
    ]
    for c in (first, second):
        assert any(f.startswith(POSSIBLE_DUP) for f in c["flags"])
    assert len({dup["id"], first["id"], second["id"]}) == 3

    r = client.delete(f"/api/review/{batch}/{first['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["dropped"] == 1

    left = client.get(f"/api/review/{batch}").json()["candidates"]
    assert [c["id"] for c in left] == [dup["id"], second["id"]]
    assert _commit(client, {**again, "candidates": left})["committed"] == 1
    ids = _journal_txn_ids(client)
    assert len(ids) == 2 and second["id"] in ids and first["id"] not in ids


def test_rejecting_one_of_two_new_identical_rows_keeps_the_other(tmp_path):
    client = _client(tmp_path)
    row = "2026-09-05,FUEL STATION,2000.00,\n"
    staged = _stage(client, "a.csv", HEADER + row * 2)
    batch = staged["batch_id"]
    keep, drop = staged["candidates"]
    assert keep["id"] != drop["id"]

    assert client.delete(f"/api/review/{batch}/{drop['id']}").json()["dropped"] == 1
    left = client.get(f"/api/review/{batch}").json()["candidates"]
    assert [c["id"] for c in left] == [keep["id"]]
    assert _commit(client, {**staged, "candidates": left})["committed"] == 1
    assert _journal_txn_ids(client) == [keep["id"]]


def test_a_new_row_never_reuses_a_recorded_txn_id(tmp_path):
    # Two identical charges were committed (ids A and A|1), then the first
    # was deleted by hand. Re-staging both rows: the first row (id A) claims
    # the one recorded entry -- whose txn-id is A|1 -- and the second row
    # would get id A|1 too. It is written, so it must move to a fresh id.
    client = _client(tmp_path)
    row = "2026-09-07,CAFE DAY,150.00,\n"
    first = _stage(client, "a.csv", HEADER + row * 2)
    first_id, second_id = (c["id"] for c in first["candidates"])
    _commit(client, first)

    ledger = client.app.state.ledger
    year = ledger.journal_dir / "2026.beancount"
    blocks = year.read_text(encoding="utf-8").split("\n\n")
    kept = [b for b in blocks if f'txn-id: "{first_id}"' not in b]
    assert len(kept) == len(blocks) - 1
    year.write_text("\n\n".join(kept), encoding="utf-8")
    assert ledger.validate() == []
    assert _journal_txn_ids(client) == [second_id]

    again = _stage(client, "b.csv", HEADER + row * 2)
    dup, new = again["candidates"]
    assert (dup["dedup_status"], new["dedup_status"]) == ("exact_dup", "new")
    assert dup["id"] == first_id
    assert new["id"] not in (first_id, second_id)

    # Staging the same file again gives the same ids.
    assert [
        c["id"] for c in _stage(client, "c.csv", HEADER + row * 2)["candidates"]
    ] == [dup["id"], new["id"]]

    assert _commit(client, again)["committed"] == 1
    ids = _journal_txn_ids(client)
    assert sorted(ids) == sorted([second_id, new["id"]])
