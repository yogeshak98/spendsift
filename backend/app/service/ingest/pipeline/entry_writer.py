from __future__ import annotations

import calendar
import re
from datetime import date
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from beancount.core import account as bc_account
from beancount.core import data
from beancount.core.amount import CURRENCY_RE, Amount
from beancount.core.inventory import Inventory
from beancount.core.position import Cost, CostSpec

from backend.app.models.storage.merge_booking import MergeBookingConfig

# CHARGE_VIOLATION (E1024) is deliberately NOT imported here any more. An
# over-tolerance charge gap no longer refuses the commit -- it books the
# remainder to MergeBookingConfig.charge_unexplained_account and flags the row
# (CandidateTxn.unexplained_charge). The error code itself stays in the
# append-only registry (backend/app/core/errors/error_codes.py) and is now
# unreferenced by the ingest paths.
from backend.app.core.errors.errors import (
    LOT_NOT_BOOKABLE,
    UNCOVERED_REDEMPTION,
    UNRECONCILED_STOCK_ORDER,
)
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.dedup import source_amount
from backend.app.service.ingest.platform_accounts import (
    GROWW,
    platform_cash_account,
    platform_clearing_account,
)
from backend.app.service.ingest.parsers.scheme_master import SchemeMaster
from backend.app.models.domain.accounts import (
    HOLDING_PREFIX,
    INVESTMENT_MUTUAL_FUND,
    OPERATING_CURRENCY,
    Vehicle,
    holding_account,
)
from backend.app.persistence.journal import Ledger, hold_write_locks

CAPITAL_GAINS = "Income:Investment:CapitalGains"
DIVIDEND_INCOME = "Income:Investment:Dividend"

# Statutory equity LTCG holding-period threshold (12 months). This is a stable
# statute (unlike the config-driven yearly rates in backend/app/service/tax/rules.py
# / config/tax/<fy>.yaml, which change with the Finance Act), so it is a
# hardcoded constant rather than a config field.
EQUITY_LTCG_MONTHS = 12

# Capital-gains buckets a realized MF gain can land in. Must match
# backend/app/service/tax/report.py's _CG_PREFIX/_CG_KINDS exactly.
CG_LTCG = "Income:CapitalGains:LTCG"
CG_STCG = "Income:CapitalGains:STCG"
CG_DEBT = "Income:CapitalGains:Debt"
# A gain whose tax treatment we cannot decide: the scheme is neither an AMFI
# equity nor an AMFI debt scheme (arbitrage, gold, hybrid, equity-savings,
# index-tracking ELSS ...) or could not be found in the scheme master at all.
CG_UNCLASSIFIED = "Income:CapitalGains:Unclassified"

# The subsystem-internal clearing account inside investments.beancount is the
# configured `platform_clearing_account(GROWW)`: the cash leg every
# PURCHASE/REDEEM subsystem entry balances against, and the source account the
# Groww MF parser stamps on its rows (the real cash lives in the configured
# Groww wallet, `platform_cash_account(GROWW)`, in the main ledger's vehicle
# entry).


# Largest INR discrepancy tolerated between an order's Value and the sum of
# the lot postings we book for it. Anything above this is a mis-consumed lot
# walk (a single missing share is orders of magnitude bigger), never rounding
# -- `_stock_lot_price` guarantees the rounding residual stays sub-paisa.
STOCK_RECONCILE_TOLERANCE = Decimal("0.01")

# Quantum used for a lot price only when the parser's 4dp `unit_cost` would
# not reproduce the order's Value (see `_stock_lot_price`).
_LOT_PRICE_EXACT_QUANT = Decimal("0.0000000001")


def _holding_account(commodity: str, vehicle: str = Vehicle.MUTUAL_FUND) -> str:
    """Holding account for `commodity` under `vehicle`.

    Defaults to mutual_fund so every existing MF call site is unchanged --
    MF's behaviour is the regression guard for this refactor. Thin alias for
    `accounts.holding_account`, which is the shared definition journal.py uses
    too.
    """
    return holding_account(commodity, vehicle)


def _charge_meta(account: str, cfg: MergeBookingConfig) -> dict:
    """Provenance for one charge posting.

    `charge-basis: statutory` is a CLAIM: this amount was derived from a
    statutory rate in the booking config. Only two accounts can ever carry an
    amount that was, so this is an ALLOW-LIST of those two rather than a
    deny-list of the one account that obviously is not. A deny-list labelled
    the two PLUG legs `statutory` as well, which is the same unevidenced
    assertion one account over:

    * `charges_account` takes the within-tolerance negative-gap credit (a
      broker-export rounding artifact) and the within-tolerance positive
      residual (whatever no rate explained, which merely happened to be
      smaller than the display precision of units/NAV). Neither came from a
      rate, and both are bounded by the tolerance band, so they get
      `rounding` -- "attributable to the precision the export was printed
      at, not to any charge we can name".
    * `charge_unexplained_account` takes the OVER-tolerance remainder, which
      rounding cannot explain either; it keeps `unattributed`.

    The order matters: the two plug accounts are tested first, so a config
    that points `charges_account` or `charge_unexplained_account` at the
    stamp-duty or STT account degrades to the weaker claim rather than the
    stronger one. (`MergeBookingStore.save` rejects such a collision, but a
    config can reach here without passing through `save`.) An account that is
    none of the four gets `unattributed` for the same reason: never claim
    `statutory` by default.
    """
    if account == cfg.charge_unexplained_account:
        return {"charge-basis": "unattributed"}
    if account == cfg.charges_account:
        return {"charge-basis": "rounding"}
    if account in (cfg.charge_stamp_duty_account, cfg.charge_stt_account):
        return {"charge-basis": "statutory"}
    return {"charge-basis": "unattributed"}


# Exact AMFI NAV per (scheme code, trade date), e.g. ("140347", 2025-10-06).
# Built by the caller (ImportService) from the NAV store and passed in, so the
# builders below stay pure.
ExactNavs = Mapping[tuple[str, date], Decimal]

# Transaction metadata recording the export's own Amount/Units when the
# booked cash/units differ from them. They are the row's dedup identity; see
# ImportService._existing_keys.
EXPORT_AMOUNT_META = "export-amount"
EXPORT_UNITS_META = "export-units"


@dataclass(frozen=True)
class _PurchaseBooking:
    """Everything an MF PURCHASE entry books, decided once.

    `legs`/`surface` are the charge legs and the unexplained flag; `flag` is
    the exact-NAV review flag (None when the exact NAV was used or never
    consulted). `snapped` says whether this is the Groww snap path, which
    records the export Amount/Units as metadata.
    """

    cash: Decimal
    units: Decimal
    unit_cost: Decimal
    legs: dict[str, Decimal]
    surface: bool
    flag: str | None
    snapped: bool


def _purchase_booking(
    candidate: CandidateTxn,
    cfg: MergeBookingConfig,
    snap_cash: bool,
    navs: ExactNavs | None = None,
) -> _PurchaseBooking:
    """How an MF PURCHASE is booked. The single authority for the builders
    below AND `unexplained_charge_of`, so the posted legs, the review flag and
    the exact-NAV flag all come from one decision.

    * `snap_cash=False` (the default at every builder; the ledger-merge path
      and the MF migration script): the export cash, units and NAV verbatim,
      charges from `compute_charges`. Exactly the pre-snap behaviour.
    * `snap_cash=True`, `navs` None (no NAV source consulted): cash snapped by
      `charges.snap_purchase_cash`, units/NAV from the export.
    * `snap_cash=True` with `navs`: when the exact NAV for (scheme, date) is
      present and `charges.exact_nav_purchase` accepts it, units and
      cost come from the exact NAV. Otherwise the snapped booking above,
      with `flag` saying why -- never a silent fallback.
    """
    from backend.app.service.ingest.pipeline.charges import (
        EXACT_NAV_UNAVAILABLE,
        compute_charges,
        exact_nav_purchase,
        snap_purchase_cash,
    )

    units, unit_cost = candidate.units, candidate.unit_cost
    assert units is not None and unit_cost is not None
    flag: str | None = None
    if snap_cash and navs is not None:
        assert candidate.commodity is not None
        nav_exact = navs.get((candidate.commodity.removeprefix("MF"), candidate.date))
        if nav_exact is None:
            flag = EXACT_NAV_UNAVAILABLE
        else:
            exact, flag = exact_nav_purchase(candidate.amount, units, nav_exact, cfg)
            if exact is not None:
                return _PurchaseBooking(
                    cash=exact.cash,
                    units=exact.units,
                    unit_cost=exact.nav,
                    legs=exact.legs,
                    surface=False,
                    flag=None,
                    snapped=True,
                )
    trade_value = units * unit_cost
    cash = (
        snap_purchase_cash(candidate.amount, trade_value, cfg)
        if snap_cash
        else candidate.amount
    )
    result = compute_charges(
        "buy",
        trade_value=trade_value,
        cash=cash,
        cost=trade_value,
        cfg=cfg,
        units=units,
        nav=unit_cost,
    )
    return _PurchaseBooking(
        cash=cash,
        units=units,
        unit_cost=unit_cost,
        legs=result.legs,
        surface=result.surface,
        flag=flag,
        snapped=snap_cash,
    )


