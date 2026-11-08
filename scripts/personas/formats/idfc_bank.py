"""IDFC FIRST Bank savings statement, `.xlsx` (layout measured from a real
export; structure only, every value here is invented).

Label/value preamble rows (STATEMENT OF ACCOUNT, CUSTOMER ID, ACCOUNT NUMBER,
STATEMENT PERIOD, CUSTOMER NAME, ACCOUNT BRANCH, addresses ...), an account
summary, the header at row index 21 with dates `dd-Mon-YYYY`, then a `Total`
row, the debit and credit counts and `End of Statement`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from scripts.personas.formats._common import (
    BankStyle,
    Move,
    Sidecar,
    Upi,
    as_float,
    customer_id,
    d_mon_y,
    identity,
    ifsc,
    numbered,
    save_xlsx,
    statement_name,
)
from scripts.personas.model import Account, Event, Persona

HEADER = (
    "Transaction Date",
    "Value Date",
    "Particulars",
    "Cheque No.",
    "Debit",
    "Credit",
    "Balance",
)
HEADER_ROW = 21
SUMMARY_LABELS = ("Opening Balance", "Total Debit", "Total Credit", "Closing Balance")


class IdfcStyle(BankStyle):
    def upi(self, u: Upi, debit: bool) -> str:
        head = "UPI-REVERSAL" if u.reversal else "UPI"
        return f"{head}-{u.name.upper()}-{u.ref}-{u.vpa.lower()}-{u.note.upper()}"

    def neft(self, m: Move) -> str:
        side = "DR" if m.debit else "CR"
        return f"NEFT {side}-{m.ifsc}-{m.name}-{m.ref}"

    def imps(self, m: Move) -> str:
        return f"IMPS/P2A/{m.ref}/{m.name}/{m.code}/XX{m.last4}"

    def card_bill(self, card: Account, ref: str) -> str:
        return f"CREDIT CARD PAYMENT-{card.institution.upper()}-XX{card.last4}-{ref}"

    def payout(self, m: Move) -> str:
        return f"NEFT CR-{m.ifsc}-{m.name} MF REDEMPTION-{m.ref}"


STYLE = IdfcStyle()


def write(
    account: Account,
    events: list[Event],
    period: tuple[date, date],
    persona: Persona,
    out_dir: Path,
) -> list[Path]:
    start, end = period
    who = identity(persona)
    rows = numbered(events)
    path = out_dir / statement_name(account, end, ".xlsx")
    sidecar = Sidecar(persona, account, path)

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Account Statement"

    def put(r: int, *cells: object) -> None:
        for c, value in enumerate(cells):
            if value is None or value == "":
                continue
            cell = sheet.cell(row=r + 1, column=c + 1)
            if isinstance(value, Decimal):
                cell.value = as_float(value)
                cell.number_format = "#,##0.00"
            else:
                cell.value = value

    debits = -sum((e.amount for e in events if e.amount < 0), Decimal(0))
    credits = sum((e.amount for e in events if e.amount > 0), Decimal(0))
    closing = account.opening_balance - debits + credits
    preamble: list[tuple[str, str]] = [
        ("STATEMENT OF ACCOUNT", ""),
        ("", ""),
        ("CUSTOMER ID", customer_id(persona, account, n=10)),
        ("ACCOUNT NUMBER", account.number),
        ("STATEMENT PERIOD", f"{d_mon_y(start)} TO {d_mon_y(end)}"),
        ("CUSTOMER NAME", who.name),
        ("ACCOUNT BRANCH", f"{who.city} BRANCH"),
        ("CUSTOMER ADDRESS", who.lines[0]),
        ("", who.lines[1]),
        ("", who.lines[2]),
        ("BRANCH ADDRESS", f"IDFC FIRST BANK, {who.lines[1]}, {who.city}"),
        ("ACCOUNT TYPE", "SAVINGS ACCOUNT"),
        ("CURRENCY", "INR"),
        ("IFSC", ifsc(persona, account)),
        ("NOMINATION", "REGISTERED"),
        ("ACCOUNT OPENING DATE", "09-Aug-2019"),
        ("", ""),
        ("ACCOUNT SUMMARY", ""),
    ]
    for r, (label, value) in enumerate(preamble):
        put(r, label, value)
    put(18, *SUMMARY_LABELS)
    put(19, account.opening_balance, debits, credits, closing)
    put(HEADER_ROW, *HEADER)

    balance = account.opening_balance
    r = HEADER_ROW + 1
    for row in rows:
        e = row.event
        text = STYLE.render(e, persona)
        balance += e.amount
        out, inn = (-e.amount, None) if e.amount < 0 else (None, e.amount)
        day = d_mon_y(e.date)
        put(r, day, day, text, "", out, inn, balance)
        sidecar.add(row, text, sheet_row=r)
        r += 1
    put(r, "Total", "", "", "", debits, credits, "")
    put(r + 1, "Total number of Debits", sum(1 for e in events if e.amount < 0))
    put(r + 2, "Total number of Credits", sum(1 for e in events if e.amount > 0))
    put(r + 4, "End of Statement")

    save_xlsx(book, path, end)
    sidecar.write()
    return [path]
