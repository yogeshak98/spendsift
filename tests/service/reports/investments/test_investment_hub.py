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
from backend.app.service.reports.investments.investment_hub import (
    at_cost_holdings,
    investment_hub,
)


def _open_accounts(ledger: Ledger, lines: list[str]) -> None:
    """Write commodity/open directives directly.

    `Ledger.ensure_commodity_accounts` hard-codes the mutual-fund account path
    (Assets:Investment:MutualFund:Groww:{symbol}) and cannot open equity or FD
    accounts, so every vehicle here is opened by hand.
    """
    main = ledger.main_file.read_text(encoding="utf-8")
    ledger.main_file.write_text(
        main.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )


def _buy_commodity(
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


def _sell_fifo(
    ledger: Ledger,
    account: str,
    symbol: str,
    qty: str,
    sale_price: str,
    consumed_cost: str,
    when: date,
) -> None:
    """Sell `qty` units with an empty CostSpec -- the same shape
    `backend/app/service/ingest/pipeline/entry_writer.py` writes for a redemption, and beancount's own
    FIFO booking resolves against whatever lot(s) are held. Mirrors
    `tests/service/reports/investments/test_instrument_detail.py`'s helper of the same name.
    `consumed_cost` must be precomputed by the caller to match what FIFO will
    actually consume, so the transaction balances."""
    proceeds = Decimal(qty) * Decimal(sale_price)
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


def _deposit(ledger: Ledger, account: str, amount: str, when: date) -> None:
    txn = data.Transaction(
        data.new_metadata("<test>", 0),
        when,
        "*",
        None,
        "FD deposit",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(
                account, Amount(Decimal(amount), "INR"), None, None, None, None
            ),
            data.Posting(
                "Assets:Checking:HDFC",
                Amount(-Decimal(amount), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )
    ledger.append([txn])


@pytest.fixture
def hub_ledger(ledger: Ledger) -> Ledger:
    _open_accounts(
        ledger,
        [
            '2000-01-01 commodity SBIN\n  name: "State Bank of India"',
            '2000-01-01 open Assets:Investment:Stock:SBIN SBIN "FIFO"',
            "2000-01-01 open Assets:Investment:FD:HDFC INR",
            '2000-01-01 commodity MF120503\n  name: "Sample Fund"',
            '2000-01-01 open Assets:Investment:MutualFund:Groww:MF120503 MF120503 "FIFO"',
        ],
    )
    _buy_commodity(
        ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 6, 18),
    )
    _buy_commodity(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF120503",
        "MF120503",
        "100",
        "200.00",
        date(2024, 7, 1),
    )
    _deposit(ledger, "Assets:Investment:FD:HDFC", "750000.00", date(2024, 1, 1))

    equity_store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    equity_store.append([NavRow("SBIN", Decimal("662.50"), date(2026, 9, 4))])
    mf_store = NavStore(ledger.storage_dir / "prices" / "nav_history.parquet")
    mf_store.append([NavRow("120503", Decimal("250.00"), date(2026, 9, 1))])
    return ledger


@pytest.fixture
def all_priced_ledger(ledger: Ledger) -> Ledger:
    _open_accounts(
        ledger,
        [
            '2000-01-01 commodity SBIN\n  name: "State Bank of India"',
            '2000-01-01 open Assets:Investment:Stock:SBIN SBIN "FIFO"',
            '2000-01-01 commodity MF120503\n  name: "Sample Fund"',
            '2000-01-01 open Assets:Investment:MutualFund:Groww:MF120503 MF120503 "FIFO"',
        ],
    )
    _buy_commodity(
        ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 6, 18),
    )
    _buy_commodity(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF120503",
        "MF120503",
        "100",
        "200.00",
        date(2024, 7, 1),
    )

    equity_store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    equity_store.append([NavRow("SBIN", Decimal("662.50"), date(2026, 9, 4))])
    mf_store = NavStore(ledger.storage_dir / "prices" / "nav_history.parquet")
    mf_store.append([NavRow("120503", Decimal("250.00"), date(2026, 9, 1))])
    return ledger


@pytest.fixture
def unresolvable_mf_ledger(ledger: Ledger) -> Ledger:
    """MF per-scheme account is open, but this ledger holds no matching
    postings for it -- the real-journal shape where MF lots live only in a
    separate investment_ledger file this ledger knows nothing about. Stock
    is priced normally so the ledger isn't otherwise empty."""
    _open_accounts(
        ledger,
        [
            '2000-01-01 commodity SBIN\n  name: "State Bank of India"',
            '2000-01-01 open Assets:Investment:Stock:SBIN SBIN "FIFO"',
            '2000-01-01 commodity MF999999\n  name: "Elsewhere Fund"',
            '2000-01-01 open Assets:Investment:MutualFund:Groww:MF999999 MF999999 "FIFO"',
        ],
    )
    _buy_commodity(
        ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 6, 18),
    )
    equity_store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    equity_store.append([NavRow("SBIN", Decimal("662.50"), date(2026, 9, 4))])
    return ledger


