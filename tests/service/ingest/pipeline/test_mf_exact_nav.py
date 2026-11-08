"""Groww MF purchase: units and cost from the exact AMFI NAV.

Groww prints Units and NAV rounded to 2 dp. When the exact NAV for the
(scheme, trade date) is known, the purchase is booked from it instead:
cash = the round order, stamp = 0.005% of it, units = (cash - stamp) / NAV
to 3 dp. All data below is fictional.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.models.storage.merge_booking import MergeBookingConfig
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import (
    EXPORT_AMOUNT_META,
    EXPORT_UNITS_META,
    _vehicle_entry,
    commit_candidates,
    exact_nav_flag_of,
    unexplained_charge_of,
)
from backend.app.models.domain.accounts import (
    INVESTMENT_MUTUAL_FUND,
    OPERATING_CURRENCY,
)
from backend.app.service.ingest.pipeline.charges import (
    EXACT_NAV_DISAGREES,
    EXACT_NAV_UNAVAILABLE,
)

# The default Groww wallet (Settings.platform_cash_accounts["groww"]).
GROWW_CASH = "Assets:Broker:Groww"
# The default Groww MF clearing account (Settings.platform_clearing_accounts).
SUBSYSTEM_CLEARING = "Assets:Investment:MutualFund:Groww:Cash"

STAMP = "Expenses:Investment:Charges:StampDuty"
ROUNDING = "Expenses:Investment:Charges:Groww"
CODE = "990001"
SYMBOL = f"MF{CODE}"
DAY = date(2025, 10, 6)


def _buy(txn_id: str, amount: str, units: str, nav: str) -> CandidateTxn:
    return CandidateTxn(
        id=txn_id,
        date=DAY,
        amount=Decimal(amount),
        raw_narration="Purchase — Example Equity Savings Fund",
        normalized_narration="EXAMPLE EQUITY SAVINGS FUND",
        source_account=SUBSYSTEM_CLEARING,
        direction=Direction.DEBIT,
        units=Decimal(units),
        commodity=SYMBOL,
        unit_cost=Decimal(nav),
    )


def _entry(led, txn_id: str):
    (entry,) = [
        e
        for e in led.load().entries
        if getattr(e, "meta", {}).get("txn-id") == txn_id and hasattr(e, "postings")
    ]
    return entry


def _inr_legs(entry) -> dict[str, Decimal]:
    legs: dict[str, Decimal] = {}
    for p in entry.postings:
        if p.units is not None and p.units.currency == OPERATING_CURRENCY:
            legs[p.account] = legs.get(p.account, Decimal(0)) + p.units.number
    return legs


def _holding(entry):
    (p,) = [p for p in entry.postings if p.units.currency == SYMBOL]
    return p


def _commit(ledger, investment_ledger, candidates, navs):
    commit_candidates(ledger, investment_ledger, candidates, exact_navs=navs)
    # The beancount loader re-checks every entry balances.
    assert ledger.validate() == []
    assert investment_ledger.validate() == []


# The worked example, with a fictional scheme code. Paid 50,000; exported
# Amount 49,997, Units 1761.26, NAV 28.39 (1761.26 * 28.39 = 50,002.17, more
# than was paid). Exact NAV 28.3873.
#   stamp = 50,000 * 0.00005 = 2.50
#   units = 49,997.50 / 28.3873 = 1761.26296 -> 1761.263
#   cost  = 1761.263 * 28.3873 = 49,997.5011599
#   remainder = 50,000 - 2.50 - cost = -0.0011599 -> folded into stamp
WORKED = ("worked", "49997", "1761.26", "28.39")
WORKED_NAVS = {(CODE, DAY): Decimal("28.3873")}


def test_worked_example_books_exact_units_at_exact_nav(ledger, investment_ledger):
    c = _buy(*WORKED)
    _commit(ledger, investment_ledger, [c], WORKED_NAVS)

    inv = _entry(investment_ledger, "worked")
    holding = _holding(inv)
    assert holding.units.number == Decimal("1761.263")
    assert holding.cost.number == Decimal("28.3873")
    assert _inr_legs(inv) == {
        SUBSYSTEM_CLEARING: Decimal("-50000.00"),
        STAMP: Decimal("2.4988401"),
    }
    main = _entry(ledger, "worked")
    assert _inr_legs(main) == {
        INVESTMENT_MUTUAL_FUND: Decimal("49997.5011599"),
        GROWW_CASH: Decimal("-50000.00"),
        STAMP: Decimal("2.4988401"),
    }
    for entry in (inv, main):
        assert all(
            p.units.number > 0
            for p in entry.postings
            if p.account.startswith("Expenses:")
        )
        # The export's own figures, which are the row's dedup identity.
        assert entry.meta[EXPORT_AMOUNT_META] == Decimal("49997")
        assert entry.meta[EXPORT_UNITS_META] == Decimal("1761.26")
    assert c.amount == Decimal("49997") and c.units == Decimal("1761.26")
    assert c.unexplained_charge is False


def test_a_positive_remainder_is_a_rounding_leg(ledger, investment_ledger):
    """9,999 exported for a 10,000 order at exact NAV 31.4157: stamp 0.50,
    units 9,999.50 / 31.4157 = 318.2963 -> 318.296, cost 9,999.4916,
    remainder >= 0 -> booked to the charges account, stamp untouched."""
    c = _buy("pos", "9999", "318.30", "31.42")
    navs = {(CODE, DAY): Decimal("31.4157")}
    _commit(ledger, investment_ledger, [c], navs)

    inv = _entry(investment_ledger, "pos")
    assert _holding(inv).units.number == Decimal("318.296")
    cost = Decimal("318.296") * Decimal("31.4157")
    assert _inr_legs(inv) == {
        SUBSYSTEM_CLEARING: Decimal("-10000.00"),
        STAMP: Decimal("0.50"),
        ROUNDING: Decimal("10000.00") - Decimal("0.50") - cost,
    }
    assert Decimal("10000.00") - Decimal("0.50") - cost > 0


def test_a_guard_blocked_order_now_snaps_with_the_exact_nav(ledger, investment_ledger):
    """The worked example is one of the orders the snap's cost guard blocks:
    export units * NAV (50,002.17) is above the round order (50,000). Without
    the exact NAV it is booked at 49,997 with a NEGATIVE charge credit; with it
    the cash is 50,000 and no Expenses leg is negative."""
    blocked = _buy("blocked", "49997", "1761.26", "28.39")
    _commit(ledger, investment_ledger, [blocked], None)
    legs = _inr_legs(_entry(ledger, "blocked"))
    assert legs[GROWW_CASH] == Decimal("-49997")
    assert legs[ROUNDING] < 0  # the pre-exact-NAV defect, pinned

    fixed = _buy("fixed", "49997", "1761.26", "28.39")
    fixed.date = date(2025, 10, 7)
    _commit(
        ledger,
        investment_ledger,
        [fixed],
        {(CODE, date(2025, 10, 7)): Decimal("28.3873")},
    )
    legs = _inr_legs(_entry(ledger, "fixed"))
    assert legs[GROWW_CASH] == Decimal("-50000.00")
    assert all(v > 0 for k, v in legs.items() if k.startswith("Expenses:"))


def test_missing_nav_keeps_the_snap_and_flags_it(ledger, investment_ledger):
    """No exact NAV for the date: the round-1 snap booking, unchanged, plus the
    review flag. Same row and legs as round 1's test (a)."""
    c = _buy("missing", "14999", "512.000", "29.2945")
    navs: dict = {}
    assert exact_nav_flag_of(c, navs) == EXACT_NAV_UNAVAILABLE
    _commit(ledger, investment_ledger, [c], navs)

    inv = _entry(investment_ledger, "missing")
    assert _holding(inv).units.number == Decimal("512.000")
    assert _holding(inv).cost.number == Decimal("29.2945")
    assert _inr_legs(_entry(ledger, "missing")) == {
        INVESTMENT_MUTUAL_FUND: Decimal("14998.784"),
        GROWW_CASH: Decimal("-15000.00"),
        STAMP: Decimal("0.75"),
        ROUNDING: Decimal("0.466"),
    }


