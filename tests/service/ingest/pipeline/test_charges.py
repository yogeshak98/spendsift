from __future__ import annotations

from decimal import Decimal

from backend.app.models.storage.merge_booking import MergeBookingConfig
from backend.app.service.ingest.pipeline.charges import (
    TOLERANCE_FLOOR,
    charge_tolerance,
    compute_charges,
)


def _cfg(**overrides) -> MergeBookingConfig:
    return MergeBookingConfig(**overrides)


# ---------------------------------------------------------------------------
# Stamp duty (buy side) — normal gap
# ---------------------------------------------------------------------------


def test_buy_stamp_duty_computed_at_rate():
    """stamp_duty = round(trade_value × rate, 2), capped at gap."""
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    # trade_value = 10_000, rate = 0.00005 → computed_stamp = 0.50
    # gap = 10_050 − 10_000 = 50; 0.50 ≤ 50 → no capping; residual = 49.50
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("10050"), Decimal("10000"), cfg
    )
    assert not result.surface
    stamp = result.legs.get("Expenses:Investment:Charges:StampDuty")
    assert stamp == Decimal("0.50")
    residual_leg = result.legs.get("Expenses:Investment:Charges:Groww")
    assert residual_leg == Decimal("49.50")


def test_buy_stamp_duty_zero_when_rate_is_zero():
    cfg = _cfg(stamp_duty_rate="0", stt_rate_sell="0", charge_tolerance="50")
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("10050"), Decimal("10000"), cfg
    )
    assert not result.surface
    assert "Expenses:Investment:Charges:StampDuty" not in result.legs


def test_sell_has_no_stamp_duty():
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    result = compute_charges(
        "sell", Decimal("10000"), Decimal("10010"), Decimal("10000"), cfg
    )
    assert not result.surface
    assert "Expenses:Investment:Charges:StampDuty" not in result.legs


# ---------------------------------------------------------------------------
# Capping: computed stamp > tiny gap → stamp CAPPED at gap, NO error
# ---------------------------------------------------------------------------


def test_buy_stamp_capped_at_tiny_gap():
    """Computed stamp (0.50) > tiny gap (0.30) → stamp capped at 0.30, NO surface signal."""
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    # gap = 10_000.30 − 10_000 = 0.30; computed_stamp = 0.50 → capped to 0.30
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("10000.30"), Decimal("10000"), cfg
    )
    assert not result.surface
    stamp = result.legs.get("Expenses:Investment:Charges:StampDuty")
    assert stamp == Decimal("0.30")  # capped, not the raw 0.50
    # residual = 0 after capping → no residual leg
    assert "Expenses:Investment:Charges:Groww" not in result.legs


# ---------------------------------------------------------------------------
# Zero-gap: no legs, no error
# ---------------------------------------------------------------------------


def test_zero_gap_produces_no_legs_and_no_surface():
    """A trade where cash == cost: gap = 0 → no charge legs, surface=False."""
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("10000"), Decimal("10000"), cfg
    )
    assert not result.surface
    assert result.legs == {}
    assert result.residual == Decimal(0)


# ---------------------------------------------------------------------------
# STT (sell side)
# ---------------------------------------------------------------------------


def test_stt_disabled_by_default_no_leg():
    cfg = _cfg(stamp_duty_rate="0", stt_rate_sell="0", charge_tolerance="50")
    # gap = 10050 - 10000 = 50; all rates zero → residual = 50 = tolerance (not > tolerance)
    result = compute_charges(
        "sell", Decimal("10000"), Decimal("10050"), Decimal("10000"), cfg
    )
    assert not result.surface
    assert "Expenses:Investment:Charges:STT" not in result.legs


