"""Learning at commit: the preview, the rules written, the accounts opened.

Fictional narrations only. `_service` / `_saved_batch` are the shared
helpers in tests/service/ingest/test_service.py.
"""

from __future__ import annotations

import pytest

from backend.app.persistence.learned_rules import LearnedRules
from backend.app.core.errors.errors import CodedError
from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.service.reports.health.ledger_view import all_postings, open_accounts
from backend.app.service.ingest import service as import_service
from tests.service.ingest.test_service import _saved_batch, _service

CSV = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"03/10/2026,FICTIVEBIKES BANGALORE,1500.00,\n"
    b"04/10/2026,FICTIVEBIKES BENGALURU,900.00,\n"
    b"05/10/2026,FICTIVE CAFE,240.00,\n"
)

MIXED = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"03/10/2026,FICTIVEBIKES BANGALORE,1500.00,\n"
    b"03/06/2026,OFFUS EMI 03/06,2500.00,\n"
    b"11/07/2026,IGST @18% ON FX MARKUP REF 1234567891,72.00,\n"
    b"05/10/2026,FICTIVE CAFE,240.00,\n"
    b"06/10/2026,UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl/412,412.00,\n"
)


def _rules_file(tmp_path):
    return tmp_path / "storage" / "rules" / "learned.yaml"


def _ids(b):
    return [c["id"] for c in b["candidates"]]


