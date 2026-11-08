import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.service.reports.cashflow.expense import by_tag
from backend.app.service.reports.health.ledger_view import all_postings
from tests.service.ingest.test_service import _saved_batch, _service

CSV = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"03/10/2026,FICTIVEBIKES BANGALORE,1500.00,\n"
    b"03/10/2026,FICTIVE STAYS MADIKERI,4200.00,\n"
)


def test_a_tag_set_in_review_reaches_the_tags_report(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    bid = b["batch_id"]
    for c in b["candidates"]:
        svc.edit(bid, c["id"], {"tags": ["travel/Madikeri Oct-2026"]})
    svc.commit(bid)
    tags = by_tag(all_postings(svc._ledger))
    assert tags == [
        {"tag": "travel/madikeri-oct-2026", "amount": "5700.00", "count": 2}
    ]
    text = (svc._ledger.journal_dir / "2026.beancount").read_text()
    assert "#travel/madikeri-oct-2026" in text


def test_edit_refuses_a_tag_beancount_cannot_parse_with_E1072(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CSV, "hdfc.csv")
    with pytest.raises(CodedError) as exc:
        svc.edit(b["batch_id"], b["candidates"][0]["id"], {"tags": ["café"]})
    assert exc.value.code == "E1072"
    assert svc.get_review(b["batch_id"])["candidates"][0]["tags"] == []


# --- lane A2: the edit path, end to end through the service -----------------

from decimal import Decimal  # noqa: E402

from backend.app.service.categorize.review_flags import (  # noqa: E402
    FEE_SPLIT_FLAG,
    FEE_UNLINKED_FLAG,
    UNCATEGORIZED,
    UNCATEGORIZED_FLAG,
)

SWIGGY = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    b"02/04/2026,UPI/DR/SWIGGY/518,518.00,\n"
)

BIKES = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"03/10/2026,FICTIVEBIKES BANGALORE,1500.00,\n"
    b"04/10/2026,FICTIVEBIKES BENGALURU,900.00,\n"
    b"05/10/2026,FICTIVE CAFE,240.00,\n"
)

FEES = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"03/10/2026,FICTIVEAI SAN FRANCISCO US,1700.00,\n"
    b"03/10/2026,IGST @18% ON FX MARKUP REF 1234567890,61.20,\n"
    b"03/10/2026,FX MARKUP FEE REF 1234567891,340.00,\n"
)

# Review Focus 5: an unlearnable (generic) pattern and fee rows with no charge.
UNLEARNABLE = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"03/06/2026,OFFUS EMI 03/06,2500.00,\n"
    b"03/07/2026,OFFUS EMI 07/12,2500.00,\n"
    b"10/07/2026,IGST @18% ON FX MARKUP REF 1234567890,61.20,\n"
    b"11/07/2026,IGST @18% ON FX MARKUP REF 1234567891,72.00,\n"
)


def _rows(svc, bid):
    return svc.get_review(bid)["candidates"]


def _coded(fn, *args):
    with pytest.raises(CodedError) as exc:
        fn(*args)
    return exc.value


def test_resaving_the_same_category_through_splits_is_not_a_user_set(tmp_path):
    # Review Focus 1: the browser's first save re-posts every categorised row
    # with one split on its unchanged account (plus date/narration/tags).
    svc = _service(tmp_path)
    b = _saved_batch(svc, SWIGGY, "hdfc.csv")
    bid = b["batch_id"]
    first = b["candidates"][0]
    assert [c["category_source"] for c in b["candidates"]] == ["dictionary"] * 2
    svc.edit(
        bid,
        first["id"],
        {
            "splits": [{"account": "Expenses:Food", "amount": "412.00"}],
            "date": first["date"],
            "raw_narration": first["raw_narration"],
            "source_account": first["source_account"],
            "tags": [],
        },
    )
    rows = _rows(svc, bid)
    assert [r["category_source"] for r in rows] == ["dictionary", "dictionary"]
    assert [r["suggested_category"] for r in rows] == ["Expenses:Food"] * 2
    assert svc.learning(bid)["will_learn"] == []
    assert svc.learning(bid)["not_learned"] == []


def test_a_tags_only_patch_on_a_split_row_is_not_a_user_set(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, SWIGGY, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][0]["id"]
    rows = svc._review_store.get_batch(bid)
    rows[0].splits = [
        {"account": "Expenses:Food", "amount": "300.00"},
        {"account": "Expenses:Travel", "amount": "112.00"},
    ]
    svc.edit(bid, cid, {"tags": ["trip"]})
    assert _rows(svc, bid)[0]["category_source"] == "dictionary"