def test_stt_enabled_produces_leg_and_shrinks_residual():
    """stt_rate_sell = 0.001 → STT = 10.00 on 10000 trade; residual = gap - STT."""
    cfg = _cfg(stamp_duty_rate="0", stt_rate_sell="0.001", charge_tolerance="50")
    # gap = 10050 - 10000 = 50; STT = 10000 × 0.001 = 10.00; residual = 40.00
    result = compute_charges(
        "sell", Decimal("10000"), Decimal("10050"), Decimal("10000"), cfg
    )
    assert not result.surface
    stt_leg = result.legs.get("Expenses:Investment:Charges:STT")
    assert stt_leg == Decimal("10.00")
    residual_leg = result.legs.get("Expenses:Investment:Charges:Groww")
    assert residual_leg == Decimal("40.00")


def test_stt_capped_when_gap_smaller_than_computed_stt():
    """Computed STT (100) > gap (30) → STT capped at 30, no surface."""
    cfg = _cfg(stamp_duty_rate="0", stt_rate_sell="0.01", charge_tolerance="50")
    # gap = 10030 - 10000 = 30; computed_stt = 100 → capped to 30
    result = compute_charges(
        "sell", Decimal("10000"), Decimal("10030"), Decimal("10000"), cfg
    )
    assert not result.surface
    stt_leg = result.legs.get("Expenses:Investment:Charges:STT")
    assert stt_leg == Decimal("30")
    assert "Expenses:Investment:Charges:Groww" not in result.legs


def test_buy_has_no_stt_even_when_rate_set():
    cfg = _cfg(stamp_duty_rate="0", stt_rate_sell="0.001", charge_tolerance="50")
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("10050"), Decimal("10000"), cfg
    )
    assert not result.surface
    assert "Expenses:Investment:Charges:STT" not in result.legs


# ---------------------------------------------------------------------------
# Surface signals
# ---------------------------------------------------------------------------


def test_surface_fires_on_negative_buy_gap_beyond_tolerance():
    """cash < cost on a buy, shortfall bigger than tolerance → anomalous → surface."""
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    # gap = 9899 - 10000 = -101 (shortfall exceeds the 50 tolerance)
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("9899"), Decimal("10000"), cfg
    )
    assert result.surface
    # Was `result.legs == {}` under the pre-P5 contract, where surfacing meant
    # "book nothing and refuse the commit". It now means "book the remainder
    # to the unexplained account and flag the row", so the legs are the whole
    # gap, in one leg, and they still sum to it.
    assert result.legs == {"Expenses:Investment:Charges:Unexplained": Decimal("-101")}
    assert sum(result.legs.values()) == result.gap
    # Still no stamp-duty leg on a negative gap, even beyond tolerance: cash
    # was LESS than units * nav, which is no evidence a stamp duty was paid.
    assert "Expenses:Investment:Charges:StampDuty" not in result.legs


def test_small_negative_gap_within_tolerance_books_as_rounding_credit():
    """Groww's own xlsx export rounds units/NAV/amount for display, which can
    make the computed cost (units × displayed-NAV) exceed the reported cash
    by a few rupees even though the trade is internally consistent at full
    precision. A shortfall within the applied tolerance band is exactly that
    artifact, not an anomalous trade — book it as a credit into the same
    charges account instead of surfacing."""
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    # Real trade: 2967.455 units @ 30.3275 = 89995.50 cash. The rounded xlsx
    # export reports 2967.45 units @ 30.33 -> cost = 90002.7585, gap = -7.7585.
    result = compute_charges(
        "buy",
        Decimal("90002.7585"),
        Decimal("89995"),
        Decimal("90002.7585"),
        cfg,
    )
    assert not result.surface
    # NO stamp-duty leg: a genuinely-charged stamp duty is deducted before
    # allotment and so shows up as a POSITIVE gap. A negative gap means the
    # export rounding swamped it, so its value is unresolvable here and the
    # single plug leg is the whole truthful statement.
    assert result.legs == {"Expenses:Investment:Charges:Groww": Decimal("-7.7585")}
    assert sum(result.legs.values()) == result.gap
    assert result.residual == Decimal("-7.7585")
    assert result.gap == Decimal("-7.7585")


