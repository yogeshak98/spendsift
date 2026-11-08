from __future__ import annotations

from pathlib import Path

from backend.app.core.paths import REPO_ROOT
from backend.app.service.categorize.categorizer import Categorizer
from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.persistence.review_batches import ReviewStore
from backend.app.persistence.templates import TemplateStore
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.service import ImportService

# The default Groww wallet (Settings.platform_cash_accounts["groww"]).
GROWW_STOCK_CASH_ACCOUNT = "Assets:Broker:Groww"

_FIXTURES = REPO_ROOT / "tests" / "fixtures"
_STOCKS = _FIXTURES / "statements" / "groww_stocks.csv"
_MASTER = _FIXTURES / "scheme_master.csv"


def _service(tmp_path) -> ImportService:
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
    )


def test_detect_routes_stock_file_to_stock_candidates(tmp_path):
    svc = _service(tmp_path)
    result = svc.detect("groww_stocks.csv", _STOCKS.read_bytes())
    assert result["matched"] is True
    assert result["source"] == "groww_stocks"
    cands = result["candidates"]
    # 6 Executed rows in the fixture (ORD-0006 is Cancelled -> ignored).
    assert len(cands) == 6
    acme_buy = next(c for c in cands if c["order_id"] == "ORD-0001")
    assert acme_buy["commodity"] == "ACME"
    assert acme_buy["units"] == "100.0"


def test_detect_surfaces_ignored_and_unparsed_rows(tmp_path):
    # A cancelled order must never be silently dropped from the response --
    # the user needs to see it was excluded, and why.
    svc = _service(tmp_path)
    result = svc.detect("groww_stocks.csv", _STOCKS.read_bytes())
    assert any("ORD-0006" in row["text"] for row in result["ignored"])
    assert result["unparsed"] == []


def test_dispatch_prefers_the_stock_parser_for_a_stock_export():
    # Ruling 8: dispatch order IS the safety property. Both `is_groww_header`
    # and `is_groww_stock_header` use `issubset` and tolerate extra columns,
    # so a header carrying the union of both exports' columns satisfies
    # both. If detect() tested the MF predicate first, such a file would be
    # routed to the MF parser and stock rows would be booked as fund lots
    # into the wrong vehicle account, silently. This is not a real Groww
    # export -- it exists to pin the dispatch order.
    import tempfile

    svc = _service(Path(tempfile.mkdtemp()))
    header = (
        "Stock name,Symbol,ISIN,Type,Quantity,Value,Exchange,"
        "Exchange Order Id,Execution date and time,Order status,"
        "Scheme Name,Transaction Type,Units,NAV,Amount,Date\n"
    )
    row = (
        "ACME INDUSTRIES LTD,ACME,INE000A01001,BUY,100.0,25000.0,NSE,"
        "ORD-9001,10-05-2024 09:15 AM,Executed,,,,,,\n"
    )
    csv_bytes = (header + row).encode("utf-8")
    result = svc.detect("union_header.csv", csv_bytes)
    assert result["matched"] is True
    assert result["source"] == "groww_stocks"
    assert result["candidates"][0]["commodity"] == "ACME"


def test_bank_csv_still_routes_to_template_path(tmp_path):
    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    result = svc.detect("hdfc.csv", csv)
    assert result["matched"] is False
    assert "source" not in result or result.get("source") != "groww_stocks"


def test_reimporting_the_same_file_adds_nothing(tmp_path):
    # The strongest guarantee this unit offers: import the fixture, commit
    # it, then import the SAME file again and prove the journal is
    # unchanged and zero new candidates are produced -- not merely that a
    # count looks right without ever having committed anything.
    svc = _service(tmp_path)
    first = svc.detect("groww_stocks.csv", _STOCKS.read_bytes())
    # Acknowledge the "new symbol" review hold (one per brand-new symbol —
    # ACME, BOREAL, DUSK) the same way the review UI would, before committing.
    for cand in svc._review_store.get_batch(first["batch_id"]):
        if cand.unresolved_scheme:
            svc._review_store.update(
                first["batch_id"], cand.id, {"unresolved_scheme": None}
            )
    committed = svc.commit(first["batch_id"])
    assert committed["committed"] == 6

    def _journal_snapshot() -> dict[str, str]:
        snap = {
            p.name: p.read_text(encoding="utf-8")
            for p in svc._ledger.journal_dir.glob("*.beancount")
        }
        assert snap, f"no journal files in {svc._ledger.journal_dir}"
        return snap

    journal_before = _journal_snapshot()

    second = svc.detect("groww_stocks.csv", _STOCKS.read_bytes())
    assert second["candidates"] == []

    journal_after = _journal_snapshot()
    assert journal_after == journal_before


