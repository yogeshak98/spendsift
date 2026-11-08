"""End-to-end: parse -> review -> commit -> reconcile, over one flow.

Every other test in this unit (fingerprint, parser, commit, service dispatch,
backfill, new-symbol handling) covers exactly one layer in isolation. This is
the only test that runs the whole pipeline the way the app actually will --
detect() on the raw fixture bytes, resolve the new-symbol review holds the way
the review UI would, commit(), then reconcile the committed journal against
what the broker file says should be held. It exists because the 2026-09-07
reconciliation found the *composition* of correctly-tested layers still
produced a wrong journal: seven missing buys, a sale booked once instead of
twice, 18 phantom shares no order supported, and capital gains understated
across two lots with different holding periods. A test that only checks "some
transactions were written" cannot catch any of that; this one reconciles net
units per symbol, and the capital-gains sign and lot attribution, against the
fixture's own arithmetic.

Step 3 of this unit's brief (manual verification against the user's real
broker export and real journal) is explicitly OUT of scope here and was not
run — see task-9-report.md.
"""

from __future__ import annotations

from decimal import Decimal

from beancount.core import data

from backend.app.core.paths import REPO_ROOT
from backend.app.service.categorize.categorizer import Categorizer
from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.service.ingest.pipeline.entry_writer import _current_lots
from backend.app.persistence.review_batches import ReviewStore
from backend.app.persistence.templates import TemplateStore
from backend.app.models.domain.accounts import Vehicle
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.service import ImportService

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


def _resolve_new_symbol_holds(svc: ImportService, batch_id: str) -> None:
    """Acknowledge the "new symbol" review hold the same way the review UI
    would: clear `unresolved_scheme` on every flagged row before committing."""
    for cand in svc._review_store.get_batch(batch_id):
        if cand.unresolved_scheme:
            svc._review_store.update(batch_id, cand.id, {"unresolved_scheme": None})


def _journal_snapshot(ledger: Ledger) -> dict[str, str]:
    snap = {
        p.name: p.read_text(encoding="utf-8")
        for p in ledger.journal_dir.glob("*.beancount")
    }
    assert snap, f"no journal files in {ledger.journal_dir}"
    return snap


def _capital_gains_postings(ledger: Ledger) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for entry in ledger.load().entries:
        if not isinstance(entry, data.Transaction):
            continue
        for p in entry.postings:
            if p.account == "Income:Investment:CapitalGains":
                out[entry.narration] = p.units.number
    return out


def test_full_stock_import_flow_reconciles_against_the_broker_file(tmp_path):
    # Fixture recap (fictional, tests/fixtures/statements/groww_stocks.csv):
    #   ACME   BUY 100 @ value 25000.00 (ORD-0001)
    #   ACME   BUY  50 @ value 13500.00 (ORD-0002)
    #   ACME   SELL 120 @ value 33600.00 (ORD-0003)  <- multi-lot, in-batch
    #   BOREAL BUY  10 @ value  2000.00 (ORD-0004)
    #   BOREAL BUY  10 @ value  1990.00 (ORD-0005)
    #   CINDER BUY   5 @ value  1000.00 (ORD-0006)   <- Cancelled, ignored
    #   DUSK   BUY 1767 @ value 39871.33 (ORD-0007)  <- doesn't divide evenly
    svc = _service(tmp_path)
    raw = _STOCKS.read_bytes()

    detected = svc.detect("groww_stocks.csv", raw)
    assert detected["matched"] is True
    assert detected["source"] == "groww_stocks"
    # ORD-0006 (Cancelled) is excluded from candidates, surfaced as ignored.
    assert len(detected["candidates"]) == 6
    assert any("ORD-0006" in row["text"] for row in detected["ignored"])
    assert detected["unparsed"] == []

    batch_id = detected["batch_id"]
    _resolve_new_symbol_holds(svc, batch_id)
    committed = svc.commit(batch_id)
    assert committed["committed"] == 6

    ledger = svc._ledger

    # --- Reconciliation 1: net position per symbol, from the journal itself,
    # not from a running tally kept by the test. This is the property the
    # whole unit exists to guarantee.
    acme_lots = _current_lots(ledger, "ACME", Vehicle.STOCK)
    boreal_lots = _current_lots(ledger, "BOREAL", Vehicle.STOCK)
    dusk_lots = _current_lots(ledger, "DUSK", Vehicle.STOCK)
    cinder_lots = _current_lots(ledger, "CINDER", Vehicle.STOCK)

    def net_units(lots) -> Decimal:
        return sum((lot[0] for lot in lots), Decimal(0))

    # ACME: bought 100 + 50 = 150, sold 120 -> 30 held.
    assert net_units(acme_lots) == Decimal("30")
    # BOREAL: bought 10 + 10, nothing sold -> 20 held.
    assert net_units(boreal_lots) == Decimal("20")
    # DUSK: one buy of 1767, value doesn't divide evenly by quantity -- must
    # still reconcile to the exact whole-share quantity the order states.
    assert net_units(dusk_lots) == Decimal("1767")
    # CINDER was Cancelled -- no holding, no account, nothing to reconcile.
    assert cinder_lots == []
    assert "Assets:Investment:Stock:CINDER" not in ledger.main_file.read_text(
        encoding="utf-8"
    )

    # The 30 ACME units remaining are exactly the tail of the SECOND buy lot
    # (FIFO consumed all of lot 1 and 20 of lot 2's 50 units) -- not merely a
    # unit count that happens to add up.
    assert acme_lots == [[Decimal("30"), Decimal("270.0000"), acme_lots[0][2]]]

    # --- Reconciliation 2: the multi-lot sell's capital-gains posting, sign
    # and magnitude. FIFO cost = 100 @ 250.0000 + 20 @ 270.0000 = 30400.00;
    # proceeds 33600.00 -> a GAIN of 3200.00, which books as a NEGATIVE
    # posting to Income:Investment:CapitalGains (a loss would be positive).
    gains = _capital_gains_postings(ledger)
    assert gains["Stock Sell: ACME INDUSTRIES LTD"] == Decimal("-3200.00")
    # BOREAL and DUSK never sold anything -- no capital-gains posting to fake.
    assert not any("BOREAL" in narration for narration in gains)
    assert not any("DUSK" in narration for narration in gains)

    assert ledger.validate() == []

    # --- Reconciliation 3: re-importing the identical file must add nothing
    # and must not rewrite anything already there. A candidate count of zero
    # is a weaker guarantee than this -- it would still pass if the second
    # import silently rewrote existing entries with equivalent content.
    journal_before = _journal_snapshot(ledger)
    investments_before = _journal_snapshot(svc._investment_ledger)

    redetected = svc.detect("groww_stocks.csv", raw)
    assert redetected["candidates"] == []

    assert _journal_snapshot(ledger) == journal_before
    assert _journal_snapshot(svc._investment_ledger) == investments_before
