from __future__ import annotations

from datetime import date as _d
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.position import Cost, CostSpec

from backend.app.core.errors.errors import CodedError
from backend.app.persistence.journal import Ledger
from backend.app.models.domain.nav import NavRow
from backend.app.service.investments.service import InvestmentService

_NAMES = {
    "MF111": "Held Priced Fund",
    "MF222": "Exited Fund",
    "MF999": "Unpriced Held Fund",
}


class StubNavStore:
    """Duck-typed NavStore: returns canned NavRows keyed by bare code."""

    def __init__(self, navs: dict[str, NavRow], as_of: _d | None) -> None:
        self._navs = navs
        self._as_of = as_of

    def latest(self, codes: set[str]) -> dict[str, NavRow]:
        return {c: self._navs[c] for c in codes if c in self._navs}

    def as_of(self) -> _d | None:
        return self._as_of


def _buy(
    ledger: Ledger, symbol: str, units: str, unit_cost: str, amount: str, day: _d
) -> None:
    meta = data.new_metadata("<t>", 0)
    postings = [
        data.Posting(
            f"Assets:Investment:MutualFund:Groww:{symbol}",
            Amount(Decimal(units), symbol),
            Cost(Decimal(unit_cost), "INR", day, None),
            None,
            None,
            None,
        ),
        data.Posting(
            "Assets:Investment:MutualFund:Groww:Cash",
            Amount(-Decimal(amount), "INR"),
            None,
            None,
            None,
            None,
        ),
    ]
    ledger.append(
        [
            data.Transaction(
                meta,
                day,
                "*",
                None,
                f"buy {symbol}",
                data.EMPTY_SET,
                data.EMPTY_SET,
                postings,
            )
        ]
    )


def _sell_all(
    ledger: Ledger, symbol: str, units: str, sale_unit: str, amount: str, day: _d
) -> None:
    meta = data.new_metadata("<t>", 0)
    postings = [
        data.Posting(
            f"Assets:Investment:MutualFund:Groww:{symbol}",
            Amount(-Decimal(units), symbol),
            CostSpec(None, None, "INR", None, None, False),
            Amount(Decimal(sale_unit), "INR"),
            None,
            None,
        ),
        data.Posting(
            "Assets:Investment:MutualFund:Groww:Cash",
            Amount(Decimal(amount), "INR"),
            None,
            None,
            None,
            None,
        ),
        data.Posting("Income:Investment:CapitalGains", None, None, None, None, None),
    ]
    ledger.append(
        [
            data.Transaction(
                meta,
                day,
                "*",
                None,
                f"sell {symbol}",
                data.EMPTY_SET,
                data.EMPTY_SET,
                postings,
            )
        ]
    )


def _ledger(tmp_path: Path) -> Ledger:
    led = Ledger(tmp_path / "storage")
    led.bootstrap()
    led.ensure_commodity_accounts([(s, _NAMES[s]) for s in _NAMES])
    # Held + priced: buy 100 @100 (cost 10000).
    _buy(led, "MF111", "100", "100", "10000", _d(2024, 6, 29))
    # Held + unpriced: buy 10 @100 (cost 1000).
    _buy(led, "MF999", "10", "100", "1000", _d(2024, 6, 29))
    # Exited: buy 50 @100 (5000) then sell all 50 @130 (6500).
    _buy(led, "MF222", "50", "100", "5000", _d(2023, 6, 29))
    _sell_all(led, "MF222", "50", "130", "6500", _d(2025, 6, 29))
    return led