def test_same_day_same_symbol_same_value_orders_both_survive(tmp_path):
    # Content-based dedup cannot tell two genuinely distinct stock
    # orders apart when they share date/symbol/value (a real occurrence --
    # e.g. a split fill). Only differing order-ids protect both from being
    # collapsed into one.
    from datetime import date
    from decimal import Decimal

    from backend.app.models.domain.candidate import CandidateTxn, Direction

    svc = _service(tmp_path)

    def order(order_id: str) -> CandidateTxn:
        return CandidateTxn(
            date=date(2026, 3, 1),
            amount=Decimal("52.00"),
            raw_narration="Stock Buy: CINDER MICROSYSTEMS LTD",
            normalized_narration="CINDER MICROSYSTEMS LTD",
            source_account=GROWW_STOCK_CASH_ACCOUNT,
            direction=Direction.DEBIT,
            units=Decimal("4.0"),
            commodity="CINDER",
            unit_cost=Decimal("13.0000"),
            order_id=order_id,
        )

    batch_id = svc._review_store.add_batch([order("ORD-A"), order("ORD-B")])
    committed = svc.commit(batch_id)
    assert committed["committed"] == 2

    holdings = [
        p
        for e in svc._ledger.load().entries
        for p in getattr(e, "postings", [])
        if getattr(p.units, "currency", None) == "CINDER"
    ]
    # Both orders' holding legs made it into the journal -- neither was
    # collapsed as a content-based duplicate of the other.
    assert len(holdings) == 2


def test_duplicated_journal_order_id_is_not_treated_as_new(tmp_path):
    # The trap: existing_order_ids() deliberately EXCLUDES an order-id the
    # journal already holds twice from `.orders` (it refuses to pick a
    # winner). A naive `.orders.get(order_id)` would then return None for
    # exactly this ambiguous case, reading as "not seen before" and
    # importing a THIRD entry. Detect() must check `duplicate_order_ids`
    # first and hold such a candidate rather than treat it as new.
    from datetime import date
    from decimal import Decimal

    from backend.app.models.domain.candidate import CandidateTxn, Direction

    svc = _service(tmp_path)

    def order(order_id: str, units: str, day) -> CandidateTxn:
        return CandidateTxn(
            date=day,
            amount=Decimal("1000.00"),
            raw_narration="Stock Buy: DUSK METALS LTD",
            normalized_narration="DUSK METALS LTD",
            source_account=GROWW_STOCK_CASH_ACCOUNT,
            direction=Direction.DEBIT,
            units=Decimal(units),
            commodity="DUSK",
            unit_cost=(Decimal("1000.00") / Decimal(units)),
            order_id=order_id,
        )

    # Commit two DISTINCT orders that share the same order-id directly
    # (bypassing detect()'s dedup, which is what this task builds) --
    # simulating the state a botched backfill (stamping order-id onto
    # hand-entered transactions) could leave behind in a real journal.
    batch_id = svc._review_store.add_batch(
        [
            order("ORD-DUP", "10.0", date(2026, 1, 1)),
            order("ORD-DUP", "12.0", date(2026, 1, 2)),
        ]
    )
    committed = svc.commit(batch_id)
    assert committed["committed"] == 2

    # Now exercise detect()'s own dedup path with a real file whose header
    # row carries this duplicated order-id -- it must not be silently
    # imported as new.
    stock_csv = (
        "Stock name,Symbol,ISIN,Type,Quantity,Value,Exchange,"
        "Exchange Order Id,Execution date and time,Order status\n"
        "DUSK METALS LTD,DUSK,,BUY,10.0,1000.0,NSE,ORD-DUP,"
        "04-01-2026 09:15 AM,Executed\n"
    ).encode("utf-8")
    result = svc.detect("dusk_dup.csv", stock_csv)
    produced = result["candidates"]
    assert len(produced) == 1
    assert produced[0]["order_restated"] is True