@pytest.fixture
def coarse_only_mf_ledger(ledger: Ledger) -> Ledger:
    """The real production shape after the MutualFund detach: main.beancount
    opens ONLY the coarse `Assets:Investment:MutualFund` account (its
    long-standing clearing/rollup account) and never opens or posts to a
    single per-scheme Groww:<scheme> account -- those are opened and posted
    only in the separate investment_ledger file (`ensure_commodity_accounts`
    writes them there, not here). This is distinct from `unresolvable_mf_ledger`
    above, which opens the per-scheme account in THIS ledger with no
    postings -- a synthetic shape the earlier `_mf_open_symbol_accounts`
    guard happened to catch but the real, coarse-only shape did not."""
    _open_accounts(
        ledger,
        [
            '2000-01-01 commodity SBIN\n  name: "State Bank of India"',
            '2000-01-01 open Assets:Investment:Stock:SBIN SBIN "FIFO"',
            "2000-01-01 open Assets:Investment:MutualFund INR",
        ],
    )
    _buy_commodity(
        ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 6, 18),
    )
    equity_store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    equity_store.append([NavRow("SBIN", Decimal("662.50"), date(2026, 9, 4))])
    return ledger


@pytest.fixture
def fully_redeemed_mf_ledger(ledger: Ledger) -> Ledger:
    """A mutual fund bought and then fully sold: real postings exist (unlike
    `unresolvable_mf_ledger`/`coarse_only_mf_ledger`, where NO posting for the
    vehicle exists at all) but net to zero units held. This must NOT be
    treated the same as "no data for this vehicle" -- the widened
    `not saw_any_posting` guard (finding 2) must key on the absence of ANY
    posting, not on a net-zero position, or a genuinely exited fund would
    wrongly vanish from the hub instead of reporting an honest priced ₹0."""
    _open_accounts(
        ledger,
        [
            '2000-01-01 commodity MF120503\n  name: "Sample Fund"',
            '2000-01-01 open Assets:Investment:MutualFund:Groww:MF120503 MF120503 "FIFO"',
        ],
    )
    _buy_commodity(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF120503",
        "MF120503",
        "100",
        "200.00",
        date(2024, 7, 1),
    )
    _sell_fifo(
        ledger,
        "Assets:Investment:MutualFund:Groww:MF120503",
        "MF120503",
        "100",
        "250.00",
        "20000.00",
        date(2025, 7, 1),
    )
    return ledger


@pytest.fixture
def mixed_priced_stock_ledger(ledger: Ledger) -> Ledger:
    """Two stock holdings: SBIN priced, ONGC with no stored close at all."""
    _open_accounts(
        ledger,
        [
            '2000-01-01 commodity SBIN\n  name: "State Bank of India"',
            '2000-01-01 open Assets:Investment:Stock:SBIN SBIN "FIFO"',
            '2000-01-01 commodity ONGC\n  name: "ONGC"',
            '2000-01-01 open Assets:Investment:Stock:ONGC ONGC "FIFO"',
        ],
    )
    _buy_commodity(
        ledger,
        "Assets:Investment:Stock:SBIN",
        "SBIN",
        "100",
        "590.00",
        date(2024, 6, 18),
    )
    _buy_commodity(
        ledger,
        "Assets:Investment:Stock:ONGC",
        "ONGC",
        "400",
        "237.50",
        date(2024, 6, 18),
    )
    equity_store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    equity_store.append([NavRow("SBIN", Decimal("662.50"), date(2026, 9, 4))])
    return ledger


@pytest.fixture
def all_unvalued_ledger(ledger: Ledger) -> Ledger:
    """Only an FD -- no vehicle in this ledger has any valuation source."""
    _open_accounts(ledger, ["2000-01-01 open Assets:Investment:FD:HDFC INR"])
    _deposit(ledger, "Assets:Investment:FD:HDFC", "750000.00", date(2024, 1, 1))
    return ledger


