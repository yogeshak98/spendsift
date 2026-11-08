from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.models.storage.merge_booking import MergeBookingConfig
from backend.app.core.errors.errors import CodedError
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import (
    _classify_lot,
    _commodity_entry,
    _consume_fifo,
    _subsystem_entry,
    _vehicle_entry,
    candidate_to_entry,
    commit_candidates,
)
from backend.app.service.ingest.parsers.scheme_master import SchemeMaster
from backend.app.models.domain.accounts import INVESTMENT_MUTUAL_FUND

# The default Groww MF clearing account (Settings.platform_clearing_accounts).
SUBSYSTEM_CLEARING = "Assets:Investment:MutualFund:Groww:Cash"


def _base(**overrides) -> dict:
    fields = dict(
        date=date(2026, 3, 18),
        amount=Decimal("21500.00"),
        raw_narration="SIP — Parag Parikh Flexi Cap",
        normalized_narration="PARAG PARIKH FLEXI CAP",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
    )
    fields.update(overrides)
    return fields


def test_candidate_investment_fields_default_to_none():
    c = CandidateTxn(**_base())
    assert c.units is None
    assert c.commodity is None
    assert c.unit_cost is None
    assert c.unresolved_scheme is None


def test_candidate_carries_investment_fields_when_set():
    c = CandidateTxn(
        **_base(),
        units=Decimal("100.0"),
        commodity="MF122639",
        unit_cost=Decimal("215.0"),
    )
    assert c.units == Decimal("100.0")
    assert c.commodity == "MF122639"
    assert c.unit_cost == Decimal("215.0")


def test_candidate_carries_unresolved_scheme_flag():
    c = CandidateTxn(**_base(), unresolved_scheme="Unknown Fund - Direct Growth")
    assert c.unresolved_scheme == "Unknown Fund - Direct Growth"


def _purchase() -> CandidateTxn:
    return CandidateTxn(
        date=date(2026, 3, 18),
        amount=Decimal("21500.00"),
        raw_narration="Purchase — Parag Parikh Flexi Cap",
        normalized_narration="PARAG PARIKH FLEXI CAP",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal("100.0"),
        commodity="MF122639",
        unit_cost=Decimal("215.0"),
    )


def _redeem() -> CandidateTxn:
    return CandidateTxn(
        date=date(2026, 4, 15),
        amount=Decimal("12000.00"),
        raw_narration="Redeem — Parag Parikh Flexi Cap",
        normalized_narration="PARAG PARIKH FLEXI CAP",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.CREDIT,
        units=Decimal("50.0"),
        commodity="MF122639",
        unit_cost=Decimal("240.0"),
    )


def _dividend() -> CandidateTxn:
    return CandidateTxn(
        date=date(2026, 3, 22),
        amount=Decimal("120.00"),
        raw_narration="Dividend — Parag Parikh Flexi Cap",
        normalized_narration="PARAG PARIKH FLEXI CAP",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.CREDIT,
        commodity="MF122639",
    )


def test_purchase_emits_units_at_cost_with_inr_cash_leg():
    entry = candidate_to_entry(_purchase())
    holding = next(p for p in entry.postings if p.account.endswith(":Groww:MF122639"))
    cash = next(p for p in entry.postings if p.account == "Assets:Broker:Groww")
    assert holding.units.number == Decimal("100.0")
    assert holding.units.currency == "MF122639"
    assert holding.cost.number == Decimal("215.0")
    assert holding.cost.currency == "INR"
    assert cash.units.number == Decimal("-21500.00")
    assert cash.units.currency == "INR"


def test_redeem_emits_units_out_at_held_cost_with_capital_gains_plug():
    entry = candidate_to_entry(_redeem())
    holding = next(p for p in entry.postings if p.account.endswith(":Groww:MF122639"))
    cash = next(p for p in entry.postings if p.account == "Assets:Broker:Groww")
    gains = next(
        p for p in entry.postings if p.account == "Income:Investment:CapitalGains"
    )
    assert holding.units.number == Decimal("-50.0")
    assert holding.cost is not None  # empty cost spec -> FIFO booking fills it
    assert cash.units.number == Decimal("12000.00")
    assert gains.units is None  # auto-balanced plug


def test_subsystem_entry_purchase_uses_internal_clearing_not_real_wallet():
    entry = _subsystem_entry(_purchase())
    holding = next(p for p in entry.postings if p.account.endswith(":Groww:MF122639"))
    clearing = next(p for p in entry.postings if p.account == SUBSYSTEM_CLEARING)
    assert holding.units.number == Decimal("100.0")
    assert holding.cost.number == Decimal("215.0")
    assert clearing.units.number == Decimal("-21500.00")
    # The real Groww wallet never appears in the subsystem-internal entry.
    assert all(p.account != "Assets:Broker:Groww" for p in entry.postings)