def test_a_nav_that_disagrees_with_the_export_falls_back_and_flags(
    ledger, investment_ledger
):
    """NAV 28.3000 gives (15,000 - 0.75) / 28.3 = 530.009 units, 18 away from
    the export's 512.000 (> 0.006): not used. The row books exactly as it
    would with no NAV at all (round 1's test (a) legs), and says why."""
    c = _buy("disagree", "14999", "512.000", "29.2945")
    navs = {(CODE, DAY): Decimal("28.3000")}
    assert exact_nav_flag_of(c, navs) == EXACT_NAV_DISAGREES
    _commit(ledger, investment_ledger, [c], navs)

    inv = _entry(investment_ledger, "disagree")
    assert _holding(inv).units.number == Decimal("512.000")
    assert _inr_legs(_entry(ledger, "disagree")) == {
        INVESTMENT_MUTUAL_FUND: Decimal("14998.784"),
        GROWW_CASH: Decimal("-15000.00"),
        STAMP: Decimal("0.75"),
        ROUNDING: Decimal("0.466"),
    }


def test_units_tolerance_boundary():
    """0.006 is inclusive: 1761.263 vs export 1761.257 passes, vs 1761.256
    (0.007 away) does not."""
    navs = WORKED_NAVS
    assert exact_nav_flag_of(_buy("t", "49997", "1761.257", "28.39"), navs) is None
    assert (
        exact_nav_flag_of(_buy("t", "49997", "1761.256", "28.39"), navs)
        == EXACT_NAV_DISAGREES
    )


