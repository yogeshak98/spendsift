from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Literal

from backend.app.models.domain.charges import ChargeResult
from backend.app.models.storage.merge_booking import MergeBookingConfig

Side = Literal["buy", "sell"]


# The error propagation below bounds the uncertainty in `cost = units * nav`,
# but the quantity actually being bounded is `gap = abs(cash) - cost`, and
# `cash` is itself a rounded printed figure. The missing term is `ulp(cash)`,
# which this floor stands in for: the Groww Amount column is printed in whole
# rupees (21500, 12000, 1000), so ulp(cash) == 1. The floor is therefore
# derivable rather than arbitrary -- it is just not currently derived, because
# `cash` is not an argument to charge_tolerance.
TOLERANCE_FLOOR = Decimal("1")


def _ulp(value: Decimal) -> Decimal:
    """The unit in the last place of `value` AS DISPLAYED.

    `Decimal` preserves the exponent of the string it was parsed from, so a
    broker export showing `52.3456` gives exponent -4 and a ulp of 0.0001.
    That is exactly the uncertainty the export introduced: the true NAV lies
    somewhere within half a ulp of what was printed.
    """
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        # A NaN/Infinity Decimal carries 'n'/'N'/'F' here, not an int, and
        # Decimal(1).scaleb('n') raises. This narrowing is what makes `ty`
        # accept the scaleb call; it is NOT runtime safety, because a
        # non-finite operand still flows through abs(units) * _ulp(nav)
        # (NaN raises InvalidOperation out of max(); Infinity would yield an
        # INFINITE tolerance that accepts any discrepancy at all). Non-finite
        # units/NAV are rejected at the parser boundary instead -- see
        # backend/app/service/ingest/parsers/groww_mf.py::_decimal.
        return TOLERANCE_FLOOR
    return Decimal(1).scaleb(exponent)


