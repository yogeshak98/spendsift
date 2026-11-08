"""ICICI Bank credit-card statement, a text PDF (layout measured from a real
statement; structure only, every value here is invented).

Page 1 holds the holder, the card number masked as `4XXX XXXX XXXX 1234`, a
summary panel (Statement Date, Payment Due Date, Total Amount Due, Minimum
Amount Due, Credit Limit: labels on one line, values centred below), the
statement period, the summary equation (Previous Balance, Purchases /
Charges, Cash Advances, Payments / Credits) and the transactions table:
`Date | Ser. No. | Transaction Details | Reward Points | Intl. Amount |
Amount (in ₹)`, credits suffixed `CR`. The table header repeats on every
page. The real statement's font extracts the rupee sign as a backtick, and
the app reads a backtick as the rupee prefix, so the PDF prints one (the
core fonts cannot draw `₹`).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF

from scripts.personas.formats._common import (
    CardStatement,
    Sidecar,
    card_statements,
    card_text,
    credit_limit,
    d_m_y,
    digits,
    identity,
    indian,
    new_pdf,
    statement_name,
    text_centred,
    text_right,
)
from scripts.personas.model import Account, Event, Persona

RUPEE = "`"
HEADER = (
    f"Date Ser. No. Transaction Details Reward Points Intl. Amount Amount (in {RUPEE})"
)
PAYMENT = "PAYMENT RECEIVED - THANK YOU"
SUMMARY = (
    (80.0, "Statement Date"),
    (190.0, "Payment Due Date"),
    (300.0, "Total Amount Due"),
    (410.0, "Minimum Amount Due"),
    (515.0, "Credit Limit"),
)
EQUATION = (
    (90.0, "Previous Balance"),
    (215.0, "Purchases / Charges"),
    (340.0, "Cash Advances"),
    (465.0, "Payments / Credits"),
)
_PITCH = 14.0
_LAST_ROW_Y = 760.0  # leaves room for the points summary above the footer
_FIRST_ROWS_Y = 246.0
_NEXT_ROWS_Y = 56.0
_FOOTER_Y = 825.0


def masked(account: Account) -> str:
    return f"{account.number[0]}XXX XXXX XXXX {account.last4}"


def _owed(value: Decimal) -> str:
    """A summary figure: owed amounts plain, a credit balance suffixed CR."""
    text = f"{RUPEE}{indian(value)}"
    return text if value >= 0 else f"{text} CR"


def _points(e: Event) -> str:
    return str(int(-e.amount // 100) * 2) if e.kind == "spend" else ""


def _serial(account: Account, e: Event, seq: int) -> str:
    return "1" + digits(account.number, e.date.isoformat(), str(seq), n=10)


def _pages(n_rows: int) -> int:
    first = int((_LAST_ROW_Y - _FIRST_ROWS_Y) // _PITCH) + 1
    rest = int((_LAST_ROW_Y - _NEXT_ROWS_Y) // _PITCH) + 1
    return 1 + max(0, -(-(n_rows - first) // rest))


def _statement(
    account: Account, persona: Persona, s: CardStatement, limit: Decimal, path: Path
) -> None:
    who = identity(persona)
    pdf = new_pdf(s.statement_date, "ICICI Bank Credit Card Statement")
    sidecar = Sidecar(persona, account, path)
    pages = _pages(len(s.rows))

    def footer(page: int) -> None:
        text_centred(pdf, 297.0, _FOOTER_Y, f"Page {page} of {pages}")

    pdf.add_page()
    pdf.text(35, 40, "ICICI Bank")
    pdf.text(35, 52, f"{account.product} Credit Card Statement")
    pdf.text(35, 70, who.name)
    pdf.text(35, 82, who.lines[0])
    pdf.text(35, 94, f"{who.lines[1]}, {who.lines[2]}")
    pdf.text(35, 112, masked(account))
    values = (
        d_m_y(s.statement_date),
        d_m_y(s.due_date),
        _owed(s.total_due),
        _owed(s.minimum_due),
        f"{RUPEE}{indian(limit)}",
    )
    for (centre, label), value in zip(SUMMARY, values, strict=True):
        text_centred(pdf, centre, 134, label)
        text_centred(pdf, centre, 146, value)
    pdf.text(
        35,
        170,
        f"Statement Period : {d_m_y(s.start)} to {d_m_y(s.statement_date)}",
    )
    figures = (_owed(-s.opening), _owed(s.debits), _owed(Decimal(0)), _owed(s.credits))
    for (centre, label), value in zip(EQUATION, figures, strict=True):
        text_centred(pdf, centre, 194, label)
        text_centred(pdf, centre, 206, value)
    pdf.text(35, 230, HEADER)

    page, y = 1, _FIRST_ROWS_Y
    for row in s.rows:
        if y > _LAST_ROW_Y:
            footer(page)
            pdf.add_page()
            page, y = page + 1, _NEXT_ROWS_Y
            pdf.text(35, 40, HEADER)
        e = row.event
        text = card_text(e, PAYMENT)
        _row(pdf, y, account, row.seq, e, text)
        sidecar.add(row, text, page=page, y=y)
        y += _PITCH
    pdf.text(35, y + 20, "Reward Points Summary")
    earned = sum(int(p) for r in s.rows if (p := _points(r.event)))
    pdf.text(35, y + 34, f"Points earned this statement {earned}")
    footer(page)
    if page != pages:
        raise AssertionError(f"{path.name}: {page} pages, expected {pages}")
    pdf.output(str(path))
    sidecar.write()


def _row(pdf: FPDF, y: float, account: Account, seq: int, e: Event, text: str) -> None:
    pdf.text(35, y, d_m_y(e.date))
    pdf.text(85, y, _serial(account, e, seq))
    pdf.text(140, y, text)
    points = _points(e)
    if points:
        text_right(pdf, 400, y, points)
    text_right(pdf, 540, y, indian(e.amount))
    if e.amount > 0:
        pdf.text(545, y, "CR")


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
        path = out_dir / statement_name(account, s.statement_date, ".pdf")
        _statement(account, persona, s, limit, path)
        paths.append(path)
    return paths