def _export_meta(
    meta: dict, candidate: CandidateTxn, booking: _PurchaseBooking
) -> None:
    """Record the export's Amount/Units on a snap-path purchase, so a re-import
    of the same export still finds it after the booked cash/units moved."""
    if booking.snapped:
        meta[EXPORT_AMOUNT_META] = candidate.amount
        assert candidate.units is not None
        meta[EXPORT_UNITS_META] = candidate.units


def _commodity_entry(
    candidate: CandidateTxn,
    cfg: MergeBookingConfig | None = None,
    *,
    snap_cash: bool = False,
    navs: ExactNavs | None = None,
) -> data.Transaction:
    """Build a commodity-lot transaction for a Groww order.

    PURCHASE  -> units in at cost ({nav INR}); INR cash out of the clearing acct.
    REDEEM    -> units out at held lot cost (empty CostSpec -> FIFO booking),
                 INR cash in, realized P&L plugged to CapitalGains.
    DIVIDEND  -> INR cash in, Dividend income out; no units.

    `snap_cash`/`navs` apply to PURCHASE only; see `_purchase_booking`.
    """
    _cfg = cfg or MergeBookingConfig()

    meta = data.new_metadata("<ingest>", 0)
    meta["txn-id"] = candidate.id
    symbol = candidate.commodity
    assert symbol is not None  # caller guarantees this branch only for commodities

    postings: list[data.Posting] = []
    if candidate.units is not None:
        holding = _holding_account(symbol)
        units = candidate.units
        unit_cost = candidate.unit_cost
        assert unit_cost is not None  # PURCHASE/REDEEM rows always carry the NAV
        if candidate.direction == Direction.DEBIT:  # PURCHASE: units in at cost
            booking = _purchase_booking(candidate, _cfg, snap_cash, navs)
            _export_meta(meta, candidate, booking)
            cost = Cost(booking.unit_cost, OPERATING_CURRENCY, candidate.date, None)
            postings.append(
                data.Posting(
                    holding,
                    Amount(booking.units, symbol),
                    cost,
                    None,
                    None,
                    None,
                )
            )
            postings.append(
                data.Posting(
                    platform_cash_account(GROWW),
                    Amount(-booking.cash, OPERATING_CURRENCY),
                    None,
                    None,
                    None,
                    None,
                )
            )
            if booking.surface:
                # Flag, do not refuse. The remainder is in booking.legs (bound
                # for charge_unexplained_account), so the entry still
                # balances; refusing the commit here is the gate P5 removed.
                candidate.unexplained_charge = True
            for account, amount in booking.legs.items():
                charge_meta: dict = _charge_meta(account, _cfg)
                postings.append(
                    data.Posting(
                        account,
                        Amount(amount, OPERATING_CURRENCY),
                        None,
                        None,
                        None,
                        charge_meta,
                    )
                )
        else:  # REDEEM: units out at held cost (FIFO), cash in, P&L plug
            empty_cost = CostSpec(None, None, OPERATING_CURRENCY, None, None, False)
            sale_price = Amount(unit_cost, OPERATING_CURRENCY)
            postings.append(
                data.Posting(
                    holding,
                    Amount(-units, symbol),
                    empty_cost,
                    sale_price,
                    None,
                    None,
                )
            )
            postings.append(
                data.Posting(
                    platform_cash_account(GROWW),
                    Amount(candidate.amount, OPERATING_CURRENCY),
                    None,
                    None,
                    None,
                    None,
                )
            )
            postings.append(data.Posting(CAPITAL_GAINS, None, None, None, None, None))
            # STT on sell (only if rate > 0, matching merge path behaviour)
            if Decimal(_cfg.stt_rate_sell) > 0:
                from backend.app.service.ingest.pipeline.charges import (
                    compute_charges as _compute_charges,
                )

                trade_value = units * unit_cost
                result = _compute_charges(
                    "sell",
                    trade_value=trade_value,
                    cash=candidate.amount,
                    cost=trade_value,
                    cfg=_cfg,
                    units=units,
                    nav=unit_cost,
                )
                if result.surface:
                    # Flag, do not refuse -- see the PURCHASE branch above.
                    candidate.unexplained_charge = True
                for account, amount in result.legs.items():
                    charge_meta_sell: dict = _charge_meta(account, _cfg)
                    postings.append(
                        data.Posting(
                            account,
                            Amount(amount, OPERATING_CURRENCY),
                            None,
                            None,
                            None,
                            charge_meta_sell,
                        )
                    )
    else:  # DIVIDEND: cash in, dividend income out, no units
        postings.append(
            data.Posting(
                platform_cash_account(GROWW),
                Amount(candidate.amount, OPERATING_CURRENCY),
                None,
                None,
                None,
                None,
            )
        )
        postings.append(
            data.Posting(
                DIVIDEND_INCOME,
                Amount(-candidate.amount, OPERATING_CURRENCY),
                None,
                None,
                None,
                None,
            )
        )

    return data.Transaction(
        meta,
        candidate.date,
        "*",
        None,
        candidate.raw_narration,
        data.EMPTY_SET,
        data.EMPTY_SET,
        postings,
    )


def _subsystem_entry(
    candidate: CandidateTxn,
    cfg: MergeBookingConfig | None = None,
    lots: list[tuple[Decimal, Decimal, date]] | None = None,
    *,
    snap_cash: bool = False,
    navs: ExactNavs | None = None,
) -> data.Transaction:
    """PURCHASE/REDEEM lot posting, written to investments.beancount.

    Identical posting shape to `_commodity_entry`'s PURCHASE/REDEEM branches,
    except the cash leg targets the subsystem-internal clearing account (the
    configured `platform_clearing_account(GROWW)`) instead of the real Groww
    wallet (the configured `platform_cash_account(GROWW)`) — the real cash leg
    lives in the paired `_vehicle_entry`, written to the main
    ledger. The CAPITAL_GAINS plug here is purely a computation aid so
    beancount's FIFO booking resolves the empty CostSpec; it is never read by
    any report (it stays local to investments.beancount).

    `lots`, when given (REDEEM only — computed by `commit_candidates` via
    `_consume_fifo`/the in-batch lot cache), replaces the single
    empty-`CostSpec` holding leg `_commodity_entry` builds with one exact-Cost
    posting per consumed lot, so investments.beancount is deterministic
    rather than delegating FIFO resolution to beancount's own booking at load
    time. Left `None` (the default), the old empty-CostSpec placeholder shape
    is preserved unchanged — used only by direct unit tests of this
    function, never by the real commit path."""
    entry = _commodity_entry(candidate, cfg, snap_cash=snap_cash, navs=navs)
    wallet = platform_cash_account(GROWW)
    clearing = platform_clearing_account(GROWW)
    symbol = candidate.commodity
    holding = _holding_account(symbol) if symbol is not None else None
    postings: list[data.Posting] = []
    for p in entry.postings:
        if p.account == wallet:
            postings.append(p._replace(account=clearing))
        elif (
            lots is not None
            and holding is not None
            and p.account == holding
            and isinstance(p.cost, CostSpec)
        ):
            assert symbol is not None
            for lot_units, lot_cost, lot_date in lots:
                postings.append(
                    data.Posting(
                        holding,
                        Amount(-lot_units, symbol),
                        Cost(lot_cost, OPERATING_CURRENCY, lot_date, None),
                        None,
                        None,
                        None,
                    )
                )
        else:
            postings.append(p)
    return entry._replace(postings=postings)