def charge_tolerance(
    units: Decimal, nav: Decimal, cap: Decimal | None = None
) -> Decimal:
    """How far the computed cost may legitimately differ from reported cash.

    First-order error propagation on `cost = units * nav`: an error of
    `ulp(nav)` in the price contributes `units * ulp(nav)`, and an error of
    `ulp(units)` in the quantity contributes `nav * ulp(units)`.

    This replaces `max(flat, pct * trade_value)`. A percentage band is wrong
    in BOTH directions: far too wide on a large trade whose units and NAV were
    exported at full precision (it silently absorbed real discrepancies), and
    too narrow on a small trade whose units were rounded to whole numbers.
    The uncertainty was never about how much money moved -- it is about how
    many digits the broker chose to print.

    `cap` bounds the result from ABOVE, and callers must supply the legacy
    `max(flat, pct * trade_value)` band for it. The reason this is still
    needed even now that xlsx precision is recovered: the "AS DISPLAYED"
    premise in `_ulp` depends on the Decimal it is given actually carrying
    the printed exponent, and that is not guaranteed everywhere. `.xls`
    (the old binary format) and a column formatted `General` or as text
    carry no recoverable display precision at all, and
    `backend.app.service.ingest.parsers.csv_xls._widen_to_display` leaves those cells alone
    rather than guess. Before the parser recovered `number_format`,
    `pd.read_excel(path, header=None, dtype=str)` read the stored double for
    EVERY xlsx cell, not the cell's number_format, so a cell holding 100.0
    and DISPLAYING `100.000` came back as the string "100": exponent 0, ulp
    1 on both columns, giving 100 * 1 + 215 * 1 = 315 on a 21,500 trade --
    1.465%, and 6.3x WIDER than the 50 band this function replaces. The cap
    is what kept that shape a no-regression change rather than a silent
    widening while the recovery was unbuilt, and it still protects the
    inputs recovery cannot reach.

    Capping makes the change a strict improvement with no regression: the band
    narrows wherever the precision is genuinely known, and falls back to
    exactly the historical band wherever it is not knowable.

    WHAT THIS ACTUALLY DELIVERS, stated honestly: the xlsx parser CAN read
    `cell.number_format` at the parser boundary
    (`backend.app.service.ingest.parsers.csv_xls._widen_to_display`) and widen each cell's
    `Decimal` to the exponent that format implies, moving the exponent
    without changing the value. IF a NAV cell held 179.99 and DISPLAYED
    `179.9900` as a genuinely numeric, number-formatted cell, it would arrive
    as `Decimal("179.9900")`, exponent -4, and a units cell holding 1000.0
    and displaying `1000.000` the same way would arrive as
    `Decimal("1000.000")`, exponent -3 -- `ulp` would then reflect what was
    actually printed, not pandas' shortest round-trip of the stored double.
    On the worked example above (1000 units @ 179.99 NAV, legacy band
    89.995) the band would then be `1000 * 0.0001 + 179.99 * 0.001 =
    0.27999`, under `TOLERANCE_FLOOR`, so the applied band would be the
    1-rupee cash floor rather than the 89.995 cap -- roughly a 90x narrowing.

    That is what the mechanism does WHEN a sheet carries real number
    formats. That was confirmed by hand against one of this user's own
    bank-statement xlsx files -- a sheet with 204 numeric cells carrying a
    real number format, 152 of which widened -- NOT by a test: the file is
    private data outside the repo, so no test can reference it, and the
    synthetic fixtures in `tests/service/ingest/parsers/test_csv_xls.py` are what pin the
    behaviour. It is NOT what
    happens on a real Groww export today: every Groww order-history
    workbook examined (seven files, FY2019-20 through October 2026) stores
    Units, NAV, and Amount as TEXT cells formatted 'General', so
    `display_places` returns `None` for every one of them, `_widen_to_display`
    is a no-op on all seven, and `ulp` sees exactly the same exponents it saw
    before this unit shipped. The flag that opts Groww into this
    (`backend.app.service.ingest.parsers.groww_mf.parse_groww`) stays on because it costs nothing
    and is correct for a Groww export that ever starts writing numeric
    cells, not because it currently changes anything.

    One consequence worth recording plainly: the ₹14.84 absorbed
    discrepancy that originally motivated this work is therefore STILL
    UNEXPLAINED. It was not caused by display-precision loss -- precision
    recovery is a no-op on the Groww files that discrepancy came from -- so
    it remains open, and needs re-diagnosing against the legacy `cap`,
    `TOLERANCE_FLOOR`, or a source outside this function altogether. This
    docstring does not guess which.

    So the premise was never that xlsx data is coarse; it is that `ulp`
    reading the SHORTEST representation of a float-round-tripped string
    overstates uncertainty precisely for round-ish values, which are the
    common case, and that the fix is to stop reading the shortest
    representation and read the display format instead. An xlsx datum
    really is EXACT -- "100" means the stored number is 100, not "something
    rounded to a whole number" -- the missing information was never in the
    value, only in the format openpyxl exposes and pandas discarded. (An
    earlier version of this note claimed such a cell carried about +/-157 of
    true uncertainty, and used that to reject clamping the exponent to -2.
    The claim was false -- measured, not reasoned. Clamping would have given
    3.15 on that shape, about 3x LOOSER than the truth, never tighter. The
    cap still ships because it needs only one property, `tolerance <=
    legacy_band` on every input, and provably has it; a heuristic does not.)

    The cap remains the guarantee of no regression for inputs where
    precision is unknowable -- `.xls`, a column formatted `General` or as
    text, or a cell the two readers disagree about -- not the final answer
    for those. One further term is still underivable rather than done:
    `ulp(cash)` is not an argument to `charge_tolerance`, so the floor below
    stands in for it as a constant rather than a derived figure (see
    `TOLERANCE_FLOOR`). That is a separate, smaller unit, deliberately not
    done here.
    """
    band = max(
        abs(units) * _ulp(nav) + abs(nav) * _ulp(units),
        TOLERANCE_FLOOR,
    )
    if cap is None:
        return band
    return min(band, cap)


