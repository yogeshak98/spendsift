"""Zerodha Kite tradebook, `.csv`.

Public layout (Console's tradebook download), not verified against a real
export: `symbol,isin,trade_date,exchange,segment,series,trade_type,auction,
quantity,price,trade_id,order_id,order_execution_time`, ISO dates, quantity
and price to 6 decimals, one trade per order. The existing fixture
`tests/fixtures/statements/kite_orders.csv` is a hand-made generic CSV
(Date, Instrument, Transaction Type, Amount, Order ID), not this layout.

Trades only: funding transfers into the Zerodha account are printed on the
bank statements and listed as unrendered in the sidecar.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from scripts.personas.formats._common import (
    Sidecar,
    clock,
    digits,
    iso,
    numbered,
    statement_name,
    write_csv,
)
from scripts.personas.instruments import TICKERS
from scripts.personas.model import Account, Event, Persona

HEADER = [
    "symbol",
    "isin",
    "trade_date",
    "exchange",
    "segment",
    "series",
    "trade_type",
    "auction",
    "quantity",
    "price",
    "trade_id",
    "order_id",
    "order_execution_time",
]
SIX_DP = Decimal("0.000001")


def write(
    account: Account,
    events: list[Event],
    period: tuple[date, date],
    persona: Persona,
    out_dir: Path,
) -> list[Path]:
    _, end = period
    path = out_dir / statement_name(account, end, ".csv")
    sidecar = Sidecar(persona, account, path)
    lines: list[list[str]] = [HEADER]
    for row in numbered(events):
        e = row.event
        if e.kind not in ("stock_buy", "stock_sell"):
            sidecar.skip(row, "not a trade: the tradebook prints trades only")
            continue
        if e.commodity is None or e.units is None or e.price is None:
            raise ValueError(
                f"{account.key}: trade without symbol, units or price: {e}"
            )
        h, m, s = clock(e.ref or "", e.date.isoformat())
        lines.append(
            [
                e.commodity,
                TICKERS[e.commodity].isin,
                iso(e.date),
                "NSE",
                "EQ",
                "EQ",
                "buy" if e.kind == "stock_buy" else "sell",
                "false",
                f"{abs(e.units).quantize(SIX_DP):f}",
                f"{e.price.quantize(SIX_DP):f}",
                digits(e.ref or "", "trade", n=8),
                e.ref or "",
                f"{iso(e.date)}T{h:02d}:{m:02d}:{s:02d}",
            ]
        )
        sidecar.add(row, e.commodity, csv_line=len(lines) - 1)
    write_csv(path, lines)
    sidecar.write()
    return [path]