def test_valued_and_unvalued_vehicles_both_appear(hub_ledger):
    out = investment_hub(hub_ledger)
    keys = {v["key"] for v in out["vehicles"]}
    assert {"mutual_fund", "stock", "fd"} <= keys


def test_unvalued_vehicle_has_null_value_not_cost(hub_ledger):
    fd = next(v for v in investment_hub(hub_ledger)["vehicles"] if v["key"] == "fd")
    assert fd["mode"] == "unvalued"
    assert fd["current_value"] is None
    assert fd["unrealized_gain"] is None
    assert fd["cost_basis"] == "750000.00"


def test_totals_exclude_unvalued_and_say_so(hub_ledger):
    t = investment_hub(hub_ledger)["totals"]
    # cost = 59000 (stock) + 20000 (MF) + 750000 (FD, unvalued) = 829000.
    # value = 66250 (stock) + 25000 (MF) ONLY -- FD's 750000 excluded.
    # gain = 7250 (stock) + 5000 (MF) -- FD contributes nothing either way.
    assert t["cost_basis"] == "829000.00"
    assert t["current_value"] == "91250.00"
    assert t["unrealized_gain"] == "12250.00"
    assert t["unvalued_count"] == 1
    assert t["is_partial"] is True


def test_all_valued_reports_complete(all_priced_ledger):
    t = investment_hub(all_priced_ledger)["totals"]
    assert t["unvalued_count"] == 0
    assert t["is_partial"] is False


def test_as_of_is_the_newest_price_date(hub_ledger):
    assert investment_hub(hub_ledger)["as_of"] == "2026-09-04"


def test_money_fields_are_strings_not_floats(hub_ledger):
    v = investment_hub(hub_ledger)["vehicles"][0]
    assert isinstance(v["cost_basis"], str)


def test_stock_vehicle_values_match_equity_holdings(hub_ledger):
    stock = next(
        v for v in investment_hub(hub_ledger)["vehicles"] if v["key"] == "stock"
    )
    assert stock["mode"] == "priced"
    assert stock["cost_basis"] == "59000.00"
    assert stock["current_value"] == "66250.00"
    assert stock["unrealized_gain"] == "7250.00"
    assert stock["holdings"] == 1


def test_mutual_fund_vehicle_values(hub_ledger):
    mf = next(
        v for v in investment_hub(hub_ledger)["vehicles"] if v["key"] == "mutual_fund"
    )
    assert mf["mode"] == "priced"
    assert mf["cost_basis"] == "20000.00"
    assert mf["current_value"] == "25000.00"
    assert mf["unrealized_gain"] == "5000.00"
    assert mf["holdings"] == 1


def test_empty_ledger_returns_no_vehicles(ledger):
    out = investment_hub(ledger)
    assert out["vehicles"] == []
    assert out["as_of"] is None
    assert out["totals"]["current_value"] == "0.00"
    assert out["totals"]["cost_basis"] == "0.00"
    assert out["totals"]["unvalued_count"] == 0
    assert out["totals"]["is_partial"] is False


def test_source_state_reflects_the_stalest_holding_not_the_newest(hub_ledger):
    """`hub_ledger` prices SBIN on 2026-09-04 and MF120503 on 2026-09-01. At
    today=2026-09-04 the newest date is 0 days old ("live") and the oldest is
    3 days old ("cached"). The headline must report the OLDEST -- calling the
    hub "live" would assert a freshness the MF leg cannot support."""
    out = investment_hub(hub_ledger, today=date(2026, 9, 4))
    assert out["as_of"] == "2026-09-04"
    assert out["stalest_as_of"] == "2026-09-01"
    assert out["source_state"] == "cached"


def test_unresolvable_mf_vehicle_is_excluded_not_a_priced_zero(unresolvable_mf_ledger):
    """Finding #1: MF accounts are open but this ledger has no postings for
    them (they live in a separate ledger this call was never given). The
    vehicle must NOT appear as a confident, complete zero -- it must be
    dropped, and the hub must say the total is incomplete because of it."""
    out = investment_hub(unresolvable_mf_ledger)
    keys = {v["key"] for v in out["vehicles"]}
    assert "mutual_fund" not in keys
    # Stock is still fully resolved and must not be marked incomplete itself.
    stock = next(v for v in out["vehicles"] if v["key"] == "stock")
    assert stock["is_partial"] is False
    assert out["totals"]["unvalued_count"] == 1
    assert out["totals"]["is_partial"] is True
    # The one real, resolvable holding is not hidden by the exclusion.
    assert out["totals"]["current_value"] == "66250.00"