# The round unit a Groww MF order is placed in, and how far the estimated
# gross may sit below it. See `snap_purchase_cash`.
SNAP_UNIT = Decimal("100")
SNAP_BAND = Decimal("1.00")


def snap_purchase_cash(
    amount: Decimal, cost: Decimal, cfg: MergeBookingConfig
) -> Decimal:
    """The cash actually paid for a Groww MF PURCHASE whose export `Amount` is
    `amount`, or `amount` itself when that cannot be told.

    Groww's order-history `Amount` is the amount AFTER the purchase stamp duty
    (`cfg.stamp_duty_rate`), truncated to the rupee: a 15,000 SIP is exported
    as 14,999. Booking 14,999 strands a rupee per order on the Groww cash
    account (the bank sent 15,000) and under-books stamp duty. So:

    * estimate the gross paid, `est = amount / (1 - rate)`;
    * let `R` be the smallest multiple of 100 that is >= `amount`;
    * if `0 <= R - est <= 1.00`, the cash is `R`; otherwise it is `amount`.

    Two further conditions keep today's behaviour wherever the premise does
    not hold: a non-positive `rate` (stamp duty disabled, so there is no
    deduction to undo) and `R < cost` (the snapped cash would still be less
    than units * NAV, so snapping explains nothing and would only feed the
    negative-gap credit path). A snapped order therefore always reaches
    `compute_charges` with a non-negative gap, and never books a negative
    charge.

    Two consequences of the band, both accepted:

    * It is an absolute 1.00, not a fraction of the order, so a tiny order
      snaps at a large relative rate: an Amount of 99 becomes 100 and books
      about 1.00 of rounding (about 1%). Groww's minimum MF order is 100, so
      such rows are not expected in practice.
    * The rounding leg is NOT bounded by 1.00. It is about
      `(R - est) + (amount - cost)`, so at the band edge (`R - est` = 1.00
      exactly) on a row whose units * NAV sits below the Amount, it reaches
      about 1.50 (e.g. an Amount of 14,998.25005 with units * NAV of
      14,997.75005, snapped to 15,000: stamp 0.75, rounding 1.49995). Whether that is booked as rounding or flagged depends on
      `compute_charges`' tolerance: under the legacy fallback band it is
      booked to `charges_account` as rounding, unflagged; under the
      precision band's 1-rupee floor the part over the band goes to
      `charge_unexplained_account` and the row is flagged for review.
      Either way it is booked and visible, never dropped or negative.

    Only the Groww MF purchase path may call this -- its `Amount` is the only
    one with these semantics. The ledger-merge path's cash comes from a
    journal posting, which is real cash, and stock orders carry their own
    order value; both book their figure verbatim. `candidate.amount` is NOT
    changed: it is the dedup identity of the row.
    """
    rounded = round_gross_of(amount, cfg)
    if rounded is None or rounded < cost:
        return amount
    return rounded


def round_gross_of(amount: Decimal, cfg: MergeBookingConfig) -> Decimal | None:
    """The band test of `snap_purchase_cash` alone, with no cost guard: the
    round gross `R` (quantized to 0.01) when `amount` is consistent with a
    round order less stamp duty, else None."""
    rate = Decimal(cfg.stamp_duty_rate)
    if amount <= 0 or rate <= 0 or rate >= 1:
        return None
    est = amount / (1 - rate)
    rounded = (amount / SNAP_UNIT).to_integral_value(rounding=ROUND_CEILING) * SNAP_UNIT
    if not (0 <= rounded - est <= SNAP_BAND):
        return None
    return rounded.quantize(Decimal("0.01"))


# Units are allotted to 3 decimal places.
UNITS_QUANT = Decimal("0.001")
# How far the exact-NAV units may sit from the export's printed units: the
# export's 2-dp rounding (0.005) plus the 3-dp allotment step (0.001).
EXACT_UNITS_TOLERANCE = Decimal("0.006")