def test_subsystem_entry_redeem_keeps_capital_gains_plug_internal():
    entry = _subsystem_entry(_redeem())
    holding = next(p for p in entry.postings if p.account.endswith(":Groww:MF122639"))
    clearing = next(p for p in entry.postings if p.account == SUBSYSTEM_CLEARING)
    gains = next(
        p for p in entry.postings if p.account == "Income:Investment:CapitalGains"
    )
    assert holding.units.number == Decimal("-50.0")
    assert clearing.units.number == Decimal("12000.00")
    assert gains.units is None  # auto-balanced plug, internal to investments.beancount
    assert all(p.account != "Assets:Broker:Groww" for p in entry.postings)


def test_vehicle_entry_purchase_books_plain_inr_split_no_commodity():
    entry = _vehicle_entry(_purchase())
    accounts = {p.account for p in entry.postings}
    assert accounts == {INVESTMENT_MUTUAL_FUND, "Assets:Broker:Groww"}
    vehicle = next(p for p in entry.postings if p.account == INVESTMENT_MUTUAL_FUND)
    cash = next(p for p in entry.postings if p.account == "Assets:Broker:Groww")
    assert vehicle.units.number == Decimal("21500.00")
    assert vehicle.units.currency == "INR"
    assert vehicle.cost is None  # no per-lot cost at the vehicle level
    assert cash.units.number == Decimal("-21500.00")


def test_vehicle_entry_redeem_reverses_direction_and_books_capital_gains_leg():
    # Unit 4: the vehicle-level REDEEM entry now carries the real per-lot
    # realized gain, removing cost basis at the lot's actual cost (not the
    # full cash amount) and plugging the difference into Income:CapitalGains.
    # Single lot, no scheme_master -> no category is decidable -> routed
    # entirely to Income:CapitalGains:Unclassified (never Debt).
    lots = [(Decimal("50.0"), Decimal("215.0"), date(2026, 3, 18))]
    entry = _vehicle_entry(_redeem(), None, lots)
    accounts = {p.account for p in entry.postings}
    assert accounts == {
        INVESTMENT_MUTUAL_FUND,
        "Assets:Broker:Groww",
        "Income:CapitalGains:Unclassified",
    }
    vehicle = next(p for p in entry.postings if p.account == INVESTMENT_MUTUAL_FUND)
    cash = next(p for p in entry.postings if p.account == "Assets:Broker:Groww")
    gains = next(
        p for p in entry.postings if p.account == "Income:CapitalGains:Unclassified"
    )
    # total_lot_cost = 50 * 215 = 10750; cash = 12000; gain = 1250
    assert vehicle.units.number == Decimal("-10750.0")
    assert cash.units.number == Decimal("12000.00")
    assert gains.units.number == Decimal("-1250.00")
    inr_sum = sum(p.units.number for p in entry.postings if p.units is not None)
    assert inr_sum == 0


def test_dividend_emits_cash_in_and_dividend_income_no_units():
    entry = candidate_to_entry(_dividend())
    accounts = {p.account for p in entry.postings}
    assert accounts == {
        "Assets:Broker:Groww",
        "Income:Investment:Dividend",
    }
    cash = next(p for p in entry.postings if p.account == "Assets:Broker:Groww")
    assert cash.units.number == Decimal("120.00")
    assert all(p.units.currency == "INR" for p in entry.postings)


def test_non_investment_candidate_still_uses_plain_inr_path():
    plain = CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("412.00"),
        raw_narration="UPI/DR/SWIGGY",
        normalized_narration="SWIGGY",
        source_account="Assets:Checking:HDFC",
        direction=Direction.DEBIT,
        suggested_category="Expenses:Food",
    )
    entry = candidate_to_entry(plain)
    assert {p.account for p in entry.postings} == {
        "Expenses:Food",
        "Assets:Checking:HDFC",
    }
    assert all(p.units.currency == "INR" for p in entry.postings)


def test_uncovered_redeem_blocks_with_coded_error_not_beancount_crash(
    ledger, investment_ledger
):
    # REDEEM of 50 units with no prior PURCHASE in the ledger or batch: beancount
    # would raise a raw "Too many missing numbers" LedgerValidationError. The
    # commit guard must intercept it as the FE-friendly E1013 instead.
    with pytest.raises(CodedError) as exc:
        commit_candidates(ledger, investment_ledger, [_redeem()])
    assert exc.value.code == "E1013"
    # Nothing was written — the redeem never reached either ledger.
    assert ledger.validate() == []
    assert investment_ledger.validate() == []
    year_file = investment_ledger.journal_dir / "investments-2026.beancount"
    assert not year_file.exists() or "MF122639" not in year_file.read_text(
        encoding="utf-8"
    )


def test_redeem_covered_by_earlier_purchase_in_same_batch_commits(
    ledger, investment_ledger
):
    # PURCHASE 100 then REDEEM 50 of the same commodity in ONE batch: the earlier
    # candidate covers the redemption, so no false positive — it commits cleanly.
    commit_candidates(ledger, investment_ledger, [_purchase(), _redeem()])
    assert ledger.validate() == []
    assert investment_ledger.validate() == []
    assert "MF122639" in (
        investment_ledger.journal_dir / "investments-2026.beancount"
    ).read_text(encoding="utf-8")
    assert "open Assets:Investment:MutualFund" in ledger.main_file.read_text(
        encoding="utf-8"
    )