def test_negative_gap_exactly_at_tolerance_boundary_does_not_surface():
    cfg = _cfg(
        stamp_duty_rate="0",
        stt_rate_sell="0",
        charge_tolerance="50",
        charge_tolerance_pct="0",
    )
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("9950"), Decimal("10000"), cfg
    )
    assert not result.surface
    assert result.legs == {"Expenses:Investment:Charges:Groww": Decimal("-50")}


# ---------------------------------------------------------------------------
# Relative tolerance (charge_tolerance_pct) — scales with trade size
# ---------------------------------------------------------------------------


def test_relative_tolerance_covers_large_trade_rounding_gap():
    """A ~6L trade (36834.64 units @ 16.29, Groww's rounded xlsx export)
    produces a -66.2856 gap that exceeds the flat 50 tolerance but is well
    within 0.05% of the 600036.2856 trade value (~300) -- should book as a
    rounding credit, not surface."""
    cfg = _cfg(
        stamp_duty_rate="0.00005",
        stt_rate_sell="0",
        charge_tolerance="50",
        charge_tolerance_pct="0.0005",
    )
    result = compute_charges(
        "buy",
        Decimal("600036.2856"),
        Decimal("599970"),
        Decimal("600036.2856"),
        cfg,
    )
    assert not result.surface
    # No stamp-duty leg on a negative gap, even though the computed stamp
    # would be 600036.2856 * 0.00005 = 30.00 -- the row's cash evidence
    # cannot support it. See the negative branch in charges.py.
    assert result.legs == {"Expenses:Investment:Charges:Groww": Decimal("-66.2856")}
    assert sum(result.legs.values()) == result.gap


def test_relative_tolerance_disabled_falls_back_to_flat():
    """charge_tolerance_pct='0' → effective tolerance is just the flat value,
    so the same large-trade gap still surfaces."""
    cfg = _cfg(
        stamp_duty_rate="0.00005",
        stt_rate_sell="0",
        charge_tolerance="50",
        charge_tolerance_pct="0",
    )
    result = compute_charges(
        "buy",
        Decimal("600036.2856"),
        Decimal("599970"),
        Decimal("600036.2856"),
        cfg,
    )
    assert result.surface


def test_surface_fires_when_residual_exceeds_tolerance():
    """All rates zero; gap > tolerance → residual > tolerance → surface=True."""
    cfg = _cfg(stamp_duty_rate="0", stt_rate_sell="0", charge_tolerance="50")
    # gap = 200; computed = 0; residual = 200 > 50 → surface
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("10200"), Decimal("10000"), cfg
    )
    assert result.surface
    # Was `result.legs == {}` under the pre-P5 contract. The over-tolerance
    # residual is now booked to the unexplained account rather than dropped.
    assert result.legs == {"Expenses:Investment:Charges:Unexplained": Decimal("200")}
    assert sum(result.legs.values()) == result.gap


def test_no_residual_leg_when_residual_is_zero():
    """Stamp exactly equals the gap → residual = 0 → no residual leg."""
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    # gap = 0.50; computed_stamp = 10000 × 0.00005 = 0.50 → stamp capped at 0.50; residual = 0
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("10000.50"), Decimal("10000"), cfg
    )
    assert not result.surface
    assert "Expenses:Investment:Charges:Groww" not in result.legs
    stamp = result.legs.get("Expenses:Investment:Charges:StampDuty")
    assert stamp == Decimal("0.50")


def test_charge_result_carries_no_reason_field():
    """`reason` built two strings nothing in production read: its only
    consumer was the detail payload of the CHARGE_VIOLATION error P5 removed.
    Pinned as deleted so it is not reintroduced as dead structure."""
    cfg = _cfg(stamp_duty_rate="0", stt_rate_sell="0", charge_tolerance="50")
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("10200"), Decimal("10000"), cfg
    )
    assert result.surface
    assert not hasattr(result, "reason")


