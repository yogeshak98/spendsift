"""State Bank of India savings statement, `.xlsx`.

Public layout, not verified against a real export: the label/value account
block SBI's online statement download prints (Account Name, Address, Account
Number, IFS Code, `Balance as on <date>` ...), a period line, then
`Txn Date | Value Date | Description | Ref No./Cheque No. | Debit | Credit |
Balance` with dates `d Mon YYYY`, and the computer-generated note. SBI
prefixes transfers `TO TRANSFER-` (out) and `BY TRANSFER-` (in).
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
    handle_bank,
    identity,
    ifsc,
    numbered,
    save_xlsx,
    statement_name,
)
from scripts.personas.model import Account, Event, Persona

HEADER = (
    "Txn Date",
    "Value Date",
    "Description",
    "Ref No./Cheque No.",
    "Debit",
    "Credit",
    "Balance",
)


def sbi_number(account: Account) -> str:
    """SBI savings numbers are 11 digits: the first digit of the invented
    number, then its last 10 (so the last 4 stay the account's)."""
    number = account.number[0] + account.number[-10:]
    if len(number) != 11 or not number.isdigit():
        raise ValueError(f"{account.key}: no 11-digit SBI number in {account.number!r}")
    return number


def _side(debit: bool) -> str:
    return "TO TRANSFER-" if debit else "BY TRANSFER-"


class SbiStyle(BankStyle):
    def upi(self, u: Upi, debit: bool) -> str:
        dr = "DR" if debit else "CR"
        name, bank = u.name.upper(), handle_bank(u.vpa)
        return (
            f"{_side(debit)}UPI/{dr}/{u.ref}/{name}/{bank}/{u.vpa.lower()}/{u.note}--"
        )

    def neft(self, m: Move) -> str:
        return f"{_side(m.debit)}NEFT*{m.ifsc}*N{m.ref}*{m.name}--"

    def imps(self, m: Move) -> str:
        return f"{_side(m.debit)}IMPS/{m.ref}/{m.name}/{m.code}/XX{m.last4}--"

    def card_bill(self, card: Account, ref: str) -> str:
        issuer = card.institution.upper()
        return f"TO TRANSFER-CREDIT CARD PAYMENT {issuer} XX{card.last4} {ref}--"

    def payout(self, m: Move) -> str:
        return f"BY TRANSFER-NEFT*{m.ifsc}*N{m.ref}*{m.name} MF REDEMPTION--"


STYLE = SbiStyle()


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
    sheet.title = "Statement"

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

    preamble: list[tuple[str, object]] = [
        ("Account Name :", who.name),
        ("Address :", who.lines[0]),
        ("", who.lines[1]),
        ("", who.lines[2]),
        ("Date :", d_mon_y(end, " ", pad=False)),
        ("Account Number :", sbi_number(account)),
        ("Account Description :", "REGULAR SB CHQ-INDIVIDUALS"),
        ("Branch :", f"{who.city} MAIN BRANCH"),
        ("Drawing Power :", Decimal(0)),
        ("Interest Rate(% p.a.) :", "2.5"),
        ("MOD Balance :", Decimal(0)),
        ("CIF No. :", customer_id(persona, account, n=11)),
        ("IFS Code :", ifsc(persona, account)),
        ("Nomination Registered :", "Yes"),
        (f"Balance as on {d_mon_y(start, ' ', pad=False)} :", account.opening_balance),
        ("", ""),
        (
            f"Account Statement from {d_mon_y(start, ' ', pad=False)}"
            f" to {d_mon_y(end, ' ', pad=False)}",
            "",
        ),
        ("", ""),
    ]
    for r, (label, value) in enumerate(preamble):
        put(r, label, value)
    header_row = len(preamble)
    put(header_row, *HEADER)

    balance = account.opening_balance
    r = header_row + 1
    for row in numbered(events):
        e = row.event
        text = STYLE.render(e, persona)
        balance += e.amount
        out, inn = (-e.amount, None) if e.amount < 0 else (None, e.amount)
        day = d_mon_y(e.date, " ", pad=False)
        put(r, day, day, text, e.ref or "", out, inn, balance)
        sidecar.add(row, text, sheet_row=r)
        r += 1
    put(
        r + 1,
        "**This is a computer generated statement and does not require a signature.",
    )

    save_xlsx(book, path, end)
    sidecar.write()
    return [path]
