from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount

from backend.app.persistence.journal import Ledger
from backend.app.persistence.nav_store import NavStore
from backend.app.models.domain.nav import NavRow
from backend.app.service.reports.investments.equity_holdings import equity_holdings


def _open_stock_accounts(ledger: Ledger, specs: list[tuple[str, str]]) -> None:
    """Write Stock commodity + open directives directly.

    Ledger.ensure_commodity_accounts hard-codes the mutual-fund account path
    (Assets:Investment:MutualFund:Groww:{symbol}) and will not open equity
    accounts. Writing these in the real journal's Stock form directly avoids
    that: without an explicit FIFO-booked open, append's _ensure_accounts_open
    would open the account with an INR constraint and beancount would reject
    the commodity postings.
    """
    main = ledger.main_file.read_text(encoding="utf-8")
    lines = []
    for symbol, name in specs:
        lines.append(f'2000-01-01 commodity {symbol}\n  name: "{name}"')
        lines.append(
            f'2000-01-01 open Assets:Investment:Stock:{symbol} {symbol} "FIFO"'
        )
    ledger.main_file.write_text(
        main.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )


def _buy(ledger: Ledger, symbol: str, qty: str, price: str, when: date) -> None:
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
            data.Posting(
                f"Assets:Investment:Stock:{symbol}",
                Amount(Decimal(qty), symbol),
                cost,
                None,
                None,
                None,
            ),
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


def _setup(tmp_path):
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    _open_stock_accounts(ledger, [("SBIN", "State Bank of India"), ("ONGC", "ONGC")])
    _buy(ledger, "SBIN", "100", "590.00", date(2024, 6, 18))
    _buy(ledger, "ONGC", "400", "237.50", date(2024, 6, 18))
    store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    store.append([NavRow("SBIN", Decimal("662.50"), date(2026, 9, 3))])
    return ledger, store


def test_values_holdings_at_market_and_computes_gain(tmp_path):
    ledger, store = _setup(tmp_path)
    out = equity_holdings(ledger, store)
    sbin = next(h for h in out["holdings"] if h["symbol"] == "SBIN")
    assert sbin["units"] == "100"
    assert sbin["price"] == "662.50"
    assert sbin["current_value"] == "66250.00"
    assert sbin["cost_basis"] == "59000.00"
    assert sbin["unrealized_gain"] == "7250.00"


def test_name_comes_from_commodity_metadata(tmp_path):
    ledger, store = _setup(tmp_path)
    sbin = next(
        h for h in equity_holdings(ledger, store)["holdings"] if h["symbol"] == "SBIN"
    )
    assert sbin["name"] == "State Bank of India"


def test_unpriced_holding_is_listed_separately_at_cost(tmp_path):
    ledger, store = _setup(tmp_path)
    out = equity_holdings(ledger, store)
    assert [u["symbol"] for u in out["unpriced"]] == ["ONGC"]
    assert out["unpriced"][0]["cost_basis"] == "95000.00"
    # An unpriced holding must never be counted as if it were valued.
    assert out["totals"]["current_value"] == "66250.00"


def test_totals_sum_only_priced_holdings(tmp_path):
    ledger, store = _setup(tmp_path)
    totals = equity_holdings(ledger, store)["totals"]
    assert totals["cost_basis"] == "59000.00"
    assert totals["unrealized_gain"] == "7250.00"


def test_as_of_is_the_latest_stored_price_date(tmp_path):
    ledger, store = _setup(tmp_path)
    assert equity_holdings(ledger, store)["as_of"] == "2026-09-03"


def test_empty_ledger_returns_zero_totals_not_an_error(tmp_path):
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    out = equity_holdings(ledger, store)
    assert out["holdings"] == []
    assert out["totals"]["current_value"] == "0.00"
    assert out["as_of"] is None
    assert out["totals"]["unpriced_count"] == 0
    assert out["totals"]["is_partial"] is False


def test_response_carries_mode_and_source_state(tmp_path):
    ledger, store = _setup(tmp_path)
    out = equity_holdings(ledger, store, today=date(2026, 9, 3))
    assert out["mode"] == "priced"
    assert out["source_state"] == "live"


def test_source_state_is_live_when_price_dated_today(tmp_path):
    ledger, store = _setup(tmp_path)
    out = equity_holdings(ledger, store, today=date(2026, 9, 3))
    assert out["source_state"] == "live"


