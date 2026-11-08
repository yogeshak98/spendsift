from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.models.storage.merge_booking import MergeBookingConfig
from backend.app.models.domain.ledger_cli import ParsedLedger, ParsedPosting, ParsedTxn
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.ledger_merge.service import (
    GROWW_HOLDING_PREFIX,
    convert_batch_split,
)

# The default Groww MF clearing account (Settings.platform_clearing_accounts).
GROWW_BROKER_CASH = "Assets:Investment:MutualFund:Groww:Cash"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SYMBOL = "PPFCF"
HOLDING_ACCOUNT = GROWW_HOLDING_PREFIX + SYMBOL
SOURCE_ACCOUNT = "Assets:Checking:HDFC"


def _buy_txn(units="100.0", nav="215.0", cash="-21501.08") -> ParsedTxn:
    """A ledger-CLI buy: units in + cash out.
    units=100, nav=215 → cost=21500; cash=21501.08 → gap=1.08 → stamp duty at
    0.00005 × 21500 = 1.075, capped at gap=1.08 → stamp=1.075 rounded to 1.08;
    residual = 1.08 - 1.08 = 0."""
    return ParsedTxn(
        date=date(2026, 3, 18),
        narration="Buy PPFCF",
        postings=[
            ParsedPosting(
                account=SOURCE_ACCOUNT,
                amount=Decimal(units),
                commodity=SYMBOL,
                cost=Decimal(nav),
                cost_currency="INR",
                comment=None,
            ),
            ParsedPosting(
                account=SOURCE_ACCOUNT,
                amount=Decimal(cash),
                commodity="INR",
                cost=None,
                cost_currency=None,
                comment=None,
            ),
        ],
        source_line=1,
    )


def _sell_txn(units="-50.0", nav="220.0", cash="11000.00") -> ParsedTxn:
    """A ledger-CLI sell: units out (negative) + cash in (positive).
    Default: sell 50 units at NAV 220 → proceeds = 11000 INR."""
    return ParsedTxn(
        date=date(2026, 5, 10),
        narration="Sell PPFCF",
        postings=[
            ParsedPosting(
                account=SOURCE_ACCOUNT,
                amount=Decimal(units),
                commodity=SYMBOL,
                cost=Decimal(nav),
                cost_currency="INR",
                comment=None,
            ),
            ParsedPosting(
                account=SOURCE_ACCOUNT,
                amount=Decimal(cash),
                commodity="INR",
                cost=None,
                cost_currency=None,
                comment=None,
            ),
        ],
        source_line=20,
    )


def _non_investment_txn() -> ParsedTxn:
    return ParsedTxn(
        date=date(2026, 4, 1),
        narration="Transfer HDFC → ICICI",
        postings=[
            ParsedPosting(
                account="Assets:Checking:HDFC",
                amount=Decimal("-5000.00"),
                commodity="INR",
                cost=None,
                cost_currency=None,
                comment=None,
            ),
            ParsedPosting(
                account="Assets:Checking:ICICI",
                amount=Decimal("5000.00"),
                commodity="INR",
                cost=None,
                cost_currency=None,
                comment=None,
            ),
        ],
        source_line=10,
    )


def _batch(txns):
    return ParsedLedger(transactions=txns, unparsed=[], ignored_lines=0)


def _cfg(**kw) -> MergeBookingConfig:
    return MergeBookingConfig(**kw)