def test_a_browser_splits_change_is_a_user_set_and_syncs_the_category(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][0]["id"]
    svc.edit(
        bid,
        cid,
        {
            "splits": [
                {"account": "Expenses:Food", "amount": "500.00"},
                {"account": "Expenses:Travel", "amount": "1000.00"},
            ]
        },
    )
    rows = _rows(svc, bid)
    assert rows[0]["category_source"] == "user"
    assert rows[0]["suggested_category"] == "Expenses:Travel"  # the largest split
    # ... and it spread to the same merchant, as a category, not as splits.
    assert rows[1]["category_source"] == "taught_batch"
    assert rows[1]["suggested_category"] == "Expenses:Travel"
    assert rows[1]["splits"] == []
    assert UNCATEGORIZED_FLAG not in rows[1]["flags"]
    assert rows[2]["category_source"] == "none"


def test_spread_never_overrides_a_row_set_in_review(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    bid = b["batch_id"]
    a, second = b["candidates"][0]["id"], b["candidates"][1]["id"]
    svc.edit(bid, second, {"suggested_category": "Expenses:Travel:Bike"})
    svc.edit(bid, a, {"suggested_category": "Expenses:Travel"})
    rows = _rows(svc, bid)
    assert (rows[1]["suggested_category"], rows[1]["category_source"]) == (
        "Expenses:Travel:Bike",
        "user",
    )


def test_category_edits_never_touch_amounts_or_the_card_side(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    before = [
        (c["amount"], c["source_account"], c["direction"]) for c in b["candidates"]
    ]
    svc.edit(
        b["batch_id"],
        b["candidates"][0]["id"],
        {"suggested_category": "Expenses:Travel"},
    )
    after = [
        (c["amount"], c["source_account"], c["direction"])
        for c in _rows(svc, b["batch_id"])
    ]
    assert after == before


def test_tags_never_change_the_card_side_or_the_amounts_in_the_journal(tmp_path):
    # Q17(b): the committed postings are the same with and without tags.
    def postings(root, tag: bool):
        svc = _service(root)
        b = _saved_batch(svc, BIKES, "hdfc.csv")
        if tag:
            for c in b["candidates"]:
                svc.edit(b["batch_id"], c["id"], {"tags": ["travel/madikeri-oct-2026"]})
        svc.commit(b["batch_id"])
        return sorted(
            (str(p.date), p.account, p.amount) for p in all_postings(svc._ledger)
        )

    plain = postings(tmp_path / "plain", tag=False)
    tagged = postings(tmp_path / "tagged", tag=True)
    assert plain == tagged
    assert ("2026-10-03", "Assets:Bank:HDFC", Decimal("-1500.00")) in tagged


def test_a_journal_with_posting_meta_tags_still_loads_and_survives_a_commit(tmp_path):
    # Q17(b): journals written before T2 carry posting meta `tags: "a,b"`.
    svc = _service(tmp_path)
    year = svc._ledger.journal_dir / "2026.beancount"
    svc._ledger.open_named_accounts(["Assets:Bank:HDFC"])
    legacy = (
        '2026-09-01 * "FICTIVE OLD SHOP"\n'
        '  txn-id: "legacy-1"\n'
        "  Expenses:Food  100.00 INR\n"
        '    tags: "trip,food"\n'
        "  Assets:Bank:HDFC  -100.00 INR\n"
    )
    svc._ledger._ensure_include(2026)
    year.write_text(legacy, encoding="utf-8")
    assert svc._ledger.validate() == []
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    svc.commit(b["batch_id"])
    assert svc._ledger.validate() == []
    text = year.read_text(encoding="utf-8")
    assert legacy in text  # the old entry is kept verbatim
    (old,) = [p for p in all_postings(svc._ledger) if p.meta.get("tags")]
    assert (old.account, old.meta["tags"]) == ("Expenses:Food", "trip,food")


def test_edit_refuses_an_illegal_account_name_with_E1017(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    err = _coded(
        svc.edit,
        b["batch_id"],
        b["candidates"][0]["id"],
        {"suggested_category": "expenses travel"},
    )
    assert (err.code, err.status) == ("E1017", 422)
    assert _rows(svc, b["batch_id"])[0]["suggested_category"] is None


def test_a_refused_patch_changes_nothing_even_with_several_keys(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][0]["id"]
    before = _rows(svc, bid)
    for patch in (
        {"suggested_category": "Expenses:Travel", "tags": ["café"]},
        {"suggested_category": "Expenses:Travel", "learn": "no"},
        {"tags": ["trip"], "suggested_category": "expenses travel"},
        {"suggested_category": "Expenses:Travel", "amount": "not a number"},
    ):
        with pytest.raises((CodedError, ValueError)):
            svc.edit(bid, cid, dict(patch))
        assert _rows(svc, bid) == before, patch


def test_edit_refuses_category_source_from_a_client(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][0]["id"]
    err = _coded(svc.edit, bid, cid, {"category_source": "rule"})
    assert (err.code, err.status) == ("E1037", 422)
    assert "category_source is set by the app, not by an edit" in str(err)
    assert _rows(svc, bid)[0]["category_source"] == "none"


def test_follows_accepts_only_null(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, FEES, "hdfc.csv")
    bid = b["batch_id"]
    rows = _rows(svc, bid)
    charge, igst = rows[0]["id"], rows[1]["id"]
    err = _coded(svc.edit, bid, rows[2]["id"], {"follows": igst})
    assert (err.code, err.status) == ("E1037", 422)
    assert (
        "follows can only be cleared (null); set a category to override the link"
        in str(err)
    )
    assert _rows(svc, bid)[2]["follows"] == charge


def test_unlinking_a_fee_row_with_follows_null_leaves_it_flagged(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, FEES, "hdfc.csv")
    bid = b["batch_id"]
    rows = _rows(svc, bid)
    svc.edit(bid, rows[0]["id"], {"suggested_category": "Expenses:Subscriptions"})
    svc.edit(bid, rows[1]["id"], {"follows": None})
    igst = _rows(svc, bid)[1]
    assert igst["follows"] is None
    assert igst["suggested_category"] is None
    assert igst["category_source"] == "none"
    assert FEE_UNLINKED_FLAG in igst["flags"]
    # The other fee row still follows its charge.
    assert _rows(svc, bid)[2]["suggested_category"] == "Expenses:Subscriptions"


def test_follows_null_on_a_row_that_follows_nothing_changes_nothing(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, SWIGGY, "hdfc.csv")
    bid = b["batch_id"]
    before = _rows(svc, bid)
    # Q28: a no-op, with no change and no error.
    assert svc.edit(bid, before[0]["id"], {"follows": None}) == {"ok": True}
    assert _rows(svc, bid) == before


@pytest.mark.parametrize(
    "bad", ["travel", 7, None, {"a": 1}, ["trip", 3], [None]], ids=repr
)
def test_tags_that_are_not_a_list_of_strings_are_E1037(tmp_path, bad):
    # Q17: never split a bare string into characters, never a 500.
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][0]["id"]
    err = _coded(svc.edit, bid, cid, {"tags": bad})
    assert (err.code, err.status) == ("E1037", 422)
    assert "tags must be a list of strings" in str(err)
    assert _rows(svc, bid)[0]["tags"] == []


def test_typed_tags_are_normalised_before_they_are_stored(tmp_path):
    # Review Focus 3, through the service.
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][0]["id"]
    svc.edit(bid, cid, {"tags": ["travel/Madikeri Oct-2026", "#Trip", "TRIP"]})
    assert _rows(svc, bid)[0]["tags"] == ["travel/madikeri-oct-2026", "trip"]


@pytest.mark.parametrize("bad", ["yes", 1, None], ids=repr)
def test_learn_must_be_a_boolean(tmp_path, bad):
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    err = _coded(svc.edit, b["batch_id"], b["candidates"][0]["id"], {"learn": bad})
    assert (err.code, err.status) == ("E1037", 422)
    assert "learn must be true or false" in str(err)


@pytest.mark.parametrize("bad", [5, ["Expenses:Travel"], {"a": "b"}], ids=repr)
def test_a_non_string_category_is_E1037(tmp_path, bad):
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    err = _coded(
        svc.edit, b["batch_id"], b["candidates"][0]["id"], {"suggested_category": bad}
    )
    assert (err.code, err.status) == ("E1037", 422)
    assert "suggested_category must be an account name or null" in str(err)


def test_edit_on_an_unknown_batch_or_candidate_is_404(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    err = _coded(svc.edit, "no-such-batch", "x", {"tags": ["trip"]})
    assert (err.code, err.status) == ("E1006", 404)
    err = _coded(
        svc.edit,
        b["batch_id"],
        "no-such-row",
        {"suggested_category": "Expenses:Travel"},
    )
    assert (err.code, err.status) == ("E1007", 404)


def test_remember_is_accepted_and_ignored(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    svc.edit(
        b["batch_id"],
        b["candidates"][0]["id"],
        {"suggested_category": "Expenses:Travel", "remember": True},
    )
    assert not (tmp_path / "storage" / "rules" / "learned.yaml").exists()
    assert "remember" not in _rows(svc, b["batch_id"])[0]


def test_setting_a_row_back_to_uncategorized_is_a_user_change(tmp_path):
    # Q22: the browser sends Expenses:Uncategorized when a categorised row is
    # moved back. It sticks, re-flags, never spreads and is never learned.
    svc = _service(tmp_path)
    b = _saved_batch(svc, SWIGGY, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][0]["id"]
    svc.edit(bid, cid, {"suggested_category": UNCATEGORIZED})
    rows = _rows(svc, bid)
    assert rows[0]["category_source"] == "user"
    assert rows[0]["suggested_category"] == UNCATEGORIZED
    assert rows[0]["flags"].count(UNCATEGORIZED_FLAG) == 1
    # No spread: the other SWIGGY row keeps its dictionary category.
    assert (rows[1]["suggested_category"], rows[1]["category_source"]) == (
        "Expenses:Food",
        "dictionary",
    )
    lr = svc.learning(bid)
    assert lr["will_learn"] == [] and lr["not_learned"] == []
    result = svc.commit(bid)
    assert result["learned"] == []
    assert result["uncategorized"] == 1
    assert not (tmp_path / "storage" / "rules" / "learned.yaml").exists()


def test_fee_row_takes_a_category_set_on_its_charge_later(tmp_path):
    # Review Focus 2.
    svc = _service(tmp_path)
    b = _saved_batch(svc, FEES, "hdfc.csv")
    bid = b["batch_id"]
    rows = _rows(svc, bid)
    assert rows[1]["follows"] == rows[0]["id"] and rows[2]["follows"] == rows[0]["id"]
    assert rows[1]["suggested_category"] is None
    assert UNCATEGORIZED_FLAG in rows[1]["flags"]
    svc.edit(bid, rows[0]["id"], {"suggested_category": "Expenses:Subscriptions"})
    rows = _rows(svc, bid)
    assert [r["suggested_category"] for r in rows] == ["Expenses:Subscriptions"] * 3
    assert [r["category_source"] for r in rows] == [
        "user",
        "fee_follows",
        "fee_follows",
    ]
    assert all(UNCATEGORIZED_FLAG not in r["flags"] for r in rows)
    # A later split of the charge is followed too, through its largest split.
    svc.edit(
        bid,
        rows[0]["id"],
        {
            "splits": [
                {"account": "Expenses:Software", "amount": "1200.00"},
                {"account": "Expenses:Subscriptions", "amount": "500.00"},
            ]
        },
    )
    rows = _rows(svc, bid)
    assert [r["suggested_category"] for r in rows[1:]] == ["Expenses:Software"] * 2
    assert all(FEE_SPLIT_FLAG in r["flags"] for r in rows[1:])


def test_setting_a_fee_rows_category_breaks_its_link(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, FEES, "hdfc.csv")
    bid = b["batch_id"]
    rows = _rows(svc, bid)
    svc.edit(bid, rows[1]["id"], {"suggested_category": "Expenses:Tax"})
    svc.edit(bid, rows[0]["id"], {"suggested_category": "Expenses:Subscriptions"})
    rows = _rows(svc, bid)
    assert rows[1]["follows"] is None and rows[1]["category_source"] == "user"
    assert rows[1]["suggested_category"] == "Expenses:Tax"
    # Setting the fee row did not spread to the other fee row (Review Focus 5).
    assert rows[2]["follows"] == rows[0]["id"]
    assert rows[2]["suggested_category"] == "Expenses:Subscriptions"


def test_rejecting_the_charge_unlinks_its_fee_rows(tmp_path):
    # Review Focus 2.
    svc = _service(tmp_path)
    b = _saved_batch(svc, FEES, "hdfc.csv")
    bid = b["batch_id"]
    rows = _rows(svc, bid)
    svc.edit(bid, rows[0]["id"], {"suggested_category": "Expenses:Subscriptions"})
    svc.reject(bid, rows[0]["id"])
    rows = _rows(svc, bid)
    assert len(rows) == 2
    for r in rows:
        assert r["follows"] is None
        assert r["suggested_category"] is None
        assert r["flags"].count(FEE_UNLINKED_FLAG) == 1
        assert UNCATEGORIZED_FLAG not in r["flags"]


def test_an_unlearnable_or_fee_row_does_not_spread(tmp_path):
    # Review Focus 5, end to end: one OFFUS EMI row (washing machine) and one
    # IGST row set by hand leave every other EMI and IGST row alone.
    svc = _service(tmp_path)
    b = _saved_batch(svc, UNLEARNABLE, "hdfc.csv")
    bid = b["batch_id"]
    rows = _rows(svc, bid)
    svc.edit(
        bid, rows[0]["id"], {"suggested_category": "Expenses:Household:Appliances"}
    )
    svc.edit(bid, rows[2]["id"], {"suggested_category": "Expenses:Subscriptions"})
    rows = _rows(svc, bid)
    assert [r["suggested_category"] for r in rows] == [
        "Expenses:Household:Appliances",
        None,
        "Expenses:Subscriptions",
        None,
    ]
    assert [r["category_source"] for r in rows] == ["user", "none", "user", "none"]
    assert FEE_UNLINKED_FLAG in rows[3]["flags"]


def test_a_fee_row_with_merchant_words_does_not_spread_or_teach(tmp_path):
    # Review Focus 5: "FICTIVEHOTEL DCC FEE" has a learnable pattern, so only
    # the fee guard stops one hand-set fee row recategorising its twin fee
    # rows, and stops it being learned.
    svc = _service(tmp_path)
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"10/07/2026,FICTIVEHOTEL DCC FEE,61.20,\n"
        b"11/07/2026,FICTIVEHOTEL DCC FEE,72.00,\n"
    )
    b = _saved_batch(svc, csv, "hdfc.csv")
    bid = b["batch_id"]
    rows = _rows(svc, bid)
    svc.edit(bid, rows[0]["id"], {"suggested_category": "Expenses:Travel"})
    rows = _rows(svc, bid)
    assert [r["suggested_category"] for r in rows] == ["Expenses:Travel", None]
    assert rows[1]["category_source"] == "none"
    assert FEE_UNLINKED_FLAG in rows[1]["flags"]
    lr = svc.learning(bid)
    assert lr["will_learn"] == []
    assert [n["reason"] for n in lr["not_learned"]] == ["fee row"]


# Q30: "FICTIVEHOTEL @18% IGST" is a fee row whose learn pattern, FICTIVEHOTEL,
# is the merchant row's. Each of batch_teach's two fee guards has a case only
# it stops, so neither is redundant.
HOTEL = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"10/07/2026,FICTIVEHOTEL @18% IGST,90.00,\n"
    b"12/07/2026,FICTIVEHOTEL BANGALORE,500.00,\n"
)


def test_a_fee_row_set_in_review_never_spreads_to_its_merchant(tmp_path):
    # The source guard: a hand-set fee row must not recategorise the merchant.
    svc = _service(tmp_path)
    b = _saved_batch(svc, HOTEL, "hdfc.csv")
    bid = b["batch_id"]
    fee, merchant = _rows(svc, bid)
    assert fee["follows"] is None  # no same-date charge beside it
    svc.edit(bid, fee["id"], {"suggested_category": "Expenses:Tax"})
    merchant = _rows(svc, bid)[1]
    assert (merchant["suggested_category"], merchant["category_source"]) == (
        None,
        "none",
    )


def test_a_merchant_set_in_review_never_spreads_to_a_fee_row(tmp_path):
    # The target guard: setting the merchant must not recategorise its fee row.
    svc = _service(tmp_path)
    b = _saved_batch(svc, HOTEL, "hdfc.csv")
    bid = b["batch_id"]
    fee, merchant = _rows(svc, bid)
    svc.edit(bid, merchant["id"], {"suggested_category": "Expenses:Travel"})
    fee = _rows(svc, bid)[0]
    assert (fee["suggested_category"], fee["category_source"]) == (None, "none")
    assert FEE_UNLINKED_FLAG in fee["flags"]


@pytest.mark.parametrize(
    "bad", ["Expenses:Travel\n", "Expenses:Travel ", " Expenses:Travel"], ids=repr
)
def test_an_account_with_surrounding_whitespace_is_E1017(tmp_path, bad):
    # I2: the pattern ends in `$`, which `re.match` lets sit before a final
    # "\n"; such a name would reach the preview and break the commit.
    svc = _service(tmp_path)
    b = _saved_batch(svc, BIKES, "hdfc.csv")
    bid = b["batch_id"]
    err = _coded(svc.edit, bid, b["candidates"][0]["id"], {"suggested_category": bad})
    assert (err.code, err.status) == ("E1017", 422)
    assert _rows(svc, bid)[0]["suggested_category"] is None
    assert svc.learning(bid)["would_open"] == ["Assets:Bank:HDFC"]


def test_a_fee_row_follows_a_charge_the_dictionary_categorised_at_staging(tmp_path):
    # M1: the refresh at the end of `_postprocess` gives a fee row its
    # charge's category before any edit.
    svc = _service(tmp_path)
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        b"06/10/2026,UPI/DR/SWIGGY/412,412.00,\n"
        b"06/10/2026,IGST @18% REF 1234567890,9.00,\n"
    )
    b = _saved_batch(svc, csv, "hdfc.csv")
    charge, fee = b["candidates"]
    assert charge["category_source"] == "dictionary"
    assert fee["follows"] == charge["id"]
    assert (fee["suggested_category"], fee["category_source"]) == (
        "Expenses:Food",
        "fee_follows",
    )
    assert UNCATEGORIZED_FLAG not in fee["flags"]