# Why the exact-NAV booking was not used for a row. Review flags; never silent.
EXACT_NAV_UNAVAILABLE = (
    "exact NAV unavailable — booked from the export's rounded units/NAV"
)
EXACT_NAV_DISAGREES = (
    "exact NAV disagrees with the export's units — booked from the export's "
    "rounded units/NAV"
)
EXACT_NAV_NOT_ROUND = (
    "exact NAV not applied — the Amount is not a round order less stamp duty, "
    "so the cash paid is not known; booked from the export's rounded units/NAV"
)
EXACT_NAV_COST_EXCEEDS_CASH = (
    "exact cost exceeds cash — units * exact NAV is more than the cash paid "
    "even after the unit step-down; not booked, check this row"
)
EXACT_NAV_FLAGS = (
    EXACT_NAV_UNAVAILABLE,
    EXACT_NAV_DISAGREES,
    EXACT_NAV_NOT_ROUND,
    EXACT_NAV_COST_EXCEEDS_CASH,
)
# The step `exact_nav_purchase` takes down when half-up units overshoot the
# cash. A module constant only so a test can disable it and reach the guard.
UNITS_STEP_DOWN = UNITS_QUANT


@dataclass(frozen=True)
class ExactPurchase:
    """A Groww MF purchase re-derived from the exact AMFI NAV.

    `legs` maps charge account -> amount, every amount > 0, and
    `units * nav + sum(legs) == cash` EXACTLY (no quantization of the
    remainder), so the entry balances with no reliance on beancount's
    tolerance.
    """

    cash: Decimal
    units: Decimal
    nav: Decimal
    legs: dict[str, Decimal]