def _add_months(d: date, months: int) -> date:
    """Calendar-anniversary date arithmetic: `d` plus `months` months, landing
    on the same day-of-month in the target month/year. Clamps to the target
    month's last day when the source day doesn't exist there (e.g. Jan 31 + 1
    month -> Feb 28/29; Feb 29 (leap) + 12 months -> Feb 28 of the following,
    non-leap, year)."""
    total_month_index = d.month - 1 + months
    year = d.year + total_month_index // 12
    month = total_month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _classify_lot(redemption_date: date, lot_date: date, category: str | None) -> str:
    """Bucket a single consumed lot's gain by scheme category + holding period.

    Equity: statutory 12-month LTCG/STCG split (EQUITY_LTCG_MONTHS), decided
    by comparing `redemption_date` to the exact calendar anniversary of
    `lot_date` (not a floor of whole-month counts, which under-classifies as
    STCG any redemption that falls between the anniversary day and the start
    of the next month, e.g. lot 2026-03-18 redeemed 2027-03-19 — one day past
    the 12-month mark — must be LTCG). Redemption exactly on the anniversary
    day has not yet "exceeded" 12 months, so it stays STCG.

    Debt: always Income:CapitalGains:Debt, no holding-period split -- since the
    Finance Act 2023, debt mutual funds get no LTCG indexation or holding-period
    benefit at all; gains are always taxed at slab rate regardless of holding
    period (matches backend/app/service/tax/report.py's `_UNCOVERED_KINDS`: surfaced in the
    tax workspace, never slab-estimated there).

    Anything else ("other", i.e. AMFI's section banner says neither "Equity
    Scheme" nor "Debt Scheme"; or the scheme could not be resolved): NOT debt.
    Arbitrage, gold, hybrid and equity-savings funds and index-tracking ELSS
    land here, and their holding period and rate differ from both of the other
    cases, so booking them as Debt would assert a treatment we do not know.
    They go to Income:CapitalGains:Unclassified, which the tax workspace lists
    as uncovered (see `_UNCOVERED_KINDS`) -- reported, never estimated."""
    if category == "equity":
        anniversary = _add_months(lot_date, EQUITY_LTCG_MONTHS)
        return CG_LTCG if redemption_date > anniversary else CG_STCG
    if category == "debt":
        return CG_DEBT
    return CG_UNCLASSIFIED


def _bucket_gains(
    candidate: CandidateTxn,
    symbol: str,
    lots: list[tuple[Decimal, Decimal, date]],
    scheme_master: SchemeMaster | None,
) -> dict[str, Decimal]:
    """Realized gain per Income:CapitalGains:* bucket for a REDEEM, allocating
    the real net cash proceeds (`candidate.amount`) proportionally by units
    across the consumed lots so `sum(bucket gains) == candidate.amount -
    total_lot_cost` exactly (no independent residual to reconcile). To avoid
    Decimal rounding drift across lots, every lot but the last gets a
    proportionally-quantized share of the proceeds; the last lot absorbs
    whatever remains so the total always reconciles to the paisa."""
    scheme = (
        scheme_master.resolve_by_code(symbol.removeprefix("MF"))
        if scheme_master is not None
        else None
    )
    # Two different conditions, one booking. A scheme AMFI does not banner as
    # equity or debt carries category "other"; a scheme that is not in the
    # master (or no master supplied) has no category at all. Both are
    # undecidable, so both go to Income:CapitalGains:Unclassified -- never to
    # Debt, which means "AMFI says debt fund". Neither raises: the import is not
    # refused, because resolving the scheme cannot supply a category.
    category = scheme.category if scheme is not None else None

    total_units = sum((lot_units for lot_units, _, _ in lots), Decimal(0))
    gains: dict[str, Decimal] = {}
    allocated = Decimal(0)
    last = len(lots) - 1
    for i, (lot_units, lot_cost, lot_date) in enumerate(lots):
        if i < last:
            proceeds = (lot_units / total_units * candidate.amount).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            allocated += proceeds
        else:
            proceeds = candidate.amount - allocated
        lot_gain = proceeds - lot_units * lot_cost
        bucket = _classify_lot(candidate.date, lot_date, category)
        gains[bucket] = gains.get(bucket, Decimal(0)) + lot_gain
    return gains


def _vehicle_entry(
    candidate: CandidateTxn,
    scheme_master: SchemeMaster | None = None,
    lots: list[tuple[Decimal, Decimal, date]] | None = None,
    extra_postings: list[data.Posting] | None = None,
    cfg: MergeBookingConfig | None = None,
    *,
    snap_cash: bool = False,
    navs: ExactNavs | None = None,
) -> data.Transaction:
    """PURCHASE/REDEEM vehicle-level INR posting, written to main.beancount.

    PURCHASE: books the coarse Assets:Investment:MutualFund vehicle account at
    the SAME cost basis (units * unit_cost, i.e. trade value) that
    `_subsystem_entry`/`_commodity_entry` books onto the per-scheme holding
    lot in investments.beancount -- NOT `candidate.amount` (the real cash
    debited from the wallet), which can differ from trade value by any
    statutory purchase charge (e.g. stamp duty). Using `candidate.amount`
    here was a real bug (found via the Unit 6 historical-migration real-data
    run): it silently desynced the coarse
    main.beancount total from investments.beancount's granular total by the
    aggregate purchase-charge amount whenever `stamp_duty_rate` is nonzero
    (the statutory default), on EVERY purchase, live imports included -- not
    just this one-time migration. The real wallet leg (the Groww wallet) still
    carries the full `candidate.amount` (real cash out is real cash out);
    the gap between that and the trade-value vehicle posting is recomputed
    via the identical `compute_charges` call `_commodity_entry` already made
    for the SAME candidate (same `cfg`), so it resolves to the exact same
    itemized charge legs/account names -- INCLUDING the
    charge_unexplained_account leg when the gap exceeds tolerance, so the two
    journals stay in step on the surfacing path too and each one balances.
    (It also sets `unexplained_charge` on the candidate, idempotently; so
    does `_commodity_entry`, and they agree because they are given the same
    `units`/`unit_cost`/`cfg`.) This
    is symmetric with the REDEEM branch below, which has always correctly
    used the lot cost basis (`total_lot_cost`), never `candidate.amount`.

    REDEEM: cost-basis removed from the vehicle account at lot cost, cash
    into the real wallet, PLUS one posting per non-zero realized-gain bucket
    into Income:CapitalGains:{LTCG,STCG,Debt,Unclassified} (see `_bucket_gains`). `lots`
    (the FIFO-consumed lots for this redemption, from `commit_candidates`)
    is required for REDEEM candidates.

    `extra_postings`, when given, is appended verbatim after the computed
    cash/gain legs -- used by `ledger_merge_service.convert_batch_split` to
    carry through arbitrary user-authored non-MF legs (elided or explicit)
    from a ledger-cli transaction that this function itself has no way to
    know about. Default `None` preserves every existing call site
    (`entry_writer.py`/`commit_candidates`) unchanged.

    `cfg`, when given, is the SAME `MergeBookingConfig` passed to the paired
    `_subsystem_entry`/`_commodity_entry` call for this candidate -- callers
    that pass a non-default `cfg` there (currently only
    `ledger_merge_service.convert_batch_split`) must pass the identical `cfg`
    here too, or the PURCHASE charge computation below could disagree with
    investments.beancount's already-booked charge legs. Default `None`
    (-> `MergeBookingConfig()`) matches every call site that never overrides
    `cfg` on the paired call either.

    `snap_cash`/`navs` must likewise match the paired `_subsystem_entry`
    call; see `_purchase_booking`. They apply to PURCHASE only."""
    meta = data.new_metadata("<ingest>", 0)
    meta["txn-id"] = candidate.id
    if candidate.direction == Direction.DEBIT:  # PURCHASE
        assert candidate.units is not None and candidate.unit_cost is not None
        _cfg = cfg or MergeBookingConfig()
        booking = _purchase_booking(candidate, _cfg, snap_cash, navs)
        _export_meta(meta, candidate, booking)
        postings = [
            data.Posting(
                INVESTMENT_MUTUAL_FUND,
                Amount(booking.units * booking.unit_cost, OPERATING_CURRENCY),
                None,
                None,
                None,
                None,
            ),
            data.Posting(
                platform_cash_account(GROWW),
                Amount(-booking.cash, OPERATING_CURRENCY),
                None,
                None,
                None,
                None,
            ),
        ]
        if booking.surface:
            # Flag, do not refuse. `_purchase_booking` is the same decision
            # `_commodity_entry` made for the SAME candidate.
            candidate.unexplained_charge = True
        for account, amount in booking.legs.items():
            postings.append(
                data.Posting(
                    account,
                    Amount(amount, OPERATING_CURRENCY),
                    None,
                    None,
                    None,
                    _charge_meta(account, _cfg),
                )
            )
    else:  # REDEEM
        assert lots is not None  # commit_candidates always supplies consumed lots
        symbol = candidate.commodity
        assert symbol is not None
        total_lot_cost = sum(
            (lot_units * lot_cost for lot_units, lot_cost, _ in lots), Decimal(0)
        )
        postings = [
            data.Posting(
                platform_cash_account(GROWW),
                Amount(candidate.amount, OPERATING_CURRENCY),
                None,
                None,
                None,
                None,
            ),
            data.Posting(
                INVESTMENT_MUTUAL_FUND,
                Amount(-total_lot_cost, OPERATING_CURRENCY),
                None,
                None,
                None,
                None,
            ),
        ]
        for account, gain in _bucket_gains(
            candidate, symbol, lots, scheme_master
        ).items():
            if gain != 0:
                # Income postings are credits (negative) by beancount
                # convention -- matches backend/app/service/tax/report.py's
                # classify_items, which reads `-p.amount` as the positive
                # gain magnitude for any Income:CapitalGains:* posting.
                postings.append(
                    data.Posting(
                        account,
                        Amount(-gain, OPERATING_CURRENCY),
                        None,
                        None,
                        None,
                        None,
                    )
                )
    if extra_postings:
        postings = postings + extra_postings
    return data.Transaction(
        meta,
        candidate.date,
        "*",
        None,
        candidate.raw_narration,
        data.EMPTY_SET,
        data.EMPTY_SET,
        postings,
    )