def test_gains_assembles_held_exited_and_unpriced(tmp_path):
    led = _ledger(tmp_path)
    nav = StubNavStore(
        {"111": NavRow(scheme_code="111", nav=Decimal("130"), date=_d(2026, 6, 29))},
        as_of=_d(2026, 6, 29),
    )
    svc = InvestmentService(led, nav)
    out = svc.gains()

    assert out["as_of"] == "2026-06-29"
    # Held + priced fund appears in holdings.
    syms = [h["symbol"] for h in out["holdings"]]
    assert syms == ["MF111"]
    h = out["holdings"][0]
    assert h["name"] == "Held Priced Fund"
    assert h["current_value"] == "13000.00"
    assert h["cost_basis"] == "10000.00"
    assert h["unrealized_gain"] == "3000.00"
    # Unpriced held fund quarantined.
    assert [u["symbol"] for u in out["unpriced"]] == ["MF999"]
    assert out["unpriced_count"] == 1
    # Exited fund appears in NEITHER holdings nor unpriced.
    assert "MF222" not in syms
    assert "MF222" not in [u["symbol"] for u in out["unpriced"]]
    # Exited flows still feed portfolio lifetime XIRR (defined).
    assert out["totals"]["lifetime_xirr"] is not None
    # All money fields are strings (or None).
    for key in ("current_value", "cost_basis", "unrealized_gain"):
        assert isinstance(out["totals"][key], str)
    assert h["current_xirr"] is None or isinstance(h["current_xirr"], str)


def test_exited_flows_move_lifetime_xirr(tmp_path):
    """Dropping the exited fund's cashflows must produce a different lifetime_xirr.

    MF111 alone gives one XIRR; with MF222's −5000/+6500 flows added the
    portfolio lifetime XIRR must change.  If exited flows were silently dropped,
    both runs would return the same value and the assertion would fail.
    """
    nav = StubNavStore(
        {"111": NavRow(scheme_code="111", nav=Decimal("130"), date=_d(2026, 6, 29))},
        as_of=_d(2026, 6, 29),
    )

    # WITH exited fund: uses the shared _ledger fixture (MF111 + MF999 + MF222).
    led_with = _ledger(tmp_path / "with")
    xirr_with = InvestmentService(led_with, nav).gains()["totals"]["lifetime_xirr"]

    # WITHOUT exited fund: identical ledger except MF222 buy/sell are absent.
    led_without = Ledger(tmp_path / "without" / "storage")
    led_without.bootstrap()
    led_without.ensure_commodity_accounts([(s, _NAMES[s]) for s in _NAMES])
    _buy(led_without, "MF111", "100", "100", "10000", _d(2024, 6, 29))
    _buy(led_without, "MF999", "10", "100", "1000", _d(2024, 6, 29))
    # MF222 intentionally omitted.
    xirr_without = InvestmentService(led_without, nav).gains()["totals"][
        "lifetime_xirr"
    ]

    # Both must be defined (not None) for the comparison to be meaningful.
    assert xirr_with is not None, "lifetime_xirr should be defined with exited flows"
    assert (
        xirr_without is not None
    ), "lifetime_xirr should be defined without exited flows"
    # The presence of MF222's flows must change the portfolio XIRR.
    assert xirr_with != xirr_without, (
        f"exited fund flows did not affect lifetime_xirr "
        f"(with={xirr_with}, without={xirr_without})"
    )


def test_dust_units_below_threshold_excluded_from_holdings(tmp_path):
    """A near-full redemption leaving < 0.1 units (rounding residue from a
    broker's fractional-unit redemption) must not surface as a "held" fund —
    it should be treated like a full exit: feeds lifetime XIRR only, absent
    from both holdings and unpriced.
    """
    led = Ledger(tmp_path / "storage")
    led.bootstrap()
    led.ensure_commodity_accounts([("MF111", "Held Priced Fund")])
    _buy(led, "MF111", "100", "100", "10000", _d(2024, 6, 29))
    # Redeem all but 0.05 units — dust below the 0.1 threshold.
    _sell_all(led, "MF111", "99.95", "130", "12993.5", _d(2025, 6, 29))

    nav = StubNavStore(
        {"111": NavRow(scheme_code="111", nav=Decimal("130"), date=_d(2026, 6, 29))},
        as_of=_d(2026, 6, 29),
    )
    svc = InvestmentService(led, nav)
    out = svc.gains()

    assert out["holdings"] == []
    assert out["unpriced"] == []
    assert out["unpriced_count"] == 0
    assert out["totals"]["lifetime_xirr"] is not None