def test_commit_purchase_then_redeem_validates_through_ledger(
    ledger, investment_ledger
):
    commit_candidates(ledger, investment_ledger, [_purchase()])
    commit_candidates(ledger, investment_ledger, [_redeem()])
    assert ledger.validate() == []
    assert investment_ledger.validate() == []
    text = (investment_ledger.journal_dir / "investments-2026.beancount").read_text(
        encoding="utf-8"
    )
    assert "MF122639" in text
    investment_main = investment_ledger.main_file.read_text(encoding="utf-8")
    assert "commodity MF122639" in investment_main
    assert "open Assets:Investment:MutualFund:Groww:MF122639" in investment_main
    assert '"FIFO"' in investment_main
    main = ledger.main_file.read_text(encoding="utf-8")
    assert "open Assets:Investment:MutualFund" in main
    assert "MF122639" not in main


# ---------------------------------------------------------------------------
# Task 4: statutory charge legs on Groww-CSV path
# ---------------------------------------------------------------------------


def _purchase_with_gap(cfg: MergeBookingConfig) -> CandidateTxn:
    """Purchase where cash includes a realistic stamp-duty gap.
    units=100, nav=215 → cost=21500; cash=21501.08 → gap=1.08.
    stamp at 0.00005 × 21500 = 1.075, capped at 1.08 → rounds to 1.08; residual=0."""
    return CandidateTxn(
        date=date(2026, 3, 18),
        amount=Decimal("21501.08"),
        raw_narration="Purchase — PPFCF",
        normalized_narration="PPFCF",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal("100.0"),
        commodity="MF122639",
        unit_cost=Decimal("215.0"),
    )


def _purchase_zero_gap(cfg: MergeBookingConfig) -> CandidateTxn:
    """Purchase where cash == cost exactly (zero gap)."""
    return CandidateTxn(
        date=date(2026, 3, 18),
        amount=Decimal("21500.00"),
        raw_narration="Purchase — PPFCF",
        normalized_narration="PPFCF",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal("100.0"),
        commodity="MF122639",
        unit_cost=Decimal("215.0"),
    )


def test_purchase_gains_stamp_duty_leg():
    """PURCHASE with realistic gap: stamp duty posting added."""
    cfg = MergeBookingConfig(
        stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50"
    )
    entry = _commodity_entry(_purchase_with_gap(cfg), cfg=cfg)
    stamp_postings = [
        p
        for p in entry.postings
        if p.account == "Expenses:Investment:Charges:StampDuty"
    ]
    assert len(stamp_postings) == 1
    p = stamp_postings[0]
    assert p.meta is not None and p.meta.get("charge-basis") == "statutory"
    # round(21500 × 0.00005, 2) = round(1.075, 2) = 1.08; capped at gap=1.08 → 1.08
    assert p.units.number == Decimal("1.08")


def test_purchase_with_charges_balances():
    """All INR postings sum to zero (transaction is balanced)."""
    cfg = MergeBookingConfig(
        stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50"
    )
    entry = _commodity_entry(_purchase_with_gap(cfg), cfg=cfg)
    # INR sum: -21501.08 (cash_out) + 1.08 (stamp) + 0 (residual=0)
    # + commodity lot at cost 21500 → overall beancount balance = 0
    inr_sum = sum(
        p.units.number
        for p in entry.postings
        if p.units is not None and p.units.currency == "INR"
    )
    assert inr_sum == -Decimal("21500.00")


def test_purchase_zero_gap_no_charge_leg_no_error():
    """PURCHASE where cash == cost exactly: no charge legs, no E1024 raised."""
    cfg = MergeBookingConfig(
        stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50"
    )
    entry = _commodity_entry(_purchase_zero_gap(cfg), cfg=cfg)
    charge_postings = [
        p
        for p in entry.postings
        if p.account
        in (
            "Expenses:Investment:Charges:StampDuty",
            "Expenses:Investment:Charges:STT",
            "Expenses:Investment:Charges:Groww",
        )
    ]
    assert charge_postings == []


def test_purchase_negative_gap_beyond_tolerance_books_unexplained_and_flags():
    """Was `..._raises_e1024`. Pre-P5 an over-tolerance negative gap refused
    the commit; it now books the shortfall to the unexplained account and
    flags the row, because refusing an otherwise-fine import is the gate P5
    removed and absorbing the money silently is the defect it replaced."""
    # amount=21499 < cost=21500 → gap = -1; tolerance=0.5, pct disabled → -1 exceeds it → surface
    cfg = MergeBookingConfig(
        stamp_duty_rate="0.00005",
        stt_rate_sell="0",
        charge_tolerance="0.5",
        charge_tolerance_pct="0",
    )
    negative_gap_candidate = CandidateTxn(
        date=date(2026, 3, 18),
        amount=Decimal("21499.00"),
        raw_narration="Purchase — PPFCF",
        normalized_narration="PPFCF",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal("100.0"),
        commodity="MF122639",
        unit_cost=Decimal("215.0"),
    )
    txn = _commodity_entry(negative_gap_candidate, cfg=cfg)
    unexplained = [
        p
        for p in txn.postings
        if p.account == "Expenses:Investment:Charges:Unexplained"
    ]
    assert len(unexplained) == 1
    assert unexplained[0].units.number == Decimal("-1")
    # Honest provenance: this is not a statutory charge, it is unattributed.
    assert unexplained[0].meta is not None
    assert unexplained[0].meta.get("charge-basis") == "unattributed"
    # No stamp-duty leg on a negative gap, beyond tolerance or not.
    assert [
        p for p in txn.postings if p.account == "Expenses:Investment:Charges:StampDuty"
    ] == []
    assert negative_gap_candidate.unexplained_charge is True
    # The INR legs still balance: -21499 cash + (-1) unexplained against the
    # 21500 lot cost.
    assert sum(
        p.units.number
        for p in txn.postings
        if p.units is not None and p.units.currency == "INR"
    ) == -Decimal("21500.00")


