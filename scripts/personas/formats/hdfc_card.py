"""HDFC Bank credit-card statement, `.xlsx` (layout measured from a real
export; the rows mirror `tests/fixtures/statements/hdfc_card.csv`, every
value here is invented).

A label/value preamble (Name, Address, Payment Due Date, Statement Date,
Total Amount Due, Minimum Amount Due, Credit Limit ...), the `Account
Summary` equation row `Opening Bal - Payment / Credit + Purchases / Debits +
Finance Charges = Total Dues`, the masked `Card No:` line, then `DATE |
Description | AMT | Debit /Credit | REWARDS` with `dd/mm/YYYY HH:MM:SS`
dates and credits marked `Cr`.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook

from scripts.personas.formats._common import (
    CardStatement,
    Sidecar,
    as_float,
    card_statements,
    card_text,
    clock,
    credit_limit,
    d_m_y,
    identity,
    save_xlsx,
    statement_name,
)
from scripts.personas.model import Account, Event, Persona

HEADER = ("DATE", "Description", "AMT", "Debit /Credit", "REWARDS")
SUMMARY_LABELS = (
    "Opening Bal",
    "-",
    "Payment / Credit",
    "+",
    "Purchases / Debits",
    "+",
    "Finance Charges",
    "=",
    "Total Dues",
)
PAYMENT = "BPPY CC PAYMENT DP{ref}"


def masked(account: Account) -> str:
    n = account.number
    return f"{n[:4]} {n[4:6]}XX XXXX {account.last4}"


def _when(account: Account, e: Event, seq: int) -> str:
    h, m, s = clock(account.number, e.date.isoformat(), str(seq), open_=(0, 0))
    return f"{d_m_y(e.date)} {h:02d}:{m:02d}:{s:02d}"


def _statement(
    account: Account, persona: Persona, s: CardStatement, limit: Decimal, path: Path
) -> None:
    who = identity(persona)
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
                cell.number_format = "0.00"
            else:
                cell.value = value

    available = limit - max(s.total_due, Decimal(0))
    cash_limit = (limit * Decimal("0.4")).quantize(Decimal(1), ROUND_HALF_UP)
    preamble: list[tuple[str, object]] = [
        ("Name", who.name),
        ("Address", who.lines[0]),
        ("Address", f"{who.lines[1]}, {who.lines[2]}"),
        ("Customer GSTN", ""),
        ("Payment Due Date", d_m_y(s.due_date)),
        ("Statement Date", d_m_y(s.statement_date)),
        ("Total Amount Due", s.total_due),
        ("Minimum Amount Due", s.minimum_due),
        ("Credit Limit", limit),
        ("Available Limit", available),
        ("Available Cash limit", cash_limit),
    ]
    for r, (label, value) in enumerate(preamble):
        put(r, label, value)
    put(12, "Account Summary")
    put(13, *SUMMARY_LABELS)
    put(
        14,
        -s.opening,
        "-",
        s.credits,
        "+",
        s.debits,
        "+",
        Decimal(0),
        "=",
        s.total_due,
    )
    put(16, f"Card No: {masked(account)}")
    put(18, "Domestic / International Transactions")
    put(19, *HEADER)
    r = 20
    for row in s.rows:
        e = row.event
        text = card_text(e, PAYMENT)
        rewards = int(-e.amount // 150) if e.kind == "spend" else ""
        put(
            r,
            _when(account, e, row.seq),
            text,
            abs(e.amount),
            "Cr" if e.amount > 0 else "",
            rewards,
        )
        sidecar.add(row, text, sheet_row=r)
        r += 1
    save_xlsx(book, path, s.statement_date)
    sidecar.write()


def write(
    account: Account,
    events: list[Event],
    period: tuple[date, date],
    persona: Persona,
    out_dir: Path,
) -> list[Path]:
    statements = card_statements(account, events, period)
    limit = credit_limit(account, statements)
    paths = []
    for s in statements:
        path = out_dir / statement_name(account, s.statement_date, ".xlsx")
        _statement(account, persona, s, limit, path)
        paths.append(path)
    return paths
