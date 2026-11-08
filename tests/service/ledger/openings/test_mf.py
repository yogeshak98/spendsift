"""Mutual-fund opening lots over synthetic tmp_path journals (both ledgers
in one journal/ folder) and a fake scheme lookup. A write books the lot in
both ledgers at one cost; a refusal or a failed write leaves every journal
file byte-identical."""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import date
from decimal import Decimal

import pytest
from beancount.core import data

from backend.app.core.errors.errors import CodedError
from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.models.api.openings import MfOpeningLot, MfOpeningRequest
from backend.app.models.domain.accounts import (
    EQUITY_OPENING,
    INVESTMENT_MUTUAL_FUND,
    holding_account,
)
from backend.app.persistence.journal import Ledger
from backend.app.persistence.review_batches import ReviewStore
from backend.app.persistence.templates import TemplateStore
from backend.app.service.categorize.categorizer import Categorizer
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.service.ingest import service as ingest_service
from backend.app.service.ingest.parsers.scheme_master import SchemeMatch
from backend.app.service.ingest.platform_accounts import (
    GROWW,
    platform_clearing_account,
)
from backend.app.service.ingest.service import ImportService
from backend.app.service.ledger.openings import mf
from backend.app.service.ledger.openings.keys import (
    MF_OPENING_FLAG,
    OPENING_SOURCE_KEY,
)
from backend.app.service.reports.health import doctor

TODAY = date(2026, 6, 15)

NEW = "MF100001"  # no postings anywhere yet
HELD = "MF200002"  # already has an investments-ledger posting
OTHER = "MF300003"  # no postings, a second scheme for the flag test
CASH = "Assets:Checking:Acme"

_SCHEMES = {
    NEW: SchemeMatch(code="100001", name="Acme Flexi Cap Fund - Direct Plan - Growth"),
    HELD: SchemeMatch(code="200002", name="Beta Short Term Debt Fund - Direct Plan"),
    OTHER: SchemeMatch(code="300003", name="Gamma Midcap Fund - Direct Plan - Growth"),
}


def _scheme(symbol: str) -> SchemeMatch | None:
    return _SCHEMES.get(symbol)


