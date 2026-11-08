"""Every Groww cash/clearing site follows Settings.platform_*_accounts.

The defaults ({"groww": "Assets:Broker:Groww"} for the wallet and
{"groww": "Assets:Investment:MutualFund:Groww:Cash"} for the MF clearing
account) are what every other Groww test exercises. These tests move each
account and check that every site books, routes or proposes the configured
one, and none falls back to the default.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.core import settings
from backend.app.models.domain.accounts import OPERATING_CURRENCY
from backend.app.models.domain.candidate import CandidateTxn, DedupStatus, Direction
from backend.app.persistence.journal import Ledger
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.persistence.review_batches import ReviewStore
from backend.app.persistence.templates import TemplateStore
from backend.app.service.categorize.categorizer import Categorizer
from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.service.ingest.parsers.groww_stocks import parse_groww_stocks
from backend.app.service.ingest.pipeline.dedup import Deduper
from backend.app.core.paths import REPO_ROOT
from backend.app.models.domain.ledger_cli import ParsedPosting, ParsedTxn
from backend.app.service.ingest.ledger_merge.mf_adapter import mf_leg_to_candidate
from backend.app.service.ingest.parsers.groww_mf import parse_groww
from backend.app.service.ingest.parsers.scheme_master import SchemeMaster
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.investments.service import InvestmentService
from backend.app.service.ingest.service import ImportService

DEFAULT = "Assets:Broker:Groww"
OVERRIDE = "Assets:Broker:Test"
SUBSYSTEM_CLEARING = "Assets:Investment:MutualFund:Groww:Cash"
CLEARING_OVERRIDE = "Assets:Investment:MutualFund:Test:Cash"
ORDERS = REPO_ROOT / "tests" / "fixtures" / "statements" / "groww_orders.csv"
MASTER = REPO_ROOT / "tests" / "fixtures" / "scheme_master.csv"
STOCKS = Path("tests/fixtures/statements/groww_stocks.csv")


def _configure(
    monkeypatch, request, accounts: str, var: str = "SPENDSIFT_PLATFORM_CASH_ACCOUNTS"
) -> None:
    monkeypatch.setenv(var, accounts)
    settings.get_settings.cache_clear()
    request.addfinalizer(settings.get_settings.cache_clear)


@pytest.fixture
def groww_test_wallet(monkeypatch, request):
    _configure(monkeypatch, request, '{"groww": "%s"}' % OVERRIDE)


@pytest.fixture
def groww_test_clearing(monkeypatch, request):
    _configure(
        monkeypatch,
        request,
        '{"groww": "%s"}' % CLEARING_OVERRIDE,
        var="SPENDSIFT_PLATFORM_CLEARING_ACCOUNTS",
    )


def _accounts(led) -> set[str]:
    return {p.account for e in led.load().entries for p in getattr(e, "postings", ())}


def _inr_legs(led, txn_id: str) -> dict[str, Decimal]:
    entries = [
        e
        for e in led.load().entries
        if getattr(e, "meta", {}).get("txn-id") == txn_id and hasattr(e, "postings")
    ]
    assert len(entries) == 1
    legs: dict[str, Decimal] = {}
    for p in entries[0].postings:
        if p.units is not None and p.units.currency == OPERATING_CURRENCY:
            legs[p.account] = legs.get(p.account, Decimal(0)) + p.units.number
    return legs


def _bank_debit(narration: str) -> CandidateTxn:
    return CandidateTxn(
        date=date(2026, 3, 18),
        amount=Decimal("21500.00"),
        raw_narration=narration,
        normalized_narration="GROWW",
        source_account="Assets:Checking:HDFC",
        direction=Direction.DEBIT,
    )


def test_default_is_the_groww_broker_wallet(monkeypatch):
    monkeypatch.delenv("SPENDSIFT_PLATFORM_CASH_ACCOUNTS", raising=False)
    assert settings.Settings().platform_cash_accounts == {"groww": DEFAULT}


def test_default_clearing_is_the_groww_mf_clearing_account(monkeypatch):
    monkeypatch.delenv("SPENDSIFT_PLATFORM_CLEARING_ACCOUNTS", raising=False)
    assert settings.Settings().platform_clearing_accounts == {
        "groww": SUBSYSTEM_CLEARING
    }


def test_clearing_env_override_parses_a_json_object(monkeypatch):
    monkeypatch.setenv(
        "SPENDSIFT_PLATFORM_CLEARING_ACCOUNTS", '{"groww": "%s"}' % CLEARING_OVERRIDE
    )
    assert settings.Settings().platform_clearing_accounts == {
        "groww": CLEARING_OVERRIDE
    }


def test_env_override_parses_a_json_object(monkeypatch):
    monkeypatch.setenv(
        "SPENDSIFT_PLATFORM_CASH_ACCOUNTS",
        '{"groww": "Assets:Broker:Test", "kite": "Assets:Broker:Kite"}',
    )
    assert settings.Settings().platform_cash_accounts == {
        "groww": OVERRIDE,
        "kite": "Assets:Broker:Kite",
    }


def test_a_groww_mf_commit_books_its_cash_leg_to_the_configured_wallet(
    groww_test_wallet, ledger, investment_ledger
):
    purchase = CandidateTxn(
        id="mf-buy",
        date=date(2025, 5, 5),
        amount=Decimal("15000.00"),
        raw_narration="Purchase — Example ELSS Fund",
        normalized_narration="EXAMPLE ELSS FUND",
        source_account=SUBSYSTEM_CLEARING,
        direction=Direction.DEBIT,
        units=Decimal("512.000"),
        commodity="MF999001",
        unit_cost=Decimal("29.2945"),
    )
    commit_candidates(ledger, investment_ledger, [purchase])

    assert ledger.validate() == []
    assert investment_ledger.validate() == []
    legs = _inr_legs(ledger, "mf-buy")
    assert legs[OVERRIDE] < 0
    assert DEFAULT not in _accounts(ledger)
    # The subsystem entry swaps the configured wallet for its clearing leg.
    assert SUBSYSTEM_CLEARING in _accounts(investment_ledger)
    assert OVERRIDE not in _accounts(investment_ledger)
    assert DEFAULT not in _accounts(investment_ledger)


def test_a_groww_stock_parse_and_commit_use_the_configured_wallet(
    groww_test_wallet, ledger, investment_ledger
):
    candidates = parse_groww_stocks(STOCKS).candidates
    assert candidates
    assert {c.source_account for c in candidates} == {OVERRIDE}
    buys = [c for c in candidates if c.direction == Direction.DEBIT]
    assert buys

    commit_candidates(ledger, investment_ledger, buys)

    assert ledger.validate() == []
    assert OVERRIDE in _accounts(ledger)
    assert DEFAULT not in _accounts(ledger)


def test_the_import_service_routes_stock_rows_on_the_configured_wallet(
    groww_test_wallet, tmp_path
):
    """detect -> review -> commit: the service's stock routing and new-symbol
    holds recognise rows on the configured wallet as stock rows."""
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    investment_ledger = Ledger(
        ledger.storage_dir,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    investment_ledger.bootstrap(seed_accounts=())
    svc = ImportService(
        ledger=ledger,
        investment_ledger=investment_ledger,
        categorizer=Categorizer(
            MerchantDictionary.from_seed(),
            LearnedRules(ledger.storage_dir / "rules" / "learned.yaml"),
        ),
        review_store=ReviewStore(),
        template_store=TemplateStore(ledger.storage_dir / "templates"),
        # Absent on purpose: a pure-stock commit never loads the scheme master,
        # so it only succeeds if the service sees these rows as stock rows.
        scheme_master_path=tmp_path / "no_scheme_master.csv",
    )

    detected = svc.detect("groww_stocks.csv", STOCKS.read_bytes())
    assert detected["source"] == "groww_stocks"
    batch_id = detected["batch_id"]
    held = [c for c in svc._review_store.get_batch(batch_id) if c.unresolved_scheme]
    assert held, "a brand-new symbol on the configured wallet is held for review"
    for cand in held:
        svc._review_store.update(batch_id, cand.id, {"unresolved_scheme": None})

    assert svc.commit(batch_id)["committed"] == len(detected["candidates"])
    assert OVERRIDE in _accounts(ledger)
    assert DEFAULT not in _accounts(ledger)
    assert _accounts(investment_ledger) == set()


def test_a_groww_funding_narration_suggests_the_configured_wallet(groww_test_wallet):
    c = _bank_debit("UPI/DR/GROWW/INVESTMENT/SIP")
    Deduper(set()).mark([c])
    assert c.dedup_status == DedupStatus.TRANSFER_MATCH
    assert c.suggested_other_leg == OVERRIDE


def test_each_configured_platform_key_is_its_own_funding_word(monkeypatch, request):
    _configure(
        monkeypatch,
        request,
        '{"groww": "Assets:Broker:Groww", "kite": "Assets:Broker:Kite"}',
    )
    kite = _bank_debit("NEFT/ZERODHA KITE/FUNDS")
    groww = _bank_debit("upi/dr/groww/sip")
    neither = _bank_debit("UPI/DR/KITEWORKS/412")
    Deduper(set()).mark([kite, groww, neither])
    assert kite.suggested_other_leg == "Assets:Broker:Kite"
    assert groww.suggested_other_leg == DEFAULT
    assert neither.dedup_status == DedupStatus.NEW
    assert neither.suggested_other_leg is None


def test_the_cash_balance_route_reads_the_configured_wallet(
    groww_test_wallet, tmp_path
):
    from fastapi.testclient import TestClient

    from backend.app.server.main import create_app

    client = TestClient(create_app(storage_dir=tmp_path))
    r = client.get("/api/import/investment/cash-balance")
    assert r.status_code == 200
    assert r.json()["account"] == OVERRIDE


# --- the MF clearing account -------------------------------------------------


def test_a_groww_mf_parse_and_commit_use_the_configured_clearing_account(
    groww_test_clearing, ledger, investment_ledger
):
    """parse_groww stamps the configured clearing account and the commit routes
    on it. investments.beancount books every lot's clearing leg there; the
    main ledger's vehicle entries book the (default) wallet, and the one row
    whose scheme the master cannot resolve (no commodity, so a plain entry on
    its source account) books the configured clearing account in the main
    ledger too. The default clearing account appears in neither."""
    candidates = parse_groww(ORDERS, SchemeMaster.from_csv(MASTER))
    lots = [c for c in candidates if c.units is not None]
    assert lots
    assert {c.source_account for c in lots} == {CLEARING_OVERRIDE}
    unresolved = [c for c in lots if c.commodity is None]
    assert len(unresolved) == 1

    commit_candidates(ledger, investment_ledger, lots, SchemeMaster.from_csv(MASTER))

    assert ledger.validate() == []
    assert investment_ledger.validate() == []
    inv_accounts = _accounts(investment_ledger)
    assert CLEARING_OVERRIDE in inv_accounts
    assert SUBSYSTEM_CLEARING not in inv_accounts
    main_accounts = _accounts(ledger)
    assert DEFAULT in main_accounts
    assert SUBSYSTEM_CLEARING not in main_accounts
    assert _inr_legs(ledger, unresolved[0].id)[CLEARING_OVERRIDE] < 0

    # The valuation read side finds the cash flows on the configured account.
    svc = InvestmentService(investment_ledger, nav_store=None)
    _inventories, cashflows = svc._lots_and_cashflows(investment_ledger.load().entries)
    assert cashflows
    assert all(flows for flows in cashflows.values())


def test_a_ledger_merge_mf_leg_with_no_cash_leg_uses_the_configured_clearing(
    groww_test_clearing,
):
    mf_leg = ParsedPosting(
        account="Assets:Investment:MutualFund:PPFAS",
        amount=Decimal("10"),
        commodity="MF122639",
        cost=Decimal("80"),
        cost_currency="INR",
        comment=None,
    )
    elided = ParsedPosting(
        account="Assets:Checking:HDFC",
        amount=None,
        commodity=None,
        cost=None,
        cost_currency=None,
        comment=None,
    )
    txn = ParsedTxn(
        date=date(2026, 3, 18),
        narration="Buy PPFAS",
        postings=[mf_leg, elided],
        source_line=1,
    )
    candidate, _extra = mf_leg_to_candidate(
        txn,
        mf_leg,
        {"Assets:Checking:HDFC": "Assets:Checking:HDFC"},
        {"MF122639": "MF122639"},
    )
    assert candidate.source_account == CLEARING_OVERRIDE


# --- a clearing account not named ":Cash", and other overrides ---------------

SETTLEMENT = "Assets:Investment:MutualFund:Groww:Settlement"


@pytest.fixture
def groww_settlement_clearing(monkeypatch, request):
    _configure(
        monkeypatch,
        request,
        '{"groww": "%s"}' % SETTLEMENT,
        var="SPENDSIFT_PLATFORM_CLEARING_ACCOUNTS",
    )


def _commit_resolved_mf(ledger, investment_ledger) -> None:
    master = SchemeMaster.from_csv(MASTER)
    rows = [
        c
        for c in parse_groww(ORDERS, master)
        if c.units is not None and c.commodity is not None
    ]
    assert rows
    commit_candidates(ledger, investment_ledger, rows, master)


def test_doctor_reconciles_mf_with_the_default_clearing(ledger, investment_ledger):
    from backend.app.service.reports.health import doctor

    _commit_resolved_mf(ledger, investment_ledger)
    out = doctor.checks(ledger, investment_ledger)
    assert out["mf_reconciliation_gap"] == "0.00"


def test_doctor_excludes_a_clearing_account_by_name_not_by_its_cash_suffix(
    groww_settlement_clearing, ledger, investment_ledger
):
    from backend.app.service.reports.health import doctor

    _commit_resolved_mf(ledger, investment_ledger)
    assert SETTLEMENT in _accounts(investment_ledger)
    out = doctor.checks(ledger, investment_ledger)
    assert out["mf_reconciliation_gap"] == "0.00"
    assert out["investment_ledger_valid"] is True


def test_holdings_never_list_the_configured_clearing_account(
    groww_settlement_clearing,
):
    from backend.app.service.reports.health.ledger_view import PostingRow
    from backend.app.service.reports.investments.investments import holdings

    day = date(2026, 3, 18)
    rows = [
        PostingRow(day, SETTLEMENT, Decimal("500"), "redeem", frozenset()),
        PostingRow(
            day, "Assets:Investment:FD:Bank:A", Decimal("100"), "fd", frozenset()
        ),
    ]
    out = holdings(rows)
    assert out["total"] == "100"
    assert [h["account"] for h in out["holdings"]] == ["Assets:Investment:FD:Bank:A"]


def test_an_mf_row_on_the_configured_clearing_is_not_a_funding_transfer(
    monkeypatch, request
):
    """The reviewer's case: a clearing account outside Assets:Investment, and an
    MF order whose fund name contains the platform word."""
    _configure(
        monkeypatch,
        request,
        '{"groww": "Assets:Clearing:Groww"}',
        var="SPENDSIFT_PLATFORM_CLEARING_ACCOUNTS",
    )
    mf = CandidateTxn(
        date=date(2026, 3, 18),
        amount=Decimal("1000.00"),
        raw_narration="Purchase — Groww Nifty Total Market Index Fund",
        normalized_narration="GROWW NIFTY TOTAL MARKET INDEX FUND",
        source_account="Assets:Clearing:Groww",
        direction=Direction.DEBIT,
    )
    bank = _bank_debit("UPI/DR/GROWW/INVESTMENT/SIP")
    Deduper(set()).mark([mf, bank])
    assert mf.dedup_status == DedupStatus.NEW
    assert mf.suggested_other_leg is None
    assert bank.dedup_status == DedupStatus.TRANSFER_MATCH
    assert bank.suggested_other_leg == DEFAULT


@pytest.mark.parametrize(
    ("var", "setting", "accessor"),
    [
        (
            "SPENDSIFT_PLATFORM_CASH_ACCOUNTS",
            "platform_cash_accounts",
            "platform_cash_account",
        ),
        (
            "SPENDSIFT_PLATFORM_CLEARING_ACCOUNTS",
            "platform_clearing_accounts",
            "platform_clearing_account",
        ),
    ],
)
def test_a_missing_platform_key_is_a_named_configuration_error(
    monkeypatch, request, var, setting, accessor
):
    from backend.app.service.ingest import platform_accounts

    _configure(monkeypatch, request, '{"kite": "Assets:Broker:Kite"}', var=var)
    with pytest.raises(ValueError) as raised:
        getattr(platform_accounts, accessor)("groww")
    message = str(raised.value)
    assert setting in message
    assert "'groww'" in message
    assert var in message
