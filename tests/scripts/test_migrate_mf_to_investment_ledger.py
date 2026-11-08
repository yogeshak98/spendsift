"""Migration script tests -- run ENTIRELY against synthetic tmp_path fixtures.

Never point this test (or the script it tests) at the real repo `storage/`
directory. Every fixture here is hand-built via `commit._commodity_entry`
(the legacy pre-split posting shape) with fictional symbols/amounts, mirroring
Jane-Doe-persona conventions used elsewhere in this repo's tests.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from beancount import loader
from beancount.core import data

from backend.app.models.storage.merge_booking import MergeBookingConfig
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import (
    CAPITAL_GAINS,
    CG_UNCLASSIFIED,
    _commodity_entry,
    _subsystem_entry,
    _vehicle_entry,
)
from backend.app.models.domain.accounts import EQUITY_OPENING
from backend.app.persistence.journal import Ledger
from backend.app.service.reports.health import doctor
from backend.app.service.reports.health.ledger_view import all_postings
from scripts.migrate_mf_to_investment_ledger import (
    _MIGRATION_MARKER,
    MigrationAlreadyRunError,
    MigrationReconciliationError,
    migrate,
)

# The default Groww wallet (Settings.platform_cash_accounts["groww"]).
GROWW_CASH = "Assets:Broker:Groww"

SYMBOL = "MF900001"
VEHICLE_ACCOUNT = "Assets:Investment:MutualFund"
HOLDING_ACCOUNT = f"Assets:Investment:MutualFund:Groww:{SYMBOL}"


def _investment_ledger(storage_dir: Path) -> Ledger:
    return Ledger(
        storage_dir,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )


def _build_legacy_fixture(storage_dir: Path) -> Ledger:
    """Two PURCHASEs + one partial REDEEM, all pre-split (legacy) shape,
    committed directly into main.beancount -- the exact state this migration
    targets."""
    ledger = Ledger(storage_dir)
    ledger.bootstrap()

    purchase1 = CandidateTxn(
        date=date(2024, 4, 10),
        amount=Decimal("5000.00"),
        raw_narration="Jane Doe SIP - Fictional Flexi Cap Fund",
        normalized_narration="jane doe sip fictional flexi cap fund",
        source_account=GROWW_CASH,
        direction=Direction.DEBIT,
        units=Decimal("100"),
        commodity=SYMBOL,
        unit_cost=Decimal("50.00"),
    )
    purchase2 = CandidateTxn(
        date=date(2024, 5, 10),
        amount=Decimal("2750.00"),
        raw_narration="Jane Doe SIP - Fictional Flexi Cap Fund",
        normalized_narration="jane doe sip fictional flexi cap fund",
        source_account=GROWW_CASH,
        direction=Direction.DEBIT,
        units=Decimal("50"),
        commodity=SYMBOL,
        unit_cost=Decimal("55.00"),
    )
    redeem = CandidateTxn(
        date=date(2025, 4, 10),
        amount=Decimal("7200.00"),
        raw_narration="Jane Doe REDEEM - Fictional Flexi Cap Fund",
        normalized_narration="jane doe redeem fictional flexi cap fund",
        source_account=GROWW_CASH,
        direction=Direction.CREDIT,
        units=Decimal("120"),
        commodity=SYMBOL,
        unit_cost=Decimal("60.00"),
    )

    ledger.ensure_commodity_accounts([(SYMBOL, "Fictional Flexi Cap Fund")])
    ledger.append(
        [
            _commodity_entry(purchase1),
            _commodity_entry(purchase2),
            _commodity_entry(redeem),
        ]
    )
    return ledger


# FIFO: redeem 120 units consumes 100@50 (purchase1) + 20@55 (purchase2).
EXPECTED_TOTAL_LOT_COST = Decimal("100") * Decimal("50.00") + Decimal("20") * Decimal(
    "55.00"
)  # 6100.00
EXPECTED_GAIN = Decimal("7200.00") - EXPECTED_TOTAL_LOT_COST  # 1100.00
EXPECTED_REMAINING_HOLDING = Decimal("30") * Decimal("55.00")  # 1650.00


def _cg_bucket_total(ledger: Ledger, account: str) -> Decimal:
    return sum(
        (-r.inr_amount for r in all_postings(ledger) if r.account == account),
        Decimal(0),
    )


def test_migrate_splits_legacy_mf_into_investment_ledger(tmp_path: Path) -> None:
    storage_dir = tmp_path / "storage"
    ledger = _build_legacy_fixture(storage_dir)
    investment_ledger = _investment_ledger(storage_dir)

    report = migrate(storage_dir)

    assert report.success
    assert report.migrated_count == 3

    # (a) investments.beancount gets the exact lot postings.
    inv_rows = all_postings(investment_ledger)
    holding_rows = [r for r in inv_rows if r.account == HOLDING_ACCOUNT]
    purchase_costs = sorted(r.inr_amount for r in holding_rows if r.amount > 0)
    assert purchase_costs == [Decimal("2750.00"), Decimal("5000.00")]
    consumed_costs = sorted((-r.inr_amount for r in holding_rows if r.amount < 0))
    assert consumed_costs == [Decimal("1100.00"), Decimal("5000.00")]
    clearing_rows = [
        r for r in inv_rows if r.account == "Assets:Investment:MutualFund:Groww:Cash"
    ]
    assert len(clearing_rows) == 3  # one clearing leg per migrated transaction

    # No per-scheme MF account remains in main.beancount.
    main_rows = all_postings(ledger)
    assert not any(r.account == HOLDING_ACCOUNT for r in main_rows)
    assert not any(r.account == CAPITAL_GAINS for r in main_rows)

    # (b) main.beancount's vehicle line balance == pre-migration holdings total.
    vehicle_total = sum(
        (r.inr_amount for r in main_rows if r.account == VEHICLE_ACCOUNT), Decimal(0)
    )
    assert vehicle_total == EXPECTED_REMAINING_HOLDING

    # (c) capital-gains total reconciles (no scheme_master -> "other" -> Debt bucket).
    assert _cg_bucket_total(ledger, CG_UNCLASSIFIED) == EXPECTED_GAIN

    # doctor reconciliation is clean post-migration.
    doctor_out = doctor.checks(ledger, investment_ledger)
    assert Decimal(doctor_out["mf_reconciliation_gap"]) == 0
    assert doctor_out["investment_ledger_valid"]
    assert not ledger.validate()
    assert not investment_ledger.validate()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    storage_dir = tmp_path / "storage"
    _build_legacy_fixture(storage_dir)
    investment_ledger = _investment_ledger(storage_dir)

    migrate(storage_dir)

    # (a) every transaction migrate() wrote into investments.beancount carries
    # the migration marker -- the mechanism `_already_migrated()` keys off.
    inv_entries = investment_ledger.load().entries
    marked = [
        e
        for e in inv_entries
        if isinstance(e, data.Transaction)
        and e.meta.get("migration") == _MIGRATION_MARKER
    ]
    assert len(marked) == 3  # 2 purchases + 1 redeem

    # (b) a second run is refused (idempotency preserved).
    with pytest.raises(MigrationAlreadyRunError):
        migrate(storage_dir)


def test_migrate_not_falsely_refused_by_preexisting_non_migration_mf_activity(
    tmp_path: Path,
) -> None:
    """A real `investments.beancount` may already contain legitimate,
    non-migration MF commodities by the time someone runs this historical
    migration -- e.g. a fresh Groww import via the live Unit 3/4 pipeline, or
    a ledger-cli merge via the live Unit 7 pipeline, landing before this
    one-time script is ever run against real storage/ (the live paths were
    built first and the historical migration last; see the module docstring).
    The idempotency guard must key off this script's own migration marker,
    not "any MF* commodity present", so that scenario does not falsely refuse
    to run."""
    storage_dir = tmp_path / "storage"
    _build_legacy_fixture(storage_dir)
    investment_ledger = _investment_ledger(storage_dir)
    investment_ledger.bootstrap(seed_accounts=())

    # Simulate forward-going (non-migration) MF activity already landed in
    # investments.beancount via the live commit path -- a different symbol,
    # written the same way `commit_candidates` writes it, with no migration
    # marker.
    live_symbol = "MF999999"
    live_candidate = CandidateTxn(
        date=date(2025, 6, 1),
        amount=Decimal("1000.00"),
        raw_narration="Jane Doe SIP - Fictional Debt Fund",
        normalized_narration="jane doe sip fictional debt fund",
        source_account=GROWW_CASH,
        direction=Direction.DEBIT,
        units=Decimal("10"),
        commodity=live_symbol,
        unit_cost=Decimal("100.00"),
    )
    investment_ledger.ensure_commodity_accounts([(live_symbol, "Fictional Debt Fund")])
    investment_ledger.append([_subsystem_entry(live_candidate)])
    # Mirror commit_candidates's own pairing: the paired coarse vehicle-level
    # posting in main.beancount, so this synthetic fixture reconciles cleanly
    # (matching the real live pipeline's shape) and does not introduce an
    # unrelated holdings-gap artifact of its own. `append`'s own naive
    # "already open" substring check would false-positive on the already-open
    # per-scheme child account (the exact bug the script's `_open_missing_accounts`
    # helper works around), so open the coarse vehicle account explicitly first.
    main_ledger = Ledger(storage_dir)
    main_text = main_ledger.main_file.read_text(encoding="utf-8")
    main_ledger.main_file.write_text(
        main_text.rstrip() + "\n2000-01-01 open Assets:Investment:MutualFund INR\n",
        encoding="utf-8",
    )
    main_ledger.append([_vehicle_entry(live_candidate)])

    # migrate() must proceed normally, not raise MigrationAlreadyRunError.
    report = migrate(storage_dir)
    assert report.success
    assert report.migrated_count == 3


def test_migrate_aborts_and_rolls_back_on_reconciliation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage_dir = tmp_path / "storage"
    _build_legacy_fixture(storage_dir)
    investment_ledger = _investment_ledger(storage_dir)
    # Realistic pre-migration deployment state: the app has already bootstrapped
    # (empty) investments.beancount at startup, as backend/app/service/wiring.py does.
    investment_ledger.bootstrap(seed_accounts=())

    main_before = (storage_dir / "journal" / "main.beancount").read_bytes()
    main_2024_before = (storage_dir / "journal" / "2024.beancount").read_bytes()
    main_2025_before = (storage_dir / "journal" / "2025.beancount").read_bytes()
    inv_before = (storage_dir / "journal" / "investments.beancount").read_bytes()

    def _fake_checks(ledger, inv_ledger=None):  # noqa: ANN001
        return {
            "journal_valid": True,
            "errors": [],
            "transaction_count": 0,
            "uncategorized_count": 0,
            "investment_ledger_valid": True,
            "investment_ledger_errors": [],
            "mf_reconciliation_gap": "999.99",  # force a nonzero gap
            "mf_migration_pending": False,
        }

    monkeypatch.setattr(
        "scripts.migrate_mf_to_investment_ledger.doctor.checks", _fake_checks
    )

    with pytest.raises(MigrationReconciliationError) as excinfo:
        migrate(storage_dir)

    assert not excinfo.value.report.success
    assert any("mf_reconciliation_gap" in p for p in excinfo.value.problems)

    assert (storage_dir / "journal" / "main.beancount").read_bytes() == main_before
    assert (storage_dir / "journal" / "2024.beancount").read_bytes() == main_2024_before
    assert (storage_dir / "journal" / "2025.beancount").read_bytes() == main_2025_before
    assert (
        storage_dir / "journal" / "investments.beancount"
    ).read_bytes() == inv_before


def test_migrate_report_surfaces_charge_gain_delta(tmp_path: Path) -> None:
    """The legacy (charge-net) vs new (charge-gross) gain totals are
    *expected* to differ by the total statutory charges moved into
    investments.beancount -- this must never be a hard-abort gate, but it
    must be surfaced prominently for human review (critiq finding 2)."""
    storage_dir = tmp_path / "storage"
    _build_legacy_fixture(storage_dir)

    report = migrate(storage_dir)

    assert report.success
    expected_delta = Decimal(report.pre_legacy_gain_total) - Decimal(
        report.post_bucket_gain_total
    )
    assert Decimal(report.charge_gain_delta) == expected_delta
    # This fixture has no statutory-charge legs, so the delta is 0 -- the
    # report field/print path must still surface it explicitly either way.
    assert Decimal(report.charge_gain_delta) == Decimal(0)


def _build_legacy_fixture_with_real_charge(storage_dir: Path) -> Ledger:
    """One PURCHASE + one partial-covering REDEEM, same legacy pre-split shape
    as `_build_legacy_fixture`, except the REDEEM is built with a
    `MergeBookingConfig` that has `stt_rate_sell` enabled and a genuine gap
    between `candidate.amount` and `units * unit_cost` -- the exact shape
    `tests/service/ingest/pipeline/test_charges.py::test_stt_capped_when_gap_smaller_than_computed_stt`
    uses to force a real, itemized `Expenses:Investment:Charges:STT` leg via
    `compute_charges`/`_commodity_entry` (mirrors the live Groww commit path
    for a real redemption with statutory charges). This is the fixture
    `test_migrate_report_surfaces_charge_gain_delta` was missing: that test's
    `_build_legacy_fixture` has `cash == units * unit_cost` exactly on every
    candidate, so `compute_charges` never produces a nonzero charge leg and
    `charge_gain_delta` is always 0 there."""
    ledger = Ledger(storage_dir)
    ledger.bootstrap()

    # trade_value = 100 * 100.00 = 10000.00; gap = 10030.00 - 10000.00 = 30.00;
    # stt_rate_sell=0.01 -> computed STT = 100.00, capped to the 30.00 gap
    # budget -> exactly one STT leg of 30.00, no residual leg (same numbers as
    # test_charges.py::test_stt_capped_when_gap_smaller_than_computed_stt).
    cfg = MergeBookingConfig(
        stamp_duty_rate="0", stt_rate_sell="0.01", charge_tolerance="50"
    )

    purchase = CandidateTxn(
        date=date(2024, 4, 10),
        amount=Decimal("8000.00"),
        raw_narration="Jane Doe SIP - Fictional Flexi Cap Fund",
        normalized_narration="jane doe sip fictional flexi cap fund",
        source_account=GROWW_CASH,
        direction=Direction.DEBIT,
        units=Decimal("100"),
        commodity=SYMBOL,
        unit_cost=Decimal("80.00"),
    )
    redeem = CandidateTxn(
        date=date(2025, 4, 10),
        amount=Decimal("10030.00"),
        raw_narration="Jane Doe REDEEM - Fictional Flexi Cap Fund",
        normalized_narration="jane doe redeem fictional flexi cap fund",
        source_account=GROWW_CASH,
        direction=Direction.CREDIT,
        units=Decimal("100"),
        commodity=SYMBOL,
        unit_cost=Decimal("100.00"),
    )

    ledger.ensure_commodity_accounts([(SYMBOL, "Fictional Flexi Cap Fund")])
    ledger.append(
        [
            _commodity_entry(purchase, cfg),
            _commodity_entry(redeem, cfg),
        ]
    )
    return ledger


def test_migrate_report_surfaces_nonzero_charge_gain_delta_with_real_charges(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Closes critiq's finding-2 evidence gap: exercise the one path
    `test_migrate_report_surfaces_charge_gain_delta` could not -- a REDEEM
    with a real, nonzero statutory-charge leg -- and assert `charge_gain_delta`
    equals the exact expected non-zero number (not just "not zero")."""
    storage_dir = tmp_path / "storage"
    _build_legacy_fixture_with_real_charge(storage_dir)

    report = migrate(storage_dir)

    assert report.success

    # Legacy (charge-net) gain: beancount's balancing plug on
    # Income:Investment:CapitalGains absorbs the STT leg too, so it reports
    # (amount - lot_cost + charge) = (10030.00 - 8000.00 + 30.00) = 2060.00.
    expected_legacy_gain = Decimal("2060.00")
    # New (charge-gross) bucket gain: `_vehicle_entry` never carries the
    # charge leg through, so it is exactly (amount - lot_cost) = 2030.00.
    expected_bucket_gain = Decimal("2030.00")
    expected_charge = Decimal("30.00")

    assert Decimal(report.pre_legacy_gain_total) == expected_legacy_gain
    assert Decimal(report.post_bucket_gain_total) == expected_bucket_gain
    assert Decimal(report.charge_gain_delta) == expected_charge
    assert Decimal(report.charge_gain_delta) != Decimal(0)

    # Cross-check against the report's own delta formula (same assertion
    # style as test_migrate_report_surfaces_charge_gain_delta).
    expected_delta = Decimal(report.pre_legacy_gain_total) - Decimal(
        report.post_bucket_gain_total
    )
    assert Decimal(report.charge_gain_delta) == expected_delta

    from scripts.migrate_mf_to_investment_ledger import _print_report

    _print_report(report)
    out = capsys.readouterr().out
    assert "REVIEW REQUIRED" in out
    assert str(expected_charge) in out