def test_commit_refuses_a_restated_order_and_leaves_journal_unchanged(tmp_path):
    # Fix round 1, item 1: order_restated had no server-side gate -- only the
    # review UI honoured it. Any client that calls commit() directly (a
    # replayed request, a UI that didn't refresh flags) would otherwise write
    # a restated order straight through, exactly the ambiguous case Task 5's
    # flag_restated_orders exists to hold.
    from datetime import date
    from decimal import Decimal

    import pytest

    from backend.app.core.errors.errors import CodedError
    from backend.app.models.domain.candidate import CandidateTxn, Direction

    svc = _service(tmp_path)

    original = CandidateTxn(
        date=date(2026, 1, 1),
        amount=Decimal("1000.00"),
        raw_narration="Stock Buy: DUSK METALS LTD",
        normalized_narration="DUSK METALS LTD",
        source_account=GROWW_STOCK_CASH_ACCOUNT,
        direction=Direction.DEBIT,
        units=Decimal("10.0"),
        commodity="DUSK",
        unit_cost=Decimal("100.00"),
        order_id="ORD-RESTATE",
    )
    svc.commit(svc._review_store.add_batch([original]))

    def _journal_snapshot() -> dict[str, str]:
        snap = {
            p.name: p.read_text(encoding="utf-8")
            for p in svc._ledger.journal_dir.glob("*.beancount")
        }
        assert snap, f"no journal files in {svc._ledger.journal_dir}"
        return snap

    journal_before = _journal_snapshot()

    # A fresh candidate carrying the SAME order-id but DIFFERENT units --
    # already flagged order_restated by flag_restated_orders (mirroring what
    # detect() would have set on a re-import of a changed export row).
    restated = CandidateTxn(
        date=date(2026, 1, 1),
        amount=Decimal("1000.00"),
        raw_narration="Stock Buy: DUSK METALS LTD",
        normalized_narration="DUSK METALS LTD",
        source_account=GROWW_STOCK_CASH_ACCOUNT,
        direction=Direction.DEBIT,
        units=Decimal("15.0"),
        commodity="DUSK",
        unit_cost=Decimal("66.6667"),
        order_id="ORD-RESTATE",
        order_restated=True,
    )
    batch_id = svc._review_store.add_batch([restated])
    with pytest.raises(CodedError) as ei:
        svc.commit(batch_id)
    assert ei.value.code == "E1045"
    assert ei.value.status == 422

    assert _journal_snapshot() == journal_before


def test_pure_stock_commit_succeeds_with_no_scheme_master_file(tmp_path):
    # Fix round 1, item 2: commit() loaded the MF scheme master whenever ANY
    # candidate carried `commodity`, but a stock symbol needs no scheme
    # resolution at all -- the symbol IS the commodity. A batch of nothing
    # but stock orders must commit even when the MF scheme-master file was
    # never fetched (the real next import is six years of stock-only
    # history).
    from datetime import date
    from decimal import Decimal

    from backend.app.service.categorize.categorizer import Categorizer
    from backend.app.service.categorize.dictionary import MerchantDictionary
    from backend.app.persistence.learned_rules import LearnedRules
    from backend.app.models.domain.candidate import CandidateTxn, Direction
    from backend.app.persistence.review_batches import ReviewStore
    from backend.app.persistence.templates import TemplateStore
    from backend.app.persistence.journal import Ledger
    from backend.app.service.ingest.service import ImportService

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
    # No scheme_master_path given, and no file exists at the default path --
    # this must not matter for a stock-only batch.
    svc = ImportService(
        ledger=ledger,
        investment_ledger=investment_ledger,
        categorizer=categorizer,
        review_store=ReviewStore(),
        template_store=TemplateStore(ledger.storage_dir / "templates"),
    )
    assert not svc._resolve_scheme_master_path().exists()

    candidate = CandidateTxn(
        date=date(2026, 1, 1),
        amount=Decimal("1000.00"),
        raw_narration="Stock Buy: DUSK METALS LTD",
        normalized_narration="DUSK METALS LTD",
        source_account=GROWW_STOCK_CASH_ACCOUNT,
        direction=Direction.DEBIT,
        units=Decimal("10.0"),
        commodity="DUSK",
        unit_cost=Decimal("100.00"),
        order_id="ORD-1",
    )
    committed = svc.commit(svc._review_store.add_batch([candidate]))
    assert committed["committed"] == 1
    assert svc._ledger.validate() == []