def _stock_lot_price(
    amount: Decimal, units: Decimal, unit_cost: Decimal | None
) -> Decimal:
    """Per-unit INR price for a stock order, chosen so `units * price`
    reproduces the order's `amount` (Groww's "Value").

    The parser quantizes `unit_cost = Value / Quantity` to 4dp, which is exact
    for the tick-sized prices most orders carry -- and is what the
    hand-entered lots already in the journal look like, so it is preferred.
    But for a large partially-filled order the 4dp value does NOT reproduce
    Value: the fixture's 1767-unit / 39871.33 order drifts by ~2.5 paisa,
    which is both above `STOCK_RECONCILE_TOLERANCE` and above beancount's own
    0.005 INR balance tolerance -- i.e. booking the 4dp price would raise a
    bogus review flag AND fail to balance, on legitimate data. In that case
    fall back to a 10dp quotient, whose residual (<= units * 5e-11) is
    sub-paisa for any realistic order size.

    Money stays Decimal throughout; no float ever touches a price."""
    if unit_cost is not None and units * unit_cost == amount:
        return unit_cost
    return (amount / units).quantize(_LOT_PRICE_EXACT_QUANT, rounding=ROUND_HALF_UP)


def _stock_entry(
    candidate: CandidateTxn,
    lots: list[tuple[Decimal, Decimal, date]] | None = None,
) -> data.Transaction:
    """A Groww stock order as a single main-ledger transaction.

    Unlike the MF path there is no `_subsystem_entry`/`_vehicle_entry` split:
    that duality exists only because MF keeps its per-scheme lots in a second
    file (investments.beancount). Stocks keep their lots in the main journal,
    so a stock lot written to investments.beancount would be counted twice by
    report_routes' merge of the two ledgers.

    BUY  -> units in at cost, cash out of the Groww wallet.
    SELL -> cash into the wallet, one posting per FIFO-consumed lot at its
            exact held cost (annotated `@ sale price`), and the realized P&L
            to Income:Investment:CapitalGains.

    Capital-gains sign, matching the entries already in the journal: the
    posting is `total_lot_cost - proceeds`, so a LOSS is POSITIVE and a GAIN
    is NEGATIVE. This is also exactly the amount that balances the entry --
    the holding legs weigh `-total_lot_cost` and the cash leg `+proceeds` --
    so the plug is computed here rather than left to beancount to interpolate,
    which keeps the number visible in the journal and assertable in tests."""
    symbol = candidate.commodity
    assert symbol is not None  # caller guarantees a stock commodity row
    assert candidate.units is not None
    assert candidate.unit_cost is not None

    meta = data.new_metadata("<ingest>", 0)
    meta["txn-id"] = candidate.id
    if candidate.order_id:
        # The broker-side identity of the order, and the dedup key for stock
        # re-imports.
        meta["order-id"] = candidate.order_id

    holding = _holding_account(symbol, Vehicle.STOCK)
    price = _stock_lot_price(candidate.amount, candidate.units, candidate.unit_cost)
    postings: list[data.Posting] = []

    if candidate.direction == Direction.DEBIT:  # BUY
        postings.append(
            data.Posting(
                holding,
                Amount(candidate.units, symbol),
                Cost(price, OPERATING_CURRENCY, candidate.date, None),
                None,
                None,
                None,
            )
        )
        postings.append(
            data.Posting(
                platform_cash_account(GROWW),
                Amount(-candidate.amount, OPERATING_CURRENCY),
                None,
                None,
                None,
                None,
            )
        )
    else:  # SELL
        assert lots is not None  # commit_candidates always supplies consumed lots
        postings.append(
            data.Posting(
                platform_cash_account(GROWW),
                Amount(candidate.amount, OPERATING_CURRENCY),
                None,
                None,
                None,
                None,
            )
        )
        total_lot_cost = Decimal(0)
        sale_price = Amount(price, OPERATING_CURRENCY)
        for lot_units, lot_cost, lot_date in lots:
            postings.append(
                data.Posting(
                    holding,
                    Amount(-lot_units, symbol),
                    Cost(lot_cost, OPERATING_CURRENCY, lot_date, None),
                    sale_price,
                    None,
                    None,
                )
            )
            total_lot_cost += lot_units * lot_cost
        realized = total_lot_cost - candidate.amount
        # Print money at the paisa grain the rest of the journal uses, but
        # ONLY when that is lossless -- rounding here would shift the entry's
        # balance, and a stock sale must never be made to balance by fudging
        # the realized gain.
        rounded = realized.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if rounded == realized:
            realized = rounded
        if realized != 0:
            postings.append(
                data.Posting(
                    CAPITAL_GAINS,
                    Amount(realized, OPERATING_CURRENCY),
                    None,
                    None,
                    None,
                    None,
                )
            )

    return data.Transaction(
        meta,
        candidate.date,
        "*",
        None,
        candidate.raw_narration,
        data.EMPTY_SET,
        data.EMPTY_SET,
        postings,
    )