def test_print_report_includes_review_required_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.migrate_mf_to_investment_ledger import _print_report

    storage_dir = tmp_path / "storage"
    _build_legacy_fixture(storage_dir)
    report = migrate(storage_dir)

    _print_report(report)
    out = capsys.readouterr().out
    assert "REVIEW REQUIRED" in out
    assert report.charge_gain_delta in out


# ---------------------------------------------------------------------------
# unit6-fix: real legacy MF transactions funded via Equity:Opening-Balances
# instead of the real Groww wallet (an initial-position seed, the second of
# the only two legacy cash-leg shapes found in the real journal).
# These fixtures are built by parsing literal beancount text through
# beancount's own loader/booking engine (not `_commodity_entry`, which always
# hardcodes GROWW_CASH), so the auto-balancing plug legs
# (Income:Investment:Rounding / Income:Investment:CapitalGains) resolve
# exactly as beancount itself resolved them in the real journal.
# ---------------------------------------------------------------------------

OB_SYMBOL = "MF900002"
OB_HOLDING_ACCOUNT = f"Assets:Investment:MutualFund:Groww:{OB_SYMBOL}"


def _load_literal_transactions(text: str) -> list[data.Transaction]:
    """Parse `text` via beancount's own loader, returning only the resulting
    Transaction entries. "Unknown account"/"not open" errors are expected and
    ignored here -- `text` is parsed in isolation from any real ledger's own
    `open` directives; the entries this returns are fed into a real
    bootstrapped `Ledger` via `.append()`, which opens whatever accounts they
    touch. Booking of the reducing (REDEEM) leg still resolves correctly
    without an explicit FIFO `open` directive as long as (as here) exactly one
    matching lot exists in the snippet."""
    entries, _errors, _options = loader.load_string(text)
    return [e for e in entries if isinstance(e, data.Transaction)]