def test_coarse_only_mf_vehicle_is_excluded_not_a_fabricated_priced_zero(
    coarse_only_mf_ledger,
):
    """Regression: the coarse-only production shape (only
    `Assets:Investment:MutualFund` open, no per-scheme Groww account open at
    all in this ledger) must be excluded exactly like the synthetic
    per-scheme-open-but-unposted shape -- not silently reported as a
    confident, complete ₹0 mutual_fund vehicle."""
    out = investment_hub(coarse_only_mf_ledger)
    keys = {v["key"] for v in out["vehicles"]}
    assert "mutual_fund" not in keys
    stock = next(v for v in out["vehicles"] if v["key"] == "stock")
    assert stock["is_partial"] is False
    assert out["totals"]["unvalued_count"] == 1
    assert out["totals"]["is_partial"] is True
    assert out["totals"]["current_value"] == "66250.00"


def test_fully_redeemed_mf_vehicle_reports_honest_zero_not_excluded(
    fully_redeemed_mf_ledger,
):
    """Guard against a future "simplification" that treats zero-value the
    same as no-data: a fund with a real buy and a matching full sell has
    postings (`saw_any_posting` is True) even though it nets to zero units,
    so it must still appear in `vehicles` as a priced, complete ₹0 line --
    NOT be excluded like `unresolvable_mf_ledger`/`coarse_only_mf_ledger`,
    which have no postings for the vehicle at all."""
    out = investment_hub(fully_redeemed_mf_ledger)
    mf = next((v for v in out["vehicles"] if v["key"] == "mutual_fund"), None)
    assert mf is not None
    assert mf == {
        "key": "mutual_fund",
        "label": "Mutual Fund",
        "holdings": 0,
        "cost_basis": "0.00",
        "current_value": "0.00",
        "unrealized_gain": "0.00",
        "mode": "priced",
        "as_of": None,
        "stalest_as_of": None,
        "is_partial": False,
    }
    assert out["totals"]["unvalued_count"] == 0
    assert out["totals"]["is_partial"] is False


def test_stock_vehicle_propagates_equity_holdings_partial_signal(
    mixed_priced_stock_ledger,
):
    """Finding #2: ONGC has no stored close, so equity_holdings already
    excludes its cost from `totals`. The vehicle (and the hub) must say so
    rather than reporting `mode: priced` with no partial signal."""
    out = investment_hub(mixed_priced_stock_ledger)
    stock = next(v for v in out["vehicles"] if v["key"] == "stock")
    assert stock["mode"] == "priced"
    assert stock["is_partial"] is True
    assert stock["holdings"] == 2  # SBIN priced + ONGC unpriced, both counted
    # No vehicle is wholly unvalued here -- the partial signal comes solely
    # from within the (still nominally "priced") stock vehicle.
    assert out["totals"]["unvalued_count"] == 0
    assert out["totals"]["is_partial"] is True


def test_all_unvalued_ledger_reports_zero_value_and_partial(all_unvalued_ledger):
    """Finding #3: a ledger with only unvalued vehicles must report a
    complete, honest picture of "we have no valuation for anything" -- zero
    valued current_value, is_partial True, no as_of, and the most
    conservative source_state."""
    out = investment_hub(all_unvalued_ledger)
    assert out["as_of"] is None
    assert out["source_state"] == "stale"
    assert out["totals"]["current_value"] == "0.00"
    assert out["totals"]["cost_basis"] == "750000.00"
    assert out["totals"]["unvalued_count"] == 1
    assert out["totals"]["is_partial"] is True


# ── at_cost_holdings: the drill-down behind an unvalued vehicle card ────────


def test_at_cost_holdings_reports_accounts_and_total(all_unvalued_ledger):
    out = at_cost_holdings(all_unvalued_ledger, "fd")
    assert out == {
        "key": "fd",
        "label": "FD",
        "mode": "unvalued",
        "as_of": None,
        "totals": {"cost_basis": "750000.00"},
        "holdings": [
            {
                "account": "Assets:Investment:FD:HDFC",
                "account_display": "Assets:Investment:FD:HDFC",
                "name": "HDFC",
                "cost_basis": "750000.00",
            }
        ],
    }