def _stock_lot_gap(candidate: CandidateTxn, entry: data.Transaction) -> Decimal:
    """INR by which the lot postings in `entry` fail to account for the
    order's Value (`candidate.amount`). Zero means they reconcile exactly.

    Each commodity leg is valued at the INR the ORDER attributes to it: the
    sale price for a sell (the lot's own historical cost is the basis, not the
    proceeds) and the lot cost for a buy.

    BE CLEAR ABOUT WHAT THIS DOES AND DOES NOT CATCH.

    On the current production path the gap is ZERO BY CONSTRUCTION, and this
    function cannot report otherwise. `_stock_entry` prices every leg of a
    sell with the same `_stock_lot_price(amount, units, ...)`, and
    `_consume_from_lots` consumes exactly `candidate.units` (it asserts as
    much), so the sum is `price * units == amount` however the lots were
    split. `_stock_lot_price` is in turn chosen so that identity holds
    exactly rather than to 4dp. A buy is the one-lot case of the same thing.

    In particular this does NOT catch the 2026-09-07 defect, and an earlier
    version of this docstring wrongly claimed it did. That defect was
    HAND-ENTRY: proceeds for a 332-share sale spread across postings drawn
    from 350 units. This code path structurally cannot produce it, because
    the lot walk -- not the postings -- fixes the unit count. What ends that
    defect is the import path existing at all, not this check.

    So this is defence-in-depth, kept deliberately: if a future change to lot
    construction (per-lot prices, partial fills priced separately, a
    hand-supplied lot list) ever starts attributing a different amount of
    value than the order carries, the batch is refused instead of silently
    misbooking. Note that both tests reaching a non-zero gap do so by
    bypassing `commit_candidates` (calling `_stock_entry` with a
    deliberately wrong lot list) or by monkeypatching `_stock_lot_price` --
    the production path is not, and cannot currently be, exercised."""
    symbol = candidate.commodity
    total = Decimal(0)
    for posting in entry.postings:
        units = posting.units
        if units is None or units.currency != symbol or units.number is None:
            continue
        per_unit: Decimal | None = None
        if posting.price is not None:
            per_unit = posting.price.number
        elif isinstance(posting.cost, Cost):
            # A booked lot. An un-booked CostSpec carries no single per-unit
            # number to value the leg with -- `_stock_entry` never emits one
            # (unlike the MF path, which delegates FIFO to beancount), so
            # reaching this is a caller error, and skipping the leg makes the
            # gap large rather than silently zero.
            per_unit = posting.cost.number
        if per_unit is None:
            continue
        total += abs(units.number) * per_unit
    return candidate.amount - total


def candidate_to_entry(candidate: CandidateTxn) -> data.Transaction:
    """Balanced transaction. Commodity candidates take the lot path; everything
    else uses the legacy split / suggested-leg INR path unchanged."""
    if candidate.commodity is not None:
        return _commodity_entry(candidate)

    amount = candidate.amount
    if candidate.splits:
        legs: list[tuple[str, Decimal, list[str]]] = [
            (s.account, s.amount, s.tags) for s in candidate.splits
        ]
    else:
        other = (
            candidate.suggested_other_leg
            or candidate.suggested_category
            or "Expenses:Uncategorized"
        )
        legs = [(other, amount, [])]

    # The source posting is `source_amount`, shared with dedup so a re-import
    # keys the row by exactly what is written here.
    source_amt = source_amount(candidate)
    leg_sign = 1 if candidate.direction == Direction.DEBIT else -1

    meta = data.new_metadata("<ingest>", 0)
    meta["txn-id"] = candidate.id
    if candidate.fx:
        meta["fx"] = candidate.fx

    postings = []
    for account, leg_amount, _ in legs:
        postings.append(
            data.Posting(
                account,
                Amount(leg_sign * leg_amount, OPERATING_CURRENCY),
                None,
                None,
                None,
                None,
            )
        )
    postings.append(
        data.Posting(
            candidate.source_account,
            Amount(source_amt, OPERATING_CURRENCY),
            None,
            None,
            None,
            None,
        )
    )
    # Transaction #tags are the only representation: split tags fold in.
    tags = frozenset(candidate.tags).union(*(s.tags for s in candidate.splits))
    return data.Transaction(
        meta,
        candidate.date,
        "*",
        None,
        candidate.raw_narration,
        tags,
        data.EMPTY_SET,
        postings,
    )


def _is_lot_row(c: CandidateTxn) -> bool:
    return c.commodity is not None and c.units is not None


# The only source accounts a lot-bearing candidate may carry, and the holding
# tree each one routes to. This is an allow-list on purpose: `source_account`
# is editable through the untyped review patch route
# (POST /api/review/{batch}/{candidate}, which the Statement Review screen
# already writes through), so an unrecognised value is a real input, not a
# theoretical one. Defaulting it would misfile an arbitrary lot row as a Groww
# mutual fund -- silently, into the wrong journal. Built per call because the
# stock source is the configured Groww wallet.
def _lot_source_vehicle() -> dict[str, Vehicle]:
    return {
        platform_cash_account(GROWW): Vehicle.STOCK,
        platform_clearing_account(GROWW): Vehicle.MUTUAL_FUND,
    }


def _vehicle_of(c: CandidateTxn) -> Vehicle | None:
    """Which holding tree a commodity candidate belongs to, or None when its
    `source_account` is not one this module knows how to route.

    Groww's two exports are unambiguous: the MF parser books against the
    configured subsystem clearing account and
    the stock parser against the real, configured Groww wallet.
    Anything else is refused by `commit_candidates` rather than guessed at --
    see `_unbookable_lot_reason`."""
    return _lot_source_vehicle().get(c.source_account)


# beancount's OWN currency grammar, not a hand-copied approximation of it --
# reusing it means this cannot drift out of sync with the parser that will
# actually have to read what we write.
_VALID_COMMODITY = re.compile(CURRENCY_RE)


def _unbookable_lot_reason(c: CandidateTxn) -> str | None:
    """Why this lot-bearing candidate cannot safely be written to a journal,
    or None if it can. Checked for the WHOLE batch before any journal write.

    Four ways a lot row is unbookable:

    0. It is a STOCK order with no broker `order-id`. Stock dedup keys
       exclusively on that id (content-based dedup is bypassed for stocks --
       two distinct same-day/same-value orders share a content key), so a row
       without one has NO dedup key at all: it would be written with no
       `order-id` metadata, be invisible to `existing_order_ids`, and be
       booked a second time by the next import of the same file. The one-time
       backfill already refuses this exact case ("the matching export row
       carries no Exchange Order Id"); the importer must give the same answer
       rather than importing something it can never recognise again. Applies
       to stocks only -- mutual-fund rows dedup on content and never carry an
       order-id.
    1. Its `source_account` isn't one we route (see `_lot_source_vehicle`).
    2. Its symbol isn't a legal beancount COMMODITY name. Real NSE tickers
       contain characters beancount's lexer rejects -- `M&MFIN` is the
       obvious one.
    3. Its symbol is a legal commodity but not a legal ACCOUNT component, so
       the holding account we would open is unparseable. These are different
       grammars and the account one is stricter: `NT.TO` is a perfectly valid
       beancount commodity, but `Assets:Investment:Stock:NT.TO` is not a valid
       account name (dots are allowed in currencies, not in account
       components).

    Left unchecked, any of 2/3 would have `ensure_commodity_accounts` emit a
    directive `Ledger.append` then fails to parse, rolling the import back
    with an opaque lexer error like "Invalid token: '.TO'".

    Deliberately NOT slugged or rewritten into something legal: a holding
    filed under a mangled ticker is worse than a refused import, because it
    looks correct."""
    vehicle = _vehicle_of(c)
    if vehicle is None:
        return (
            f"source account {c.source_account!r} is not a recognised investment "
            "source, so there is no way to tell which holding tree this lot "
            "belongs to"
        )
    symbol = c.commodity
    assert symbol is not None  # only ever called for lot rows
    if not _VALID_COMMODITY.fullmatch(symbol):
        return (
            f"symbol {symbol!r} is not a legal beancount commodity name "
            "(letters, digits and ' . _ - only, starting with a letter and "
            "ending with a letter or digit)"
        )
    if vehicle == Vehicle.STOCK and not c.order_id:
        return (
            "this stock order carries no broker Exchange Order Id, which is "
            "the only key stock imports dedup on -- importing it would leave "
            "a row that a re-import of the same file books a second time"
        )
    holding = holding_account(symbol, vehicle)
    if not bc_account.is_valid(holding):
        return (
            f"symbol {symbol!r} cannot be used as a holding account name "
            f"({holding!r} is not a legal beancount account -- account "
            "components allow only letters, digits and '-')"
        )
    return None


def _is_stock(c: CandidateTxn) -> bool:
    return _is_lot_row(c) and _vehicle_of(c) == Vehicle.STOCK


def _is_stock_purchase(c: CandidateTxn) -> bool:
    return _is_stock(c) and c.direction == Direction.DEBIT


def _is_stock_sell(c: CandidateTxn) -> bool:
    return _is_stock(c) and c.direction == Direction.CREDIT


def _is_redeem(c: CandidateTxn) -> bool:
    # `not _is_stock` matters: without it a stock SELL would fall into the MF
    # dual-write branch and land a stock lot in investments.beancount.
    return _is_lot_row(c) and not _is_stock(c) and c.direction == Direction.CREDIT


