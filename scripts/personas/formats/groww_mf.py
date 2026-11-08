"""Groww mutual-fund order history, `.xlsx` (layout measured from a real
export; structure only, every value here is invented).

A preamble (`Personal Details`, Name, Mobile Number, PAN, `HISTORY FROM <dd
Mon YYYY> TO <dd Mon YYYY>`), then the header at row index 11: `Scheme Name |
Transaction Type | Units | NAV | Amount | Date`. As in the real export,
Units, NAV and Amount are text cells: Units and NAV with 2 decimals, Amount
in whole rupees, truncated, and net of stamp duty on a purchase. The app's
round-cash snap depends on exactly that.

The order history holds orders only. Money moving between the bank and
Groww (platform funding, redemption payouts) is not printed here; those
events are listed as unrendered in the sidecar, and their bank legs are
printed on the bank statements.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook

from scripts.personas.formats._common import (
    MONTH_ABBR,
    Sidecar,
    digits,
    identity,
    numbered,
    save_xlsx,
    statement_name,
    truncate_rupee,
)
from scripts.personas.model import Account, Event, Persona

HEADER = ("Scheme Name", "Transaction Type", "Units", "NAV", "Amount", "Date")
HEADER_ROW = 11
TWO_DP = Decimal("0.01")


def groww_date(day: date) -> str:
    return f"{day.day:02d} {MONTH_ABBR[day.month - 1]} {day.year:04d}"


def two_dp(value: Decimal) -> str:
    return f"{abs(value).quantize(TWO_DP, rounding=ROUND_HALF_UP):.2f}"


def amount_text(e: Event) -> str:
    """Whole rupees, truncated. A purchase prints the cash net of stamp
    duty; a redemption prints its proceeds."""
    if e.kind == "mf_buy":
        return str(truncate_rupee(-e.amount - (e.charges or Decimal(0))))
    return str(truncate_rupee(e.amount))


def masked_pan(persona: Persona) -> str:
    return "*****" + digits(persona.slug, "pan", n=4) + "*"


def write(
    account: Account,
    events: list[Event],
    period: tuple[date, date],
    persona: Persona,
    out_dir: Path,
) -> list[Path]:
    start, end = period
    who = identity(persona)
    path = out_dir / statement_name(account, end, ".xlsx")
    sidecar = Sidecar(persona, account, path)

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Sheet1"

    def put(r: int, *cells: str) -> None:
        for c, value in enumerate(cells):
            if value:
                sheet.cell(row=r + 1, column=c + 1).value = value

    put(0, "Personal Details")
    put(1, "Name", who.name.title())
    put(2, "Mobile Number", "XXXXXX" + digits(persona.slug, "mobile", n=4))
    put(3, "PAN", masked_pan(persona))
    put(6, f"HISTORY FROM {groww_date(start)} TO {groww_date(end)}")
    put(HEADER_ROW, *HEADER)
    r = HEADER_ROW + 1
    for row in numbered(events):
        e = row.event
        if e.kind not in ("mf_buy", "mf_sell"):
            sidecar.skip(row, "not an order: Groww's order history prints orders only")
            continue
        if e.units is None or e.price is None:
            raise ValueError(f"{account.key}: MF order without units or NAV: {e}")
        put(
            r,
            e.narration,
            "PURCHASE" if e.kind == "mf_buy" else "REDEEM",
            two_dp(e.units),
            two_dp(e.price),
            amount_text(e),
            groww_date(e.date),
        )
        sidecar.add(row, e.narration, sheet_row=r)
        r += 1
    save_xlsx(book, path, end)
    sidecar.write()
    return [path]