def exact_nav_purchase(
    amount: Decimal,
    export_units: Decimal,
    nav_exact: Decimal,
    cfg: MergeBookingConfig,
) -> tuple[ExactPurchase | None, str | None]:
    """Book a Groww MF purchase from the exact NAV, or say why not.

    Returns `(purchase, None)` on success and `(None, reason)` otherwise,
    where `reason` is one of `EXACT_NAV_FLAGS` for the review flag.

    1. `cash` = the round gross `R` (`round_gross_of`). No `R` -> not round.
    2. `stamp = round_half_up(cash * rate, 2)`.
    3. `units = round_half_up((cash - stamp) / nav_exact, 3)`. ASSUMPTION:
       the registrar rounds allotted units half-up to 3 dp. A registrar that
       truncates would allot up to 0.001 fewer units; the sanity check below
       still admits such a row, and the difference shows up as a slightly
       larger (still non-negative) rounding leg.
    4. Step-down: if `units * nav_exact > cash` -- the half-up overshoot is
       more than the stamp can absorb, so folding it in would take stamp
       below 0 -- take one step down (`units -= 0.001`, i.e. round down).
       Small orders on large NAVs hit this (Amount 99 at NAV 150: 0.667
       units cost 100.05 > 100, so 0.666). The remainder is then >= 0 and
       the row stays on the exact path.
    5. Sanity: `|units - export_units| <= 0.006` (after any step-down), else
       disagrees.
    6. `remainder = cash - stamp - units * nav_exact`. If >= 0 it is the
       rounding leg (`charges_account`), at most about one unit step times
       the NAV (0.001 * nav after a step-down); if negative (|remainder| <=
       stamp) it is folded into the stamp leg.
    7. Guard: should the stamp leg still be negative -- unreachable once the
       step-down exists, kept as a defensive check -- the result is
       `EXACT_NAV_COST_EXCEEDS_CASH`, never "disagrees", and
       `commit_candidates` refuses the row rather than booking anything:
       every fallback booking would carry a negative charge here.
    """
    cash = round_gross_of(amount, cfg)
    if cash is None:
        return None, EXACT_NAV_NOT_ROUND
    if nav_exact <= 0:
        return None, EXACT_NAV_DISAGREES
    rate = Decimal(cfg.stamp_duty_rate)
    stamp = (cash * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    units = ((cash - stamp) / nav_exact).quantize(UNITS_QUANT, rounding=ROUND_HALF_UP)
    if units * nav_exact > cash:
        units -= UNITS_STEP_DOWN
    if abs(units - export_units) > EXACT_UNITS_TOLERANCE:
        return None, EXACT_NAV_DISAGREES
    remainder = cash - stamp - units * nav_exact
    if remainder >= 0:
        stamp_leg, rounding_leg = stamp, remainder
    else:
        stamp_leg, rounding_leg = stamp + remainder, Decimal(0)
    if stamp_leg < 0:
        return None, EXACT_NAV_COST_EXCEEDS_CASH
    legs: dict[str, Decimal] = {}
    # Accumulated, not assigned -- see compute_charges step 5.
    for account, leg in (
        (cfg.charge_stamp_duty_account, stamp_leg),
        (cfg.charges_account, rounding_leg),
    ):
        if leg > 0:
            legs[account] = legs.get(account, Decimal(0)) + leg
    return ExactPurchase(cash=cash, units=units, nav=nav_exact, legs=legs), None


def compute_charges(
    side: Side,
    trade_value: Decimal,
    cash: Decimal,
    cost: Decimal,
    cfg: MergeBookingConfig,
    *,
    units: Decimal | None = None,
    nav: Decimal | None = None,
) -> ChargeResult:
    """Compute itemized statutory investment charges for one MF trade.

    Parameters
    ----------
    side:
        "buy" or "sell"
    trade_value:
        The gross trade value (units * NAV), positive.
    cash:
        Absolute cash amount from the broker source (e.g. debit on buy).
    cost:
        Computed cost basis (trade_value), positive.
    cfg:
        MergeBookingConfig with rates and account names.
    units:
        Traded units exactly as the broker displayed them, if known. Supplied
        together with `nav`, this selects the precision-derived tolerance.
    nav:
        Per-unit NAV exactly as the broker displayed it, if known.

    Returns
    -------
    ChargeResult with itemized legs, residual, surface flag, and the gap.

    Invariant on every return path: **`sum(legs.values()) == gap`.** The
    entry must balance whether or not we understood the money; when part of
    the gap is not attributable to a known rate, that part is booked to
    `cfg.charge_unexplained_account` and `surface` is set so the caller flags
    the row for review. Nothing is refused and nothing is dropped.
    """
    gap = abs(cash) - cost
    legacy_band = max(
        Decimal(cfg.charge_tolerance),
        trade_value * Decimal(cfg.charge_tolerance_pct),
    )
    if units is not None and nav is not None:
        # Capped by the legacy band so the precision band can only ever
        # NARROW the tolerance, never widen it -- see charge_tolerance.
        tolerance = charge_tolerance(units, nav, cap=legacy_band)
    else:
        # Not-yet-migrated call site: preserve the historical band exactly.
        tolerance = legacy_band

    # Stamp duty is a statutory charge on every purchase, so it is computed
    # from trade value before the gap's sign is inspected. It is only ever
    # EMITTED on the non-negative path, capped at `remaining`; the
    # negative-gap branch below explains why it cannot be known there.
    stamp_rate = Decimal(cfg.stamp_duty_rate)
    if side == "buy" and stamp_rate > 0:
        computed_stamp = (trade_value * stamp_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        computed_stamp = Decimal(0)

    # Step 1: negative buy-gap. A small shortfall (within tolerance) is a
    # broker-export rounding artifact (units/NAV/amount all get rounded for
    # display, and the computed cost can overshoot the reported cash by a few
    # rupees on a trade that is otherwise fully consistent) -- book it as a
    # credit into the charges account rather than surfacing. A shortfall
    # bigger than tolerance is still anomalous.
    if gap < 0:
        if -gap <= tolerance:
            # NO stamp-duty leg here, deliberately. Stamp duty is deducted
            # from the subscription BEFORE units are allotted, so a stamp
            # duty that was genuinely charged shows up as a POSITIVE gap of
            # about its own value. A negative gap means the export's rounding
            # noise swamped the stamp, so the true figure lies somewhere in
            # [0, computed_stamp] and is unresolvable at this display
            # precision. Booking `computed_stamp` here would assert an
            # observation the row's only evidence (cash was LESS than
            # units * nav) cannot support, overstate
            # Expenses:Investment:Charges:StampDuty by up to its full value,
            # and inflate this plug leg by the same amount (-30.10 where the
            # artifact is -0.10). This is also what the
            # `min(computed_stamp, remaining)` cap on the non-negative path
            # enforces: `remaining` is negative here, so the cap is 0.
            # Do not "fix" this back.
            legs: dict[str, Decimal] = {}
            legs[cfg.charges_account] = legs.get(cfg.charges_account, Decimal(0)) + gap
            return ChargeResult(
                legs=legs,
                residual=gap,
                surface=False,
                gap=gap,
                tolerance=tolerance,
            )
        # Beyond tolerance: still book it, to the unexplained account, so the
        # entry balances and the shortfall is a number someone can total and
        # chase. `surface=True` makes the caller flag the row.
        #
        # Exactly ONE leg, and no stamp-duty leg -- for the same reason the
        # within-tolerance branch above emits none. A negative gap means cash
        # was LESS than units * nav, which is no evidence at all that a stamp
        # duty was charged; asserting `computed_stamp` here would overstate
        # Expenses:Investment:Charges:StampDuty and inflate this leg by the
        # same amount. Do not "fix" this back.
        legs = {cfg.charge_unexplained_account: gap}
        return ChargeResult(
            legs=legs,
            residual=gap,
            surface=True,
            gap=gap,
            tolerance=tolerance,
        )

    remaining = gap  # a running budget; always >= 0

    # Step 2: stamp duty (buy only, computed above), capped at remaining.
    # The cap exists so booked charges never exceed the real cash gap.
    stamp = min(computed_stamp, remaining)
    remaining -= stamp

    # Step 3: STT (sell only), capped at remaining.
    stt_rate = Decimal(cfg.stt_rate_sell)
    if side == "sell" and stt_rate > 0:
        computed_stt = (trade_value * stt_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        stt = min(computed_stt, remaining)
        remaining -= stt
    else:
        stt = Decimal(0)

    residual = remaining  # always >= 0

    # Step 4: over-tolerance residual. The statutory legs computed above are
    # evidenced and are kept; what is left over is NOT attributable to any
    # rate we know, so it goes to the unexplained account rather than into
    # `charges_account` (which would silently inflate a bucket that is read
    # as "broker charges") or nowhere at all. `surface=True` makes the caller
    # flag the row. `stamp + stt + residual == gap` by construction, so the
    # sum-to-gap invariant is preserved exactly as on the booking path.
    over_tolerance = residual > tolerance
    unexplained_account = cfg.charge_unexplained_account if over_tolerance else None

    # Step 5: build itemized legs, omitting any zero-amount leg.
    # Accumulated, not assigned: charges_account, charge_stamp_duty_account
    # and charge_stt_account are all user-configurable and persisted, so two
    # of them can name the same account. A plain assignment would then drop a
    # leg and break the legs-sum-to-gap invariant the caller's transaction
    # balance depends on. MergeBookingStore.save also rejects duplicates, but
    # a config can reach here without passing through save.
    legs = {}
    for account, amount in (
        (cfg.charge_stamp_duty_account, stamp),
        (cfg.charge_stt_account, stt),
        (unexplained_account or cfg.charges_account, residual),
    ):
        if amount > 0:
            legs[account] = legs.get(account, Decimal(0)) + amount

    return ChargeResult(
        legs=legs,
        residual=residual,
        surface=over_tolerance,
        gap=gap,
        tolerance=tolerance,
    )