def _is_purchase(c: CandidateTxn) -> bool:
    return _is_lot_row(c) and not _is_stock(c) and c.direction == Direction.DEBIT


def _ledger_unit_holdings(ledger: Ledger, vehicle: str) -> dict[str, Decimal]:
    """Net units held per commodity symbol already in `ledger` for `vehicle`
    (purchases minus redemptions). The holding account posts the raw signed
    unit count, so summing posting amounts over the holding accounts gives the
    available lots.

    `vehicle` is required rather than defaulted: this used to hardcode the MF
    prefix, which made already-committed stock lots invisible to
    `uncovered_redemptions` -- a later import's sell would be flagged
    uncovered even though its covering buys were committed by an earlier one.
    A review queue that cries wolf is how real warnings get clicked through."""
    # Imported lazily to avoid a module-level reports<->ingest import cycle.
    from backend.app.service.reports.health.ledger_view import all_postings

    held: dict[str, Decimal] = {}
    prefix = HOLDING_PREFIX[vehicle]
    clearing = platform_clearing_account(GROWW)
    for row in all_postings(ledger):
        if not row.account.startswith(prefix):
            continue
        if row.account == clearing:  # the clearing account, not a holding
            continue
        symbol = row.account[len(prefix) :]
        held[symbol] = held.get(symbol, Decimal(0)) + row.amount
    return held


def _current_lots(
    investment_ledger: Ledger, symbol: str, vehicle: str = Vehicle.MUTUAL_FUND
) -> list[list]:
    """Mutable `[units, cost_per_unit, date]` rows, oldest-first, for the lots
    of `symbol` currently committed to `investment_ledger`. Read-only
    snapshot at call time -- see `_consume_fifo`'s docstring for the in-batch
    caveat.

    `vehicle` defaults to mutual_fund so every existing call site is
    unchanged; pass "stock" to list stock lots instead."""
    holding = _holding_account(symbol, vehicle)
    inv = Inventory()
    for entry in investment_ledger.load().entries:
        if not isinstance(entry, data.Transaction):
            continue
        for p in entry.postings:
            if p.account == holding and p.units is not None:
                inv.add_amount(p.units, p.cost)
    positions = [pos for pos in inv.get_positions() if pos.units.number > 0]
    positions.sort(key=lambda pos: pos.cost.date if pos.cost is not None else date.min)
    return [[pos.units.number, pos.cost.number, pos.cost.date] for pos in positions]


def _consume_from_lots(
    lots: list[list], units: Decimal
) -> list[tuple[Decimal, Decimal, date]]:
    """FIFO-consume `units` from `lots` (mutated in place), oldest-first.
    Returns the consumed `(lot_units, lot_cost, lot_date)` tuples."""
    consumed: list[tuple[Decimal, Decimal, date]] = []
    remaining = units
    for lot in lots:
        if remaining <= 0:
            break
        available = lot[0]
        if available <= 0:
            continue
        take = min(available, remaining)
        consumed.append((take, lot[1], lot[2]))
        lot[0] = available - take
        remaining -= take
    # uncovered_redemptions already blocks insufficient coverage upstream, so
    # this should be unreachable in practice -- assert defensively rather
    # than silently under-consuming and mis-booking an exact Cost.
    assert remaining == 0, (
        f"FIFO consumption short by {remaining} units -- "
        "uncovered_redemptions should have blocked this redemption"
    )
    return consumed


def _consume_fifo(
    investment_ledger: Ledger, symbol: str, units: Decimal
) -> list[tuple[Decimal, Decimal, date]]:
    """FIFO-consume `units` of `symbol`, oldest-first, against the lots
    already committed to `investment_ledger`. Returns `[(lot_units_consumed,
    lot_cost_per_unit, lot_date), ...]`.

    Known limitation: this is read-only against investment_ledger's
    currently-committed state. It does NOT also see an earlier PURCHASE of
    the same symbol in the same not-yet-appended commit batch --
    `uncovered_redemptions` already credits such an in-batch purchase for its
    narrower unit-count-only coverage check, but this function alone cannot
    resolve per-lot cost/date for it. `commit_candidates` works around this
    by maintaining its own in-memory lot cache (seeded via `_current_lots`,
    the same lot-listing logic this function uses) that is updated as each
    PURCHASE candidate in the batch is processed -- so the in-batch case is
    handled at the orchestration layer, not silently pretended away here."""
    lots = _current_lots(investment_ledger, symbol)
    return _consume_from_lots(lots, units)


# An MF redemption may exceed the units held by at most this much and still be
# a FULL redemption: the export prints units to 2 dp, so a holding of 1761.257
# is redeemed "1761.26". Anything over it is a real shortfall.
FULL_REDEMPTION_TOLERANCE = Decimal("0.005")


def _redeemable_units(requested: Decimal, held: Decimal) -> Decimal | None:
    """The units an MF redemption of `requested` actually redeems against
    `held`, or None when it is uncovered.

    The export prints units to 2 dp, so it cannot express an exact holding.
    A redemption within `FULL_REDEMPTION_TOLERANCE` of `held` is therefore a
    FULL redemption and redeems exactly `held`, in both directions:

    * rounded UP: `requested` exceeds `held` by no more than the tolerance
      (1761.257 held, "1761.26" redeemed);
    * rounded DOWN: it would leave more than 0 and no more than the
      tolerance behind (1761.263 held, "1761.26" redeemed -- 0.003 of dust).

    Otherwise a covered redemption redeems `requested` exactly as before.

    The single rule both `uncovered_redemptions` (review and the commit gate)
    and `commit_candidates` (the booking) apply, so they cannot disagree."""
    if requested <= held:
        if 0 < held - requested <= FULL_REDEMPTION_TOLERANCE:
            return held
        return requested
    if held > 0 and requested - held <= FULL_REDEMPTION_TOLERANCE:
        return held
    return None


def uncovered_redemptions(
    investment_ledger: Ledger,
    candidates: list[CandidateTxn],
    ledger: Ledger | None = None,
    exact_navs: ExactNavs | None = None,
) -> list[str]:
    """IDs of the REDEEM/SELL candidates `redeemed_units_of` cannot cover."""
    redeemed = redeemed_units_of(investment_ledger, candidates, ledger, exact_navs)
    return [
        c.id
        for c, units in zip(candidates, redeemed)
        if _is_lot_row(c) and c.direction != Direction.DEBIT and units is None
    ]


def redeemed_units_of(
    investment_ledger: Ledger,
    candidates: list[CandidateTxn],
    ledger: Ledger | None = None,
    exact_navs: ExactNavs | None = None,
) -> list[Decimal | None]:
    """Per candidate (same positions), the units each REDEEM/SELL actually
    redeems -- None where it is uncovered, and None for every row that is
    not a redemption. Coverage counts both the already-committed holdings AND earlier
    purchases in THIS batch. Candidates are walked in order so an in-batch
    PURCHASE that precedes its REDEEM is credited first (matching commit
    order).

    `ledger` is the MAIN journal, where stock lots live (MF lots live in
    `investment_ledger`). It is optional only so a caller with no stock rows
    stays unchanged; pass it whenever stock candidates are possible, or a sell
    covered by a previously-committed stock lot is falsely flagged.

    An in-batch MF PURCHASE is credited with the units `commit_candidates`
    will BOOK for it (`_purchase_booking` with the same `exact_navs`), not the
    export's printed units, and an MF REDEMPTION is covered when
    `_redeemable_units` says so -- the same two decisions the commit makes.
    Stock sells keep the strict comparison."""
    available: dict[tuple[str, str], Decimal] = {
        (Vehicle.MUTUAL_FUND.value, symbol): units
        for symbol, units in _ledger_unit_holdings(
            investment_ledger, Vehicle.MUTUAL_FUND
        ).items()
    }
    if ledger is not None:
        for symbol, units in _ledger_unit_holdings(ledger, Vehicle.STOCK).items():
            available[(Vehicle.STOCK.value, symbol)] = units

    out: list[Decimal | None] = []
    for c in candidates:
        out.append(None)
        if not _is_lot_row(c):
            continue
        assert c.units is not None and c.commodity is not None
        vehicle = _vehicle_of(c)
        # An unroutable source gets its own availability bucket rather than
        # borrowing the mutual-fund one; `commit_candidates` refuses these
        # outright, and until then they must not appear to cover an MF
        # redemption.
        key = (vehicle.value if vehicle is not None else c.source_account, c.commodity)
        if c.direction == Direction.DEBIT:
            units = c.units
            if _is_purchase(c) and c.unit_cost is not None:
                units = _purchase_booking(
                    c, MergeBookingConfig(), True, exact_navs
                ).units
            available[key] = available.get(key, Decimal(0)) + units
        else:
            have = available.get(key, Decimal(0))
            redeemed = (
                _redeemable_units(c.units, have)
                if _is_redeem(c)
                else (c.units if c.units <= have else None)
            )
            if redeemed is not None:
                available[key] = have - redeemed
                out[-1] = redeemed
    return out