def test_purchase_small_negative_gap_within_tolerance_books_rounding_credit():
    """cash < cost by less than tolerance → broker-export rounding artifact →
    books cleanly with a credit leg into the charges account, no error."""
    # amount=21499 < cost=21500 → gap = -1, within the default 50 tolerance.
    cfg = MergeBookingConfig(
        stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50"
    )
    negative_gap_candidate = CandidateTxn(
        date=date(2026, 3, 18),
        amount=Decimal("21499.00"),
        raw_narration="Purchase — PPFCF",
        normalized_narration="PPFCF",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal("100.0"),
        commodity="MF122639",
        unit_cost=Decimal("215.0"),
    )
    txn = _commodity_entry(negative_gap_candidate, cfg=cfg)
    charge_postings = [
        p for p in txn.postings if p.account == "Expenses:Investment:Charges:Groww"
    ]
    assert len(charge_postings) == 1
    assert charge_postings[0].units.number == Decimal("-1")
    # NO stamp-duty posting. A stamp duty that was really charged is deducted
    # before allotment and so shows up as a POSITIVE gap; a negative gap means
    # the export rounding swamped it, so the journal must not assert the
    # computed 1.08 on this row's evidence.
    stamp_postings = [
        p for p in txn.postings if p.account == "Expenses:Investment:Charges:StampDuty"
    ]
    assert stamp_postings == []
    # The charge legs still sum to the -1 gap, which is what keeps the
    # transaction balanced.
    assert sum(p.units.number for p in charge_postings + stamp_postings) == Decimal(
        "-1"
    )


def test_purchase_over_tolerance_residual_books_unexplained_and_flags():
    """Was `..._raises_e1024`. The over-tolerance residual is now booked to
    the unexplained account and the row flagged, instead of the commit being
    refused. The residual must NOT land in charges_account, which is read as
    "broker charges" and would misattribute it."""
    # rate=0; gap=200 → over-tolerance residual → booked as unexplained
    cfg = MergeBookingConfig(
        stamp_duty_rate="0", stt_rate_sell="0", charge_tolerance="5"
    )
    over_tol_candidate = CandidateTxn(
        date=date(2026, 3, 18),
        amount=Decimal("21700.00"),  # gap = 200, beyond the 31.5 precision band
        raw_narration="Purchase — PPFCF",
        normalized_narration="PPFCF",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal("100.0"),
        commodity="MF122639",
        unit_cost=Decimal("215.0"),
    )
    txn = _commodity_entry(over_tol_candidate, cfg=cfg)
    unexplained = [
        p
        for p in txn.postings
        if p.account == "Expenses:Investment:Charges:Unexplained"
    ]
    assert len(unexplained) == 1
    assert unexplained[0].units.number == Decimal("200")
    assert unexplained[0].meta is not None
    assert unexplained[0].meta.get("charge-basis") == "unattributed"
    assert [
        p for p in txn.postings if p.account == "Expenses:Investment:Charges:Groww"
    ] == []
    assert over_tol_candidate.unexplained_charge is True
    assert sum(
        p.units.number
        for p in txn.postings
        if p.units is not None and p.units.currency == "INR"
    ) == -Decimal("21500.00")


def test_redeem_no_charges_when_stt_zero():
    """REDEEM with stt_rate_sell=0: no charge legs added (existing behaviour)."""
    cfg = MergeBookingConfig(
        stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50"
    )
    candidate = CandidateTxn(
        date=date(2026, 4, 15),
        amount=Decimal("12000.00"),
        raw_narration="Redeem — PPFCF",
        normalized_narration="PPFCF",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.CREDIT,
        units=Decimal("50.0"),
        commodity="MF122639",
        unit_cost=Decimal("240.0"),
    )
    entry = _commodity_entry(candidate, cfg=cfg)
    charge_postings = [
        p
        for p in entry.postings
        if p.account
        in (
            "Expenses:Investment:Charges:StampDuty",
            "Expenses:Investment:Charges:STT",
        )
    ]
    assert len(charge_postings) == 0


# ---------------------------------------------------------------------------
# Unit 4: FIFO lot consumption + category-aware capital-gains routing
# ---------------------------------------------------------------------------


