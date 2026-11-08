"""HDFC Bank savings statement, legacy `.xls` (layout measured from a real
export; structure only, every value here is invented).

A 19-row preamble of `Label :` lines, a row of asterisks, the header at row
index 20, another asterisk row, the rows (dates `dd/mm/yy`), then the
STATEMENT SUMMARY block, a `Generated On:` line and the end marker.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import xlwt

from scripts.personas.formats._common import (
    BankStyle,
    Move,
    Sidecar,
    Upi,
    as_float,
    customer_id,
    d_m_y,
    d_mon_y,
    identity,
    ifsc,
    masked_email,
    masked_mobile,
    numbered,
    statement_name,
)
from scripts.personas.model import Account, Event, Persona

HEADER = (
    "Date",
    "Narration",
    "Chq./Ref.No.",
    "Value Dt",
    "Withdrawal Amt.",
    "Deposit Amt.",
    "Closing Balance",
)
HEADER_ROW = 20
STARS = "*" * 10
SUMMARY_LABELS = (
    "Opening Balance",
    "Dr Count",
    "Cr Count",
    "Debits",
    "Credits",
    "Closing Bal",
)


class HdfcStyle(BankStyle):
    def upi(self, u: Upi, debit: bool) -> str:
        head = "UPI/REV" if u.reversal else "UPI"
        return f"{head}/{u.ref}/{u.name.upper()}/{u.vpa.lower()}/{u.note}"

    def neft(self, m: Move) -> str:
        if m.debit:
            return f"NEFT DR-{m.ifsc}-{m.name}-NETBANK, MUM-N{m.ref}-TRANSFER"
        return f"NEFT CR-{m.ifsc}-{m.name}-N{m.ref}"

    def imps(self, m: Move) -> str:
        return f"IMPS-{m.ref}-{m.name}-{m.code}-XXXXXXXX{m.last4}-TRANSFER"

    def card_bill(self, card: Account, ref: str) -> str:
        issuer = card.institution.upper()
        return f"IB BILLPAY DR-{issuer} CREDIT CARD-XXXXXXXXXXXX{card.last4}-{ref}"

    def payout(self, m: Move) -> str:
        return f"NEFT CR-{m.ifsc}-{m.name} MF REDEMPTION-N{m.ref}"


STYLE = HdfcStyle()


def _ref(e: Event) -> str:
    return f"{e.ref:0>16}" if e.ref else ""


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
    path = out_dir / statement_name(account, end, ".xls")
    sidecar = Sidecar(persona, account, path)

    book = xlwt.Workbook(encoding="utf-8")
    sheet = book.add_sheet("Sheet 1")
    money = xlwt.easyxf(num_format_str="0.00")

    def put(r: int, *cells: object) -> None:
        for c, value in enumerate(cells):
            if value is None or value == "":
                continue
            if isinstance(value, Decimal):
                sheet.write(r, c, as_float(value), money)
            else:
                sheet.write(r, c, value)

    preamble: list[tuple[str, str, str]] = [
        ("HDFC BANK Ltd.", "Page No .: 1", "Statement of accounts"),
        ("", "", ""),
        (who.name, "Account Branch :", who.city),
        (who.lines[0], "Address :", f"{who.lines[1]}, {who.city}"),
        (who.lines[1], "City :", f"{who.city} {who.pin}"),
        (who.lines[2], "State :", who.state),
        ("", "Phone no. :", masked_mobile(persona)),
        ("", "Email :", masked_email(persona)),
        ("JOINT HOLDERS :", "Cust ID :", customer_id(persona, account)),
        ("", "Account No :", f"{account.number}   OTHER"),
        ("Nomination : Registered", "A/C Open Date :", "14/06/18"),
        ("", "Account Status :", "Regular"),
        ("", "RTGS/NEFT IFSC :", ifsc(persona, account)),
        ("", "Branch Code :", ifsc(persona, account)[-4:]),
        ("", "Product Code :", "100"),
        (f"Statement From : {d_m_y(start)}", "", ""),
        (f"Statement To : {d_m_y(end)}", "", ""),
        ("", "", ""),
        ("", "", ""),
    ]
    for r, (left, label, value) in enumerate(preamble):
        put(r, left, "", "", label, value, "", "")
    put(HEADER_ROW - 1, *([STARS] * len(HEADER)))
    put(HEADER_ROW, *HEADER)
    put(HEADER_ROW + 1, *([STARS] * len(HEADER)))

    balance = account.opening_balance
    debits = credits = Decimal(0)
    dr = cr = 0
    r = HEADER_ROW + 2
    for row in rows:
        e = row.event
        text = STYLE.render(e, persona)
        balance += e.amount
        out, inn = (-e.amount, None) if e.amount < 0 else (None, e.amount)
        if e.amount < 0:
            debits, dr = debits - e.amount, dr + 1
        else:
            credits, cr = credits + e.amount, cr + 1
        day = d_m_y(e.date, year4=False)
        put(r, day, text, _ref(e), day, out, inn, balance)
        sidecar.add(row, text, sheet_row=r)
        r += 1

    r += 1
    put(r, "STATEMENT SUMMARY  :-")
    put(r + 2, *SUMMARY_LABELS)
    put(r + 3, account.opening_balance, dr, cr, debits, credits, balance)
    put(r + 5, f"Generated On: {d_mon_y(end)} 09:00:00")
    put(r + 6, "This is a computer generated statement and does not require signature.")
    put(r + 8, "--- End Of Statement ---")

    book.save(str(path))
    sidecar.write()
    return [path]