# --- C1: a category set after saved splits replaces them ---------------------

CAFE_GYM = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
    b"05/10/2026,FICTIVE CAFE,240.00,\n"
    b"06/10/2026,FICTIVEGYM CLUB,999.00,\n"
)


def _journal_accounts(svc, narration):
    return sorted(
        p.account
        for p in all_postings(svc._ledger)
        if narration in (p.narration or "") and p.account.startswith("Expenses:")
    )


def _split(account, amount):
    return [{"account": account, "amount": amount}]


def test_uncategorized_after_saved_splits_books_uncategorized(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CAFE_GYM, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][0]["id"]
    svc.edit(bid, cid, {"splits": _split("Expenses:Food:Cafe", "240.00")})
    svc.edit(bid, cid, {"suggested_category": UNCATEGORIZED})
    assert _rows(svc, bid)[0]["splits"] == []
    result = svc.commit(bid)
    assert result["uncategorized"] == 2  # the cafe row and the untouched gym row
    assert _journal_accounts(svc, "FICTIVE CAFE") == [UNCATEGORIZED]


def test_a_category_after_saved_splits_books_it_and_learns_it(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CAFE_GYM, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][1]["id"]
    svc.edit(bid, cid, {"splits": _split("Expenses:Health", "999.00")})
    svc.edit(bid, cid, {"suggested_category": "Expenses:Fitness"})
    result = svc.commit(bid)
    assert _journal_accounts(svc, "FICTIVEGYM") == ["Expenses:Fitness"]
    assert [(w["pattern"], w["category"]) for w in result["learned"]] == [
        ("FICTIVEGYM CLUB", "Expenses:Fitness")
    ]


