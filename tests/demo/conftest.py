"""Shared fixtures for the persona tests.

`stub_lookups` is a stand-in for the offline NAV/price snapshot. Its values are
deterministic and plausible: each instrument starts near its real October 2025
level and drifts a little each day, so NAVs are never round. It has a value
for every day, holidays included, so the catalog and writer tests do not
depend on the calendar. The real offline snapshot (`scripts.personas.snapshot`)
is exercised by `test_snapshot_and_templates.py`, including the net-worth
band on real values.

`personas` imports and checks each catalog persona once per session, on
first use (opt-in), for the rule book and the opening-onboarding tests.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest

from scripts.personas.model import PERIOD_START, NavLookup, PriceLookup

if TYPE_CHECKING:
    from scripts.personas import qa

_NAV_BASE: dict[str, Decimal] = {
    "120716": Decimal("172.3417"),
    "122639": Decimal("89.6142"),
    "118989": Decimal("206.8731"),
    "120503": Decimal("101.2358"),
    "125497": Decimal("187.0624"),
    "118834": Decimal("121.4486"),
}
_PRICE_BASE: dict[str, Decimal] = {
    "RELIANCE": Decimal("1381.40"),
    "TCS": Decimal("3046.20"),
    "INFY": Decimal("1478.65"),
    "HDFCBANK": Decimal("962.35"),
    "ICICIBANK": Decimal("1368.90"),
    "ITC": Decimal("408.75"),
    "LT": Decimal("3712.50"),
    "SBIN": Decimal("871.30"),
}


class Lookups(NamedTuple):
    nav: NavLookup
    prices: PriceLookup


def _drift(base: Decimal, on: date, quant: str) -> Decimal:
    days = (on - PERIOD_START).days
    wiggle = Decimal((days * 37) % 11 - 5)
    value = base * (1 + Decimal("0.00035") * days + Decimal("0.0007") * wiggle)
    return value.quantize(Decimal(quant), rounding=ROUND_HALF_UP)


def _stub_nav(code: str, on: date) -> Decimal:
    return _drift(_NAV_BASE[code], on, "0.0001")


def _stub_price(symbol: str, on: date) -> Decimal:
    return _drift(_PRICE_BASE[symbol], on, "0.05")


@pytest.fixture(scope="session")
def stub_lookups() -> Lookups:
    return Lookups(nav=_stub_nav, prices=_stub_price)


# --- every catalog persona, imported once (opt-in) ---------------------------


class _Recorder:
    """A TestClient stand-in that records every file name sent to detect."""

    def __init__(self, client, seen: list[str]) -> None:
        self._client = client
        self._seen = seen

    def post(self, url: str, *args, **kwargs):
        if url == "/api/import/detect":
            self._seen += [name for name, _ in kwargs.get("files", {}).values()]
        return self._client.post(url, *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._client, name)


class Personas:
    """Each persona imported and checked once, on first use."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.done: dict[str, qa.QaRun] = {}
        self.detected: dict[str, list[str]] = {}

    def __getitem__(self, slug: str) -> qa.QaRun:
        from scripts.personas import qa
        from scripts.personas.catalog import ALL

        if slug not in self.done:
            seen: list[str] = []
            real = qa.client_for
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(qa, "client_for", lambda d: _Recorder(real(d), seen))
                self.done[slug] = qa.run_persona(ALL[slug], self.root / slug)
            self.detected[slug] = seen
        return self.done[slug]


@pytest.fixture(scope="session")
def personas(tmp_path_factory) -> Personas:
    from tests.demo import optin

    optin.require()
    return Personas(tmp_path_factory.mktemp("rule-book"))