def unexplained_charge_of(
    candidate: CandidateTxn,
    cfg: MergeBookingConfig | None = None,
    *,
    snap_cash: bool = False,
    navs: ExactNavs | None = None,
    redeemed_units: Decimal | None = None,
) -> Decimal | None:
    """The amount this candidate's commit would book to
    `cfg.charge_unexplained_account`, or None if it would book none.

    THE one authority on the question "would this row be flagged?", shared by
    every caller that needs the answer without committing:

    * `backend/app/service/ingest/service.detect` -- sets
      `CandidateTxn.unexplained_charge` at PREVIEW time, before the batch is
      stored for review, so the user is warned BEFORE the write.
    * `backend/app/service/ingest/service.commit` -- RECOMPUTES the flag against
      the rows actually being committed, immediately before
      `commit_candidates`, exactly as it recomputes `flag_restated_orders`
      and `uncovered_redemptions` and for the same reason: the review patch
      route (`ReviewStore.update`) sets candidate attributes verbatim, so a
      client can patch `amount`/`units`/`unit_cost` after the preview said
      the row was clean -- or patch `unexplained_charge: false` outright --
      and the preview answer would be stale or suppressed at the write.
      Unlike the hard gates, this one does not refuse: it is counted and
      returned as `unexplained` in the commit response.

    The commit-time sites in `_commodity_entry`/`_vehicle_entry` set the same
    flag themselves while writing. That is an additional, later signal -- not
    a replacement for either of the two above -- and it IS read: `commit()`
    counts the flag after `commit_candidates` returns, so a row only the
    write path recognised still reaches the response.

    `backend/app/service/ingest/ledger_merge/service.convert_batch_split` deliberately
    does NOT pre-check: it books the remainder through the same entry
    builders and the preview response carries the resulting
    `charge_unexplained_account` posting. See the comment at that call site.

    It mirrors `_commodity_entry`'s `compute_charges` calls exactly and has no
    side effects. The mirroring is what makes preview and commit agree, and
    that agreement is asserted directly (see
    tests/service/ingest/test_service_groww_mf.py). `compute_charges` remains the
    single authority on the charge arithmetic itself -- this function only
    decides WHICH call `_commodity_entry` would make for this candidate:

    * DEBIT (PURCHASE) -> the "buy" call.
    * CREDIT (REDEEM)  -> the "sell" call, and only when
      `cfg.stt_rate_sell > 0`; below that `_commodity_entry` computes no
      charges at all, so neither does this.
    * no units/NAV (DIVIDEND) -> no charge computation on either path.

    `cfg` defaults to `MergeBookingConfig()` because `commit_candidates` takes
    no `cfg` and so commits the Groww import path on exactly those defaults.
    A caller that commits with a non-default `cfg` must pass the identical one
    here or the two answers may differ.

    `units`/`unit_cost` on the `ledger_merge_service` path come from a ledger
    posting rather than a broker export, so their precision reflects the
    journal, not the original export. That is the best information available
    there, and it is why `TOLERANCE_FLOOR` exists; it is deliberately NOT
    special-cased.

    One known over-warning, chosen deliberately: a lot row whose
    `source_account` is edited to a STOCK wallet after preview commits via
    `_stock_entry`, which computes no charges, so the preview flag stays set
    with nothing behind it. Warning about money that turned out to be fine is
    the safe direction; the reverse is not.

    `snap_cash` mirrors `commit_candidates`, which snaps the cash of exactly
    the rows it books through the MF purchase branch (`_is_purchase`): a
    caller answering for rows `commit_candidates` will write passes True,
    and the snap is then applied to those same rows and no others -- never a
    stock order, which `_stock_entry` books verbatim. `navs` is the same
    exact-NAV mapping `commit_candidates` is given. See `_purchase_booking`.

    `redeemed_units` is what a REDEEM actually redeems (`redeemed_units_of`):
    a full redemption the export rounded is booked on the units HELD, and its
    STT must be computed on those same units here.
    """
    if candidate.units is None or candidate.unit_cost is None:
        return None
    _cfg = cfg or MergeBookingConfig()
    from backend.app.service.ingest.pipeline.charges import (
        compute_charges as _compute_charges,
    )

    side: Literal["buy", "sell"]
    if candidate.direction == Direction.DEBIT:
        side = "buy"
    elif Decimal(_cfg.stt_rate_sell) > 0:
        side = "sell"
    else:
        return None
    if side == "buy":
        booking = _purchase_booking(
            candidate, _cfg, snap_cash and _is_purchase(candidate), navs
        )
        surface, legs = booking.surface, booking.legs
    else:
        units = redeemed_units if redeemed_units is not None else candidate.units
        trade_value = units * candidate.unit_cost
        result = _compute_charges(
            side,
            trade_value=trade_value,
            cash=candidate.amount,
            cost=trade_value,
            cfg=_cfg,
            units=units,
            nav=candidate.unit_cost,
        )
        surface, legs = result.surface, result.legs
    if not surface:
        return None
    # `surface` is True exactly when part of the gap was routed to the
    # unexplained account, so this leg is always present -- but read it from
    # `legs` rather than recomputing it, so the figure shown in review is the
    # same number the journal will carry.
    return legs.get(_cfg.charge_unexplained_account)


def exact_nav_flag_of(
    candidate: CandidateTxn,
    navs: ExactNavs,
    cfg: MergeBookingConfig | None = None,
) -> str | None:
    """The exact-NAV review flag `commit_candidates(..., exact_navs=navs)`
    would book this row under, or None (exact NAV used, or not an MF
    purchase). Shares `_purchase_booking` with the builders, so review and
    commit cannot disagree."""
    if not _is_purchase(candidate) or candidate.unit_cost is None:
        return None
    return _purchase_booking(candidate, cfg or MergeBookingConfig(), True, navs).flag


def commit_candidates(
    ledger: Ledger,
    investment_ledger: Ledger,
    candidates: list[CandidateTxn],
    scheme_master: SchemeMaster | None = None,
    extra_main_entries: list | None = None,
    exact_navs: ExactNavs | None = None,
) -> None:
    """Commit a reviewed batch to both journals as one locked unit.

    Holds both journals' write lock from the FIFO lot reads through the last
    append, so no other writer (another commit, the background price refresh)
    can interleave between the ensure_commodity_accounts and append calls or
    roll one of them back. The work itself is `_commit_candidates`.
    """
    with hold_write_locks(ledger, investment_ledger):
        _commit_candidates(
            ledger,
            investment_ledger,
            candidates,
            scheme_master,
            extra_main_entries,
            exact_navs,
        )