def _buy(
    units: Decimal, unit_cost: Decimal, txn_date: date, commodity: str = "MF122639"
) -> CandidateTxn:
    return CandidateTxn(
        date=txn_date,
        amount=units * unit_cost,
        raw_narration="Purchase — PPFCF",
        normalized_narration="PPFCF",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=units,
        commodity=commodity,
        unit_cost=unit_cost,
    )


def _equity_scheme_master(code: str = "122639") -> SchemeMaster:
    text = f"{code};Parag Parikh Flexi Cap Fund - Direct Plan - Growth;71.23;28-Jun-2026;equity\n"
    return SchemeMaster.from_text(text)


def _other_scheme_master(code: str = "122639") -> SchemeMaster:
    # Category "other": AMFI's banner says neither Equity nor Debt Scheme
    # (e.g. an arbitrage fund).
    text = (
        f"{code};Some Arbitrage Fund - Direct Plan - Growth;10.50;28-Jun-2026;other\n"
    )
    return SchemeMaster.from_text(text)


def _debt_scheme_master(code: str = "122639") -> SchemeMaster:
    text = f"{code};Some Debt Fund - Direct Plan - Growth;10.50;28-Jun-2026;debt\n"
    return SchemeMaster.from_text(text)


def test_consume_fifo_reads_committed_lots_oldest_first(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            _buy(Decimal("50.0"), Decimal("215.0"), date(2026, 1, 10)),
            _buy(Decimal("25.0"), Decimal("240.0"), date(2026, 2, 10)),
        ],
    )
    consumed = _consume_fifo(investment_ledger, "MF122639", Decimal("60.0"))
    assert consumed == [
        (Decimal("50.0"), Decimal("215.0"), date(2026, 1, 10)),
        (Decimal("10.0"), Decimal("240.0"), date(2026, 2, 10)),
    ]


def test_consume_fifo_partial_consumption_of_a_single_lot(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [_buy(Decimal("50.0"), Decimal("215.0"), date(2026, 1, 10))],
    )
    consumed = _consume_fifo(investment_ledger, "MF122639", Decimal("20.0"))
    assert consumed == [(Decimal("20.0"), Decimal("215.0"), date(2026, 1, 10))]


def test_vehicle_entry_redeem_equity_within_12_months_is_all_stcg():
    # Lot bought 2026-03-18, redeemed 2026-04-15 (< 12 months) -> all STCG.
    lots = [(Decimal("50.0"), Decimal("215.0"), date(2026, 3, 18))]
    entry = _vehicle_entry(_redeem(), _equity_scheme_master(), lots)
    accounts = {p.account for p in entry.postings}
    assert accounts == {
        INVESTMENT_MUTUAL_FUND,
        "Assets:Broker:Groww",
        "Income:CapitalGains:STCG",
    }
    gains = next(p for p in entry.postings if p.account == "Income:CapitalGains:STCG")
    assert gains.units.number == Decimal("-1250.00")  # 12000 - (50*215)


def test_vehicle_entry_redeem_equity_beyond_12_months_is_all_ltcg():
    # Lot bought 2024-01-01, redeemed 2026-04-15 (> 12 months) -> all LTCG.
    lots = [(Decimal("50.0"), Decimal("215.0"), date(2024, 1, 1))]
    entry = _vehicle_entry(_redeem(), _equity_scheme_master(), lots)
    accounts = {p.account for p in entry.postings}
    assert accounts == {
        INVESTMENT_MUTUAL_FUND,
        "Assets:Broker:Groww",
        "Income:CapitalGains:LTCG",
    }
    gains = next(p for p in entry.postings if p.account == "Income:CapitalGains:LTCG")
    assert gains.units.number == Decimal("-1250.00")


def test_classify_lot_equity_exactly_12_months_is_still_stcg():
    # Redeemed exactly on the 12-month anniversary of the lot date: holding
    # period has NOT yet exceeded 12 months, so this must stay STCG.
    assert (
        _classify_lot(date(2027, 3, 18), date(2026, 3, 18), "equity")
        == "Income:CapitalGains:STCG"
    )


def test_classify_lot_equity_one_day_past_12_months_is_ltcg():
    # One day past the 12-month anniversary: holding period exceeds 12
    # months, so this must be LTCG. This is the boundary the floor-month-count
    # bug previously misclassified as STCG.
    assert (
        _classify_lot(date(2027, 3, 19), date(2026, 3, 18), "equity")
        == "Income:CapitalGains:LTCG"
    )


def test_classify_lot_equity_leap_day_anniversary_clamps_to_feb_28():
    # Lot dated Feb 29 of a leap year (2028): the 12-month anniversary lands
    # in a non-leap year (2029), which has no Feb 29, so it clamps to Feb 28.
    # Redeeming one day after that clamped anniversary must be LTCG; on the
    # anniversary itself must still be STCG.
    assert (
        _classify_lot(date(2029, 3, 1), date(2028, 2, 29), "equity")
        == "Income:CapitalGains:LTCG"
    )
    assert (
        _classify_lot(date(2029, 2, 28), date(2028, 2, 29), "equity")
        == "Income:CapitalGains:STCG"
    )


