from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.core.paths import REPO_ROOT
from backend.app.service.categorize.categorizer import Categorizer
from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.core.errors.errors import CodedError
from backend.app.persistence.review_batches import ReviewStore
from backend.app.persistence.templates import TemplateStore
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.service import ImportService

_FIXTURES = REPO_ROOT / "tests" / "fixtures"
_MASTER = _FIXTURES / "scheme_master.csv"
_ORDERS = _FIXTURES / "statements" / "groww_orders.csv"


def _service(tmp_path, nav_source=None) -> ImportService:
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    categorizer = Categorizer(
        MerchantDictionary.from_seed(),
        LearnedRules(ledger.storage_dir / "rules" / "learned.yaml"),
    )
    investment_ledger = Ledger(
        ledger.storage_dir,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    investment_ledger.bootstrap(seed_accounts=())
    return ImportService(
        ledger=ledger,
        investment_ledger=investment_ledger,
        categorizer=categorizer,
        review_store=ReviewStore(),
        template_store=TemplateStore(ledger.storage_dir / "templates"),
        scheme_master_path=_MASTER,
        nav_source=nav_source,
    )


def test_detect_routes_groww_file_to_commodity_candidates(tmp_path):
    svc = _service(tmp_path)
    result = svc.detect("groww_orders.csv", _ORDERS.read_bytes())
    assert result["matched"] is True
    assert result["source"] == "groww"
    cands = result["candidates"]
    assert len(cands) == 4
    purchase = cands[0]
    assert purchase["commodity"] == "MF122639"
    assert purchase["units"] == "100.0"


def test_detect_flags_unresolved_scheme_for_review(tmp_path):
    svc = _service(tmp_path)
    result = svc.detect("groww_orders.csv", _ORDERS.read_bytes())
    mystery = result["candidates"][3]
    assert mystery["commodity"] is None
    assert mystery["unresolved_scheme"]
    assert mystery["needs_account"] is False  # scheme, not account, is what's missing


def test_commit_blocks_while_a_scheme_is_unresolved(tmp_path):
    svc = _service(tmp_path)
    result = svc.detect("groww_orders.csv", _ORDERS.read_bytes())
    with pytest.raises(CodedError) as ei:
        svc.commit(result["batch_id"])
    assert ei.value.code == "E1012"
    assert ei.value.status == 422


def test_resolve_scheme_then_commit_succeeds(tmp_path):
    svc = _service(tmp_path)
    result = svc.detect("groww_orders.csv", _ORDERS.read_bytes())
    bid = result["batch_id"]
    mystery_id = result["candidates"][3]["id"]
    svc.resolve_scheme(
        bid, mystery_id, commodity="MF120716", scheme_name="UTI Nifty 50"
    )
    committed = svc.commit(bid)
    # 4 rows, none deduped -> all 4 commit
    assert committed["committed"] == 4
    assert svc._ledger.validate() == []


def test_covered_redeem_in_batch_is_not_flagged_uncovered(tmp_path):
    # The fixture's REDEEM (50 units) is preceded by a PURCHASE (100 units) of the
    # same scheme, so it must NOT be flagged as uncovered (no false positive).
    svc = _service(tmp_path)
    result = svc.detect("groww_orders.csv", _ORDERS.read_bytes())
    redeem = next(c for c in result["candidates"] if "Redeem" in c["raw_narration"])
    assert redeem["uncovered_redemption"] is False


def test_uncovered_redeem_flags_and_blocks_commit_with_coded_error(tmp_path):
    from datetime import date
    from decimal import Decimal

    from backend.app.models.domain.candidate import CandidateTxn, Direction

    svc = _service(tmp_path)
    # A redemption with no covering purchase anywhere (empty ledger, lone batch).
    redeem = CandidateTxn(
        date=date(2026, 3, 25),
        amount=Decimal("12000.00"),
        raw_narration="Redeem — Parag Parikh Flexi Cap",
        normalized_narration="Parag Parikh Flexi Cap",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.CREDIT,
        units=Decimal("50.0"),
        commodity="MF122639",
        unit_cost=Decimal("240.0"),
    )
    batch_id = svc._review_store.add_batch([redeem])
    with pytest.raises(CodedError) as ei:
        svc.commit(batch_id)
    assert ei.value.code == "E1013"
    assert ei.value.status == 422


def test_bank_csv_still_routes_to_template_path(tmp_path):
    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    result = svc.detect("hdfc.csv", csv)
    assert result["matched"] is False
    assert "source" not in result or result.get("source") != "groww"


# --- committing with uncategorized rows -------------------------------------
#
# The backend has never refused an uncategorized row: commit() gates on
# `needs_account` and `unresolved_scheme` only. The COMMIT BUTTON gated on it,
# and with 96% of a real bank statement arriving uncategorized that meant ~250
# hand-classifications before a single statement could land. The journal is
# six weeks stale as a direct result.
#
# These pin the backend contract the UI now relies on: uncategorized commits,
# lands in Expenses:Uncategorized, validates, and is COUNTED so the result can
# say what still needs attention.


def test_commit_accepts_rows_with_no_category_and_the_journal_validates(tmp_path):
    from datetime import date as _date
    from decimal import Decimal as _D

    from backend.app.models.domain.candidate import CandidateTxn, Direction

    svc = _service(tmp_path)
    batch_id = svc._review_store.add_batch(
        [
            CandidateTxn(
                date=_date(2026, 3, 5),
                amount=_D("420.50"),
                raw_narration="SWIGGY BANGALORE",
                normalized_narration="SWIGGY",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
            )
        ]
    )
    out = svc.commit(batch_id)

    assert out["committed"] == 1
    assert svc._ledger.validate() == []
    text = (svc._ledger.journal_dir / "2026.beancount").read_text(encoding="utf-8")
    assert "Expenses:Uncategorized" in text


def test_commit_reports_how_many_rows_landed_uncategorized(tmp_path):
    from datetime import date as _date
    from decimal import Decimal as _D

    from backend.app.models.domain.candidate import CandidateTxn, Direction

    svc = _service(tmp_path)
    rows = [
        CandidateTxn(
            date=_date(2026, 3, 5),
            amount=_D("420.50"),
            raw_narration="SWIGGY BANGALORE",
            normalized_narration="SWIGGY",
            source_account="Assets:Checking:HDFC",
            direction=Direction.DEBIT,
        ),
        CandidateTxn(
            date=_date(2026, 3, 6),
            amount=_D("100.00"),
            raw_narration="KNOWN MERCHANT",
            normalized_narration="KNOWN",
            source_account="Assets:Checking:HDFC",
            direction=Direction.DEBIT,
            suggested_category="Expenses:Food",
        ),
    ]
    out = svc.commit(svc._review_store.add_batch(rows))

    # Not a gate -- a statement of what still needs attention, so the UI can
    # show a backlog instead of refusing the import.
    assert out["committed"] == 2
    assert out["uncategorized"] == 1


# --- unexplained charges, surfaced at PREVIEW time -------------------------
#
# The commit path books an over-tolerance remainder to
# Expenses:Investment:Charges:Unexplained and flags the row instead of
# refusing (P5 Task 2). That is only safe if the user is warned BEFORE the
# write, which is what `detect` now computes. The off-by-40 row below is
# deliberate: 40 sits BETWEEN the precision band for 100.0 units @ 215.0
# (100*0.1 + 215*0.1 = 31.5) and the legacy fallback band (50), so the row
# only surfaces while the precision-derived tolerance is the one in force.
_UNEXPLAINED_ORDERS = b"""Personal Details
Name,Jane Doe

TRANSACTIONS FROM Apr 01 2025 TO Mar 31 2026

Scheme Name,Transaction Type,Units,NAV,Amount,Date
Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PURCHASE,100.0,215.0,21500,18 Mar 2026
Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PURCHASE,100.0,215.0,21540,19 Mar 2026
Parag Parikh Flexi Cap Fund - Direct Plan - Growth,REDEEM,50.0,240.0,12000,25 Mar 2026
Parag Parikh Flexi Cap Fund - Direct Plan - Growth,DIVIDEND,0,0,120,22 Mar 2026
"""


def _unexplained_rows(svc) -> tuple[dict, dict, str]:
    """detect() the fixture above and return (clean_row, flagged_row, batch_id)
    as the serialized payload the review screen actually receives."""
    result = svc.detect("groww_unexplained.csv", _UNEXPLAINED_ORDERS)
    assert result["source"] == "groww"
    purchases = [c for c in result["candidates"] if c["direction"] == "debit"]
    assert len(purchases) == 2
    clean = next(c for c in purchases if c["amount"] == "21500")
    flagged = next(c for c in purchases if c["amount"] == "21540")
    return clean, flagged, result["batch_id"]


def test_detect_flags_an_unexplained_charge_before_the_batch_is_stored(tmp_path):
    svc = _service(tmp_path)
    _clean, flagged, batch_id = _unexplained_rows(svc)

    # The point of the whole task: the flag is TRUE in the preview payload,
    # i.e. before anything is written to either journal.
    assert flagged["unexplained_charge"] is True
    # ...and it carries how much, so review can name the money. 40 reported
    # minus the 1.08 stamp duty the rate does explain.
    assert flagged["unexplained_charge_amount"] == "38.92"
    assert svc._ledger.validate() == []

    # Same answer through GET /api/review/{batch} -- the screen reloads the
    # batch from here, not only from the detect response.
    reloaded = svc.get_review(batch_id)["candidates"]
    row = next(c for c in reloaded if c["id"] == flagged["id"])
    assert row["unexplained_charge"] is True
    assert row["unexplained_charge_amount"] == "38.92"


def test_a_fully_explained_row_is_not_flagged_unexplained(tmp_path):
    # The discriminating half: a flag that is always set is not a flag. This
    # row's cash equals units*NAV exactly.
    svc = _service(tmp_path)
    clean, _flagged, _batch_id = _unexplained_rows(svc)

    assert clean["unexplained_charge"] is False
    assert clean["unexplained_charge_amount"] is None


def test_preview_and_commit_agree_on_the_unexplained_flag(tmp_path):
    """A preview that says "fine" where the commit flags (or the reverse) is
    worse than no preview at all. Assert the two answers are EQUAL per row --
    including the REDEEM and DIVIDEND rows, which compute no charges on either
    path."""
    svc = _service(tmp_path)
    result = svc.detect("groww_unexplained.csv", _UNEXPLAINED_ORDERS)
    batch_id = result["batch_id"]
    rows = svc._review_store.get_batch(batch_id)
    preview = {c.id: c.unexplained_charge for c in rows}
    assert any(preview.values()) and not all(preview.values())

    # Clear the preview answer off the live objects, so what we compare after
    # the commit is what COMMIT decided -- not the preview's own leftovers.
    for c in rows:
        c.unexplained_charge = False
        c.unexplained_charge_amount = None

    svc.commit(batch_id)

    assert {c.id: c.unexplained_charge for c in rows} == preview
    assert svc._ledger.validate() == []
    assert svc._investment_ledger.validate() == []


def test_commit_recomputes_the_flag_after_a_review_patch(tmp_path):
    """`ReviewStore.update` setattrs arbitrary keys, so the review-patch route
    can change the very numbers the preview answer was derived from -- or
    clear the answer itself. `commit()` therefore recomputes, exactly as it
    recomputes `flag_restated_orders` and `uncovered_redemptions` and for the
    reason stated there."""
    from decimal import Decimal

    svc = _service(tmp_path)
    result = svc.detect("groww_unexplained.csv", _UNEXPLAINED_ORDERS)
    batch_id = result["batch_id"]
    rows = {c.id: c for c in svc._review_store.get_batch(batch_id)}
    clean = next(c for c in rows.values() if c.amount == Decimal("21500"))
    flagged = next(c for c in rows.values() if c.amount == Decimal("21540"))
    assert clean.unexplained_charge is False
    assert flagged.unexplained_charge is True

    # (a) Patch the clean row's amount PAST the band: the preview said clean,
    # the new numbers are not. 21500 -> 21560 is a 60 gap on a 31.5 band.
    svc.edit(batch_id, clean.id, {"amount": "21560"})
    # (b) Patch the flagged row's amount back INSIDE the band, and clear the
    # flag the way the review-patch route lets a client clear it. Both halves
    # must agree afterwards: nothing is booked to the unexplained account for
    # this row, so nothing may be reported for it either.
    svc.edit(batch_id, flagged.id, {"amount": "21500", "unexplained_charge": False})

    out = svc.commit(batch_id)

    assert clean.unexplained_charge is True, "stale clean answer survived the patch"
    # Only the recompute can set the AMOUNT: the commit-time sites in
    # _commodity_entry/_vehicle_entry flag the row while writing the leg, so
    # they are too late to say how much, and they never clear a flag.
    assert clean.unexplained_charge_amount == Decimal("58.92")
    # The clearing direction, which no other code path covers: a row the
    # preview flagged and the edit fixed must come out clean, not carry a
    # stale figure into the response.
    assert flagged.unexplained_charge is False
    assert flagged.unexplained_charge_amount is None
    assert out["unexplained"] == 1
    assert svc._ledger.validate() == []
    assert svc._investment_ledger.validate() == []


def test_commit_reports_how_many_rows_it_could_not_fully_explain(tmp_path):
    """Counted and RETURNED, parallel to `uncategorized`. Until this existed
    the commit-time flag had no production consumer at all -- `commit()`
    cleared the batch and returned only {committed, uncategorized} -- while
    three docstrings described it as a real second signal."""
    svc = _service(tmp_path)
    result = svc.detect("groww_unexplained.csv", _UNEXPLAINED_ORDERS)

    out = svc.commit(result["batch_id"])

    # Exactly the one 21540 purchase: the 21500 purchase, the REDEEM (default
    # stt_rate_sell is 0, so no charges are computed) and the DIVIDEND are not.
    assert out["unexplained"] == 1
    assert out["committed"] == 4


# --- snapped MF purchase cash does not touch dedup -------------------------
#
# Fictional orders. The first two export a truncated Amount and are booked at
# the round cash actually paid (15,000 and 93,000); the third (12,345.67) has
# no round gross within a rupee and is booked as exported.
_SNAP_ORDERS = b"""Personal Details
Name,Jane Doe

TRANSACTIONS FROM Apr 01 2025 TO Mar 31 2026

Scheme Name,Transaction Type,Units,NAV,Amount,Date
Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PURCHASE,512.000,29.2945,14999,05 May 2025
Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PURCHASE,1860.000,49.9975,92995,06 May 2025
Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PURCHASE,100.000,123.4500,12345.67,07 May 2025
"""


def test_reimporting_snapped_purchases_is_all_exact_duplicates(tmp_path):
    """The snap lives only where the entry is built. `candidate.amount` stays
    the export Amount, and re-importing the same file finds every order."""
    svc = _service(tmp_path)
    first = svc.detect("groww_snap.csv", _SNAP_ORDERS)
    assert [c["amount"] for c in first["candidates"]] == ["14999", "92995", "12345.67"]
    assert all(c["unexplained_charge"] is False for c in first["candidates"])
    out = svc.commit(first["batch_id"])
    assert out["committed"] == 3
    assert out["unexplained"] == 0
    assert svc._ledger.validate() == []
    assert svc._investment_ledger.validate() == []

    # The snap really is in force on this path: the Groww cash leg is 15,000.
    cash = sorted(
        p.units.number
        for e in svc._ledger.load().entries
        for p in getattr(e, "postings", [])
        if p.account == "Assets:Broker:Groww"
    )
    assert cash == [Decimal("-93000.00"), Decimal("-15000.00"), Decimal("-12345.67")]

    again = svc.detect("groww_snap.csv", _SNAP_ORDERS)
    assert [c["dedup_status"] for c in again["candidates"]] == ["exact_dup"] * 3
    assert [c["amount"] for c in again["candidates"]] == ["14999", "92995", "12345.67"]


# 14,999 exported against 500.000 * 29.9960 = 14,998.00. Unsnapped the gap is
# 1.00 (stamp 0.75 + rounding 0.25) and the row is clean; snapped to 15,000 the
# gap is 2.00 and 1.25 of it is over the 1-rupee band. So a review step that
# does not snap would preview this row as clean while commit books 1.25 to
# Expenses:Investment:Charges:Unexplained.
_SNAP_FLAGGED_ORDERS = b"""Personal Details
Name,Jane Doe

TRANSACTIONS FROM Apr 01 2025 TO Mar 31 2026

Scheme Name,Transaction Type,Units,NAV,Amount,Date
Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PURCHASE,500.000,29.9960,14999,05 May 2025
"""


def test_detect_previews_a_snapped_row_as_commit_will_book_it(tmp_path):
    """The detect-time call to `unexplained_charge_of` must snap, or the
    preview disagrees with the journal."""
    svc = _service(tmp_path)
    result = svc.detect("groww_snap_flagged.csv", _SNAP_FLAGGED_ORDERS)
    (row,) = result["candidates"]
    assert row["unexplained_charge"] is True
    assert Decimal(row["unexplained_charge_amount"]) == Decimal("1.25")


def test_commit_recomputes_a_snapped_row_as_it_books_it(tmp_path):
    """The commit-time recompute must snap too. The write path flags the row
    itself, so the count alone cannot tell; only the recompute sets the
    AMOUNT, which is what pins this call site."""
    svc = _service(tmp_path)
    result = svc.detect("groww_snap_flagged.csv", _SNAP_FLAGGED_ORDERS)
    batch_id = result["batch_id"]
    (row,) = svc._review_store.get_batch(batch_id)
    # Clear the preview's answer so what we read back is commit's own.
    row.unexplained_charge = False
    row.unexplained_charge_amount = None

    out = svc.commit(batch_id)

    assert out["unexplained"] == 1
    assert row.unexplained_charge is True
    assert row.unexplained_charge_amount == Decimal("1.25")
    assert svc._ledger.validate() == []
    assert svc._investment_ledger.validate() == []


# --- exact AMFI NAV: dedup across old and new entries ----------------------
#
# Fictional orders in a scheme the fixture master resolves (122639). The
# first is the worked example's shape: exported 49,997 / 1761.26 @ 28.39 for
# a 50,000 order whose exact NAV is 28.3873, so it is booked at cash 50,000
# and units 1761.263 -- BOTH of the export's identity figures move.
_EXACT_ORDERS = b"""Personal Details
Name,Jane Doe

TRANSACTIONS FROM Apr 01 2025 TO Mar 31 2026

Scheme Name,Transaction Type,Units,NAV,Amount,Date
Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PURCHASE,1761.26,28.39,49997,06 Oct 2025
Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PURCHASE,512.000,29.2945,14999,07 Oct 2025
"""


class _Navs:
    """In-memory exact-NAV source: the 6 Oct NAV is known, 7 Oct is not."""

    def __init__(self) -> None:
        self.rows = {("122639", _date_(2025, 10, 6)): Decimal("28.3873")}

    def exact(self, keys):
        return {k: v for k, v in self.rows.items() if k in keys}


def _date_(y, m, d):
    from datetime import date

    return date(y, m, d)


def _holding_units(svc) -> list[Decimal]:
    return sorted(
        p.units.number
        for e in svc._investment_ledger.load().entries
        for p in getattr(e, "postings", [])
        if p.units is not None and p.units.currency == "MF122639"
    )


def test_reimport_after_an_exact_nav_commit_is_all_exact_duplicates(tmp_path):
    svc = _service(tmp_path, nav_source=_Navs())
    first = svc.detect("groww_exact.csv", _EXACT_ORDERS)
    out = svc.commit(first["batch_id"])
    assert out["committed"] == 2
    # The trap is live: the journal holds the exact units, not the export's.
    assert _holding_units(svc) == [Decimal("512.000"), Decimal("1761.263")]

    again = svc.detect("groww_exact.csv", _EXACT_ORDERS)
    assert [c["dedup_status"] for c in again["candidates"]] == ["exact_dup"] * 2


def test_reimport_after_a_pre_change_commit_is_all_exact_duplicates(
    tmp_path, monkeypatch
):
    """Entries committed before this change carry the export's own cash and
    units and no export metadata. Simulated by forcing the pre-snap booking
    (snap_cash=False: export cash, export units, no metadata) for the first
    commit only."""
    import backend.app.service.ingest.pipeline.entry_writer as commit_mod

    real = commit_mod._purchase_booking
    monkeypatch.setattr(
        commit_mod,
        "_purchase_booking",
        lambda c, cfg, snap_cash, navs=None: real(c, cfg, False, None),
    )
    svc = _service(tmp_path, nav_source=_Navs())
    first = svc.detect("groww_exact.csv", _EXACT_ORDERS)
    svc.commit(first["batch_id"])
    main_text = svc._investment_ledger.journal_dir.joinpath(
        "investments-2025.beancount"
    ).read_text(encoding="utf-8")
    assert "export-units" not in main_text
    assert _holding_units(svc) == [Decimal("512.000"), Decimal("1761.26")]
    monkeypatch.setattr(commit_mod, "_purchase_booking", real)

    again = svc.detect("groww_exact.csv", _EXACT_ORDERS)
    assert [c["dedup_status"] for c in again["candidates"]] == ["exact_dup"] * 2


def test_review_states_why_a_purchase_was_not_booked_from_the_exact_nav(tmp_path):
    """Detect flags the 7 Oct row (no NAV that day) and not the 6 Oct row; the
    commit-time recompute reaches the same answer from the same NAV data."""
    from backend.app.service.ingest.pipeline.charges import EXACT_NAV_UNAVAILABLE

    svc = _service(tmp_path, nav_source=_Navs())
    result = svc.detect("groww_exact.csv", _EXACT_ORDERS)
    known, missing = result["candidates"]
    assert EXACT_NAV_UNAVAILABLE not in known["flags"]
    assert EXACT_NAV_UNAVAILABLE in missing["flags"]
    assert known["unexplained_charge"] is False
    assert missing["unexplained_charge"] is False

    rows = svc._review_store.get_batch(result["batch_id"])
    preview = {c.id: (list(c.flags), c.unexplained_charge) for c in rows}
    for c in rows:  # commit must re-derive, not echo
        c.flags = [f for f in c.flags if f != EXACT_NAV_UNAVAILABLE]
        c.unexplained_charge = False
    svc.commit(result["batch_id"])
    assert {c.id: (list(c.flags), c.unexplained_charge) for c in rows} == preview


def test_no_nav_source_flags_every_purchase(tmp_path):
    """Not wired is not silent: with no NAV source at all, every MF purchase
    says the exact NAV was unavailable."""
    from backend.app.service.ingest.pipeline.charges import EXACT_NAV_UNAVAILABLE

    svc = _service(tmp_path)
    result = svc.detect("groww_exact.csv", _EXACT_ORDERS)
    assert all(EXACT_NAV_UNAVAILABLE in c["flags"] for c in result["candidates"])


def test_review_and_commit_both_read_the_exact_nav(tmp_path):
    """Export 1761.26 @ 28.30 leaves 153 unexplained against the export's own
    units/NAV, but with the exact NAV (28.3873) the booking is clean. Detect
    and the commit-time recompute must both use the NAV, or the preview (or
    the commit response) reports money the journal does not book."""
    orders = _EXACT_ORDERS.replace(b"1761.26,28.39,49997", b"1761.26,28.30,49997")
    svc = _service(tmp_path, nav_source=_Navs())
    result = svc.detect("groww_exact.csv", orders)
    assert [c["unexplained_charge"] for c in result["candidates"]] == [False, False]
    out = svc.commit(result["batch_id"])
    assert out["unexplained"] == 0
    assert _holding_units(svc) == [Decimal("512.000"), Decimal("1761.263")]


# --- full redemption rounded up by the export ------------------------------
#
# Bought 06 Oct for 50,000 at exact NAV 28.3874: (50,000 - 2.50) / 28.3874 =
# 1761.2567 -> 1761.257 units held. The export prints them as 1761.26, so a
# full redemption arrives as 1761.26 -- 0.003 more than is held.
_HEADER = b"""Personal Details
Name,Jane Doe

TRANSACTIONS FROM Apr 01 2025 TO Mar 31 2026

Scheme Name,Transaction Type,Units,NAV,Amount,Date
"""
_FR_BUY = (
    b"Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PURCHASE,1761.26,28.39,"
    b"49997,06 Oct 2025\n"
)


def _fr_sell(units: bytes) -> bytes:
    return (
        b"Parag Parikh Flexi Cap Fund - Direct Plan - Growth,REDEEM,"
        + units
        + b",30.00,52837,03 Nov 2025\n"
    )


class _FrNavs:
    def exact(self, keys):
        known = {("122639", _date_(2025, 10, 6)): Decimal("28.3874")}
        return {k: v for k, v in known.items() if k in keys}


_FR_HELD = Decimal("1761.257")
_FR_COST = _FR_HELD * Decimal("28.3874")


def _fr_bought(tmp_path):
    svc = _service(tmp_path, nav_source=_FrNavs())
    svc.commit(svc.detect("buy.csv", _HEADER + _FR_BUY)["batch_id"])
    assert _holding_units(svc) == [_FR_HELD]
    return svc


def _gains(svc) -> Decimal:
    return sum(
        (
            p.units.number
            for e in svc._ledger.load().entries
            for p in getattr(e, "postings", [])
            if p.account.startswith("Income:CapitalGains:")
        ),
        Decimal(0),
    )


def test_a_full_redemption_rounded_up_by_the_export_redeems_the_units_held(tmp_path):
    svc = _fr_bought(tmp_path)
    result = svc.detect("sell.csv", _HEADER + _fr_sell(b"1761.26"))
    (row,) = result["candidates"]
    assert row["uncovered_redemption"] is False  # review agrees with commit
    assert row["units"] == "1761.26"  # identity stays the export's

    svc.commit(result["batch_id"])

    assert svc._ledger.validate() == []
    assert svc._investment_ledger.validate() == []
    held = sum(
        (
            p.units.number
            for e in svc._investment_ledger.load().entries
            for p in getattr(e, "postings", [])
            if p.units is not None and p.units.currency == "MF122639"
        ),
        Decimal(0),
    )
    assert held == 0  # all 1761.257 left, no dust and no overdraw
    # Gain = reported sale amount - cost of exactly the units held.
    assert _gains(svc) == -(Decimal("52837") - _FR_COST)


def test_reimporting_a_clamped_redemption_is_an_exact_duplicate(tmp_path):
    svc = _fr_bought(tmp_path)
    sell = _HEADER + _fr_sell(b"1761.26")
    svc.commit(svc.detect("sell.csv", sell)["batch_id"])

    again = svc.detect("both.csv", _HEADER + _FR_BUY + _fr_sell(b"1761.26"))
    assert [c["dedup_status"] for c in again["candidates"]] == ["exact_dup"] * 2


def test_an_over_redemption_beyond_the_rounding_is_still_refused(tmp_path):
    """1761.263 is 0.006 over the 1761.257 held: not rounding, a shortfall.
    Review flags it and commit refuses it, as before."""
    svc = _fr_bought(tmp_path)
    result = svc.detect("sell.csv", _HEADER + _fr_sell(b"1761.263"))
    (row,) = result["candidates"]
    assert row["uncovered_redemption"] is True
    with pytest.raises(CodedError) as ei:
        svc.commit(result["batch_id"])
    assert ei.value.code == "E1013"
    assert _holding_units(svc) == [_FR_HELD]


def test_the_rounding_bound_is_inclusive(tmp_path):
    """1761.262 is exactly 0.005 over: still a full redemption."""
    svc = _fr_bought(tmp_path)
    result = svc.detect("sell.csv", _HEADER + _fr_sell(b"1761.262"))
    assert result["candidates"][0]["uncovered_redemption"] is False
    svc.commit(result["batch_id"])
    assert svc._investment_ledger.validate() == []


def test_a_full_redemption_in_the_same_batch_as_its_purchase(tmp_path):
    """Review must credit the in-batch purchase with the units commit will
    BOOK (1761.257), not the export's 1761.26, or the two disagree."""
    svc = _service(tmp_path, nav_source=_FrNavs())
    result = svc.detect("both.csv", _HEADER + _FR_BUY + _fr_sell(b"1761.26"))
    assert [c["uncovered_redemption"] for c in result["candidates"]] == [False, False]
    svc.commit(result["batch_id"])
    assert svc._investment_ledger.validate() == []
    assert _gains(svc) == -(Decimal("52837") - _FR_COST)

    over = _service(tmp_path / "over", nav_source=_FrNavs())
    result = over.detect("both.csv", _HEADER + _FR_BUY + _fr_sell(b"1761.263"))
    assert [c["uncovered_redemption"] for c in result["candidates"]] == [False, True]
    with pytest.raises(CodedError) as ei:
        over.commit(result["batch_id"])
    assert ei.value.code == "E1013"


def test_a_partial_redemption_is_unchanged(tmp_path):
    """Covered redemptions take the old path: the export's units, no
    metadata."""
    svc = _fr_bought(tmp_path)
    svc.commit(svc.detect("sell.csv", _HEADER + _fr_sell(b"1000.00"))["batch_id"])
    text = svc._investment_ledger.journal_dir.joinpath(
        "investments-2025.beancount"
    ).read_text(encoding="utf-8")
    redeem_block = text[text.index("Redeem") :]
    assert "export-units" not in redeem_block
    assert _holding_units(svc) == [Decimal("-1000.00"), _FR_HELD]


# --- full redemption rounded DOWN by the export ----------------------------
#
# Bought at exact NAV 28.3873 (the worked example): 1761.263 units held. The
# export prints 1761.26, so a full redemption would leave 0.003 units of dust.
class _DownNavs:
    def exact(self, keys):
        known = {("122639", _date_(2025, 10, 6)): Decimal("28.3873")}
        return {k: v for k, v in known.items() if k in keys}


_DN_HELD = Decimal("1761.263")
_DN_COST = _DN_HELD * Decimal("28.3873")


def _dn_bought(tmp_path):
    svc = _service(tmp_path, nav_source=_DownNavs())
    svc.commit(svc.detect("buy.csv", _HEADER + _FR_BUY)["batch_id"])
    assert _holding_units(svc) == [_DN_HELD]
    return svc


def _net_units(svc) -> Decimal:
    return sum(
        (
            p.units.number
            for e in svc._investment_ledger.load().entries
            for p in getattr(e, "postings", [])
            if p.units is not None and p.units.currency == "MF122639"
        ),
        Decimal(0),
    )


def test_a_full_redemption_rounded_down_leaves_no_dust(tmp_path):
    svc = _dn_bought(tmp_path)
    result = svc.detect("sell.csv", _HEADER + _fr_sell(b"1761.26"))
    (row,) = result["candidates"]
    assert row["uncovered_redemption"] is False
    assert row["units"] == "1761.26"

    svc.commit(result["batch_id"])

    assert svc._investment_ledger.validate() == []
    assert svc._ledger.validate() == []
    assert _net_units(svc) == 0
    assert _gains(svc) == -(Decimal("52837") - _DN_COST)


def test_reimporting_a_rounded_down_full_redemption_is_an_exact_duplicate(tmp_path):
    svc = _dn_bought(tmp_path)
    svc.commit(svc.detect("sell.csv", _HEADER + _fr_sell(b"1761.26"))["batch_id"])
    again = svc.detect("both.csv", _HEADER + _FR_BUY + _fr_sell(b"1761.26"))
    assert [c["dedup_status"] for c in again["candidates"]] == ["exact_dup"] * 2


def test_review_and_commit_agree_on_a_rounded_down_full_redemption(tmp_path):
    """Same batch: review credits the buy with the 1761.263 commit books, and
    both sides treat the 1761.26 redemption as full."""
    svc = _service(tmp_path, nav_source=_DownNavs())
    result = svc.detect("both.csv", _HEADER + _FR_BUY + _fr_sell(b"1761.26"))
    assert [c["uncovered_redemption"] for c in result["candidates"]] == [False, False]
    svc.commit(result["batch_id"])
    assert _net_units(svc) == 0
    assert _gains(svc) == -(Decimal("52837") - _DN_COST)


def test_a_remainder_over_the_rounding_stays_a_partial_redemption(tmp_path):
    """1761.257 redeemed from 1761.263 leaves 0.006: a real remainder. Booked
    exactly as before -- the export's units, no metadata -- and kept."""
    svc = _dn_bought(tmp_path)
    svc.commit(svc.detect("sell.csv", _HEADER + _fr_sell(b"1761.257"))["batch_id"])
    assert _net_units(svc) == Decimal("0.006")
    text = svc._investment_ledger.journal_dir.joinpath(
        "investments-2025.beancount"
    ).read_text(encoding="utf-8")
    assert "export-units" not in text[text.index("Redeem") :]


def test_a_remainder_of_exactly_the_rounding_is_redeemed(tmp_path):
    """1761.258 leaves 0.005: inclusive, so a full redemption."""
    svc = _dn_bought(tmp_path)
    svc.commit(svc.detect("sell.csv", _HEADER + _fr_sell(b"1761.258"))["batch_id"])
    assert _net_units(svc) == 0


def test_review_counts_no_dust_after_a_rounded_down_full_redemption(tmp_path):
    """For the full redemption itself review's flag is False either way
    (1761.26 <= 1761.263), so parity is only observable downstream: commit
    leaves 0 held, and review must too. A later 0.004 redemption in the same
    batch is then uncovered on BOTH sides -- not covered in review by 0.003
    of dust that commit has already redeemed."""
    svc = _service(tmp_path, nav_source=_DownNavs())
    extra = (
        b"Parag Parikh Flexi Cap Fund - Direct Plan - Growth,REDEEM,0.004,30.00,"
        b"0.12,04 Nov 2025\n"
    )
    result = svc.detect("all.csv", _HEADER + _FR_BUY + _fr_sell(b"1761.26") + extra)
    assert [c["uncovered_redemption"] for c in result["candidates"]] == [
        False,
        False,
        True,
    ]
    with pytest.raises(CodedError) as ei:
        svc.commit(result["batch_id"])
    assert ei.value.code == "E1013"


def test_stt_on_a_clamped_redemption_is_computed_on_the_units_redeemed(
    tmp_path, monkeypatch
):
    """With STT on, a full redemption the export rounded up is booked on the
    1761.257 units HELD. Review (the commit-time recompute) must compute the
    charges on those same units, so the amount it reports is the amount the
    journal carries. 53,000 for 1761.26 @ 30.00 leaves a remainder over the
    band after STT, so both sides book an Unexplained leg -- 109.45 on the
    held units, 109.36 had the export's units been used."""
    import backend.app.service.ingest.pipeline.entry_writer as commit_mod
    from backend.app.models.storage.merge_booking import MergeBookingConfig

    class _SttOn(MergeBookingConfig):
        stt_rate_sell: str = "0.001"

    monkeypatch.setattr(commit_mod, "MergeBookingConfig", _SttOn)
    svc = _fr_bought(tmp_path)
    sell = _fr_sell(b"1761.26").replace(b"52837", b"53000")
    result = svc.detect("sell.csv", _HEADER + sell)
    # Preview, before anything is written.
    assert Decimal(result["candidates"][0]["unexplained_charge_amount"]) == Decimal(
        "109.45"
    )
    (row,) = svc._review_store.get_batch(result["batch_id"])
    row.unexplained_charge_amount = None  # read back commit's own recompute

    svc.commit(result["batch_id"])

    booked = [
        p.units.number
        for e in svc._investment_ledger.load().entries
        for p in getattr(e, "postings", [])
        if p.account == "Expenses:Investment:Charges:Unexplained"
    ]
    assert booked == [row.unexplained_charge_amount]
    assert row.unexplained_charge_amount == Decimal("109.45")