def _build_legacy_fixture_opening_balances(storage_dir: Path) -> Ledger:
    """One PURCHASE + one full REDEEM of `OB_SYMBOL`, both funded via
    `Equity:Opening-Balances` instead of `Assets:Broker:Groww` -- the real
    second legacy shape found in the user's actual journal. Structurally
    identical to the real examples in that journal:
    PURCHASE carries a StampDuty charge leg plus an auto-balancing
    Income:Investment:Rounding plug; REDEEM carries an auto-balancing
    Income:Investment:CapitalGains plug."""
    ledger = Ledger(storage_dir)
    ledger.bootstrap()
    ledger.ensure_commodity_accounts([(OB_SYMBOL, "Fictional Opening-Balances Fund")])

    text = f"""
2024-04-10 * "Jane Doe Purchase: Fictional Opening-Balances Fund"
  txn-id: "jane-purchase-open-1"
  {OB_HOLDING_ACCOUNT}  100.00 {OB_SYMBOL} {{50.00 INR, 2024-04-10}}
  Expenses:Tax:StampDuty  0.10 INR
  Equity:Opening-Balances  -5000.00 INR
  Income:Investment:Rounding

2025-04-10 * "Jane Doe Redeem: Fictional Opening-Balances Fund"
  txn-id: "jane-redeem-open-1"
  Equity:Opening-Balances  6000.00 INR
  {OB_HOLDING_ACCOUNT}  -100.00 {OB_SYMBOL} {{}} @ 60.00 INR
  Income:Investment:CapitalGains
"""
    ledger.append(_load_literal_transactions(text))
    return ledger