def test_vehicle_entry_redeem_equity_straddling_lots_split_ltcg_and_stcg():
    # Two lots consumed by one redemption: one > 12mo old (LTCG), one < 12mo
    # old (STCG) as of the 2026-04-15 redemption date in _redeem().
    lots = [
        (Decimal("30.0"), Decimal("200.0"), date(2024, 1, 1)),  # LTCG: cost 6000
        (Decimal("20.0"), Decimal("220.0"), date(2026, 1, 1)),  # STCG: cost 4400
    ]
    entry = _vehicle_entry(_redeem(), _equity_scheme_master(), lots)
    accounts = {p.account for p in entry.postings}
    assert accounts == {
        INVESTMENT_MUTUAL_FUND,
        "Assets:Broker:Groww",
        "Income:CapitalGains:LTCG",
        "Income:CapitalGains:STCG",
    }
    ltcg = next(p for p in entry.postings if p.account == "Income:CapitalGains:LTCG")
    stcg = next(p for p in entry.postings if p.account == "Income:CapitalGains:STCG")
    vehicle = next(p for p in entry.postings if p.account == INVESTMENT_MUTUAL_FUND)
    # proceeds allocated proportionally: lot1 (30/50 * 12000) = 7200, lot2 = 4800
    # lot1 gain = 7200 - 6000 = 1200; lot2 gain = 4800 - 4400 = 400
    assert ltcg.units.number == Decimal("-1200.00")
    assert stcg.units.number == Decimal("-400.00")
    assert vehicle.units.number == Decimal("-10400.00")  # total cost 6000+4400
    inr_sum = sum(p.units.number for p in entry.postings if p.units is not None)
    assert inr_sum == 0  # reconciles exactly, no rounding residual


def test_vehicle_entry_redeem_debt_category_routes_fully_to_debt_bucket_no_split():
    # Debt-category lots straddling 12 months still get NO holding-period
    # split (post-2023 law: no LTCG indexation benefit for debt funds).
    lots = [
        (Decimal("30.0"), Decimal("200.0"), date(2024, 1, 1)),
        (Decimal("20.0"), Decimal("220.0"), date(2026, 1, 1)),
    ]
    entry = _vehicle_entry(_redeem(), _debt_scheme_master(), lots)
    accounts = {p.account for p in entry.postings}
    assert accounts == {
        INVESTMENT_MUTUAL_FUND,
        "Assets:Broker:Groww",
        "Income:CapitalGains:Debt",
    }
    debt = next(p for p in entry.postings if p.account == "Income:CapitalGains:Debt")
    assert debt.units.number == Decimal("-1600.00")  # 12000 - 10400, both lots pooled


def test_vehicle_entry_redeem_unresolvable_code_is_unclassified_not_debt():
    # scheme_master supplied but the code isn't in it -> no category is
    # decidable, which is not the same as "debt".
    lots = [(Decimal("50.0"), Decimal("215.0"), date(2026, 3, 18))]
    unrelated_master = _equity_scheme_master(code="999999")
    entry = _vehicle_entry(_redeem(), unrelated_master, lots)
    accounts = {p.account for p in entry.postings}
    assert "Income:CapitalGains:Unclassified" in accounts
    assert "Income:CapitalGains:Debt" not in accounts


def test_vehicle_entry_redeem_other_category_is_unclassified_but_debt_stays_debt():
    # The pair that discriminates: the same lots and redemption, differing only
    # in the scheme's AMFI category, must book to different accounts. Pinning
    # only "other" would pass if everything were rerouted to Unclassified.
    lots = [(Decimal("50.0"), Decimal("215.0"), date(2026, 3, 18))]

    other = _vehicle_entry(_redeem(), _other_scheme_master(), lots)
    other_accounts = {p.account for p in other.postings}
    assert "Income:CapitalGains:Unclassified" in other_accounts
    assert "Income:CapitalGains:Debt" not in other_accounts
    gain = next(
        p for p in other.postings if p.account == "Income:CapitalGains:Unclassified"
    )
    assert gain.units.number == Decimal("-1250.00")

    debt = _vehicle_entry(_redeem(), _debt_scheme_master(), lots)
    debt_accounts = {p.account for p in debt.postings}
    assert "Income:CapitalGains:Debt" in debt_accounts
    assert "Income:CapitalGains:Unclassified" not in debt_accounts


def test_subsystem_entry_redeem_with_lots_posts_exact_cost_per_lot():
    lots = [
        (Decimal("30.0"), Decimal("200.0"), date(2024, 1, 1)),
        (Decimal("20.0"), Decimal("220.0"), date(2026, 1, 1)),
    ]
    entry = _subsystem_entry(_redeem(), lots=lots)
    holding_postings = [
        p for p in entry.postings if p.account.endswith(":Groww:MF122639")
    ]
    assert len(holding_postings) == 2
    assert {(p.units.number, p.cost.number, p.cost.date) for p in holding_postings} == {
        (Decimal("-30.0"), Decimal("200.0"), date(2024, 1, 1)),
        (Decimal("-20.0"), Decimal("220.0"), date(2026, 1, 1)),
    }