def test_gap_field_populated_correctly():
    cfg = _cfg(stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50")
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("10050"), Decimal("10000"), cfg
    )
    assert result.gap == Decimal("50")


# ---------------------------------------------------------------------------
# Precision-derived tolerance (units/NAV display precision, not trade value)
# ---------------------------------------------------------------------------


def test_floor_dominates_at_retail_display_precision():
    """At retail sizes the formula lands well under TOLERANCE_FLOOR, so the
    floor is the band. This pins the floor ONLY -- it would still pass if the
    formula were deleted; `test_tolerance_formula_without_the_floor_masking_it`
    is what covers the formula."""
    # units shown to 3dp, nav to 4dp -> ulp(units)=0.001, ulp(nav)=0.0001
    # tolerance = 100.123 * 0.0001 + 52.3456 * 0.001 = 0.0100123 + 0.0523456
    units, nav = Decimal("100.123"), Decimal("52.3456")
    assert charge_tolerance(units, nav) == TOLERANCE_FLOOR == Decimal("1")


def test_tolerance_formula_without_the_floor_masking_it():
    """The floor dominates at retail sizes, so exercise the formula itself on
    values large enough to clear it: the band must be exactly
    units*ulp(nav) + nav*ulp(units), with no reference to trade value."""
    units, nav = Decimal("100000.123"), Decimal("52.3456")
    # 100000.123 * 0.0001 + 52.3456 * 0.001 = 10.0000123 + 0.0523456
    assert charge_tolerance(units, nav) == Decimal("10.0523579")
    assert charge_tolerance(units, nav) > Decimal("1")  # floor is not in play


def test_tolerance_scales_with_precision_not_with_trade_value():
    # Same trade value, coarser display precision -> a wider band.
    fine = charge_tolerance(Decimal("100.123"), Decimal("52.3456"))
    coarse = charge_tolerance(Decimal("100"), Decimal("52"))
    assert coarse > fine
    # And the coarse band is NOT a percentage of trade value:
    assert coarse != Decimal("5236.00") * Decimal("0.0005")


def test_a_percentage_band_would_have_absorbed_what_precision_rejects():
    """The defect this unit exists to remove: a wide percentage band swallowed
    a real discrepancy. With precision-derived tolerance it must surface."""
    units, nav = Decimal("1000.000"), Decimal("179.9900")
    trade_value = units * nav
    cfg = MergeBookingConfig(stamp_duty_rate="0")
    # A 14.84 remainder: inside max(50, 0.05% * 179990) = 89.995,
    # outside units*ulp(nav) + nav*ulp(units) = 0.1 + 0.17999 = 0.27999.
    result = compute_charges(
        "buy",
        trade_value=trade_value,
        cash=trade_value + Decimal("14.84"),
        cost=trade_value,
        cfg=cfg,
        units=units,
        nav=nav,
    )
    assert result.tolerance < Decimal("1.01")
    assert result.surface is True
    assert result.residual == Decimal("14.84")


def test_no_stamp_duty_leg_when_the_gap_is_negative():
    """Stamp duty is deducted from the subscription BEFORE allotment, so a
    stamp duty that was really charged produces a POSITIVE gap of about its
    own value. A negative gap means the export's rounding swamped it, leaving
    the true stamp somewhere in [0, computed_stamp] and unresolvable. Booking
    the full computed 30.00 against a -0.10 gap would assert an observation
    the row cannot evidence and inflate the plug leg 300x."""
    units, nav = Decimal("1000.000"), Decimal("599.9700")
    trade_value = units * nav
    cfg = MergeBookingConfig(stamp_duty_rate="0.00005")
    result = compute_charges(
        "buy",
        trade_value=trade_value,
        cash=trade_value - Decimal("0.10"),  # tiny shortfall, within tolerance
        cost=trade_value,
        cfg=cfg,
        units=units,
        nav=nav,
    )
    assert result.surface is False
    assert cfg.charge_stamp_duty_account not in result.legs
    assert result.legs == {cfg.charges_account: Decimal("-0.10")}
    # The legs must sum to the gap or the caller's transaction will not balance.
    assert sum(result.legs.values()) == result.gap