# PURCHASE weight = 100 * 50.00 = 5000.00; StampDuty = 0.10; Opening-Balances
# = -5000.00 -> Income:Investment:Rounding plug = -0.10 (beancount-resolved,
# asserted directly against the loaded fixture in the test below).
# REDEEM: Opening-Balances = +6000.00; lot cost = 100 * 50.00 = 5000.00 ->
# Income:Investment:CapitalGains plug = -(6000.00 - 5000.00) = -1000.00.
OB_EXPECTED_GAIN = Decimal("1000.00")


def test_migrate_does_not_raise_on_opening_balances_funded_legacy_txn(
    tmp_path: Path,
) -> None:
    """Literal repro of the real crash: a legacy MF transaction whose cash
    leg is `Equity:Opening-Balances`, not `Assets:Broker:Groww`, must not
    raise `ValueError` -- write this test FIRST, watch it fail against the
    pre-fix `_extract_candidate` (which only recognizes GROWW_CASH), then fix."""
    storage_dir = tmp_path / "storage"
    ledger = _build_legacy_fixture_opening_balances(storage_dir)
    investment_ledger = _investment_ledger(storage_dir)

    report = migrate(storage_dir)

    assert report.success
    assert report.migrated_count == 2

    # The migrated main.beancount vehicle-level entries' wallet leg must land
    # on Equity:Opening-Balances -- NOT silently mismapped to GROWW_CASH
    # (Assets:Broker:Groww), which would misrepresent an opening-balance seed
    # as a real wallet movement.
    main_entries = [e for e in ledger.load().entries if isinstance(e, data.Transaction)]
    ob_entries = [
        e
        for e in main_entries
        if e.meta.get("txn-id") in ("jane-purchase-open-1", "jane-redeem-open-1")
    ]
    assert len(ob_entries) == 2
    for entry in ob_entries:
        accounts = {p.account for p in entry.postings}
        assert EQUITY_OPENING in accounts
        assert GROWW_CASH not in accounts

    # investments.beancount lot detail is unaffected/correct, same as the
    # existing GROWW_CASH-shaped assertions above.
    inv_rows = all_postings(investment_ledger)
    holding_rows = [r for r in inv_rows if r.account == OB_HOLDING_ACCOUNT]
    purchase_rows = [r for r in holding_rows if r.amount > 0]
    assert len(purchase_rows) == 1
    assert purchase_rows[0].inr_amount == Decimal("5000.00")
    consumed_rows = [r for r in holding_rows if r.amount < 0]
    assert len(consumed_rows) == 1
    assert -consumed_rows[0].inr_amount == Decimal("5000.00")

    assert _cg_bucket_total(ledger, CG_UNCLASSIFIED) == OB_EXPECTED_GAIN

    doctor_out = doctor.checks(ledger, investment_ledger)
    assert Decimal(doctor_out["mf_reconciliation_gap"]) == 0
    assert not ledger.validate()
    assert not investment_ledger.validate()