def test_vehicle_entry_purchase_books_trade_value_not_raw_cash_amount():
    """Real-run bug (found running the historical migration on real data):
    PURCHASE must book the SAME cost basis (units * unit_cost)
    onto the coarse vehicle account that `_subsystem_entry`/`_commodity_entry`
    book onto the per-scheme lot in investments.beancount -- not the raw cash
    amount, which can include a statutory purchase charge (stamp duty) the
    lot's cost basis does not. `_purchase_with_gap` has a real charge gap:
    units=100, unit_cost=215 -> trade value 21500.00; cash=21501.08 -> a
    1.08 stamp-duty gap."""
    cfg = MergeBookingConfig(
        stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50"
    )
    entry = _vehicle_entry(_purchase_with_gap(cfg), cfg=cfg)
    vehicle = next(p for p in entry.postings if p.account == INVESTMENT_MUTUAL_FUND)
    cash = next(p for p in entry.postings if p.account == "Assets:Broker:Groww")
    stamp = next(
        p
        for p in entry.postings
        if p.account == "Expenses:Investment:Charges:StampDuty"
    )
    # Vehicle account books trade value (100 * 215.0), matching
    # investments.beancount's lot cost -- NOT the 21501.08 cash amount.
    assert vehicle.units.number == Decimal("21500.00")
    assert cash.units.number == Decimal("-21501.08")
    assert stamp.units.number == Decimal("1.08")
    inr_sum = sum(p.units.number for p in entry.postings if p.units is not None)
    assert inr_sum == 0  # balances exactly, no residual


def test_vehicle_entry_purchase_zero_gap_matches_prior_behaviour():
    """No charge gap (cash == trade value exactly): behaviour is unchanged
    from before the fix -- vehicle account still equals the cash amount
    (which now also equals trade value), no spurious charge leg."""
    cfg = MergeBookingConfig(
        stamp_duty_rate="0.00005", stt_rate_sell="0", charge_tolerance="50"
    )
    entry = _vehicle_entry(_purchase_zero_gap(cfg), cfg=cfg)
    accounts = {p.account for p in entry.postings}
    assert accounts == {INVESTMENT_MUTUAL_FUND, "Assets:Broker:Groww"}
    vehicle = next(p for p in entry.postings if p.account == INVESTMENT_MUTUAL_FUND)
    assert vehicle.units.number == Decimal("21500.00")


def test_commit_candidates_redeem_gain_visible_in_main_ledger(
    ledger, investment_ledger
):
    # End-to-end: an equity redemption spanning the 12-month LTCG/STCG
    # boundary lands a split gain in main.beancount, category-classified via
    # scheme_master.
    scheme_master = _equity_scheme_master()
    commit_candidates(
        ledger,
        investment_ledger,
        [
            _buy(Decimal("30.0"), Decimal("200.0"), date(2024, 1, 1)),
            _buy(Decimal("20.0"), Decimal("220.0"), date(2026, 1, 1)),
        ],
        scheme_master,
    )
    commit_candidates(ledger, investment_ledger, [_redeem()], scheme_master)
    assert ledger.validate() == []
    assert investment_ledger.validate() == []
    main = ledger.main_file.read_text(encoding="utf-8")
    assert "open Income:CapitalGains:LTCG" in main
    assert "open Income:CapitalGains:STCG" in main


def test_an_unexplained_charge_flags_the_row_instead_of_refusing_the_commit(
    ledger, investment_ledger
):
    """Replaces the old CHARGE_VIOLATION behaviour. A commit that refuses is
    the gate P5 removed; a commit that lands with no flag is a silent import.
    Neither is acceptable -- it lands AND it is flagged."""
    # _purchase() is 100.0 units @ 215.0 = 21500 trade value. Default cfg:
    # precision band = 100*0.1 + 215*0.1 = 31.5, capped by the legacy
    # max(50, 0.05% * 21500 = 10.75) = 50 -> 31.5 applies. Cash 21700 leaves a
    # 200 gap; stamp duty accounts for 1.08, so 198.92 is unattributable.
    candidate = _purchase()
    candidate.amount = Decimal("21700.00")

    commit_candidates(ledger, investment_ledger, [candidate])

    # The commit SUCCEEDED (no CodedError) and both journals BALANCE.
    assert ledger.validate() == []
    assert investment_ledger.validate() == []
    # The row is flagged, so review can surface it.
    assert candidate.unexplained_charge is True
    # The remainder is in the journal, under its own name, in both journals
    # (the vehicle entry in main and the lot entry in investments must agree).
    main_text = ledger.main_file.read_text(encoding="utf-8")
    year_text = (ledger.journal_dir / "2026.beancount").read_text(encoding="utf-8")
    inv_text = (investment_ledger.journal_dir / "investments-2026.beancount").read_text(
        encoding="utf-8"
    )
    for text in (main_text + year_text, inv_text):
        assert "Expenses:Investment:Charges:Unexplained" in text
        assert "198.92" in text
        # The VALUE, not just the key. `charge-basis: statutory` on this leg
        # would be an unevidenced claim written into the journal files -- the
        # product's primary trust surface -- and a key-only check cannot tell
        # the two apart.
        assert 'charge-basis: "unattributed"' in text
        assert (
            'charge-basis: "statutory"'
            not in text.split("Expenses:Investment:Charges:Unexplained")[1].split(
                "\n\n"
            )[0]
        )


