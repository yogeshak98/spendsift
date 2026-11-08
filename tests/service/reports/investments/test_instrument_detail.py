"""Per-instrument detail read-model.

An unknown instrument must NOT raise: no read model under `service/reports/`
raises (raising coded errors such as E1034 is left to the layers above), so
this asserts `instrument_detail(...) is None` and the 404 belongs to the route.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.position import CostSpec

from backend.app.persistence.journal import Ledger
from backend.app.persistence.nav_store import NavStore
from backend.app.models.domain.nav import NavRow
from backend.app.service.reports.investments.instrument_detail import instrument_detail


def _open_commodity(ledger: Ledger, symbol: str, name: str, account: str) -> None:
    main = ledger.main_file.read_text(encoding="utf-8")
    lines = [
        f'2000-01-01 commodity {symbol}\n  name: "{name}"',
        f'2000-01-01 open {account} {symbol} "FIFO"',
    ]
    ledger.main_file.write_text(
        main.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )


def _buy(
    ledger: Ledger, account: str, symbol: str, qty: str, price: str, when: date
) -> None:
    cost = data.Cost(Decimal(price), "INR", when, None)
    txn = data.Transaction(
        data.new_metadata("<test>", 0),
        when,
        "*",
        None,
        f"buy {symbol}",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(account, Amount(Decimal(qty), symbol), cost, None, None, None),
            data.Posting(
                "Assets:Checking:HDFC",
                Amount(-Decimal(qty) * Decimal(price), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )
    ledger.append([txn])


@pytest.fixture
def detail_ledger(ledger: Ledger) -> Ledger:
    _open_commodity(
        ledger, "SBIN", "State Bank of India", "Assets:Investment:Stock:SBIN"
    )
    _buy(
        ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 1, 15),
    )
    _buy(
        ledger, "Assets:Investment:Stock:SBIN", "SBIN", "50", "700.00", date(2024, 6, 1)
    )
    store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    store.append([NavRow("SBIN", Decimal("662.50"), date(2026, 9, 4))])
    return ledger


def _sell_fifo(
    ledger: Ledger,
    account: str,
    symbol: str,
    qty: str,
    sale_price: str,
    consumed_cost: str,
    when: date,
) -> None:
    """Sell `qty` units with an empty CostSpec, the same shape
    `backend/app/service/ingest/pipeline/entry_writer.py` writes for a redemption -- beancount's own
    FIFO booking (declared by the account's "FIFO" open directive) resolves
    which held lot(s) this draws from when the ledger is (re)loaded, exactly
    as it does in production. `consumed_cost` must be precomputed by the
    caller to match what FIFO will actually consume, so the transaction
    balances; it is not asserted here, only spent on the P&L plug."""
    proceeds = Decimal(qty) * Decimal(sale_price)
    # Weight of the reduction leg is -consumed_cost; balancing requires
    # consumed_cost (equity weight) - proceeds (cash) + plug = 0, i.e.
    # plug = proceeds - consumed_cost... but a *gain* must be a credit
    # (negative) to Income, so the plug posted is the negative of that.
    plug = Decimal(consumed_cost) - proceeds
    txn = data.Transaction(
        data.new_metadata("<test>", 0),
        when,
        "*",
        None,
        f"sell {symbol}",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(
                account,
                Amount(-Decimal(qty), symbol),
                CostSpec(None, None, "INR", None, None, False),
                None,
                None,
                None,
            ),
            data.Posting(
                "Assets:Checking:HDFC", Amount(proceeds, "INR"), None, None, None, None
            ),
            data.Posting(
                "Income:CapitalGains:STCG",
                Amount(plug, "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )
    ledger.append([txn])


@pytest.fixture
def partial_sale_ledger(ledger: Ledger) -> Ledger:
    """Two lots at different prices, then a partial sale that consumes only
    part of the earlier lot. Chosen so FIFO and LIFO give different
    remaining cost bases:
      - buy 100 @ 590 on 2024-01-15, buy 50 @ 700 on 2024-06-01
      - sell 60 -- FIFO consumes 60 from the FIRST (590) lot, leaving
        40 @ 590 + 50 @ 700 = 23600 + 35000 = 58600 remaining cost basis.
      - LIFO would instead consume the 50 @ 700 lot fully, then 10 more from
        the 590 lot, leaving 90 @ 590 = 53100 -- a different number, so this
        test actually distinguishes the two orderings rather than agreeing
        by coincidence.
    The account is opened "FIFO", so beancount's own booking (not this test)
    performs the reduction when the ledger is loaded.
    """
    _open_commodity(
        ledger, "SBIN", "State Bank of India", "Assets:Investment:Stock:SBIN"
    )
    _buy(
        ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 1, 15),
    )
    _buy(
        ledger, "Assets:Investment:Stock:SBIN", "SBIN", "50", "700.00", date(2024, 6, 1)
    )
    _sell_fifo(
        ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "60",
        "650.00",
        "35400.00",  # 60 * 590, the cost FIFO actually consumes
        date(2024, 8, 1),
    )
    return ledger


@pytest.fixture
def bonus_only_ledger(ledger: Ledger) -> Ledger:
    """A bonus allotment: shares credited at zero acquisition cost -- a real,
    common scenario (bonus shares, some ESOP grants). cost_basis is
    genuinely zero, so gain_pct must be None, never a division blow-up."""
    _open_commodity(ledger, "FREE", "Bonus Co", "Assets:Investment:Stock:FREE")
    cost = data.Cost(Decimal("0"), "INR", date(2024, 3, 1), None)
    txn = data.Transaction(
        data.new_metadata("<test>", 0),
        date(2024, 3, 1),
        "*",
        None,
        "bonus allotment FREE",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(
                "Assets:Investment:Stock:FREE",
                Amount(Decimal("20"), "FREE"),
                cost,
                None,
                None,
                None,
            ),
            data.Posting(
                "Equity:Adjustments",
                Amount(Decimal("0"), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )
    ledger.append([txn])
    store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    store.append([NavRow("FREE", Decimal("15.00"), date(2026, 9, 4))])
    return ledger


@pytest.fixture
def unpriced_ledger(ledger: Ledger) -> Ledger:
    """A held instrument with no stored market close at all."""
    _open_commodity(
        ledger, "UTLSOLAR", "Utility Solar", "Assets:Investment:Stock:UTLSOLAR"
    )
    _buy(
        ledger,
        "Assets:Investment:Stock:UTLSOLAR",
        "UTLSOLAR",
        "30",
        "120.00",
        date(2024, 2, 10),
    )
    return ledger


def test_lots_are_listed_not_averaged(detail_ledger):
    d = instrument_detail(detail_ledger, "stock", "SBIN")
    assert [lot["units"] for lot in d["lots"]] == ["100", "50"]
    assert d["cost_basis"] == "94000.00"  # 59000 + 35000, not units * avg


def test_avg_cost_is_derived_from_real_lot_cost(detail_ledger):
    d = instrument_detail(detail_ledger, "stock", "SBIN")
    assert d["units"] == "150"
    assert d["avg_cost"] == "626.67"  # 94000 / 150


def test_lot_cost_and_long_term_on_are_per_lot(detail_ledger):
    d = instrument_detail(detail_ledger, "stock", "SBIN")
    first, second = d["lots"]
    assert first["date"] == "2024-01-15"
    assert first["cost"] == "590.00"
    assert first["cost_basis"] == "59000.00"
    assert first["long_term_on"] == "2025-01-15"
    assert second["date"] == "2024-06-01"
    assert second["cost"] == "700.00"
    assert second["long_term_on"] == "2025-06-01"


def test_valued_totals_and_source_state(detail_ledger):
    d = instrument_detail(detail_ledger, "stock", "SBIN", today=date(2026, 9, 4))
    assert d["mode"] == "priced"
    assert d["price"] == "662.50"
    assert d["price_date"] == "2026-09-04"
    assert d["current_value"] == "99375.00"  # 150 * 662.50
    assert d["unrealized_gain"] == "5375.00"
    assert d["gain_pct"] == "5.72"  # 5375 / 94000 * 100
    assert d["source_state"] == "live"
    assert d["name"] == "State Bank of India"


def test_gain_pct_is_null_for_zero_cost(bonus_only_ledger):
    d = instrument_detail(bonus_only_ledger, "stock", "FREE")
    assert d["cost_basis"] == "0.00"
    assert d["gain_pct"] is None
    # A bonus lot still has a real gain in rupees, just no meaningful ratio.
    assert d["unrealized_gain"] == "300.00"  # 20 * 15.00 - 0


def test_unpriced_instrument_reports_unvalued_not_cost(unpriced_ledger):
    d = instrument_detail(unpriced_ledger, "stock", "UTLSOLAR")
    assert d["mode"] == "unvalued"
    assert d["current_value"] is None
    assert d["price"] is None
    assert d["unrealized_gain"] is None
    assert d["gain_pct"] is None
    assert d["source_state"] == "stale"
    # Cost-side figures are still honest even though there's no market price.
    assert d["cost_basis"] == "3600.00"


def test_unknown_instrument_returns_none_not_a_fabricated_row(detail_ledger):
    # Per controller ruling, instrument_detail never raises -- an unknown
    # instrument (or one with no current holding) returns None so the route
    # layer (Task 4) can 404 with POSTING_NOT_FOUND (E1034).
    assert instrument_detail(detail_ledger, "stock", "NOPE") is None


def test_unknown_vehicle_key_returns_none(detail_ledger):
    assert instrument_detail(detail_ledger, "crypto", "SBIN") is None


def test_mutual_fund_lots_use_real_cost_never_averaged(ledger: Ledger):
    _open_commodity(
        ledger,
        "MF120503",
        "Sample Fund",
        "Assets:Investment:MutualFund:Groww:MF120503",
    )
    _buy(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF120503",
        "MF120503",
        "100",
        "200.00",
        date(2024, 7, 1),
    )
    _buy(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF120503",
        "MF120503",
        "50",
        "220.00",
        date(2025, 1, 1),
    )
    store = NavStore(ledger.storage_dir / "prices" / "nav_history.parquet")
    store.append([NavRow("120503", Decimal("250.00"), date(2026, 9, 4))])

    d = instrument_detail(ledger, "mutual_fund", "MF120503", today=date(2026, 9, 4))
    assert [lot["units"] for lot in d["lots"]] == ["100", "50"]
    assert d["cost_basis"] == "31000.00"  # 20000 + 11000
    assert d["units"] == "150"
    assert d["avg_cost"] == "206.67"
    assert d["mode"] == "priced"
    assert d["current_value"] == "37500.00"  # 150 * 250.00
    assert d["name"] == "Sample Fund"


def test_partial_sale_leaves_earliest_lot_drawn_down_fifo(partial_sale_ledger):
    # See partial_sale_ledger's docstring for the FIFO (58600) vs LIFO (53100)
    # figures this is designed to distinguish -- beancount's own FIFO
    # booking (declared on the account's open directive) resolves the sale,
    # this only asserts what it produced.
    d = instrument_detail(partial_sale_ledger, "stock", "SBIN")
    assert len(d["lots"]) == 2
    first, second = d["lots"]
    # The earliest lot is drawn down: 100 - 60 = 40 remain at its original
    # per-unit cost and date -- untouched by the sale price or gain booked.
    assert first["date"] == "2024-01-15"
    assert first["units"] == "40"
    assert first["cost"] == "590.00"
    # The later lot is completely untouched by the sale.
    assert second["date"] == "2024-06-01"
    assert second["units"] == "50"
    assert second["cost"] == "700.00"
    assert d["units"] == "90"  # 150 bought - 60 sold
    # Real remaining cost basis, not units * original avg_cost.
    assert d["cost_basis"] == "58600.00"  # 40*590 + 50*700 -- the FIFO figure
    # avg_cost is recomputed from what's actually left, not the original buys.
    assert d["avg_cost"] == "651.11"  # 58600 / 90


def test_long_term_on_leap_day_purchase(detail_ledger):
    # Pinning dateutil's relativedelta(months=12) behaviour at a leap-day
    # boundary, per controller instruction -- not fixing anything, just
    # recording what it already does. 2024 is a leap year, 2025 is not, so
    # Feb 29 has no exact same-day-next-year counterpart.
    _buy(
        detail_ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "10",
        "600.00",
        date(2024, 2, 29),
    )
    d = instrument_detail(detail_ledger, "stock", "SBIN")
    leap_lot = next(lot for lot in d["lots"] if lot["date"] == "2024-02-29")
    assert leap_lot["long_term_on"] == "2025-02-28"


def test_long_term_on_month_end_purchase(detail_ledger):
    # Same pin, for a plain (non-leap) Feb 28 purchase -- the mirror case
    # crossing back into a leap year.
    _buy(
        detail_ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "10",
        "600.00",
        date(2025, 2, 28),
    )
    d = instrument_detail(detail_ledger, "stock", "SBIN")
    lot = next(lot for lot in d["lots"] if lot["date"] == "2025-02-28")
    assert lot["long_term_on"] == "2026-02-28"