def test_migrate_handles_mixed_groww_cash_and_opening_balances_shapes(
    tmp_path: Path,
) -> None:
    """A single journal with BOTH legacy shapes (some legs on
    Assets:Broker:Groww, some on Equity:Opening-Balances) must migrate all of
    them, each retaining its own original wallet account -- proving the fix
    does not regress the already-passing GROWW_CASH path or misattribute one
    shape's wallet leg to the other."""
    storage_dir = tmp_path / "storage"
    ledger = _build_legacy_fixture(storage_dir)  # GROWW_CASH-shaped, 3 txns
    ledger.ensure_commodity_accounts([(OB_SYMBOL, "Fictional Opening-Balances Fund")])
    text = f"""
2024-04-10 * "Jane Doe Purchase: Fictional Opening-Balances Fund"
  txn-id: "jane-purchase-open-1"
  {OB_HOLDING_ACCOUNT}  100.00 {OB_SYMBOL} {{50.00 INR, 2024-04-10}}
  Expenses:Tax:StampDuty  0.10 INR
  Equity:Opening-Balances  -5000.00 INR
  Income:Investment:Rounding

2025-04-10 * "Jane Doe Redeem: Fictional Opening-Balances Fund"
  txn-id: "jane-redeem-open-1"
  Equity:Opening-Balances  6000.00 INR
  {OB_HOLDING_ACCOUNT}  -100.00 {OB_SYMBOL} {{}} @ 60.00 INR
  Income:Investment:CapitalGains
"""
    ledger.append(_load_literal_transactions(text))
    investment_ledger = _investment_ledger(storage_dir)

    report = migrate(storage_dir)

    assert report.success
    assert report.migrated_count == 5  # 3 GROWW_CASH + 2 Opening-Balances

    main_entries = [e for e in ledger.load().entries if isinstance(e, data.Transaction)]
    by_id = {e.meta.get("txn-id"): e for e in main_entries}

    ob_purchase = by_id["jane-purchase-open-1"]
    ob_redeem = by_id["jane-redeem-open-1"]
    for entry in (ob_purchase, ob_redeem):
        accounts = {p.account for p in entry.postings}
        assert EQUITY_OPENING in accounts
        assert GROWW_CASH not in accounts

    # The pre-existing GROWW_CASH-shaped fixture's vehicle entries must still
    # land on GROWW_CASH, unaffected by the Opening-Balances fix.
    groww_cash_entries = [
        e
        for e in main_entries
        if e.meta.get("txn-id") not in ("jane-purchase-open-1", "jane-redeem-open-1")
        and e.postings
        and any(p.account == "Assets:Investment:MutualFund" for p in e.postings)
    ]
    assert len(groww_cash_entries) == 3
    for entry in groww_cash_entries:
        accounts = {p.account for p in entry.postings}
        assert GROWW_CASH in accounts
        assert EQUITY_OPENING not in accounts

    doctor_out = doctor.checks(ledger, investment_ledger)
    assert Decimal(doctor_out["mf_reconciliation_gap"]) == 0
    assert not ledger.validate()
    assert not investment_ledger.validate()