def test_commit_learns_the_user_set_pattern_and_reports_it(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid, ids = b["batch_id"], _ids(b)
    svc.edit(bid, ids[0], {"suggested_category": "Expenses:Travel"})
    assert svc.learning(bid)["will_learn"] == [
        {
            "pattern": "FICTIVEBIKES",
            "category": "Expenses:Travel",
            "candidate_ids": [ids[0]],
            "replaces": None,
            "conflicting": {},
            "warning": None,
        }
    ]
    assert not _rules_file(tmp_path).exists()  # nothing learned before commit
    result = svc.commit(bid)
    assert result["learned"] == [
        {"pattern": "FICTIVEBIKES", "category": "Expenses:Travel", "replaces": None}
    ]
    assert result["committed"] == 3
    assert LearnedRules(_rules_file(tmp_path)).match("FICTIVEBIKES MYSORE") == (
        "Expenses:Travel"
    )
    # Only the user-set pattern: the spread row did not add a second rule.
    assert LearnedRules(_rules_file(tmp_path))._rules == [
        {"pattern": "FICTIVEBIKES", "category": "Expenses:Travel"}
    ]


def test_learning_is_read_only(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid = b["batch_id"]
    svc.edit(bid, _ids(b)[0], {"suggested_category": "Expenses:Travel"})
    before = svc.get_review(bid)
    main_before = svc._ledger.main_file.read_text()
    svc.learning(bid)
    svc.learning(bid)
    assert svc.get_review(bid) == before
    assert svc._ledger.main_file.read_text() == main_before
    assert not _rules_file(tmp_path).exists()


def test_veto_generic_and_fee_rows_are_listed_not_learned(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, MIXED, "hdfc.csv")
    bid, ids = b["batch_id"], _ids(b)
    svc.edit(bid, ids[0], {"suggested_category": "Expenses:Travel", "learn": False})
    svc.edit(bid, ids[1], {"suggested_category": "Expenses:Household:Appliances"})
    svc.edit(bid, ids[2], {"suggested_category": "Expenses:Subscriptions"})
    lr = svc.learning(bid)
    assert lr["will_learn"] == []
    assert lr["not_learned"] == [
        {
            "candidate_id": ids[1],
            "pattern": "OFFUS EMI",
            "reason": "only generic words",
        },
        {"candidate_id": ids[2], "pattern": "IGST", "reason": "fee row"},
        {
            "candidate_id": ids[0],
            "pattern": "FICTIVEBIKES",
            "reason": "you said don't learn this",
        },
    ]
    result = svc.commit(bid)
    assert result["learned"] == []
    assert not _rules_file(tmp_path).exists()


def test_learn_true_lifts_the_veto(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid, cid = b["batch_id"], _ids(b)[0]
    svc.edit(bid, cid, {"suggested_category": "Expenses:Travel", "learn": False})
    svc.edit(bid, cid, {"learn": True})
    assert [w["pattern"] for w in svc.learning(bid)["will_learn"]] == ["FICTIVEBIKES"]


def test_a_rule_or_dictionary_row_teaches_nothing(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, MIXED, "hdfc.csv")
    assert b["candidates"][4]["category_source"] == "dictionary"
    lr = svc.learning(b["batch_id"])
    assert lr["will_learn"] == [] and lr["not_learned"] == []
    assert svc.commit(b["batch_id"])["learned"] == []
    assert not _rules_file(tmp_path).exists()


def test_confirming_the_dictionarys_category_writes_no_rule(tmp_path):
    # Q14: set in review, but the dictionary already answers it.
    svc = _service(tmp_path)
    b = _saved_batch(svc, MIXED, "hdfc.csv")
    bid, cid = b["batch_id"], _ids(b)[4]
    svc.edit(bid, cid, {"suggested_category": "Expenses:Food"})
    lr = svc.learning(bid)
    assert lr["will_learn"] == []
    assert lr["not_learned"] == [
        {
            "candidate_id": cid,
            # A UPI row is learned under its payee identity.
            "pattern": "UPI:SWIGGY|SWIGGY@YBL",
            "reason": "already learned or in the dictionary",
        }
    ]
    assert svc.commit(bid)["learned"] == []
    assert not _rules_file(tmp_path).exists()


def test_replacing_an_existing_rule_names_the_old_category(tmp_path):
    svc = _service(tmp_path)
    svc._categorizer.rules.add("FICTIVEBIKES", "Expenses:Travel:Bike")
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid, cid = b["batch_id"], _ids(b)[0]
    assert b["candidates"][0]["category_source"] == "rule"
    svc.edit(bid, cid, {"suggested_category": "Expenses:Travel"})
    (item,) = svc.learning(bid)["will_learn"]
    assert item["replaces"] == "Expenses:Travel:Bike"
    result = svc.commit(bid)
    assert result["learned"] == [
        {
            "pattern": "FICTIVEBIKES",
            "category": "Expenses:Travel",
            "replaces": "Expenses:Travel:Bike",
        }
    ]
    assert LearnedRules(_rules_file(tmp_path))._rules == [
        {"pattern": "FICTIVEBIKES", "category": "Expenses:Travel"}
    ]


def test_a_substring_rule_is_not_named_as_replaced(tmp_path):
    # Q12: "BIKES" matches the narration but add_many will not replace it.
    svc = _service(tmp_path)
    svc._categorizer.rules.add("BIKES", "Expenses:Travel:Bike")
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid = b["batch_id"]
    svc.edit(bid, _ids(b)[0], {"suggested_category": "Expenses:Travel"})
    (item,) = svc.learning(bid)["will_learn"]
    assert item["replaces"] is None
    svc.commit(bid)
    assert LearnedRules(_rules_file(tmp_path))._rules == [
        {"pattern": "BIKES", "category": "Expenses:Travel:Bike"},
        {"pattern": "FICTIVEBIKES", "category": "Expenses:Travel"},
    ]


def test_would_open_lists_new_accounts_and_commit_reports_them_opened(tmp_path):
    svc = _service(tmp_path)
    # Assets:Checking:HDFC is seeded; Assets:Bank:HDFC would itself be listed.
    b = _saved_batch(svc, CSV, "hdfc.csv", default_account="Assets:Checking:HDFC")
    bid = b["batch_id"]
    svc.edit(bid, _ids(b)[0], {"suggested_category": "Expenses:Subscriptions"})
    svc.edit(bid, _ids(b)[2], {"suggested_category": "Expenses:Food:Cafe"})
    assert svc.learning(bid)["would_open"] == [
        "Expenses:Food:Cafe",
        "Expenses:Subscriptions",
    ]
    assert svc.commit(bid)["opened"] == ["Expenses:Food:Cafe", "Expenses:Subscriptions"]
    assert {"Expenses:Food:Cafe", "Expenses:Subscriptions"} <= set(
        open_accounts(svc._ledger)
    )


def test_commit_opens_an_account_that_prefixes_an_open_one(tmp_path):
    # Review Focus 4, end to end through the service.
    svc = _service(tmp_path)
    svc._ledger.open_named_accounts(["Expenses:Subscriptions:Claude"])
    b = _saved_batch(svc, CSV, "hdfc.csv", default_account="Assets:Checking:HDFC")
    bid = b["batch_id"]
    svc.edit(bid, _ids(b)[0], {"suggested_category": "Expenses:Subscriptions"})
    assert "Expenses:Subscriptions" in svc.learning(bid)["would_open"]
    result = svc.commit(bid)
    assert "Expenses:Subscriptions" in result["opened"]
    accounts = {p.account for p in all_postings(svc._ledger)}
    assert "Expenses:Subscriptions" in accounts


def test_opened_is_measured_from_the_ledger_not_copied_from_the_preview(
    tmp_path, monkeypatch
):
    # The write path, not the preview, is the authority on what got opened.
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv", default_account="Assets:Checking:HDFC")
    bid = b["batch_id"]
    assert svc.learning(bid)["would_open"] == []
    real = import_service.commit_candidates

    def writes_and_opens_one_more(ledger, *args, **kwargs):
        real(ledger, *args, **kwargs)
        ledger.open_named_accounts(["Expenses:Fictive:Extra"])

    monkeypatch.setattr(import_service, "commit_candidates", writes_and_opens_one_more)
    assert svc.commit(bid)["opened"] == ["Expenses:Fictive:Extra"]


def test_an_already_open_account_is_not_reported_opened(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv", default_account="Assets:Checking:HDFC")
    bid = b["batch_id"]
    svc.edit(bid, _ids(b)[0], {"suggested_category": "Expenses:Travel"})
    assert svc.commit(bid)["opened"] == []


def test_a_refused_commit_learns_and_opens_nothing(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid, ids = b["batch_id"], _ids(b)
    svc.edit(bid, ids[0], {"suggested_category": "Expenses:Subscriptions"})
    svc.edit(bid, ids[2], {"needs_account": True})
    main_before = svc._ledger.main_file.read_text()
    with pytest.raises(CodedError) as exc:
        svc.commit(bid)
    assert exc.value.code == "E1010"
    assert not _rules_file(tmp_path).exists()
    assert svc._ledger.main_file.read_text() == main_before
    # The batch is still there to fix and retry.
    assert len(svc.get_review(bid)["candidates"]) == 3


def test_a_failed_journal_write_learns_nothing(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid = b["batch_id"]
    svc.edit(bid, _ids(b)[0], {"suggested_category": "Expenses:Travel"})

    def fails(*args, **kwargs):
        raise LedgerValidationError(["fictional failure"])

    monkeypatch.setattr(import_service, "commit_candidates", fails)
    with pytest.raises(LedgerValidationError):
        svc.commit(bid)
    assert not _rules_file(tmp_path).exists()
    assert len(svc.get_review(bid)["candidates"]) == 3


def test_commit_clears_the_batch_before_it_writes_rules(tmp_path, monkeypatch):
    # A rules-file failure after the journal write must never leave the batch
    # staged: a browser retry does not re-run dedup and would write it twice.
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid = b["batch_id"]
    svc.edit(bid, _ids(b)[0], {"suggested_category": "Expenses:Travel"})
    seen: list[bool] = []

    def add_many(pairs, identity=()):
        try:
            svc.get_review(bid)
            seen.append(True)
        except CodedError:
            seen.append(False)
        raise OSError("fictional: rules file not writable")

    monkeypatch.setattr(svc._categorizer.rules, "add_many", add_many)
    with pytest.raises(CodedError):
        svc.commit(bid)
    assert seen == [False]  # the batch was already gone when rules were written
    with pytest.raises(CodedError) as exc:
        svc.commit(bid)  # the retry cannot write the rows a second time
    assert exc.value.code == "E1006"
    narrations = [p.narration for p in all_postings(svc._ledger) if p.amount > 0]
    assert sorted(narrations) == sorted(
        ["FICTIVEBIKES BANGALORE", "FICTIVEBIKES BENGALURU", "FICTIVE CAFE"]
    )


def test_a_rules_file_failure_says_the_rows_were_committed(tmp_path, monkeypatch):
    # Q29: the user must not read this as a failed commit and retry it.
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid = b["batch_id"]
    svc.edit(bid, _ids(b)[0], {"suggested_category": "Expenses:Travel"})

    def add_many(pairs, identity=()):
        raise OSError("fictional: rules file not writable")

    monkeypatch.setattr(svc._categorizer.rules, "add_many", add_many)
    with pytest.raises(CodedError) as exc:
        svc.commit(bid)
    assert exc.value.description == (
        "the rows were committed; the learned rules were not saved:"
        " fictional: rules file not writable"
    )
    assert (exc.value.code, exc.value.status) == ("E1073", 500)
    assert "Do not commit this batch again" in exc.value.resolution
    assert "FICTIVEBIKES -> Expenses:Travel" in exc.value.resolution


def test_the_fee_rows_category_is_refreshed_at_commit(tmp_path):
    # A client can patch a fee row's category verbatim through a non-contract
    # key path; commit re-derives it from the charge before writing.
    svc = _service(tmp_path)
    fees = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"03/10/2026,FICTIVEAI SAN FRANCISCO US,1700.00,\n"
        b"03/10/2026,IGST @18% ON FX MARKUP REF 1234567890,61.20,\n"
    )
    b = _saved_batch(svc, fees, "hdfc.csv", default_account="Assets:Checking:HDFC")
    bid, ids = b["batch_id"], _ids(b)
    svc.edit(bid, ids[0], {"suggested_category": "Expenses:Subscriptions"})
    svc._review_store.get_batch(bid)[1].suggested_category = "Expenses:Food"
    svc.commit(bid)
    igst = [p for p in all_postings(svc._ledger) if p.narration.startswith("IGST")]
    assert {p.account for p in igst} == {
        "Expenses:Subscriptions",
        "Assets:Checking:HDFC",
    }


def test_unknown_batch_learning_and_commit_are_E1006(tmp_path):
    svc = _service(tmp_path)
    for call in (svc.learning, svc.commit):
        with pytest.raises(CodedError) as exc:
            call("no-such-batch")
        assert (exc.value.code, exc.value.status) == ("E1006", 404)


def test_learning_skips_rows_commit_would_not_write(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid = b["batch_id"]
    svc.edit(bid, _ids(b)[2], {"suggested_category": "Expenses:Food:Cafe"})
    from backend.app.models.domain.candidate import DedupStatus

    svc._review_store.get_batch(bid)[2].dedup_status = DedupStatus.EXACT_DUP
    lr = svc.learning(bid)
    assert lr["will_learn"] == []
    assert "Expenses:Food:Cafe" not in lr["would_open"]


def test_copy_paste_import_sets_categories_in_review_and_learns_at_commit(tmp_path):
    svc = _service(tmp_path)
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"06/04/2026,UPI/DR/412345678901/SUNRISE TIFFINS/YESB/sunrise.tiffins@ybl/180,180.00,\n"
        b"07/04/2026,UPI/DR/412345678902/SUNRISE TIFFINS/YESB/sunrise.tiffins@ybl/220,220.00,\n"
    )
    b = _saved_batch(svc, csv, "hdfc.csv")
    bid = b["batch_id"]
    export = svc.export_unknowns(bid)
    first = sorted(export["index"].keys())[0]
    applied = svc.import_result(
        bid, {"text": f"{first}\tExpenses:Food\n", "index": export["index"]}
    )
    assert applied == {"applied": 1}
    rows = svc.get_review(bid)["candidates"]
    assert [r["category_source"] for r in rows] == ["user", "taught_batch"]
    assert not _rules_file(tmp_path).exists()
    result = svc.commit(bid)
    assert result["learned"] == [
        {
            "pattern": "UPI:SUNRISE TIFFINS|SUNRISE.TIFFINS@YBL",
            "category": "Expenses:Food",
            "replaces": None,
        }
    ]


def test_copy_paste_import_refuses_a_bad_account_before_any_row(tmp_path):
    svc = _service(tmp_path)
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"06/04/2026,UPI/DR/SUNRISE TIFFINS/180,180.00,\n"
        b"08/04/2026,FICTIVE CAFE,90.00,\n"
    )
    b = _saved_batch(svc, csv, "hdfc.csv")
    bid = b["batch_id"]
    export = svc.export_unknowns(bid)
    n1, n2 = sorted(export["index"].keys())
    with pytest.raises(CodedError) as exc:
        svc.import_result(
            bid,
            {
                "text": f"{n1}\tExpenses:Food\n{n2}\tExpenses:food cafe\n",
                "index": export["index"],
            },
        )
    assert exc.value.code == "E1017"
    rows = svc.get_review(bid)["candidates"]
    assert [r["suggested_category"] for r in rows] == [None, None]


def test_commit_learns_nothing_from_a_row_it_does_not_write(tmp_path):
    # M2: the commit's preview is over the committable rows, as learning() is.
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid = b["batch_id"]
    svc.edit(bid, _ids(b)[2], {"suggested_category": "Expenses:Food:Cafe"})
    from backend.app.models.domain.candidate import DedupStatus

    svc._review_store.get_batch(bid)[2].dedup_status = DedupStatus.EXACT_DUP
    result = svc.commit(bid)
    assert result["committed"] == 2
    assert result["learned"] == []
    assert not _rules_file(tmp_path).exists()


def test_a_row_set_back_to_uncategorized_is_exported_for_copy_paste(tmp_path):
    # M3 (Q22): export_unknowns lists it with the never-categorised rows.
    svc = _service(tmp_path)
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
        b"08/04/2026,FICTIVE CAFE,90.00,\n"
    )
    b = _saved_batch(svc, csv, "hdfc.csv")
    bid, ids = b["batch_id"], _ids(b)
    assert list(svc.export_unknowns(bid)["index"].values()) == [ids[1]]
    svc.edit(bid, ids[0], {"suggested_category": "Expenses:Uncategorized"})
    assert sorted(svc.export_unknowns(bid)["index"].values()) == sorted(ids)


def test_copy_paste_import_applies_nothing_when_a_row_has_gone(tmp_path):
    # M6: a row rejected after the export makes the whole paste refused.
    svc = _service(tmp_path)
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"06/04/2026,UPI/DR/SUNRISE TIFFINS/180,180.00,\n"
        b"08/04/2026,FICTIVE CAFE,90.00,\n"
    )
    b = _saved_batch(svc, csv, "hdfc.csv")
    bid = b["batch_id"]
    export = svc.export_unknowns(bid)
    n1, n2 = sorted(export["index"].keys())
    svc.reject(bid, export["index"][n2])
    with pytest.raises(CodedError) as exc:
        svc.import_result(
            bid,
            {
                "text": f"{n1}\tExpenses:Food\n{n2}\tExpenses:Food:Cafe\n",
                "index": export["index"],
            },
        )
    assert (exc.value.code, exc.value.status) == ("E1007", 404)
    rows = svc.get_review(bid)["candidates"]
    assert [(r["suggested_category"], r["category_source"]) for r in rows] == [
        (None, "none")
    ]


def test_copy_paste_import_on_an_unknown_batch_is_E1006(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(CodedError) as exc:
        svc.import_result(
            "no-such-batch", {"text": "1\tExpenses:Food\n", "index": {"1": "x"}}
        )
    assert exc.value.code == "E1006"


UPI_OCT = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"03/10/2026,UPI/DR/412345678901/FICTIVE TIFFINS/YESB/fictive.tiffins@ybl/lunch,180.00,\n"
)
UPI_NOV = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"03/11/2026,UPI/DR/512345678902/FICTIVE TIFFINS/YESB/fictive.tiffins@ybl/team dinner,640.00,\n"
    b"04/11/2026,UPI/DR/512345678903/FICTIVE TIFFINS/YESB/fictive.other@ybl/x,90.00,\n"
)


def test_a_upi_rule_learned_at_commit_categorises_next_months_payee(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, UPI_OCT, "hdfc.csv")
    svc.edit(b["batch_id"], _ids(b)[0], {"suggested_category": "Expenses:Food:Tiffin"})
    result = svc.commit(b["batch_id"])
    assert [(w["pattern"], w["category"]) for w in result["learned"]] == [
        ("UPI:FICTIVE TIFFINS|FICTIVE.TIFFINS@YBL", "Expenses:Food:Tiffin")
    ]
    # Stored marked as an identity rule, so it is never read as a substring
    # (and a substring rule starting with "UPI:" is never read as one).
    assert LearnedRules(_rules_file(tmp_path))._rules == [
        {
            "pattern": "UPI:FICTIVE TIFFINS|FICTIVE.TIFFINS@YBL",
            "category": "Expenses:Food:Tiffin",
            "match": "identity",
        }
    ]

    nov = svc.detect("hdfc.csv", UPI_NOV)["candidates"]
    assert (nov[0]["suggested_category"], nov[0]["category_source"]) == (
        "Expenses:Food:Tiffin",
        "rule",
    )
    assert nov[1]["category_source"] != "rule"  # another VPA is another payee
