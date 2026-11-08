"""Kotak Mahindra Bank savings statement, `.csv`.

Public layout, not verified against a real export: an account block, the
opening balance, then `Sl. No. | Transaction Date | Value Date | Description
| Chq / Ref No. | Amount | Dr / Cr | Balance` (one amount column with a DR/CR
indicator, the running balance suffixed `CR`, dates `dd-mm-YYYY`), and a
closing summary of totals.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from scripts.personas.formats._common import (
    BankStyle,
    Move,
    Sidecar,
    Upi,
    customer_id,
    d_m_y,
    identity,
    ifsc,
    numbered,
    plain,
    statement_name,
    write_csv,
)
from scripts.personas.model import Account, Event, Persona

HEADER = [
    "Sl. No.",
    "Transaction Date",
    "Value Date",
    "Description",
    "Chq / Ref No.",
    "Amount",
    "Dr / Cr",
    "Balance",
]


class KotakStyle(BankStyle):
    def upi(self, u: Upi, debit: bool) -> str:
        head = "UPI/REV" if u.reversal else "UPI"
        return f"{head}/{u.name.upper()}/{u.ref}/{u.note}"

    def neft(self, m: Move) -> str:
        return f"NEFT/{m.ifsc}/{m.ref}/{m.name}"

    def imps(self, m: Move) -> str:
        return f"IMPS/{m.ref}/{m.name}/{m.code} XX{m.last4}"

    def card_bill(self, card: Account, ref: str) -> str:
        return f"CC PAYMENT/{card.institution.upper()} XX{card.last4}/{ref}"

    def payout(self, m: Move) -> str:
        return f"NEFT/{m.ifsc}/{m.ref}/{m.name} MF REDEMPTION"


STYLE = KotakStyle()


def _balance(value: Decimal) -> str:
    return f"{plain(abs(value))} {'CR' if value >= 0 else 'DR'}"


def write(
    account: Account,
    events: list[Event],
    period: tuple[date, date],
    persona: Persona,
    out_dir: Path,
) -> list[Path]:
    start, end = period
    who = identity(persona)
    path = out_dir / statement_name(account, end, ".csv")
    sidecar = Sidecar(persona, account, path)

    lines: list[list[str]] = [
        ["Kotak Mahindra Bank"],
        ["Account Statement"],
        ["Account No.", account.number],
        ["Customer Name", who.name],
        ["CRN", customer_id(persona, account, n=9)],
        ["Address", f"{who.lines[0]}, {who.lines[1]}, {who.lines[2]}"],
        ["Branch", f"{who.city} BRANCH"],
        ["IFSC Code", ifsc(persona, account)],
        ["Period", f"{d_m_y(start, '-')} to {d_m_y(end, '-')}"],
        ["Opening Balance", _balance(account.opening_balance)],
        [],
        HEADER,
    ]
    balance = account.opening_balance
    debits = credits = Decimal(0)
    for n, row in enumerate(numbered(events), start=1):
        e = row.event
        text = STYLE.render(e, persona)
        balance += e.amount
        if e.amount < 0:
            debits -= e.amount
        else:
            credits += e.amount
        day = d_m_y(e.date, "-")
        lines.append(
            [
                str(n),
                day,
                day,
                text,
                e.ref or "",
                plain(abs(e.amount)),
                "DR" if e.amount < 0 else "CR",
                _balance(balance),
            ]
        )
        sidecar.add(row, text, csv_line=len(lines) - 1)
    lines += [
        [],
        ["Opening Balance", _balance(account.opening_balance)],
        ["Total Withdrawals", plain(debits)],
        ["Total Deposits", plain(credits)],
        ["Closing Balance", _balance(balance)],
    ]
    write_csv(path, lines)
    sidecar.write()
    return [path]