# ---------------------------------------------------------------------------
# real-run gap (2026-09-05): a real migration run against the user's actual
# storage/ aborted with mf_reconciliation_gap == -206.66 despite the
# unit6-fix wallet-leg fix above already being in place. Root cause: a
# legacy PURCHASE's recorded per-unit cost (the Cost on its holding posting)
# does not always agree with `cash_leg_amount / units` -- real Groww
# statements/legacy imports sometimes recorded a per-unit NAV that, combined
# with a real statutory charge (StampDuty) and an auto-balancing Rounding
# plug, means `candidate.amount` (read straight off the cash leg) differs
# from `units * unit_cost` (the trade value/cost basis actually recorded on
# the lot). `_vehicle_entry`'s PURCHASE branch (backend/app/service/ingest/pipeline/entry_writer.py)
# posted `candidate.amount` into the coarse main.beancount vehicle account
# instead of `units * unit_cost` (the SAME cost basis
# `_subsystem_entry`/`_commodity_entry` books onto the per-scheme lot in
# investments.beancount) -- a real, pre-existing bug in the shared function,
# not something specific to this migration script (it silently desyncs any
# live Groww-CSV purchase with a nonzero purchase charge too, since
# MergeBookingConfig's default `stamp_duty_rate` is nonzero). This fixture's
# PURCHASE reproduces that exact shape: unit_cost=50.10 * units=100 =
# 5010.00 trade value, but the real cash leg only reads 5000.00 (a real
# StampDuty=0.25 leg + an auto-balancing Rounding=-10.25 plug make up the
# difference) -- `candidate.amount` (5000.00) != trade value (5010.00).
# Was previously never exercised: unit6-fix's own Opening-Balances fixture
# happened to have amount == trade value exactly (no charge gap), so it
# could not catch this.
# ---------------------------------------------------------------------------