def test_at_cost_holdings_money_fields_are_strings(all_unvalued_ledger):
    out = at_cost_holdings(all_unvalued_ledger, "fd")
    assert isinstance(out["totals"]["cost_basis"], str)
    assert all(isinstance(h["cost_basis"], str) for h in out["holdings"])


def test_at_cost_holdings_agrees_with_the_hub_row(hub_ledger):
    """One walk, two views: the hub's FD row and this drill-down must never
    disagree about how many holdings there are or what they cost."""
    row = next(v for v in investment_hub(hub_ledger)["vehicles"] if v["key"] == "fd")
    out = at_cost_holdings(hub_ledger, "fd")
    assert out["totals"]["cost_basis"] == row["cost_basis"]
    assert len(out["holdings"]) == row["holdings"]


def test_at_cost_holdings_unknown_vehicle_is_none_not_empty(all_unvalued_ledger):
    """None, so the caller can 404. An empty holdings list would be
    indistinguishable from a real vehicle in which nothing is held."""
    assert at_cost_holdings(all_unvalued_ledger, "real_estate") is None


def test_at_cost_holdings_refuses_a_valued_vehicle(hub_ledger):
    assert at_cost_holdings(hub_ledger, "stock") is None
    assert at_cost_holdings(hub_ledger, "mutual_fund") is None


def test_at_cost_holdings_names_a_bare_vehicle_account_for_its_vehicle(ledger):
    """A posting made directly on the vehicle account has no remainder below
    the prefix; it must still be named, never rendered as a blank cell."""
    _open_accounts(ledger, ["2000-01-01 open Assets:Investment:IndWealth INR"])
    _deposit(ledger, "Assets:Investment:IndWealth", "1732.00", date(2025, 3, 1))
    out = at_cost_holdings(ledger, "ind_wealth")
    assert out["holdings"] == [
        {
            "account": "Assets:Investment:IndWealth",
            "account_display": "Assets:Investment:IndWealth",
            "name": "Ind Wealth",
            "cost_basis": "1732.00",
        }
    ]


def test_at_cost_holdings_resolves_a_trailing_scheme_code(ledger):
    """`models/domain/accounts.py:investment()` sanctions
    `Assets:Investment:<vehicle>:<issuer>:<instrument>` for ANY vehicle, and
    `_unvalued_balances` filters posting CURRENCY, not account naming -- so a
    trailing commodity code is possible under an at-cost vehicle. It must be
    resolved through the repo-wide display-account contract, in
    `account_display` AND in the `name` the UI renders, exactly as
    reports/assets.py does for the same paths."""
    _open_accounts(
        ledger,
        [
            '2000-01-01 commodity MF120503\n  name: "Sample Fund"',
            "2000-01-01 open Assets:Investment:IndWealth:Managed:MF120503 INR",
        ],
    )
    _deposit(
        ledger,
        "Assets:Investment:IndWealth:Managed:MF120503",
        "1732.00",
        date(2025, 3, 1),
    )
    out = at_cost_holdings(ledger, "ind_wealth")
    assert out["holdings"] == [
        {
            "account": "Assets:Investment:IndWealth:Managed:MF120503",
            "account_display": "Assets:Investment:IndWealth:Managed:Sample Fund",
            # Derived from the RESOLVED path -- never the raw code.
            "name": "Managed · Sample Fund",
            "cost_basis": "1732.00",
        }
    ]


def test_at_cost_holding_names_do_not_split_a_digit_from_an_uppercase_letter(ledger):
    """A tenor-suffixed fixed deposit ("HDFC5Y") is one name, not "HDFC5 Y".
    The camel-hump rule must break on lower-to-upper only."""
    _open_accounts(ledger, ["2000-01-01 open Assets:Investment:FD:HDFC5Y INR"])
    _deposit(ledger, "Assets:Investment:FD:HDFC5Y", "100000.00", date(2025, 3, 1))
    out = at_cost_holdings(ledger, "fd")
    assert [h["name"] for h in out["holdings"]] == ["HDFC5Y"]


def test_camel_hump_label_and_key_still_split_real_words(ledger):
    """The digit fix must not cost the genuine word break it exists for."""
    _open_accounts(ledger, ["2000-01-01 open Assets:Investment:IndWealth:Managed INR"])
    _deposit(ledger, "Assets:Investment:IndWealth:Managed", "10.00", date(2025, 3, 1))
    out = at_cost_holdings(ledger, "ind_wealth")
    assert out["label"] == "Ind Wealth"
    assert out["key"] == "ind_wealth"
