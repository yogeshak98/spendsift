"""MF purchase cash snapped to the round amount actually paid.

Groww's order-history `Amount` is the amount AFTER the 0.005% stamp duty,
truncated to the rupee: a 15,000 SIP is exported as 14,999. Booking that
figure as the cash leg strands about a rupee per order on the Groww cash
account and under-books stamp duty. On the Groww MF purchase path the cash
leg is therefore the round gross amount when the export amount is consistent
with one (see `charges.snap_purchase_cash`); everything else is
unchanged. All numbers below are fictional.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.models.storage.merge_booking import MergeBookingConfig
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import (
    _vehicle_entry,
    commit_candidates,
    unexplained_charge_of,
)
from backend.app.models.domain.accounts import (
    INVESTMENT_MUTUAL_FUND,
    OPERATING_CURRENCY,
)

# The default Groww wallet (Settings.platform_cash_accounts["groww"]).
GROWW_CASH = "Assets:Broker:Groww"
# The default Groww MF clearing account (Settings.platform_clearing_accounts).
SUBSYSTEM_CLEARING = "Assets:Investment:MutualFund:Groww:Cash"
GROWW_STOCK_CASH_ACCOUNT = GROWW_CASH

STAMP = "Expenses:Investment:Charges:StampDuty"
ROUNDING = "Expenses:Investment:Charges:Groww"  # cfg.charges_account
UNCLASSIFIED = "Income:CapitalGains:Unclassified"


def _mf(
    txn_id: str,
    amount: str,
    units: str,
    nav: str,
    direction: Direction = Direction.DEBIT,
    day: int = 5,
) -> CandidateTxn:
    kind = "Purchase" if direction == Direction.DEBIT else "Redeem"
    return CandidateTxn(
        id=txn_id,
        date=date(2025, 5, day),
        amount=Decimal(amount),
        raw_narration=f"{kind} — Example ELSS Fund",
        normalized_narration="EXAMPLE ELSS FUND",
        source_account=SUBSYSTEM_CLEARING,
        direction=direction,
        units=Decimal(units),
        commodity="MF999001",
        unit_cost=Decimal(nav),
    )


def _inr_legs(led, txn_id: str) -> dict[str, Decimal]:
    """INR postings of the committed transaction `txn_id`, as loaded back."""
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


def _commit(ledger, investment_ledger, *candidates):
    commit_candidates(ledger, investment_ledger, list(candidates))
    assert ledger.validate() == []
    assert investment_ledger.validate() == []


# a. 14,999 exported for a 15,000 SIP: units * NAV = 512.000 * 29.2945
#    = 14,998.784. est = 14,999 / 0.99995 = 14,999.75, R = 15,000, R - est
#    = 0.25 -> snap. Gap 1.216 = stamp 0.75 + rounding 0.466.
def test_a_truncated_sip_books_the_round_cash_and_full_stamp(ledger, investment_ledger):
    c = _mf("snap-a", "14999", "512.000", "29.2945")
    _commit(ledger, investment_ledger, c)

    assert _inr_legs(ledger, "snap-a") == {
        INVESTMENT_MUTUAL_FUND: Decimal("14998.784"),
        GROWW_CASH: Decimal("-15000.00"),
        STAMP: Decimal("0.75"),
        ROUNDING: Decimal("0.466"),
    }
    assert _inr_legs(investment_ledger, "snap-a") == {
        SUBSYSTEM_CLEARING: Decimal("-15000.00"),
        STAMP: Decimal("0.75"),
        ROUNDING: Decimal("0.466"),
    }
    assert c.unexplained_charge is False
    # Dedup identity is the export Amount, never the snapped figure.
    assert c.amount == Decimal("14999")


# b. 92,995 exported for a 93,000 order: 1860.000 * 49.9975 = 92,995.35.
#    est = 92,999.65, R = 93,000, R - est = 0.35 -> snap. Gap 4.65 is all
#    stamp duty (0.005% of 92,995.35 = 4.65), so no rounding leg.
def test_b_large_order_books_round_cash_and_statutory_stamp(ledger, investment_ledger):
    c = _mf("snap-b", "92995", "1860.000", "49.9975")
    _commit(ledger, investment_ledger, c)

    assert _inr_legs(ledger, "snap-b") == {
        INVESTMENT_MUTUAL_FUND: Decimal("92995.35"),
        GROWW_CASH: Decimal("-93000.00"),
        STAMP: Decimal("4.65"),
    }
    assert _inr_legs(investment_ledger, "snap-b") == {
        SUBSYSTEM_CLEARING: Decimal("-93000.00"),
        STAMP: Decimal("4.65"),
    }


# c. 12,345.67: R = 12,400 is nowhere near est = 12,346.29, so the cash stays
#    the export Amount. These legs were recorded on the base commit, before
#    the snap existed.
def test_c_amount_with_no_round_gross_is_booked_as_today(ledger, investment_ledger):
    c = _mf("snap-c", "12345.67", "100.000", "123.4500")
    _commit(ledger, investment_ledger, c)

    assert _inr_legs(ledger, "snap-c") == {
        INVESTMENT_MUTUAL_FUND: Decimal("12345.00"),
        GROWW_CASH: Decimal("-12345.67"),
        STAMP: Decimal("0.62"),
        ROUNDING: Decimal("0.05"),
    }
    assert _inr_legs(investment_ledger, "snap-c") == {
        SUBSYSTEM_CLEARING: Decimal("-12345.67"),
        STAMP: Decimal("0.62"),
        ROUNDING: Decimal("0.05"),
    }


# d. A redemption whose Amount (11,999) WOULD snap if it were a purchase.
#    Recorded on the base commit, before the snap existed.
def test_d_redemption_is_booked_as_today(ledger, investment_ledger):
    buy = _mf("snap-d-buy", "21500", "100.000", "215.0000", day=1)
    sell = _mf("snap-d-sell", "11999", "50.000", "239.9800", Direction.CREDIT)
    _commit(ledger, investment_ledger, buy, sell)

    assert _inr_legs(ledger, "snap-d-sell") == {
        GROWW_CASH: Decimal("11999"),
        INVESTMENT_MUTUAL_FUND: Decimal("-10750.0000000"),
        UNCLASSIFIED: Decimal("-1249.0000000"),
    }
    assert _inr_legs(investment_ledger, "snap-d-sell") == {
        SUBSYSTEM_CLEARING: Decimal("11999"),
        "Income:Investment:CapitalGains": Decimal("-1249.0000000"),
    }


# f. Review and commit agree: the shared authority returns None for (a) and
#    (c), and the commit-time flag matches.
def test_f_review_agrees_with_commit_for_snapped_and_unsnapped(
    ledger, investment_ledger
):
    a = _mf("snap-f-a", "14999", "512.000", "29.2945")
    c = _mf("snap-f-c", "12345.67", "100.000", "123.4500", day=6)
    preview = {x.id: unexplained_charge_of(x, snap_cash=True) for x in (a, c)}
    _commit(ledger, investment_ledger, a, c)
    assert preview == {"snap-f-a": None, "snap-f-c": None}
    assert (a.unexplained_charge, c.unexplained_charge) == (False, False)


def test_f_review_snaps_exactly_where_commit_does(ledger, investment_ledger):
    """The discriminating half of (f). For (a) and (c) the review answer is
    None whether or not it snaps, so they cannot catch a review authority
    that forgot to snap. This row can: 500.000 * 29.9960 = 14,998.00 against
    an export Amount of 14,999. Unsnapped, gap 1.00 = stamp 0.75 + rounding
    0.25, clean. Snapped to 15,000, gap 2.00 = stamp 0.75 + 1.25 over the
    1-rupee band, so commit books 1.25 to the unexplained account and flags
    the row -- and review must say exactly that, before the write."""
    row = _mf("snap-f2", "14999", "500.000", "29.9960")
    preview = unexplained_charge_of(row, snap_cash=True)
    _commit(ledger, investment_ledger, row)

    assert preview == Decimal("1.25")
    assert row.unexplained_charge is True
    legs = _inr_legs(ledger, "snap-f2")
    assert legs[GROWW_CASH] == Decimal("-15000.00")
    assert legs["Expenses:Investment:Charges:Unexplained"] == preview


def test_stock_rows_are_never_snapped_by_the_review_authority():
    """Stock orders are out of scope: `_stock_entry` computes no charges and
    books the Amount verbatim, so the review authority must not snap them
    either. 14,999 would snap to 15,000 as an MF purchase; 500.000 * 29.9600
    = 14,980 makes the two answers differ (18.25 vs 19.25 unexplained)."""
    stock = _mf("snap-stock", "14999", "500.000", "29.9600")
    stock.source_account = GROWW_STOCK_CASH_ACCOUNT
    assert unexplained_charge_of(stock) == Decimal("18.25")
    assert unexplained_charge_of(stock, snap_cash=True) == Decimal("18.25")


def test_ledger_merge_entry_builders_do_not_snap_by_default():
    """The ledger-merge path (`ledger_merge_service.convert_batch_split`)
    calls `_vehicle_entry` directly with a cash figure taken from a journal
    posting, which is real cash, not a truncated export Amount. It must keep
    booking that figure verbatim."""
    c = _mf("merge", "14999", "512.000", "29.2945")
    entry = _vehicle_entry(c, cfg=MergeBookingConfig())
    cash = next(p for p in entry.postings if p.account == GROWW_CASH)
    assert cash.units.number == Decimal("-14999")