def test_import_result_after_saved_splits_books_the_pasted_category(tmp_path):
    svc = _service(tmp_path)
    b = _saved_batch(svc, CAFE_GYM, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][1]["id"]
    svc.edit(bid, cid, {"splits": _split("Expenses:Health", "999.00")})
    svc.import_result(bid, {"text": "1\tExpenses:Fitness", "index": {"1": cid}})
    svc.commit(bid)
    assert _journal_accounts(svc, "FICTIVEGYM") == ["Expenses:Fitness"]


def test_a_generated_finance_row_is_never_learned_through_the_service(tmp_path):
    from backend.app.service.ingest.validation.pdf import (
        FINANCE_NARRATION,
        GENERATED_FLAG,
    )
    from backend.app.service.ingest.pipeline.normalize import normalize_narration

    svc = _service(tmp_path)
    b = _saved_batch(svc, CAFE_GYM, "hdfc.csv")
    bid, cid = b["batch_id"], b["candidates"][0]["id"]
    row = svc._review_store.get_batch(bid)[0]
    row.raw_narration = FINANCE_NARRATION
    row.normalized_narration = normalize_narration(FINANCE_NARRATION)
    row.flags = [GENERATED_FLAG]
    svc.edit(bid, cid, {"suggested_category": "Expenses:Bank:Finance"})
    lr = svc.learning(bid)
    assert lr["will_learn"] == [] and lr["not_learned"] == []
    assert svc.commit(bid)["learned"] == []