def _stock_candidate(order_id: str | None, *, day, units: str, value: str):
    from decimal import Decimal

    from backend.app.models.domain.candidate import CandidateTxn, Direction

    units_d = Decimal(units)
    value_d = Decimal(value)
    return CandidateTxn(
        date=day,
        amount=value_d,
        raw_narration="Stock Buy: ACME INDUSTRIES LTD",
        normalized_narration="ACME INDUSTRIES LTD",
        source_account=GROWW_STOCK_CASH_ACCOUNT,
        direction=Direction.DEBIT,
        units=units_d,
        commodity="ACME",
        unit_cost=(value_d / units_d).quantize(Decimal("0.0001")),
        order_id=order_id,
    )


def _unstamp(ledger) -> None:
    """Strip every `order-id` line from the journal, leaving stock
    transactions in exactly the shape HAND ENTRY leaves them: real orders the
    dedup key cannot see."""
    paths = list(ledger.journal_dir.glob("*.beancount"))
    assert paths, f"no journal files in {ledger.journal_dir}"
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if "order-id" not in text:
            continue
        path.write_text(
            "\n".join(line for line in text.splitlines() if "order-id:" not in line)
            + "\n",
            encoding="utf-8",
        )


def test_commit_refuses_a_stock_batch_while_the_journal_holds_unstamped_orders(
    tmp_path,
):
    # F1, the migration gate. Stock dedup keys ONLY on the broker order-id
    # (content dedup is bypassed for stocks on purpose), so hand-entered
    # transactions -- which carry no id -- are invisible to it: without this
    # gate every row of a full-history re-import reads as new, no flag fires,
    # and commit() writes a SECOND copy of six years of holdings.
    from datetime import date

    import pytest

    from backend.app.core.errors.errors import CodedError

    svc = _service(tmp_path)
    svc.commit(
        svc._review_store.add_batch(
            [
                _stock_candidate(
                    "ORD-0001", day=date(2024, 5, 10), units="100", value="25000.00"
                )
            ]
        )
    )
    _unstamp(svc._ledger)

    def _journal_snapshot() -> dict[str, str]:
        snap = {
            p.name: p.read_text(encoding="utf-8")
            for p in svc._ledger.journal_dir.glob("*.beancount")
        }
        assert snap, f"no journal files in {svc._ledger.journal_dir}"
        return snap

    before = _journal_snapshot()
    # The same order arriving again from the broker export. Nothing else in
    # the pipeline can tell it apart from a new one.
    batch_id = svc._review_store.add_batch(
        [
            _stock_candidate(
                "ORD-0001", day=date(2024, 5, 10), units="100", value="25000.00"
            )
        ]
    )
    with pytest.raises(CodedError) as ei:
        svc.commit(batch_id)
    assert ei.value.code == "E1046"
    assert ei.value.status == 422
    # The refusal names the migration that fixes it, not just the problem.
    assert "backfill_stock_order_ids.py" in ei.value.resolution
    assert _journal_snapshot() == before


def test_the_migration_gate_does_not_fire_on_a_journal_with_no_stock_history(tmp_path):
    # The false-positive case that matters most: a FIRST-EVER stock import.
    # Nothing in the journal holds an Assets:Investment:Stock:* posting, so
    # there is nothing unstamped to find and the batch must commit.
    from datetime import date

    svc = _service(tmp_path)
    assert svc._ledger.validate() == []
    committed = svc.commit(
        svc._review_store.add_batch(
            [
                _stock_candidate(
                    "ORD-0001", day=date(2024, 5, 10), units="100", value="25000.00"
                )
            ]
        )
    )
    assert committed["committed"] == 1

    # ...and a second, DIFFERENT order still commits afterwards: everything
    # this importer writes carries an order-id, so a fully-stamped journal
    # never trips the gate either.
    committed = svc.commit(
        svc._review_store.add_batch(
            [
                _stock_candidate(
                    "ORD-0002", day=date(2024, 6, 12), units="50", value="13500.00"
                )
            ]
        )
    )
    assert committed["committed"] == 1