def test_stamp_duty_is_emitted_on_a_positive_gap_under_the_precision_band():
    """The other half of the reorder: on a non-negative gap the statutory
    stamp duty is still computed from trade value and emitted, capped at the
    cash gap. 599970.0000 * 0.00005 = 29.99850 -> ROUND_HALF_UP -> 30.00."""
    units, nav = Decimal("1000.000"), Decimal("599.9700")
    trade_value = units * nav
    cfg = MergeBookingConfig(stamp_duty_rate="0.00005")
    result = compute_charges(
        "buy",
        trade_value=trade_value,
        cash=trade_value + Decimal("30.00"),
        cost=trade_value,
        cfg=cfg,
        units=units,
        nav=nav,
    )
    assert result.surface is False
    # Literal, not re-derived: Decimal.quantize defaults to ROUND_HALF_EVEN
    # while the implementation uses ROUND_HALF_UP, so a re-derivation here
    # would agree only by coincidence of the chosen value.
    assert result.legs == {cfg.charge_stamp_duty_account: Decimal("30.00")}
    assert sum(result.legs.values()) == result.gap


def test_precision_band_is_never_wider_than_the_legacy_band():
    """INVARIANT: the effective tolerance never exceeds the legacy
    max(flat, pct * trade_value) band.

    The "AS DISPLAYED" premise behind _ulp does not survive xlsx, which is
    what real Groww order history is. `pd.read_excel(..., dtype=str)` reads
    the stored double and discards number_format, so a cell holding 100.0 and
    DISPLAYING `100.000` arrives as the string "100". ulp is then 1 on both
    columns and the raw formula gives 100 * 1 + 215 * 1 = 315 on a 21,500
    trade: 1.465%, and 6.3x WIDER than the 50 band being replaced -- i.e. MORE
    silent absorption, the defect this unit removes. Capping by the legacy
    band makes the change a strict improvement: narrower where precision is
    known, identical where it is not.

    The proper fix is to source each column's declared precision from the
    import template rather than infer it from a float-round-tripped string;
    this cap is the guard until then, not the final answer.
    """
    cfg = MergeBookingConfig(stamp_duty_rate="0")
    shapes = [
        # The xlsx shape, with its real numbers: a 21,500 trade.
        (Decimal("100"), Decimal("215")),
        # Full-precision CSV shapes, where the precision band genuinely wins.
        (Decimal("164.788"), Decimal("30.3450")),
        (Decimal("3295.356"), Decimal("30.3456")),
        (Decimal("2967.45"), Decimal("30.33")),
        # A hand-written ledger-cli journal: whole units, whole price.
        (Decimal("10"), Decimal("30")),
    ]
    for units, nav in shapes:
        trade_value = units * nav
        legacy_band = max(
            Decimal(cfg.charge_tolerance),
            trade_value * Decimal(cfg.charge_tolerance_pct),
        )
        result = compute_charges(
            "buy",
            trade_value=trade_value,
            cash=trade_value,
            cost=trade_value,
            cfg=cfg,
            units=units,
            nav=nav,
        )
        assert result.tolerance <= legacy_band, (units, nav, legacy_band)

    # The xlsx shape specifically: the raw formula is 315, the legacy band is
    # 50, and the effective tolerance must be the 50 -- not the 315.
    assert charge_tolerance(Decimal("100"), Decimal("215")) == Decimal("315")
    xlsx = compute_charges(
        "buy",
        trade_value=Decimal("21500"),
        cash=Decimal("21500"),
        cost=Decimal("21500"),
        cfg=cfg,
        units=Decimal("100"),
        nav=Decimal("215"),
    )
    assert xlsx.tolerance == Decimal("50")