def test_source_state_is_cached_within_the_stale_boundary(tmp_path):
    # The stored close is 3 days old -- inside the 7-day boundary that
    # separates a merely-behind refresh from an unmistakably stale one.
    ledger, store = _setup(tmp_path)
    out = equity_holdings(ledger, store, today=date(2026, 9, 6))
    assert out["source_state"] == "cached"


def test_source_state_is_stale_past_the_boundary(tmp_path):
    # A user refreshed in January and opens the screen in September: the
    # newest close is many months old and must never be presented as if it
    # were current.
    ledger, store = _setup(tmp_path)
    out = equity_holdings(ledger, store, today=date(2026, 9, 3) + timedelta(days=270))
    assert out["source_state"] == "stale"


def test_source_state_at_exactly_the_boundary_is_still_cached(tmp_path):
    ledger, store = _setup(tmp_path)
    out = equity_holdings(ledger, store, today=date(2026, 9, 3) + timedelta(days=7))
    assert out["source_state"] == "cached"


def test_source_state_one_day_past_the_boundary_is_stale(tmp_path):
    ledger, store = _setup(tmp_path)
    out = equity_holdings(ledger, store, today=date(2026, 9, 3) + timedelta(days=8))
    assert out["source_state"] == "stale"


def test_source_state_with_no_priced_holdings_is_stale_not_live(tmp_path):
    # No price exists for anything -- the most conservative state, never a
    # claim of freshness that isn't backed by any data.
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    out = equity_holdings(ledger, store, today=date(2026, 9, 6))
    assert out["as_of"] is None
    assert out["source_state"] == "stale"


def test_totals_signal_when_the_portfolio_is_only_partially_valued(tmp_path):
    # _setup holds one priced symbol (SBIN) and one unpriced symbol (ONGC).
    # A consumer that renders only `totals` must be able to tell, from
    # `totals` alone, that the portfolio is understated.
    ledger, store = _setup(tmp_path)
    totals = equity_holdings(ledger, store)["totals"]
    assert totals["unpriced_count"] == 1
    assert totals["is_partial"] is True
    # The numeric totals must still exclude the unpriced holding.
    assert totals["current_value"] == "66250.00"
    assert totals["cost_basis"] == "59000.00"


def _buy_costless(ledger: Ledger, symbol: str, qty: str, when: date) -> None:
    """Write an augmenting posting on a holding account with no cost spec,
    bypassing Ledger.append (which validates and would roll this back --
    beancount flags the resulting entry as not balancing). This simulates
    data this module cannot trust arriving in the journal by some path other
    than the FIFO-booked buys `_buy` produces, which is the scenario finding
    2 of the review asked to be handled explicitly rather than silently."""
    year = when.year
    ledger._ensure_include(year)
    year_file = ledger.journal_dir / f"{year}.beancount"
    with year_file.open("a", encoding="utf-8") as handle:
        handle.write(
            f'\n{when.isoformat()} * "buy {symbol} no cost"\n'
            f"  Assets:Investment:Stock:{symbol}  {qty} {symbol}\n"
            f"  Assets:Checking:HDFC  -1 INR\n"
        )


def test_costless_posting_is_routed_to_unpriced_not_silently_undervalued(tmp_path):
    ledger, store = _setup(tmp_path)
    # SBIN already has a normal, correctly-costed FIFO lot from `_setup` and a
    # market price in `store`. Add a second, costless lot on top of it.
    _buy_costless(ledger, "SBIN", "10", date(2024, 7, 1))

    out = equity_holdings(ledger, store)

    # SBIN must never be valued while any of its cost basis is unreliable,
    # even though a market price exists for it.
    assert "SBIN" not in {h["symbol"] for h in out["holdings"]}
    sbin_unpriced = next(u for u in out["unpriced"] if u["symbol"] == "SBIN")
    assert sbin_unpriced["units"] == "110"
    # ONGC (still fully unpriced, unaffected) stays listed too.
    assert {u["symbol"] for u in out["unpriced"]} == {"SBIN", "ONGC"}
    # Nothing from SBIN leaks into the valued totals.
    assert out["totals"]["current_value"] == "0.00"
    assert out["totals"]["unpriced_count"] == 2
    assert out["totals"]["is_partial"] is True