def test_the_migration_gate_ignores_a_non_stock_batch(tmp_path):
    # An unstamped stock history must not block an ordinary BANK import --
    # the gate asks its question only of batches that actually carry stock
    # rows.
    from datetime import date
    from decimal import Decimal

    from backend.app.models.domain.candidate import CandidateTxn, Direction

    svc = _service(tmp_path)
    svc.commit(
        svc._review_store.add_batch(
            [
                _stock_candidate(
                    "ORD-0001", day=date(2024, 5, 10), units="100", value="25000.00"
                )
            ]
        )
    )
    _unstamp(svc._ledger)

    bank_row = CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("412.00"),
        raw_narration="UPI/DR/SWIGGY/412",
        normalized_narration="SWIGGY",
        source_account="Assets:Checking:HDFC",
        direction=Direction.DEBIT,
    )
    committed = svc.commit(svc._review_store.add_batch([bank_row]))
    assert committed["committed"] == 1


def test_clearing_order_restated_in_review_does_not_get_it_past_commit(tmp_path):
    # F6. The review patch route sets candidate attributes verbatim, so a
    # client can clear this flag. commit() recomputes it against the journal
    # -- the same treatment `uncovered_redemptions` gets -- so the gate holds
    # anyway. Unlike needs_account/unresolved_scheme, this is not a flag a
    # user can legitimately resolve by editing a row.
    from datetime import date

    import pytest

    from backend.app.core.errors.errors import CodedError

    svc = _service(tmp_path)
    svc.commit(
        svc._review_store.add_batch(
            [
                _stock_candidate(
                    "ORD-R", day=date(2024, 5, 10), units="100", value="25000.00"
                )
            ]
        )
    )

    def _journal_snapshot() -> dict[str, str]:
        snap = {
            p.name: p.read_text(encoding="utf-8")
            for p in svc._ledger.journal_dir.glob("*.beancount")
        }
        assert snap, f"no journal files in {svc._ledger.journal_dir}"
        return snap

    before = _journal_snapshot()

    # Same order-id, DIFFERENT quantity: a restatement.
    restated = _stock_candidate(
        "ORD-R", day=date(2024, 5, 10), units="150", value="25000.00"
    )
    restated.order_restated = True
    batch_id = svc._review_store.add_batch([restated])
    # A client clears the flag through POST /api/review/{batch}/{cid}.
    svc.edit(batch_id, restated.id, {"order_restated": False})
    assert svc._review_store.get_batch(batch_id)[0].order_restated is False

    with pytest.raises(CodedError) as ei:
        svc.commit(batch_id)
    assert ei.value.code == "E1045"
    assert _journal_snapshot() == before


_SPLIT_FILL = (
    "Name,Jane Doe,,,,,,,,\n"
    "Unique Client Code,1234567890,,,,,,,,\n"
    ",,,,,,,,,\n"
    "Order history for stocks from 01-04-2025 to 31-03-2026,,,,,,,,,\n"
    ",,,,,,,,,\n"
    "Stock name,Symbol,ISIN,Type,Quantity,Value,Exchange,Exchange Order Id,"
    "Execution date and time,Order status\n"
    "ACME INDUSTRIES LTD,ACME,INE000A01001,BUY,4.0,1000.0,NSE,ORD-0101,"
    "10-05-2025 09:15 AM,Executed\n"
    "ACME INDUSTRIES LTD,ACME,INE000A01001,BUY,4.0,1000.0,NSE,ORD-0102,"
    "10-05-2025 09:15 AM,Executed\n"
).encode()


def test_a_split_fill_twin_keeps_its_id_when_the_other_was_committed(tmp_path):
    # Two fills with identical content and their own order ids. Ids are
    # numbered before already-committed orders are dropped, so the fill still
    # to import keeps the id it had, never the committed fill's txn-id.
    svc = _service(tmp_path)
    first = svc.detect("split.csv", _SPLIT_FILL)
    ids = {c["order_id"]: c["id"] for c in first["candidates"]}
    assert len(set(ids.values())) == 2
    bid = first["batch_id"]
    svc.reject(bid, ids["ORD-0102"])
    for cand in svc._review_store.get_batch(bid):
        svc._review_store.update(bid, cand.id, {"unresolved_scheme": None})
    assert svc.commit(bid)["committed"] == 1

    again = svc.detect("split.csv", _SPLIT_FILL)
    (left,) = again["candidates"]
    assert left["order_id"] == "ORD-0102"
    assert left["id"] == ids["ORD-0102"]
    committed = {
        e.meta.get("txn-id")
        for e in svc._ledger.load().entries
        if hasattr(e, "postings")
    }
    assert left["id"] not in committed