def test_vehicle_and_commodity_entries_agree_on_the_unexplained_leg():
    """The two journals are written from two separate `compute_charges` calls
    for the same candidate. If they disagreed on the unexplained leg, the
    coarse main total would silently desync from the granular investments
    total by exactly the amount nobody can explain."""
    cfg = MergeBookingConfig()
    candidate = _purchase()
    candidate.amount = Decimal("21700.00")

    def charge_legs(entry):
        return {
            p.account: p.units.number
            for p in entry.postings
            if p.account.startswith("Expenses:Investment:Charges")
        }

    commodity = charge_legs(_commodity_entry(candidate, cfg=cfg))
    vehicle = charge_legs(_vehicle_entry(candidate, cfg=cfg))
    assert commodity == vehicle
    assert commodity == {
        "Expenses:Investment:Charges:StampDuty": Decimal("1.08"),
        "Expenses:Investment:Charges:Unexplained": Decimal("198.92"),
    }


def test_the_commit_path_uses_the_precision_band_not_the_legacy_one():
    """The discriminator for the call-site migration itself. This gap sits
    BETWEEN the two bands, so it only surfaces if `units`/`nav` actually reach
    `compute_charges`:

      precision band = 100 * ulp(215.0) + 215 * ulp(100.0) = 10 + 21.5 = 31.5
      legacy band    = max(50, 0.05% * 21500 = 10.75)       = 50

    gap = 40, of which stamp duty explains 1.08 -> residual 38.92. Under the
    legacy band that residual is silently absorbed into
    Expenses:Investment:Charges:Groww with no flag; under the precision band it
    is unattributed and the row is flagged. A call site that dropped units/nav
    would quietly take the first branch."""
    cfg = MergeBookingConfig()
    candidate = _purchase()
    candidate.amount = Decimal("21540.00")

    for entry in (
        _commodity_entry(candidate, cfg=cfg),
        _vehicle_entry(candidate, cfg=cfg),
    ):
        legs = {
            p.account: p.units.number
            for p in entry.postings
            if p.account.startswith("Expenses:Investment:Charges")
        }
        assert legs == {
            "Expenses:Investment:Charges:StampDuty": Decimal("1.08"),
            "Expenses:Investment:Charges:Unexplained": Decimal("38.92"),
        }
        assert "Expenses:Investment:Charges:Groww" not in legs
    assert candidate.unexplained_charge is True


def _charge_meta_by_account(entry) -> dict[str, str]:
    return {
        p.account: (p.meta or {}).get("charge-basis")
        for p in entry.postings
        if p.account.startswith("Expenses:Investment:Charges")
    }


def test_the_vehicle_entry_labels_the_unexplained_leg_unattributed():
    """`_vehicle_entry` writes main.beancount -- the coarse journal most read
    models consume -- and nothing asserted the provenance of ITS unexplained
    leg: the two existing meta assertions are both on `_commodity_entry`, and
    the vehicle charge tests project through a helper that keeps only
    account -> amount. Reverting `_charge_meta` to the pre-P5 literal
    {"charge-basis": "statutory"} left the whole suite green while claiming a
    statutory rate produced money no rate explains."""
    cfg = MergeBookingConfig()
    candidate = _purchase()
    candidate.amount = Decimal("21700.00")  # gap 200; stamp 1.08; 198.92 over band

    for entry in (
        _vehicle_entry(candidate, cfg=cfg),
        _commodity_entry(candidate, cfg=cfg),
    ):
        assert _charge_meta_by_account(entry) == {
            "Expenses:Investment:Charges:StampDuty": "statutory",
            "Expenses:Investment:Charges:Unexplained": "unattributed",
        }


def test_a_within_tolerance_plug_is_labelled_rounding_not_statutory():
    """`_charge_meta` was a DENY-LIST: everything that was not the unexplained
    account claimed `statutory`. Two legs routed through it that no statutory
    rate produced -- the within-tolerance positive residual and the
    within-tolerance negative-gap credit, both landing in `charges_account` --
    so both asserted a rate had been applied. That is the same defect as the
    unexplained leg, one account over."""
    cfg = MergeBookingConfig()
    # Positive: gap 20, stamp 1.08, residual 18.92 <= band 31.5 -> absorbed.
    positive = _purchase()
    positive.amount = Decimal("21520.00")
    assert _charge_meta_by_account(_vehicle_entry(positive, cfg=cfg)) == {
        "Expenses:Investment:Charges:StampDuty": "statutory",
        "Expenses:Investment:Charges:Groww": "rounding",
    }
    assert positive.unexplained_charge is False

    # Negative: gap -10, within the band -> a single credit, and no stamp leg.
    negative = _purchase()
    negative.amount = Decimal("21490.00")
    assert _charge_meta_by_account(_vehicle_entry(negative, cfg=cfg)) == {
        "Expenses:Investment:Charges:Groww": "rounding",
    }
    assert negative.unexplained_charge is False