def _commit_candidates(
    ledger: Ledger,
    investment_ledger: Ledger,
    candidates: list[CandidateTxn],
    scheme_master: SchemeMaster | None = None,
    extra_main_entries: list | None = None,
    exact_navs: ExactNavs | None = None,
) -> None:
    """Commit candidates, splitting Groww MF PURCHASE/REDEEM rows across two
    files: the per-scheme lot posting goes to `investment_ledger`
    (investments.beancount), and a coarse vehicle-level INR posting goes to
    `ledger` (main.beancount), with a category-aware realized-gain split into
    Income:CapitalGains:{LTCG,STCG,Debt,Unclassified} on REDEEM (see `_bucket_gains`).
    Non-commodity candidates and DIVIDEND rows are unaffected — they still
    book a single entry into `ledger`, exactly as before this split.

    `scheme_master` classifies each redeemed lot's category (equity/debt/
    other) for the gain split; pass `None` only when the batch has no
    commodity candidates (falls back to the conservative "other" -> Debt
    bucket for any commodity row that does slip through without one).

    Groww STOCK BUY/SELL rows take a third path: a single entry into `ledger`
    only (see `_stock_entry`) — stocks keep their lots in the main journal, so
    there is nothing to write to `investment_ledger`. Every stock entry is
    reconciled against its order's Value before anything is written; an order
    whose lot postings don't add up is flagged on the candidate and refused
    (E1043) rather than committed.

    NOTHING is written to either journal until the whole batch has been built
    and checked. `ensure_commodity_accounts` used to run first, so a batch
    later refused for E1043 still left `commodity`/`open` directives behind in
    the user's journal — a refused import that mutated the journal anyway.
    Every refusal below now happens before the first write.

    `extra_main_entries` (e.g. a card-statement `Custom` record) are appended
    to the main-journal entries, so they share that single `ledger.append`
    snapshot/validate/rollback with the rows. With no candidates and one extra
    entry, that entry is still written. `None` (the default) changes nothing."""
    uncovered = uncovered_redemptions(investment_ledger, candidates, ledger, exact_navs)
    if uncovered:
        raise UNCOVERED_REDEMPTION.exc({"candidate_ids": ", ".join(uncovered)})

    # Refuse lot rows we cannot route or cannot legally name, before anything
    # is built or written.
    unbookable: list[str] = []
    if exact_navs is not None:
        from backend.app.service.ingest.pipeline.charges import (
            EXACT_NAV_COST_EXCEEDS_CASH,
        )

        for c in candidates:
            # Defensive: unreachable while `exact_nav_purchase` steps units
            # down. If it ever fires, every fallback booking would carry a
            # negative charge, so the row is refused, not booked.
            if exact_nav_flag_of(c, exact_navs) == EXACT_NAV_COST_EXCEEDS_CASH:
                c.lot_blocked_reason = EXACT_NAV_COST_EXCEEDS_CASH
                unbookable.append(f"{c.id} ({EXACT_NAV_COST_EXCEEDS_CASH})")
    for c in candidates:
        if not _is_lot_row(c):
            continue
        reason = _unbookable_lot_reason(c)
        if reason is not None:
            c.lot_blocked_reason = reason
            unbookable.append(f"{c.id} ({reason})")
    if unbookable:
        raise LOT_NOT_BOOKABLE.exc({"details": "; ".join(unbookable)})

    # In-batch lot cache: seeded lazily from the committed state of whichever
    # journal holds that vehicle's lots (via _current_lots, the same listing
    # `_consume_fifo` uses) and extended in-memory as each PURCHASE candidate
    # is processed below, so a REDEEM/SELL later in the SAME batch can
    # FIFO-consume an earlier same-batch PURCHASE's lot (mirrors
    # uncovered_redemptions's own in-batch coverage tracking). This is why
    # commit_candidates doesn't call `_consume_fifo` directly -- that function
    # alone only sees already-committed state (see its docstring), and only
    # ever looks at investment_ledger.
    lot_cache: dict[tuple[str, str], list[list]] = {}

    def lots_for(symbol: str, vehicle: str = Vehicle.MUTUAL_FUND) -> list[list]:
        key = (Vehicle(vehicle).value, symbol)
        if key not in lot_cache:
            source = ledger if key[0] == Vehicle.STOCK.value else investment_ledger
            lot_cache[key] = _current_lots(source, symbol, vehicle)
        return lot_cache[key]

    main_entries: list = []
    investment_entries: list[data.Transaction] = []
    unreconciled: list[str] = []

    def check_reconciles(c: CandidateTxn, entry: data.Transaction) -> None:
        if abs(_stock_lot_gap(c, entry)) > STOCK_RECONCILE_TOLERANCE:
            c.unreconciled_lots = True
            unreconciled.append(c.id)

    for c in candidates:
        if _is_stock_purchase(c):
            assert c.units is not None and c.commodity is not None
            assert c.unit_cost is not None
            entry = _stock_entry(c)
            check_reconciles(c, entry)
            main_entries.append(entry)
            lots_for(c.commodity, Vehicle.STOCK).append(
                [
                    c.units,
                    _stock_lot_price(c.amount, c.units, c.unit_cost),
                    c.date,
                ]
            )
        elif _is_stock_sell(c):
            assert c.units is not None and c.commodity is not None
            consumed = _consume_from_lots(lots_for(c.commodity, Vehicle.STOCK), c.units)
            entry = _stock_entry(c, lots=consumed)
            check_reconciles(c, entry)
            main_entries.append(entry)
        elif _is_purchase(c):
            assert c.units is not None and c.commodity is not None
            assert c.unit_cost is not None
            # The Groww MF purchase path: the only caller whose cash is a
            # truncated export Amount, so the only one that snaps it.
            # `exact_navs` (None: not consulted) may re-derive units/cost
            # from the exact NAV; the in-batch lot cache must carry the lot
            # as BOOKED, or a later redemption in this batch would name a
            # lot that is not in the journal.
            investment_entries.append(
                _subsystem_entry(c, snap_cash=True, navs=exact_navs)
            )
            main_entries.append(_vehicle_entry(c, snap_cash=True, navs=exact_navs))
            booked = _purchase_booking(c, MergeBookingConfig(), True, exact_navs)
            lots_for(c.commodity).append([booked.units, booked.unit_cost, c.date])
        elif _is_redeem(c):
            assert c.units is not None and c.commodity is not None
            lots = lots_for(c.commodity)
            redeemed = _redeemable_units(
                c.units, sum((lot[0] for lot in lots), Decimal(0))
            )
            assert redeemed is not None  # uncovered_redemptions refused it above
            if redeemed == c.units:  # covered: exactly as before
                consumed = _consume_from_lots(lots, c.units)
                investment_entries.append(_subsystem_entry(c, lots=consumed))
                main_entries.append(_vehicle_entry(c, scheme_master, consumed))
            else:
                # A FULL redemption the export rounded up or down: redeem the
                # units held, at the reported sale amount. The candidate keeps
                # the export's units (its dedup identity); the entries record
                # them as metadata so a re-import still matches.
                booked = c.model_copy(update={"units": redeemed})
                consumed = _consume_from_lots(lots, redeemed)
                inv_entry = _subsystem_entry(booked, lots=consumed)
                main_entry = _vehicle_entry(booked, scheme_master, consumed)
                for entry in (inv_entry, main_entry):
                    entry.meta[EXPORT_UNITS_META] = c.units
                investment_entries.append(inv_entry)
                main_entries.append(main_entry)
                if booked.unexplained_charge:
                    c.unexplained_charge = True
        else:
            main_entries.append(candidate_to_entry(c))

    if unreconciled:
        # Nothing has been written yet — not even an `open` directive — so
        # raising here leaves the journal byte-identical. The flagged rows stay
        # in review with `unreconciled_lots` set.
        raise UNRECONCILED_STOCK_ORDER.exc(
            {
                "candidate_ids": ", ".join(unreconciled),
                "tolerance": str(STOCK_RECONCILE_TOLERANCE),
            }
        )

    # Past every refusal: now, and only now, touch the journals. Commodity
    # directives and holding accounts must exist before `append` runs, or
    # `_ensure_accounts_open` would open a commodity account `... INR` (which
    # beancount rejects) and without "FIFO" booking.
    mf_specs: list[tuple[str, str | None]] = []
    stock_specs: list[tuple[str, str | None]] = []
    isins: dict[str, str] = {}
    for c in candidates:
        if not _is_lot_row(c):
            continue
        assert c.commodity is not None
        spec = (c.commodity, c.unresolved_scheme or c.normalized_narration)
        if _is_stock(c):
            stock_specs.append(spec)
            if c.isin:
                # First one wins; ensure_commodity_accounts only writes the
                # directive when the symbol is opened for the first time.
                isins.setdefault(c.commodity, c.isin)
        else:
            mf_specs.append(spec)
    if mf_specs:
        investment_ledger.ensure_commodity_accounts(mf_specs)
    if stock_specs:
        ledger.ensure_commodity_accounts(stock_specs, Vehicle.STOCK, isins=isins)

    if extra_main_entries:
        main_entries.extend(extra_main_entries)
    if main_entries:
        ledger.append(main_entries)
    if investment_entries:
        investment_ledger.append(investment_entries)
