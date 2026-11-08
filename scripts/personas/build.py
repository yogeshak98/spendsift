"""Build a persona's statement files: simulate it, then hand each account's
events to the writer for its institution's export format.

`build` writes only into `out_dir`: the statement files it returns, and a
`<file>.truth.json` sidecar beside each (the checker's row-to-truth map;
build output, never committed, never shown to the agent in a live demo).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from datetime import date
from pathlib import Path

from scripts.personas.formats import (
    axis_card,
    groww_mf,
    groww_stocks,
    hdfc_bank,
    hdfc_card,
    icici_card,
    idfc_bank,
    kotak_bank,
    sbi_bank,
    zerodha_stocks,
)
from scripts.personas.model import (
    PERIOD_END,
    PERIOD_START,
    Account,
    Event,
    NavLookup,
    Persona,
    PriceLookup,
    Truth,
)
from scripts.personas.simulate import simulate

Writer = Callable[[Account, list[Event], tuple[date, date], Persona, Path], list[Path]]

WRITERS: dict[tuple[str, str], Writer] = {
    ("bank", "HDFC"): hdfc_bank.write,
    ("bank", "IDFC"): idfc_bank.write,
    ("bank", "SBI"): sbi_bank.write,
    ("bank", "Kotak"): kotak_bank.write,
    ("card", "ICICI"): icici_card.write,
    ("card", "Axis"): axis_card.write,
    ("card", "HDFC"): hdfc_card.write,
    ("broker_mf", "Groww"): groww_mf.write,
    ("broker_stocks", "Groww"): groww_stocks.write,
    ("broker_stocks", "Zerodha"): zerodha_stocks.write,
}


def _snapshot_lookups() -> tuple[NavLookup, PriceLookup]:
    """The offline NAV/price snapshot (Task 4), when it exists."""
    try:
        snapshot = importlib.import_module("scripts.personas.snapshot")
    except ModuleNotFoundError:
        raise RuntimeError(
            "no NAV/price snapshot yet: pass nav= and prices= (or truth=) to build"
        ) from None
    return snapshot.load_nav(), snapshot.load_prices()


def build(
    persona: Persona,
    out_dir: Path,
    *,
    nav: NavLookup | None = None,
    prices: PriceLookup | None = None,
    truth: Truth | None = None,
) -> list[Path]:
    """Write every statement file for `persona` into `out_dir`, which must
    exist, and return them in account order, then statement date order.

    The truth is simulated with `nav`/`prices`, defaulting to the offline
    snapshot; pass `truth` to reuse one already simulated."""
    if not out_dir.is_dir():
        raise NotADirectoryError(out_dir)
    if truth is None:
        if nav is None or prices is None:
            nav, prices = _snapshot_lookups()
        truth = simulate(persona, nav=nav, prices=prices)
    period = (PERIOD_START, PERIOD_END)
    paths: list[Path] = []
    for account in persona.accounts:
        writer = WRITERS.get((account.kind, account.institution))
        if writer is None:
            raise KeyError(f"no writer for a {account.institution} {account.kind}")
        events = [e for e in truth.events if e.account_key == account.key]
        paths += writer(account, events, period, persona, out_dir)
    if len({p.name for p in paths}) != len(paths):
        raise ValueError(f"{persona.slug}: two statements share a file name")
    return paths