def test_review_agrees_with_commit_for_the_worked_example(ledger, investment_ledger):
    c = _buy(*WORKED)
    preview_unexplained = unexplained_charge_of(c, snap_cash=True, navs=WORKED_NAVS)
    preview_flag = exact_nav_flag_of(c, WORKED_NAVS)
    _commit(ledger, investment_ledger, [c], WORKED_NAVS)
    assert preview_unexplained is None and c.unexplained_charge is False
    assert preview_flag is None


def test_review_uses_the_exact_nav_booking(ledger, investment_ledger):
    """Discriminating parity: a row whose export units/NAV leave an
    over-tolerance gap (flagged) but whose exact-NAV booking is clean.
    Export 1761.26 @ 28.30 = 49,843.66: 153 below the cash, far over the
    precision band -> flagged unexplained without the NAV. With exact NAV
    28.3873 the booking is the worked example: nothing unexplained."""
    c = _buy("parity", "49997", "1761.26", "28.30")
    assert unexplained_charge_of(c, snap_cash=True, navs={}) is not None
    assert unexplained_charge_of(c, snap_cash=True, navs=WORKED_NAVS) is None
    _commit(ledger, investment_ledger, [c], WORKED_NAVS)
    assert c.unexplained_charge is False


def test_a_redemption_in_the_same_batch_consumes_the_lot_as_booked(
    ledger, investment_ledger
):
    """The in-batch lot cache must hold the exact-NAV lot. If it held the
    export's 1761.26 @ 28.39, the redemption would name a lot the journal does
    not have and beancount would reject the entry."""
    buy = _buy(*WORKED)
    sell = CandidateTxn(
        id="sell",
        date=date(2025, 11, 3),
        amount=Decimal("3000"),
        raw_narration="Redeem — Example Equity Savings Fund",
        normalized_narration="EXAMPLE EQUITY SAVINGS FUND",
        source_account=SUBSYSTEM_CLEARING,
        direction=Direction.CREDIT,
        units=Decimal("100.000"),
        commodity=SYMBOL,
        unit_cost=Decimal("30.0000"),
    )
    _commit(ledger, investment_ledger, [buy, sell], WORKED_NAVS)
    lot_costs = {
        p.cost.number for p in _entry(investment_ledger, "sell").postings if p.cost
    }
    assert lot_costs == {Decimal("28.3873")}