GAP_SYMBOL = "MF900003"
GAP_HOLDING_ACCOUNT = f"Assets:Investment:MutualFund:Groww:{GAP_SYMBOL}"
# PURCHASE weight = 100 * 50.10 = 5010.00; StampDuty = 0.25; Opening-Balances
# = -5000.00 -> Income:Investment:Rounding plug = -10.25 (beancount-resolved).
# REDEEM: Opening-Balances = +6000.00; lot cost = 100 * 50.10 = 5010.00 ->
# Income:Investment:CapitalGains plug = -(6000.00 - 5010.00) = -990.00.
GAP_TRADE_VALUE = Decimal("100.00") * Decimal("50.10")  # 5010.00
GAP_EXPECTED_GAIN = Decimal("6000.00") - GAP_TRADE_VALUE  # 990.00


def _build_legacy_fixture_purchase_amount_differs_from_trade_value(
    storage_dir: Path,
) -> Ledger:
    ledger = Ledger(storage_dir)
    ledger.bootstrap()
    ledger.ensure_commodity_accounts(
        [(GAP_SYMBOL, "Fictional Charge-Gap Opening-Balances Fund")]
    )

    text = f"""
2024-04-10 * "Jane Doe Purchase: Fictional Charge-Gap Opening-Balances Fund"
  txn-id: "jane-purchase-gap-1"
  {GAP_HOLDING_ACCOUNT}  100.00 {GAP_SYMBOL} {{50.10 INR, 2024-04-10}}
  Expenses:Tax:StampDuty  0.25 INR
  Equity:Opening-Balances  -5000.00 INR
  Income:Investment:Rounding

2025-04-10 * "Jane Doe Redeem: Fictional Charge-Gap Opening-Balances Fund"
  txn-id: "jane-redeem-gap-1"
  Equity:Opening-Balances  6000.00 INR
  {GAP_HOLDING_ACCOUNT}  -100.00 {GAP_SYMBOL} {{}} @ 60.00 INR
  Income:Investment:CapitalGains
"""
    ledger.append(_load_literal_transactions(text))
    return ledger