def _ledgers(tmp_path) -> tuple[Ledger, Ledger]:
    """Both ledgers in one journal/ folder, as the app wires them. HELD has
    one Groww purchase in both; the main ledger also holds 10.00 INR of
    legacy MutualFund value, so Doctor's gap starts at a non-zero 10.00."""
    storage = tmp_path / "storage"
    ledger = Ledger(storage)
    ledger.bootstrap(seed_accounts=())
    investment = Ledger(
        storage,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    investment.bootstrap(seed_accounts=())
    clearing = platform_clearing_account(GROWW)
    journal = ledger.journal_dir
    ledger.main_file.write_text(
        'option "operating_currency" "INR"\n\n'
        "2000-01-01 commodity INR\n"
        f"2020-01-01 open {EQUITY_OPENING} INR\n"
        f"2020-01-01 open {INVESTMENT_MUTUAL_FUND} INR\n"
        f"2020-01-01 open {CASH} INR\n"
        'include "2025.beancount"\n',
        encoding="utf-8",
    )
    (journal / "2025.beancount").write_text(
        f"""\
2025-05-02 * "Beta Short Term Debt Fund - Direct Plan"
  txn-id: "t-held"
  {INVESTMENT_MUTUAL_FUND}     1000.00 INR
  {CASH}                      -1000.00 INR

2025-05-03 * "Legacy fund value"
  txn-id: "t-legacy"
  {INVESTMENT_MUTUAL_FUND}       10.00 INR
  {CASH}                        -10.00 INR
""",
        encoding="utf-8",
    )
    investment.main_file.write_text(
        'option "operating_currency" "INR"\n\n'
        "2000-01-01 commodity INR\n"
        f"2000-01-01 commodity {HELD}\n"
        '  name: "Beta Short Term Debt Fund - Direct Plan"\n'
        f'2000-01-01 open {holding_account(HELD)} "FIFO"\n'
        f"2000-01-01 open {clearing} INR\n"
        'include "investments-2025.beancount"\n',
        encoding="utf-8",
    )
    (journal / "investments-2025.beancount").write_text(
        f"""\
2025-05-02 * "Beta Short Term Debt Fund - Direct Plan"
  txn-id: "t-held"
  {holding_account(HELD)}    10 {HELD} {{100.00 INR, 2025-05-02}}
  {clearing}                -1000.00 INR
""",
        encoding="utf-8",
    )
    assert ledger.validate() == []
    assert investment.validate() == []
    return ledger, investment


def _add(ledger: Ledger, investment: Ledger, *lots: dict):
    return mf.add_lots(
        MfOpeningRequest(lots=[MfOpeningLot(**lot) for lot in lots]),
        ledger=ledger,
        investment_ledger=investment,
        scheme=_scheme,
        today=TODAY,
    )


def _lot(symbol: str = NEW, units: str = "100.1234", cost: str = "5000.00", **kw):
    return {
        "symbol": symbol,
        "units": units,
        "cost": cost,
        "date": date(2025, 6, 10),
    } | kw


def _hashes(ledger: Ledger) -> dict[str, str]:
    return {
        p.relative_to(ledger.journal_dir).as_posix(): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(ledger.journal_dir.rglob("*"))
        if p.is_file()
    }


def _snapshot_hashes(snapshot) -> dict[str, str]:
    root = snapshot / "journal"
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _snapshots(ledger: Ledger) -> int:
    if not ledger.snapshots_dir.exists():
        return 0
    return len(list(ledger.snapshots_dir.iterdir()))


def _gap(ledger: Ledger, investment: Ledger) -> Decimal:
    return Decimal(
        doctor.checks(ledger, investment, today=TODAY)["mf_reconciliation_gap"]
    )


def _txns(ledger: Ledger, txn_id: str) -> list[data.Transaction]:
    return [
        e
        for e in ledger.load(fresh=True).entries
        if isinstance(e, data.Transaction) and e.meta.get("txn-id") == txn_id
    ]


# ── the write ────────────────────────────────────────────────────────────────
def test_mf_lot_written_to_both_ledgers(tmp_path):
    ledger, investment = _ledgers(tmp_path)
    gap_before = _gap(ledger, investment)
    assert gap_before == Decimal("10.00")  # non-zero: "unchanged" is a real check
    before = _hashes(ledger)

    resp = _add(ledger, investment, _lot())

    assert ledger.validate() == []
    assert investment.validate() == []
    [w] = resp.written
    unit_cost = Decimal(w.unit_cost)
    cost_booked = Decimal(w.cost_booked)
    assert unit_cost == Decimal("49.93837604")
    assert unit_cost.as_tuple().exponent == -8
    assert cost_booked == Decimal("100.1234") * unit_cost
    assert abs(cost_booked - Decimal("5000")) < Decimal("0.01")
    # The booked figure, not the typed one: a ledger booking 5000.00 shows.
    assert cost_booked != Decimal("5000")
    assert (w.symbol, w.name, w.date) == (NEW, _SCHEMES[NEW].name, date(2025, 6, 10))
    assert w.account == holding_account(NEW)
    assert _SCHEMES[NEW].name in w.account_display

    # Investments ledger: the lot at its cost basis, dated the allotment.
    [inv] = _txns(investment, w.txn_id)
    assert inv.date == date(2025, 6, 10)
    assert inv.narration == f"Opening lot: {_SCHEMES[NEW].name}"
    assert inv.meta[OPENING_SOURCE_KEY] == "manual"
    holding, clearing = inv.postings
    assert holding.account == holding_account(NEW)
    assert (holding.units.number, holding.units.currency) == (Decimal("100.1234"), NEW)
    assert holding.cost.number == unit_cost
    assert holding.cost.currency == "INR"
    assert holding.cost.date == date(2025, 6, 10)
    assert clearing.account == platform_clearing_account(GROWW)
    assert clearing.units.number == -cost_booked

    # Main ledger: the same cost against opening equity, same txn-id.
    [main] = _txns(ledger, w.txn_id)
    assert main.date == date(2025, 6, 10)
    assert main.narration == f"Opening lot: {_SCHEMES[NEW].name}"
    assert main.meta[OPENING_SOURCE_KEY] == "manual"
    assert main.meta["commodity"] == NEW
    assert {p.account: p.units.number for p in main.postings} == {
        INVESTMENT_MUTUAL_FUND: cost_booked,
        EQUITY_OPENING: -cost_booked,
    }

    # Both ledgers moved by the same amount.
    assert _gap(ledger, investment) == gap_before
    # The response's restore point is the journal as it was before the write.
    snapshot = ledger.snapshots_dir / resp.snapshot
    assert _snapshot_hashes(snapshot) == before
    # The commodity is named after the scheme in the investments ledger.
    names = {
        e.currency: e.meta.get("name")
        for e in investment.load(fresh=True).entries
        if isinstance(e, data.Commodity)
    }
    assert names[NEW] == _SCHEMES[NEW].name


def test_mf_lot_books_cost_booked_when_it_differs_from_cost_by_paise(tmp_path):
    """A lot whose units x unit_cost is 5 paise below the typed cost: both
    ledgers carry the booked figure, so the gap stays unchanged."""
    ledger, investment = _ledgers(tmp_path)
    gap_before = _gap(ledger, investment)

    [w] = _add(
        ledger, investment, _lot(units="9876543.2109", cost="123456789.00")
    ).written

    cost_booked = Decimal(w.cost_booked)
    assert Decimal("123456789.00") - cost_booked >= Decimal("0.01")
    [main] = _txns(ledger, w.txn_id)
    assert {p.account: p.units.number for p in main.postings}[
        INVESTMENT_MUTUAL_FUND
    ] == cost_booked
    assert _gap(ledger, investment) == gap_before
    assert ledger.validate() == []
    assert investment.validate() == []


def test_mf_lots_several_in_one_request_share_one_restore_point(tmp_path):
    """Two lots of one scheme (bought on two dates: allowed) and one of
    another, in one request: three transaction pairs, one txn-id per pair,
    one restore point holding the journal as it was before."""
    ledger, investment = _ledgers(tmp_path)
    gap_before = _gap(ledger, investment)
    before = _hashes(ledger)

    resp = _add(
        ledger,
        investment,
        _lot(units="10", cost="1000.00", date=date(2024, 4, 2)),
        _lot(units="5.5", cost="700.00", date=date(2025, 1, 15)),
        _lot(OTHER, units="3", cost="100.00", date=date(2025, 2, 1)),
    )

    assert [w.symbol for w in resp.written] == [NEW, NEW, OTHER]
    assert len({w.txn_id for w in resp.written}) == 3
    for w in resp.written:
        assert len(_txns(ledger, w.txn_id)) == 1
        [inv] = _txns(investment, w.txn_id)
        assert inv.postings[0].cost.date == w.date
    assert _snapshot_hashes(ledger.snapshots_dir / resp.snapshot) == before
    assert _gap(ledger, investment) == gap_before
    assert ledger.validate() == []
    assert investment.validate() == []
    # The 2024 lot opened its year file in both ledgers.
    assert (ledger.journal_dir / "2024.beancount").is_file()
    assert (ledger.journal_dir / "investments-2024.beancount").is_file()


# ── refusals ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("lot", "field"),
    [
        (_lot("MF999999"), "symbol"),
        (_lot(HELD), "symbol"),
        (_lot(units="0"), "units"),
        (_lot(units="1.12345"), "units"),
        (_lot(units="-1"), "units"),
        (_lot(units="1e3"), "units"),
        (_lot(cost="0"), "cost"),
        (_lot(cost="1.001"), "cost"),
        (_lot(cost="0.00"), "cost"),
        (_lot(date=date(2026, 6, 16)), "date"),
    ],
)
def test_mf_lot_refusals(tmp_path, lot, field):
    """Each refusal is E1092 naming the field; it is checked for every lot
    before anything is written (a valid lot before the bad one is not
    written either), and it takes no snapshot."""
    ledger, investment = _ledgers(tmp_path)
    before = _hashes(ledger)
    snapshots = _snapshots(ledger)

    with pytest.raises(CodedError) as caught:
        _add(ledger, investment, _lot(OTHER, units="1", cost="10.00"), lot)

    assert caught.value.code == "E1092"
    assert caught.value.data == {
        "symbol": lot["symbol"],
        "field": field,
        "reason": caught.value.data["reason"],
    }
    assert _hashes(ledger) == before
    assert _snapshots(ledger) == snapshots