def test_snap_false_builders_carry_no_export_metadata():
    """Ledger-merge and migration callers stay byte-identical: no metadata,
    no exact NAV even when a mapping is handed in without snap_cash."""
    c = _buy(*WORKED)
    entry = _vehicle_entry(c, cfg=MergeBookingConfig(), navs=WORKED_NAVS)
    assert EXPORT_AMOUNT_META not in entry.meta
    assert EXPORT_UNITS_META not in entry.meta
    cash = next(p for p in entry.postings if p.account == GROWW_CASH)
    assert cash.units.number == Decimal("-49997")


# --- half-up units that overshoot the cash: one step down ------------------
#
# Amount 99 -> cash 100, stamp 0.01. Half-up units 99.99 / 150 = 0.6666 ->
# 0.667 cost 100.05, MORE than the 100 paid: folding -0.06 into a 0.01 stamp
# would take it below 0. One step down: 0.666 at 150.0000 = 99.90, remainder
# 0.09 >= 0 -> rounding leg.
SMALL = ("small", "99", "0.67", "150.00")
SMALL_NAVS = {(CODE, DAY): Decimal("150.0000")}


def test_overshooting_units_step_down_and_stay_on_the_exact_path(
    ledger, investment_ledger
):
    c = _buy(*SMALL)
    assert exact_nav_flag_of(c, SMALL_NAVS) is None
    _commit(ledger, investment_ledger, [c], SMALL_NAVS)

    inv = _entry(investment_ledger, "small")
    assert _holding(inv).units.number == Decimal("0.666")
    assert _holding(inv).cost.number == Decimal("150.0000")
    assert _inr_legs(inv) == {
        SUBSYSTEM_CLEARING: Decimal("-100.00"),
        STAMP: Decimal("0.01"),
        ROUNDING: Decimal("0.09"),
    }
    main = _entry(ledger, "small")
    assert _inr_legs(main) == {
        INVESTMENT_MUTUAL_FUND: Decimal("99.9000"),
        GROWW_CASH: Decimal("-100.00"),
        STAMP: Decimal("0.01"),
        ROUNDING: Decimal("0.09"),
    }
    for entry in (inv, main):
        assert all(
            p.units.number > 0
            for p in entry.postings
            if p.account.startswith("Expenses:")
        )


def test_review_agrees_with_commit_on_the_step_down(ledger, investment_ledger):
    c = _buy(*SMALL)
    preview = (
        unexplained_charge_of(c, snap_cash=True, navs=SMALL_NAVS),
        exact_nav_flag_of(c, SMALL_NAVS),
    )
    _commit(ledger, investment_ledger, [c], SMALL_NAVS)
    assert preview == (None, None)
    assert c.unexplained_charge is False
    assert c.lot_blocked_reason is None


def test_the_cost_guard_has_its_own_flag_and_books_nothing(
    ledger, investment_ledger, monkeypatch
):
    """The guard is unreachable while the step-down exists, so force it by
    disabling the step. It must say what happened (not "disagrees": the
    units DO agree) and refuse the row: every fallback booking would carry a
    negative charge (here the export's 0.67 * 150 = 100.50 against 99)."""
    import pytest

    import backend.app.service.ingest.pipeline.charges as mc
    from backend.app.core.errors.errors import CodedError

    monkeypatch.setattr(mc, "UNITS_STEP_DOWN", Decimal(0))
    c = _buy(*SMALL)
    assert exact_nav_flag_of(c, SMALL_NAVS) == mc.EXACT_NAV_COST_EXCEEDS_CASH
    assert mc.EXACT_NAV_COST_EXCEEDS_CASH != EXACT_NAV_DISAGREES
    before = (
        ledger.journal_dir.joinpath("2025.beancount").exists(),
        investment_ledger.journal_dir.joinpath("investments-2025.beancount").exists(),
    )
    with pytest.raises(CodedError) as ei:
        commit_candidates(ledger, investment_ledger, [c], exact_navs=SMALL_NAVS)
    assert ei.value.code == "E1044"
    assert c.lot_blocked_reason == mc.EXACT_NAV_COST_EXCEEDS_CASH
    assert before == (False, False)
    assert not ledger.journal_dir.joinpath("2025.beancount").exists()
    assert not investment_ledger.journal_dir.joinpath(
        "investments-2025.beancount"
    ).exists()