def test_gains_empty_ledger(tmp_path):
    led = Ledger(tmp_path / "storage")
    led.bootstrap()
    nav = StubNavStore({}, as_of=None)
    svc = InvestmentService(led, nav)
    out = svc.gains()
    assert out["holdings"] == []
    assert out["unpriced"] == []
    assert out["unpriced_count"] == 0
    assert out["as_of"] is None
    assert out["totals"]["current_value"] == "0.00"


def test_gains_negative_units_raises_e1016(tmp_path):
    # Force an inconsistent ledger: redeem more than bought is blocked by commit,
    # so post a raw oversell directly via append-bypassing inventory check is not
    # possible (beancount rejects). Instead, drive the guard with a monkeypatched
    # extractor that yields negative net units.
    led = Ledger(tmp_path / "storage")
    led.bootstrap()
    nav = StubNavStore({}, as_of=None)
    svc = InvestmentService(led, nav)
    svc._held_units = lambda _entries: {"MF111": Decimal("-5")}  # type: ignore[method-assign]
    with pytest.raises(CodedError) as ei:
        svc.gains()
    assert ei.value.code == "E1016"


def test_partial_redemption_surviving_holding_reflects_unredeemed_lots(tmp_path):
    """FIFO partial redemption seam: buy two lots, sell less than the first lot.

    After the partial sell the surviving holding should reflect only the
    unredeemed units and its cost basis.  Current XIRR (derived from surviving
    lots only) must differ from Lifetime XIRR (full buy+sell history), confirming
    that the service correctly segregates current vs lifetime cashflows.
    """
    led = Ledger(tmp_path / "storage")
    led.bootstrap()
    led.ensure_commodity_accounts([("MF111", "Held Priced Fund")])

    # Lot 1: buy 100 units @100 on 2023-06-29 (cost 10000, cash outflow -10000).
    _buy(led, "MF111", "100", "100", "10000", _d(2023, 6, 29))
    # Lot 2: buy 100 units @120 on 2024-06-29 (cost 12000, cash outflow -12000).
    _buy(led, "MF111", "100", "120", "12000", _d(2024, 6, 29))
    # Partial sell: redeem 60 units @150 on 2025-06-29 (proceeds 9000, cash inflow +9000).
    # Uses _sell_all helper shape but with partial units — same CostSpec reduces FIFO
    # inventory by 60 (taken from lot 1 first by beancount's FIFO reduction).
    _sell_all(led, "MF111", "60", "150", "9000", _d(2025, 6, 29))

    # Surviving: 140 units (40 from lot 1 @100 + 100 from lot 2 @120).
    # Cost basis = 40*100 + 100*120 = 4000 + 12000 = 16000.
    nav = StubNavStore(
        {"111": NavRow(scheme_code="111", nav=Decimal("200"), date=_d(2026, 6, 29))},
        as_of=_d(2026, 6, 29),
    )
    svc = InvestmentService(led, nav)
    out = svc.gains()

    holdings = out["holdings"]
    assert len(holdings) == 1, "exactly one surviving holding"
    h = holdings[0]

    # Surviving units: 140 (100 - 60 + 100).
    assert h["units"] == "140"
    # Cost basis from surviving lots: 40*100 + 100*120 = 16000.
    assert h["cost_basis"] == "16000.00"
    # Current value: 140 * 200 = 28000.
    assert h["current_value"] == "28000.00"

    # Current XIRR uses only surviving lots' cashflows; lifetime XIRR includes the
    # partial-redemption proceeds — they must diverge.
    assert h["current_xirr"] is not None, "current XIRR should be defined"
    assert h["lifetime_xirr"] is not None, "lifetime XIRR should be defined"
    assert h["current_xirr"] != h["lifetime_xirr"], (
        f"current and lifetime XIRR should differ with partial redemption history "
        f"(current={h['current_xirr']}, lifetime={h['lifetime_xirr']})"
    )