def test_mf_lot_today_is_allowed(tmp_path):
    ledger, investment = _ledgers(tmp_path)
    [w] = _add(ledger, investment, _lot(date=TODAY)).written
    assert w.date == TODAY


def test_mf_lot_refused_for_a_scheme_that_already_has_opening_lots(tmp_path):
    """A second request for a scheme that now has an opening lot is refused:
    that lot is an investments-ledger posting."""
    ledger, investment = _ledgers(tmp_path)
    _add(ledger, investment, _lot())
    before = _hashes(ledger)

    with pytest.raises(CodedError) as caught:
        _add(ledger, investment, _lot(units="1", cost="50.00"))

    assert caught.value.code == "E1092"
    assert caught.value.data["field"] == "symbol"
    assert _hashes(ledger) == before


def test_concurrent_lots_for_one_scheme_write_once(tmp_path, monkeypatch):
    """The scheme check runs under both write locks, held through the write:
    two concurrent requests for one new scheme write one, refuse the other."""
    ledger, investment = _ledgers(tmp_path)
    inside = threading.Event()
    real = mf._new_txn_id

    def slow_id() -> str:
        # Runs between the checks and the snapshot.
        inside.set()
        time.sleep(0.3)
        return real()

    monkeypatch.setattr(mf, "_new_txn_id", slow_id)
    results: list[str] = []

    def run() -> None:
        try:
            _add(ledger, investment, _lot())
            results.append("ok")
        except CodedError as exc:
            results.append(exc.code)

    first = threading.Thread(target=run)
    first.start()
    assert inside.wait(5)
    second = threading.Thread(target=run)
    second.start()
    first.join(10)
    second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert sorted(results) == ["E1092", "ok"]
    assert len(mf.schemes_with_opening_lots(investment.load(fresh=True).entries)) == 1