def test_legs_accumulate_when_two_charge_accounts_collide():
    """charges_account and charge_stamp_duty_account are both user-configurable
    and persisted. If a config names them identically, assigning into the legs
    dict would drop one leg and the legs would no longer sum to the gap --
    an unbalanced transaction. Accumulation keeps the invariant."""
    collided = "Expenses:Investment:Charges:Everything"
    cfg = MergeBookingConfig(
        stamp_duty_rate="0.00005",
        stt_rate_sell="0",
        charge_tolerance="50",
        charges_account=collided,
        charge_stamp_duty_account=collided,
    )
    # trade_value = 10000, stamp = 0.50, gap = 50 -> residual = 49.50.
    result = compute_charges(
        "buy", Decimal("10000"), Decimal("10050"), Decimal("10000"), cfg
    )
    assert result.surface is False
    assert result.legs == {collided: Decimal("50.00")}
    assert sum(result.legs.values()) == result.gap


def test_tolerance_is_populated_on_both_surfacing_paths():
    """Neither surface=True return had a tolerance assertion."""
    cfg = MergeBookingConfig(
        stamp_duty_rate="0", stt_rate_sell="0", charge_tolerance="50"
    )
    # Negative gap beyond tolerance.
    negative = compute_charges(
        "buy", Decimal("10000"), Decimal("9899"), Decimal("10000"), cfg
    )
    assert negative.surface is True
    assert negative.tolerance == Decimal("50")
    # Over-tolerance positive residual.
    positive = compute_charges(
        "buy", Decimal("10000"), Decimal("10200"), Decimal("10000"), cfg
    )
    assert positive.surface is True
    assert positive.tolerance == Decimal("50")


def test_units_and_nav_absent_falls_back_to_the_configured_flat_tolerance():
    """Call sites not yet migrated must keep their old behaviour exactly."""
    cfg = MergeBookingConfig()
    result = compute_charges(
        "buy",
        trade_value=Decimal("10000"),
        cash=Decimal("10040"),
        cost=Decimal("10000"),
        cfg=cfg,
    )
    assert result.tolerance == Decimal("50")
    assert result.surface is False


# ---------------------------------------------------------------------------
# P5 Task 2: an over-tolerance remainder is BOOKED and FLAGGED, never dropped
# and never grounds for refusing the import.
# ---------------------------------------------------------------------------


def test_an_over_tolerance_remainder_is_booked_to_a_named_account_not_dropped():
    units, nav = Decimal("1000.000"), Decimal("179.9900")
    trade_value = units * nav
    cfg = MergeBookingConfig(stamp_duty_rate="0")
    result = compute_charges(
        "buy",
        trade_value=trade_value,
        cash=trade_value + Decimal("14.84"),
        cost=trade_value,
        cfg=cfg,
        units=units,
        nav=nav,
    )
    assert result.surface is True
    # The point of the unit: the money is VISIBLE, not absorbed and not refused.
    assert result.legs == {cfg.charge_unexplained_account: Decimal("14.84")}
    assert sum(result.legs.values()) == result.gap
    # And it surfaced because of the PRECISION band, not the legacy one: the
    # legacy band here is max(50, 0.05% * 179990) = 89.995, which 14.84 would
    # sit comfortably inside. A migrated call site that silently dropped
    # units/nav would return surface=False and absorb the 14.84.
    assert result.tolerance == TOLERANCE_FLOOR
    assert result.tolerance < max(
        Decimal(cfg.charge_tolerance),
        trade_value * Decimal(cfg.charge_tolerance_pct),
    )