def test_migrate_reconciles_when_purchase_cash_amount_differs_from_trade_value(
    tmp_path: Path,
) -> None:
    """Real-scale regression: a legacy PURCHASE whose real cash-leg amount
    differs from `units * unit_cost` (due to a real statutory charge +
    rounding plug already baked into the original transaction) must still
    reconcile to a zero `mf_reconciliation_gap` post-migration -- the vehicle
    account must book the SAME cost basis investments.beancount books, not
    the raw cash amount. Write this test FIRST, watch it fail against the
    pre-fix `_vehicle_entry` (which posted `candidate.amount`), then fix."""
    storage_dir = tmp_path / "storage"
    ledger = _build_legacy_fixture_purchase_amount_differs_from_trade_value(storage_dir)
    investment_ledger = _investment_ledger(storage_dir)

    report = migrate(storage_dir)

    assert report.success
    assert report.migrated_count == 2

    # The vehicle account's PURCHASE-side contribution must equal the trade
    # value (matching investments.beancount's lot cost), not the raw
    # Equity:Opening-Balances cash amount (5000.00).
    main_rows = all_postings(ledger)
    vehicle_purchase_rows = [
        r
        for r in main_rows
        if r.account == VEHICLE_ACCOUNT and r.date == date(2024, 4, 10)
    ]
    assert len(vehicle_purchase_rows) == 1
    assert vehicle_purchase_rows[0].inr_amount == GAP_TRADE_VALUE

    assert _cg_bucket_total(ledger, CG_UNCLASSIFIED) == GAP_EXPECTED_GAIN

    doctor_out = doctor.checks(ledger, investment_ledger)
    assert Decimal(doctor_out["mf_reconciliation_gap"]) == 0
    assert not ledger.validate()
    assert not investment_ledger.validate()
