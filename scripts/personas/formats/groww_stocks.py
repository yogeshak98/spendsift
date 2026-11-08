"""Groww stock order history, `.xlsx` (layout measured from a real export;
structure only, every value here is invented).

A preamble (Name, Unique Client Code, `Order history report from dd-mm-YYYY
to dd-mm-YYYY`), then the header at row index 5: `Stock name | Symbol |
ISIN | Type | Quantity | Value | Exchange | Exchange Order Id | Execution
date and time | Order status`, times as `dd-mm-YYYY hh:mm AM`.

Orders only: funding transfers into the Groww stocks wallet are printed on
the bank statements and listed as unrendered in the sidecar.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from scripts.personas.formats._common import (
    Sidecar,
    as_float,
    clock,
    d_m_y,
    identity,
    numbered,
    save_xlsx,
    statement_name,
)
from scripts.personas.instruments import TICKERS
from scripts.personas.model import Account, Event, Persona

HEADER = (
    "Stock name",
    "Symbol",
    "ISIN",
    "Type",
    "Quantity",
    "Value",
    "Exchange",
    "Exchange Order Id",
    "Execution date and time",
    "Order status",
)
HEADER_ROW = 5


def executed_at(e: Event) -> str:
    h, m, _ = clock(e.ref or "", e.date.isoformat())
    half = "AM" if h < 12 else "PM"
    return f"{d_m_y(e.date, '-')} {(h - 1) % 12 + 1:02d}:{m:02d} {half}"


def write(
    account: Account,
    events: list[Event],
    period: tuple[date, date],
    persona: Persona,
    out_dir: Path,
) -> list[Path]:
    start, end = period
    path = out_dir / statement_name(account, end, ".xlsx")
    sidecar = Sidecar(persona, account, path)

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Sheet1"

    def put(r: int, *cells: object) -> None:
        for c, value in enumerate(cells):
            if value is None or value == "":
                continue
            cell = sheet.cell(row=r + 1, column=c + 1)
            cell.value = as_float(value) if isinstance(value, Decimal) else value

    put(0, "Name", identity(persona).name.title())
    put(1, "Unique Client Code", account.number)
    put(3, f"Order history report from {d_m_y(start, '-')} to {d_m_y(end, '-')}")
    put(HEADER_ROW, *HEADER)
    r = HEADER_ROW + 1
    for row in numbered(events):
        e = row.event
        if e.kind not in ("stock_buy", "stock_sell"):
            sidecar.skip(row, "not an order: Groww's order history prints orders only")
            continue
        if e.commodity is None or e.units is None:
            raise ValueError(f"{account.key}: stock order without symbol or units: {e}")
        put(
            r,
            e.narration,
            e.commodity,
            TICKERS[e.commodity].isin,
            "BUY" if e.kind == "stock_buy" else "SELL",
            int(abs(e.units)),
            abs(e.amount),
            "NSE",
            e.ref or "",
            executed_at(e),
            "Executed",
        )
        sidecar.add(row, e.narration, sheet_row=r)
        r += 1
    save_xlsx(book, path, end)
    sidecar.write()
    return [path]
