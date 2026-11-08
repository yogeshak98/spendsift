"""Axis Bank credit-card statement, a text PDF (layout measured from a real
statement; structure only, every value here is invented).

Page 1 holds the holder, the card number masked as `400000******1234`, a
summary panel (Total Payment Due, Minimum Payment Due, Statement Period,
Payment Due Date, Statement Generation Date: labels on one line, values
below), the summary equation in Axis's split form (Previous Balance,
Payments, Credits, Purchase, Cash Advance, Other Debit & Charges) and the
transactions table: `Transaction Date | Transaction Details | Merchant
Category | Amount (Rs.)`, each amount suffixed `Dr` or `Cr`. The header
repeats on every page.
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
    d_m_y,
    identity,
    indian,
    new_pdf,
    statement_name,
    text_centred,
    text_right,
)
from scripts.personas.merchants import (
    ENTERTAINMENT,
    FOOD,
    GROCERIES,
    HEALTH,
    RENT,
    SHOPPING,
    TRAVEL,
    UTILITIES,
)
from scripts.personas.model import Account, Event, Persona

HEADER = "Transaction Date Transaction Details Merchant Category Amount (Rs.)"
PAYMENT = "PAYMENT RECEIVED - THANK YOU"
SUMMARY = (
    (75.0, "Total Payment Due"),
    (185.0, "Minimum Payment Due"),
    (300.0, "Statement Period"),
    (415.0, "Payment Due Date"),
    (520.0, "Statement Generation Date"),
)
EQUATION = (
    (70.0, "Previous Balance"),
    (160.0, "Payments"),
    (235.0, "Credits"),
    (310.0, "Purchase"),
    (390.0, "Cash Advance"),
    (495.0, "Other Debit & Charges"),
)
# The issuer's merchant-category labels, by the spend's category.
_MCC = {
    FOOD: "RESTAURANTS",
    GROCERIES: "GROCERY STORES",
    SHOPPING: "DEPT STORES",
    TRAVEL: "TRANSPORTATION",
    UTILITIES: "UTILITIES",
    ENTERTAINMENT: "ENTERTAINMENT",
    HEALTH: "MEDICAL",
    RENT: "MISCELLANEOUS",
}
_FUEL_MERCHANTS = frozenset({"INDIAN OIL", "HPCL"})
_PITCH = 14.0
_LAST_ROW_Y = 770.0
_FIRST_ROWS_Y = 236.0
_NEXT_ROWS_Y = 56.0
_FOOTER_Y = 825.0


def masked(account: Account) -> str:
    return f"{account.number[:6]}******{account.last4}"


def _drcr(value: Decimal) -> str:
    """A balance as Axis prints it: owed is Dr, in credit is Cr."""
    return f"{indian(value)} {'Dr' if value >= 0 else 'Cr'}"


def _category(e: Event) -> str:
    if e.kind != "spend" or e.expected_category is None:
        return ""
    if e.merchant in _FUEL_MERCHANTS:
        return "FUEL"
    return _MCC.get(e.expected_category, "MISCELLANEOUS")


def _split(s: CardStatement) -> dict[str, Decimal]:
    def total(*kinds: str) -> Decimal:
        return abs(
            sum((r.event.amount for r in s.rows if r.event.kind in kinds), Decimal(0))
        )

    return {
        "payments": total("card_payment_received"),
        "credits": total("refund", "cashback"),
        "purchase": total("spend"),
        "other": total("fee", "gst_on_fee"),
    }


def _pages(n_rows: int) -> int:
    first = int((_LAST_ROW_Y - _FIRST_ROWS_Y) // _PITCH) + 1
    rest = int((_LAST_ROW_Y - _NEXT_ROWS_Y) // _PITCH) + 1
    return 1 + max(0, -(-(n_rows - first) // rest))


def _statement(
    account: Account, persona: Persona, s: CardStatement, path: Path
) -> None:
    split = _split(s)
    known = {
        "card_payment_received",
        "refund",
        "cashback",
        "spend",
        "fee",
        "gst_on_fee",
    }
    if any(r.event.kind not in known for r in s.rows):
        raise ValueError(f"{path.name}: a row kind the Axis summary does not cover")
    who = identity(persona)
    pdf = new_pdf(s.statement_date, "Axis Bank Credit Card Statement")
    sidecar = Sidecar(persona, account, path)
    pages = _pages(len(s.rows))

    def footer(page: int) -> None:
        text_centred(pdf, 297.0, _FOOTER_Y, f"Page {page} of {pages}")

    pdf.add_page()
    pdf.text(40, 40, "AXIS BANK")
    pdf.text(40, 52, f"{account.product} Credit Card Statement")
    pdf.text(40, 70, who.name)
    pdf.text(40, 82, who.lines[0])
    pdf.text(40, 94, f"{who.lines[1]}, {who.lines[2]}")
    pdf.text(40, 112, f"Card No: {masked(account)}")
    values = (
        _drcr(s.total_due),
        _drcr(s.minimum_due),
        f"{d_m_y(s.start)} - {d_m_y(s.statement_date)}",
        d_m_y(s.due_date),
        d_m_y(s.statement_date),
    )
    for (centre, label), value in zip(SUMMARY, values, strict=True):
        text_centred(pdf, centre, 134, label)
        text_centred(pdf, centre, 146, value)
    figures = (
        _drcr(-s.opening),
        indian(split["payments"]),
        indian(split["credits"]),
        indian(split["purchase"]),
        indian(Decimal(0)),
        indian(split["other"]),
    )
    for (centre, label), value in zip(EQUATION, figures, strict=True):
        text_centred(pdf, centre, 172, label)
        text_centred(pdf, centre, 184, value)
    pdf.text(40, 196, "( as on the statement generation date )")
    pdf.text(40, 220, HEADER)

    page, y = 1, _FIRST_ROWS_Y
    for row in s.rows:
        if y > _LAST_ROW_Y:
            footer(page)
            pdf.add_page()
            page, y = page + 1, _NEXT_ROWS_Y
            pdf.text(40, 40, HEADER)
        e = row.event
        text = card_text(e, PAYMENT)
        _row(pdf, y, e, text)
        sidecar.add(row, text, page=page, y=y)
        y += _PITCH
    pdf.text(40, y + 20, "**** End of Statement ****")
    footer(page)
    if page != pages:
        raise AssertionError(f"{path.name}: {page} pages, expected {pages}")
    pdf.output(str(path))
    sidecar.write()


def _row(pdf: FPDF, y: float, e: Event, text: str) -> None:
    pdf.text(40, y, d_m_y(e.date))
    pdf.text(110, y, text)
    category = _category(e)
    if category:
        pdf.text(360, y, category)
    text_right(pdf, 530, y, indian(e.amount))
    pdf.text(535, y, "Cr" if e.amount > 0 else "Dr")


def write(
    account: Account,
    events: list[Event],
    period: tuple[date, date],
    persona: Persona,
    out_dir: Path,
) -> list[Path]:
    paths = []
    for s in card_statements(account, events, period):
        path = out_dir / statement_name(account, s.statement_date, ".pdf")
        _statement(account, persona, s, path)
        paths.append(path)
    return paths