# ── restore on a failed write ────────────────────────────────────────────────
def _assert_lot_on_disk(investment: Ledger) -> None:
    lots = [
        p
        for e in investment.load(fresh=True).entries
        if isinstance(e, data.Transaction)
        for p in e.postings
        if p.account == holding_account(NEW)
    ]
    assert lots, "the investments write had not happened"


def test_mf_lot_second_write_failure_restores_both(tmp_path, monkeypatch):
    """The main append raises after the investments write landed: every
    file under journal/ is put back byte-identical and the error propagates."""
    ledger, investment = _ledgers(tmp_path)
    before = _hashes(ledger)

    def boom(entries: list) -> None:
        _assert_lot_on_disk(investment)
        raise RuntimeError("main append failed")

    monkeypatch.setattr(ledger, "append", boom)
    with pytest.raises(RuntimeError, match="main append failed"):
        _add(ledger, investment, _lot())

    assert _hashes(ledger) == before
    # A cached read sees the restored journal too.
    assert mf.schemes_with_opening_lots(investment.load().entries) == frozenset()
    assert ledger.validate() == []
    assert investment.validate() == []


def test_mf_lot_main_validation_failure_restores_both(tmp_path, monkeypatch):
    """The main append fails its own validation (it undoes only its own
    write): the investments write is undone too."""
    ledger, investment = _ledgers(tmp_path)
    before = _hashes(ledger)
    real = ledger._validate_after_write

    def invalid() -> list[str]:
        real()
        _assert_lot_on_disk(investment)
        return ["synthetic validation error"]

    monkeypatch.setattr(ledger, "_validate_after_write", invalid)
    with pytest.raises(LedgerValidationError):
        _add(ledger, investment, _lot())

    assert _hashes(ledger) == before