def _investment_ledger(tmp_path):
    ledger = Ledger(
        tmp_path,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    ledger.bootstrap(seed_accounts=())
    return ledger


def _convert(
    txns,
    cfg=None,
    account_map=None,
    commodity_map=None,
    investment_ledger=None,
    scheme_master=None,
    tmp_path=None,
):
    """Returns (main_entries, investment_entries). `investment_ledger` may be
    passed explicitly (e.g. to seed pre-existing lots for a sell); otherwise a
    fresh one is bootstrapped under `tmp_path` (required in that case)."""
    cfg = cfg or _cfg()
    account_map = account_map or {
        SOURCE_ACCOUNT: SOURCE_ACCOUNT,
        "Assets:Checking:ICICI": "Assets:Checking:ICICI",
    }
    commodity_map = commodity_map or {SYMBOL: SYMBOL}
    if investment_ledger is None:
        assert tmp_path is not None, "pass tmp_path when investment_ledger is omitted"
        investment_ledger = _investment_ledger(tmp_path)
    main_entries, investment_entries, _multi_leg_unparsed = convert_batch_split(
        _batch(txns),
        account_map,
        commodity_map,
        filename="test.ledger",
        investment_ledger=investment_ledger,
        scheme_master=scheme_master,
        cfg=cfg,
    )
    return main_entries, investment_entries


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_buy_cash_leg_goes_to_broker_cash(tmp_path):
    """After Task 3: the INR counter-leg of a buy goes to GROWW_BROKER_CASH
    (== the default platform_clearing_account("groww")) on the investment-ledger entry (Unit 7:
    the subsystem entry, not a single combined entry, carries the cash leg)."""
    _main, investment_entries = _convert([_buy_txn()], tmp_path=tmp_path)
    txn = investment_entries[0]
    cash_postings = [
        p
        for p in txn.postings
        if p.units and p.units.currency == "INR" and p.units.number < 0
    ]
    assert len(cash_postings) == 1
    assert cash_postings[0].account == GROWW_BROKER_CASH


def test_buy_stamp_duty_leg_present_with_charge_basis_meta(tmp_path):
    """Stamp duty leg appears with charge-basis: statutory metadata, on the
    investment-ledger (subsystem) entry -- Unit 7: charge legs travel with
    _subsystem_entry's output, into investments.beancount."""
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    _main, investment_entries = _convert(
        [_buy_txn(units="100.0", nav="215.0", cash="-21501.08")],
        cfg=cfg,
        tmp_path=tmp_path,
    )
    txn = investment_entries[0]
    stamp_postings = [
        p for p in txn.postings if p.account == "Expenses:Investment:Charges:StampDuty"
    ]
    assert len(stamp_postings) == 1
    p = stamp_postings[0]
    assert p.meta is not None and p.meta.get("charge-basis") == "statutory"
    assert p.units.number == Decimal(
        "1.08"
    )  # round(21500 × 0.00005, 2) = 1.08 (rounded from 1.075)


def test_buy_residual_to_charges_account(tmp_path):
    """Residual remainder goes to Expenses:Investment:Charges:Groww on the
    investment-ledger (subsystem) entry."""
    # cash=21515, cost=21500, gap=15; stamp=1.08; residual=13.92
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    _main, investment_entries = _convert(
        [_buy_txn(units="100.0", nav="215.0", cash="-21515.00")],
        cfg=cfg,
        tmp_path=tmp_path,
    )
    txn = investment_entries[0]
    residual_postings = [
        p for p in txn.postings if p.account == "Expenses:Investment:Charges:Groww"
    ]
    assert len(residual_postings) == 1


def test_buy_balances_against_real_cash(tmp_path):
    """The sum of all INR postings on the investment-ledger (subsystem) entry
    is -cost (the holding leg's Cost basis covers the rest)."""
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    _main, investment_entries = _convert(
        [_buy_txn(units="100.0", nav="215.0", cash="-21515.00")],
        cfg=cfg,
        tmp_path=tmp_path,
    )
    txn = investment_entries[0]
    inr_sum = sum(
        p.units.number
        for p in txn.postings
        if p.units is not None and p.units.currency == "INR"
    )
    # Holding posting (in commodity units) does NOT appear here; only INR cash legs + charge legs.
    # Total INR: cash_out (-21515) + stamp (1.08) + residual (13.92) = -21500 = -cost
    # Plus the holding leg balances the cost side → overall transaction balances at 0 INR.
    # For this assertion: sum of explicit INR legs == -cost (commodity lot covers the rest via cost).
    assert inr_sum == -Decimal("21500.00")


def test_surface_signal_books_the_remainder_instead_of_imbalancing(tmp_path):
    """When compute_charges returns surface=True on this path the remainder is
    BOOKED, so preview reports no imbalance and commit is not refused.

    This asserted `imbalances >= 1` until P5's fix round. `convert_batch_split`
    used to pre-check the charge gap and fall back to the raw posting shape to
    produce that imbalance on purpose -- and `commit()` turns any imbalance
    into a hard LEDGER_IMBALANCE refusal of the whole batch. Surfacing gap=10
    against tolerance=5 therefore meant "this transaction can never be
    imported", which is strictly worse than the pre-P5 absorb-and-commit.
    Both paths now book the remainder to charge_unexplained_account.
    """
    from backend.app.persistence.ledger_merge_store import LedgerMergeStore
    from backend.app.persistence.journal import Ledger
    from backend.app.persistence.yaml_stores import MergeBookingStore
    from backend.app.service.ingest.ledger_merge.service import LedgerMergeService

    # charge_tolerance=5; gap=10 → residual=10 > 5 → surface → unbalanced → E1023
    buy_text = """\
2026/03/18 Buy PPFCF
    Assets:Investment:MutualFund:Groww:PPFCF   100.0 PPFCF @ 100.0 INR
    Assets:Checking:HDFC                       -10010.00 INR
"""
    ledger = Ledger(tmp_path)
    ledger.bootstrap()
    investment_ledger = Ledger(
        tmp_path,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    investment_ledger.bootstrap(seed_accounts=())
    booking_store = MergeBookingStore(tmp_path / "config")
    # Low tolerance to force an over-tolerance residual surface signal.
    booking_store.save(MergeBookingConfig(stamp_duty_rate="0", charge_tolerance="5"))
    service = LedgerMergeService(
        ledger=ledger,
        investment_ledger=investment_ledger,
        store=LedgerMergeStore(),
        booking_store=booking_store,
    )
    out = service.preview("buy.ledger", buy_text.encode())
    assert out["summary"]["imbalances"] == 0
    # The whole 10 is unattributable (every rate is 0) and it is named, in
    # both halves of the split, where review can see it before the write.
    unexplained = [
        p
        for t in out["transactions"]
        for p in t["postings"]
        if p["account"] == "Expenses:Investment:Charges:Unexplained"
    ]
    assert [Decimal(p["amount"]) for p in unexplained] == [
        Decimal("10"),
        Decimal("10"),
    ], unexplained


def test_zero_gap_buy_books_no_charge_leg_and_no_error(tmp_path):
    """A buy where cash == cost exactly: no charge legs, no imbalance, no error."""
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    # gap = 0 → compute_charges returns surface=False, legs={}
    _main, investment_entries = _convert(
        [_buy_txn(units="100.0", nav="215.0", cash="-21500.00")],
        cfg=cfg,
        tmp_path=tmp_path,
    )
    txn = investment_entries[0]
    charge_postings = [
        p
        for p in txn.postings
        if p.account
        in (
            "Expenses:Investment:Charges:StampDuty",
            "Expenses:Investment:Charges:STT",
            "Expenses:Investment:Charges:Groww",
        )
    ]
    assert charge_postings == []


def test_non_investment_txn_cash_stays_on_source_account(tmp_path):
    """Plain INR transfers are NOT redirected to broker-cash -- zero-MF-leg
    transactions go through the unchanged main-ledger-only path."""
    main_entries, investment_entries = _convert(
        [_non_investment_txn()], tmp_path=tmp_path
    )
    assert investment_entries == []
    txn = main_entries[0]
    accounts = {p.account for p in txn.postings if p.units is not None}
    assert GROWW_BROKER_CASH not in accounts
    assert "Assets:Checking:HDFC" in accounts
    assert "Assets:Checking:ICICI" in accounts


# ---------------------------------------------------------------------------
# Sell-side cash routing (Task-3 gap fix)
# ---------------------------------------------------------------------------


def test_sell_cash_leg_goes_to_broker_cash(tmp_path):
    """The INR proceeds counter-leg of an MF sell must post to GROWW_BROKER_CASH,
    on the investment-ledger (subsystem) entry."""
    # Pre-seed the book so the sell is bookable (buy 100 units first in the batch).
    _main, investment_entries = _convert(
        [_buy_txn(units="100.0", nav="215.0", cash="-21500.00"), _sell_txn()],
        tmp_path=tmp_path,
    )
    sell_txn = investment_entries[1]
    cash_postings = [
        p
        for p in sell_txn.postings
        if p.units is not None and p.units.currency == "INR" and p.units.number > 0
    ]
    assert len(cash_postings) == 1, f"expected 1 cash posting, got {sell_txn.postings}"
    assert cash_postings[0].account == GROWW_BROKER_CASH


def test_sell_still_balances_with_gains_legs(tmp_path):
    """After redirecting sell proceeds to broker-cash, both the
    investment-ledger subsystem entry (plugged via the internal
    CAPITAL_GAINS account) and the main-ledger vehicle entry (which now
    carries the actual LTCG/STCG gain legs, per Unit 7) balance
    independently."""
    from beancount import loader
    from beancount.parser import printer

    # Buy 100 @ 215, sell 50 @ 220 → STCG = 50 × (220-215) = 250 (held < 365d)
    main_entries, investment_entries = _convert(
        [_buy_txn(units="100.0", nav="215.0", cash="-21500.00"), _sell_txn()],
        tmp_path=tmp_path,
    )
    for label, entry in (
        ("investment", investment_entries[1]),
        ("main", main_entries[1]),
    ):
        text = printer.format_entry(entry)
        _, errors, _ = loader.load_string(text)
        balance_errors = [
            e for e in errors if "does not balance" in getattr(e, "message", "")
        ]
        assert (
            balance_errors == []
        ), f"{label} sell entry imbalanced: {balance_errors}\n{text}"


def test_sell_no_stt_charge_leg_by_default(tmp_path):
    """With stt_rate_sell='0' (default), no STT/charge leg is emitted for a sell —
    only the cash-account redirect changed."""
    cfg = _cfg(stt_rate_sell="0")
    _main, investment_entries = _convert(
        [_buy_txn(units="100.0", nav="215.0", cash="-21500.00"), _sell_txn()],
        cfg=cfg,
        tmp_path=tmp_path,
    )
    sell_txn = investment_entries[1]
    charge_postings = [
        p
        for p in sell_txn.postings
        if p.account
        in (
            "Expenses:Investment:Charges:STT",
            "Expenses:Investment:Charges:StampDuty",
            "Expenses:Investment:Charges:Groww",
        )
    ]
    assert charge_postings == [], f"unexpected charge legs: {charge_postings}"


def test_buy_cash_routing_unchanged_after_sell_fix(tmp_path):
    """Regression: a buy still routes its cash leg to GROWW_BROKER_CASH."""
    _main, investment_entries = _convert([_buy_txn()], tmp_path=tmp_path)
    txn = investment_entries[0]
    cash_postings = [
        p
        for p in txn.postings
        if p.units is not None and p.units.currency == "INR" and p.units.number < 0
    ]
    assert len(cash_postings) >= 1
    assert all(p.account == GROWW_BROKER_CASH for p in cash_postings)


def _surface_preview(tmp_path, cash: str, cfg: MergeBookingConfig):
    """Run `preview` on a one-buy ledger-cli file and return the unexplained
    charge amounts its transactions carry.

    `convert_batch_split` books the unattributable part of the gap to
    `cfg.charge_unexplained_account` through the same entry builders the Groww
    path uses, so the presence of that POSTING -- not an imbalance count -- is
    the observable signal for this call site surfacing. One entry per split
    half, so a surfacing buy yields two equal amounts.
    """
    from backend.app.persistence.yaml_stores import MergeBookingStore
    from backend.app.persistence.ledger_merge_store import LedgerMergeStore
    from backend.app.persistence.journal import Ledger
    from backend.app.service.ingest.ledger_merge.service import LedgerMergeService

    buy_text = (
        "2026/03/18 Buy PPFCF\n"
        "    Assets:Investment:MutualFund:Groww:PPFCF   100.0 PPFCF @ 215.0 INR\n"
        f"    Assets:Checking:HDFC                       {cash} INR\n"
    )
    ledger = Ledger(tmp_path)
    ledger.bootstrap()
    investment_ledger = Ledger(
        tmp_path,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    investment_ledger.bootstrap(seed_accounts=())
    booking_store = MergeBookingStore(tmp_path / "config")
    booking_store.save(cfg)
    service = LedgerMergeService(
        ledger=ledger,
        investment_ledger=investment_ledger,
        store=LedgerMergeStore(),
        booking_store=booking_store,
    )
    out = service.preview("buy.ledger", buy_text.encode())
    assert out["summary"]["imbalances"] == 0, "the split shape must always balance"
    return [
        Decimal(p["amount"])
        for t in out["transactions"]
        for p in t["postings"]
        if p["account"] == cfg.charge_unexplained_account
    ]


def test_the_merge_preview_charge_check_uses_the_precision_band(tmp_path):
    """Discriminator for the `_would_surface_charges` call-site migration.

    The gap sits BETWEEN the two bands, so it only surfaces if `units`/`nav`
    reach `compute_charges`:

      precision band = 100 * ulp(215.0) + 215 * ulp(100.0) = 10 + 21.5 = 31.5
      legacy band    = max(50, 0.05% * 21540 = 10.77)       = 50

    gap = 40 and all rates are 0, so the whole 40 is the residual. Migrated,
    it surfaces -- booked to Expenses:Investment:Charges:Unexplained and
    visible in the preview postings; un-migrated, the legacy 50 band swallows
    it into Expenses:Investment:Charges:Groww with no signal at all.
    """
    cfg = MergeBookingConfig(stamp_duty_rate="0", stt_rate_sell="0")
    over = _surface_preview(tmp_path / "over", "-21540.00", cfg)
    assert over == [Decimal("40"), Decimal("40")]
    # Control: a gap INSIDE the precision band must not surface, so the test
    # above cannot be satisfied by a band of zero.
    under = _surface_preview(tmp_path / "under", "-21520.00", cfg)
    assert under == []