def test_the_legs_still_sum_to_the_real_cash_gap_when_surfacing():
    """Whatever we decide to call the remainder, the entry must still balance:
    an unexplained amount that silently fails to balance is worse than one
    that blocks."""
    units, nav = Decimal("500.00"), Decimal("20.0000")
    trade_value = units * nav
    cfg = MergeBookingConfig(stamp_duty_rate="0.00005")
    result = compute_charges(
        "buy",
        trade_value=trade_value,
        cash=trade_value + Decimal("75.00"),
        cost=trade_value,
        cfg=cfg,
        units=units,
        nav=nav,
    )
    assert result.surface is True
    assert sum(result.legs.values()) == Decimal("75.00")
    assert cfg.charge_stamp_duty_account in result.legs
    assert cfg.charge_unexplained_account in result.legs
    # The evidenced statutory leg is still exactly the statutory figure --
    # the unexplained bucket takes the remainder, not the whole gap.
    assert result.legs[cfg.charge_stamp_duty_account] == Decimal("0.50")
    assert result.legs[cfg.charge_unexplained_account] == Decimal("74.50")


def test_a_negative_gap_beyond_tolerance_still_surfaces_and_still_balances():
    units, nav = Decimal("100.00"), Decimal("10.0000")
    trade_value = units * nav
    cfg = MergeBookingConfig(stamp_duty_rate="0")
    result = compute_charges(
        "buy",
        trade_value=trade_value,
        cash=trade_value - Decimal("40.00"),
        cost=trade_value,
        cfg=cfg,
        units=units,
        nav=nav,
    )
    assert result.surface is True
    assert sum(result.legs.values()) == Decimal("-40.00")
    assert result.legs == {cfg.charge_unexplained_account: Decimal("-40.00")}


def test_precision_band_binds_once_units_and_nav_carry_their_display_exponents():
    # Pre-P5a these arrived as Decimal("1000") and Decimal("179.99"):
    # band = 1000*0.01 + 179.99*1 = 189.99, capped back to the legacy band.
    # With the display exponents recovered the band is
    # 1000*0.0001 + 179.99*0.001 = 0.27999, i.e. under the 1-rupee cash
    # floor, so the floor applies -- a ~90x narrowing on this row.
    assert (
        charge_tolerance(
            Decimal("1000.000"), Decimal("179.9900"), cap=Decimal("89.995")
        )
        == TOLERANCE_FLOOR
    )
    # And the pre-P5a inputs still cap, unchanged: no regression wherever
    # precision is not recoverable.
    assert charge_tolerance(
        Decimal("1000"), Decimal("179.99"), cap=Decimal("89.995")
    ) == Decimal("89.995")


def test_the_cap_binds_on_a_surfacing_path_too():
    """Review minor (3) from the Task 1 re-review: every existing assertion on
    the cap lands on a non-surfacing shape, so a future edit that recomputed a
    tolerance inside a `surface=True` branch would go unnoticed. This pins it
    with `cash != cost` AND `surface is True`.

    units/nav both parse back with exponent 0 (the xlsx shape), so the raw
    precision band is 100*1 + 215*1 = 315 -- 6.3x WIDER than the legacy 50.
    The cap must bring it back to 50."""
    units, nav = Decimal("100"), Decimal("215")
    trade_value = units * nav  # 21500
    cfg = _cfg(stamp_duty_rate="0", stt_rate_sell="0", charge_tolerance="50")
    legacy_band = max(
        Decimal(cfg.charge_tolerance),
        trade_value * Decimal(cfg.charge_tolerance_pct),
    )
    assert charge_tolerance(units, nav) == Decimal("315")  # uncapped
    for cash in (trade_value + Decimal("300"), trade_value - Decimal("300")):
        result = compute_charges(
            "buy",
            trade_value=trade_value,
            cash=cash,
            cost=trade_value,
            cfg=cfg,
            units=units,
            nav=nav,
        )
        assert result.surface is True, cash
        assert result.tolerance == legacy_band == Decimal("50"), cash
        assert sum(result.legs.values()) == result.gap, cash