def test_mf_lot_failure_after_commodity_accounts_restores(tmp_path, monkeypatch):
    """ensure_commodity_accounts wrote the commodity and holding account,
    then failed: the investments main file is put back."""
    ledger, investment = _ledgers(tmp_path)
    before = _hashes(ledger)
    real = investment.ensure_commodity_accounts

    def ensure_then_fail(specs, *args, **kwargs) -> None:
        real(specs, *args, **kwargs)
        assert f"commodity {NEW}" in investment.main_file.read_text(encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(investment, "ensure_commodity_accounts", ensure_then_fail)
    with pytest.raises(OSError, match="disk full"):
        _add(ledger, investment, _lot())

    assert _hashes(ledger) == before


# ── the Groww import's soft flag ─────────────────────────────────────────────
_GROWW_HEAD = """Personal Details
Name,Jane Doe

TRANSACTIONS FROM Apr 01 2025 TO Mar 31 2026

Scheme Name,Transaction Type,Units,NAV,Amount,Date
"""


def _import_service(tmp_path, ledger: Ledger, investment: Ledger) -> ImportService:
    master = tmp_path / "scheme_master.csv"
    master.write_text(
        "".join(f"{m.code};{m.name};100.00;28-Jun-2026\n" for m in _SCHEMES.values()),
        encoding="utf-8",
    )
    return ImportService(
        ledger=ledger,
        investment_ledger=investment,
        categorizer=Categorizer(
            MerchantDictionary.from_seed(),
            LearnedRules(ledger.storage_dir / "rules" / "learned.yaml"),
        ),
        review_store=ReviewStore(),
        template_store=TemplateStore(ledger.storage_dir / "templates"),
        scheme_master_path=master,
    )


def test_groww_mf_scan_flags_scheme_with_opening_lots(tmp_path, monkeypatch):
    ledger, investment = _ledgers(tmp_path)
    _add(ledger, investment, _lot())
    svc = _import_service(tmp_path, ledger, investment)
    calls: list[int] = []
    real = ingest_service.schemes_with_opening_lots

    def counted(entries: list) -> frozenset[str]:
        calls.append(1)
        return real(entries)

    monkeypatch.setattr(ingest_service, "schemes_with_opening_lots", counted)
    rows = (
        f"{_SCHEMES[NEW].name},PURCHASE,10.0,100.0,1000,18 Mar 2026\n"
        f"{_SCHEMES[OTHER].name},PURCHASE,10.0,100.0,1000,18 Mar 2026\n"
        f"{_SCHEMES[NEW].name},PURCHASE,2.0,100.0,200,19 Mar 2026\n"
    )
    staged = svc.detect("orders.csv", (_GROWW_HEAD + rows).encode())

    assert staged["source"] == "groww"
    flagged, other, again = staged["candidates"]  # nothing dropped
    assert (flagged["commodity"], other["commodity"]) == (NEW, OTHER)
    assert MF_OPENING_FLAG in flagged["flags"]
    assert MF_OPENING_FLAG in again["flags"]
    assert MF_OPENING_FLAG not in other["flags"]
    assert calls == [1]  # read once per scan, not per row
    # Advisory only: the stored batch keeps every row, flag included.
    stored = svc._review_store.get_batch(staged["batch_id"])
    assert [MF_OPENING_FLAG in c.flags for c in stored] == [True, False, True]


def test_groww_mf_scan_without_opening_lots_flags_nothing(tmp_path):
    ledger, investment = _ledgers(tmp_path)
    svc = _import_service(tmp_path, ledger, investment)
    rows = f"{_SCHEMES[NEW].name},PURCHASE,10.0,100.0,1000,18 Mar 2026\n"
    staged = svc.detect("orders.csv", (_GROWW_HEAD + rows).encode())
    [row] = staged["candidates"]
    assert MF_OPENING_FLAG not in row["flags"]


def test_schemes_with_opening_lots_ignores_imported_orders(tmp_path):
    """Only opening-source transactions count: HELD's imported order is not
    an opening lot."""
    ledger, investment = _ledgers(tmp_path)
    assert mf.schemes_with_opening_lots(investment.load().entries) == frozenset()
    _add(ledger, investment, _lot())
    assert mf.schemes_with_opening_lots(investment.load().entries) == {NEW}


# ── the symbol as the scheme master knows it ─────────────────────────────────
def _add_real_scheme(tmp_path, ledger: Ledger, investment: Ledger, *lots: dict):
    """add_lots with the app's own lookup, `ImportService.scheme_by_symbol`
    over a synthetic scheme master (it also resolves a bare AMFI code)."""
    svc = _import_service(tmp_path, ledger, investment)
    return mf.add_lots(
        MfOpeningRequest(lots=[MfOpeningLot(**lot) for lot in lots]),
        ledger=ledger,
        investment_ledger=investment,
        scheme=svc.scheme_by_symbol,
        today=TODAY,
    )


def test_mf_lot_bare_code_of_a_held_scheme_is_refused(tmp_path):
    """ "200002" names HELD (MF200002), which already has a posting: E1092 on
    symbol, nothing written, no snapshot."""
    ledger, investment = _ledgers(tmp_path)
    before = _hashes(ledger)
    snapshots = _snapshots(ledger)

    with pytest.raises(CodedError) as caught:
        _add_real_scheme(tmp_path, ledger, investment, _lot("200002"))

    assert caught.value.code == "E1092"
    assert caught.value.data["field"] == "symbol"
    assert caught.value.data["symbol"] == HELD
    assert _hashes(ledger) == before
    assert _snapshots(ledger) == snapshots


def test_mf_lot_bare_code_is_written_under_the_scheme_symbol(tmp_path):
    ledger, investment = _ledgers(tmp_path)
    [w] = _add_real_scheme(tmp_path, ledger, investment, _lot("100001")).written
    assert w.symbol == NEW
    assert w.account == holding_account(NEW)
    [main] = _txns(ledger, w.txn_id)
    assert main.meta["commodity"] == NEW
    assert mf.schemes_with_opening_lots(investment.load(fresh=True).entries) == {NEW}


# ── a later Groww sale consumes the opening lot ──────────────────────────────
def test_groww_redemption_sells_from_the_opening_lot(tmp_path):
    """An opening lot, then a Groww REDEEM of part of it committed through
    the import: the sale books against the lot's cost (FIFO), both ledgers
    validate, and the realised gain is proceeds - units_sold x unit_cost."""
    ledger, investment = _ledgers(tmp_path)
    [w] = _add(ledger, investment, _lot()).written
    unit_cost = Decimal(w.unit_cost)
    svc = _import_service(tmp_path, ledger, investment)
    sold, proceeds = Decimal("40.0"), Decimal("2400")
    row = f"{_SCHEMES[NEW].name},REDEEM,{sold},60.0,{proceeds},18 Mar 2026\n"

    staged = svc.detect("orders.csv", (_GROWW_HEAD + row).encode())
    [cand] = staged["candidates"]
    assert not cand["uncovered_redemption"]
    assert MF_OPENING_FLAG in cand["flags"]
    assert svc.commit(staged["batch_id"])["committed"] == 1

    assert ledger.validate() == []
    assert investment.validate() == []
    [sale] = [
        e
        for e in investment.load(fresh=True).entries
        if isinstance(e, data.Transaction) and e.meta.get("txn-id") == cand["id"]
    ]
    [out] = [p for p in sale.postings if p.account == holding_account(NEW)]
    assert out.units.number == -sold
    assert (out.cost.number, out.cost.currency, out.cost.date) == (
        unit_cost,
        "INR",
        date(2025, 6, 10),
    )
    [main] = _txns(ledger, cand["id"])
    gain = -sum(
        (
            p.units.number
            for p in main.postings
            if p.account.startswith("Income:CapitalGains:")
        ),
        Decimal(0),
    )
    assert gain == proceeds - sold * unit_cost
    mf_leg = {p.account: p.units.number for p in main.postings}[INVESTMENT_MUTUAL_FUND]
    assert mf_leg == -(sold * unit_cost)
