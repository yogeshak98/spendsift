"""Generate fictional ICICI- and Axis-style card-statement PDFs for tests.

The layouts reproduce what was measured on real statements (counts and
shapes only, never content): word x
positions, line offsets, glued glyphs and markers. Every name, number and
amount here is fictional. No real data lives here.

ICICI prints its summary values glued to the rupee sign (`₹9,999.99`, one
word), which the PDF core fonts cannot draw, so these fixtures embed a small
generated font: one box glyph per character, every glyph the same width.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Literal

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fpdf import FPDF

from tests.service.ingest.pdf_fixtures import _Writer, indian

_FONT_SIZE = 8
_ADVANCE = 400  # font units of 1000: 3.2 pt per character at 8 pt
_ROW_PITCH = 14.0
_FOOTER_Y = 825.0

Kind = Literal["purchase", "cash", "other", "payment", "refund"]
_CREDIT_KINDS = ("payment", "refund")

ICICI_HOLDER = ("NEHA KULKARNI", "FLAT 4B ORCHID TOWERS BANER PUNE 411045")
ICICI_CARD = "5241XXXXXXXX7731"
ICICI_PERIOD = "September 22, 2026 to October 21, 2026"
AXIS_HOLDER = ("VIKRAM SETHI", "22 PALM GROVE ROAD THRISSUR 680001")
AXIS_CARD = "543210******4821"


@dataclass
class Txn:
    """One printed transaction. `kind` decides which summary figure it adds
    to; ICICI prints `ref` and `points`, Axis prints `category` and
    `cashback`."""

    date: str
    description: str
    amount: str
    kind: Kind = "purchase"
    ref: str = "20260000000"
    points: int | None = None
    category: str = "SHOPPING"
    cashback: str = "0.00"

    @property
    def credit(self) -> bool:
        return self.kind in _CREDIT_KINDS

    @property
    def value(self) -> Decimal:
        return Decimal(self.amount.replace(",", ""))


def _total(rows: list[Txn], *kinds: str) -> Decimal:
    return sum((r.value for r in rows if r.kind in kinds), Decimal("0"))


# ---------------------------------------------------------------------------
# The font


@cache
def _font_path() -> Path:
    """A generated TrueType font covering printable ASCII and `₹`."""
    chars = [chr(c) for c in range(0x21, 0x7F)] + ["₹"]
    names = [".notdef", "space", *(f"u{ord(c):04X}" for c in chars)]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap(
        {0x20: "space", **{ord(c): f"u{ord(c):04X}" for c in chars}}
    )
    pen = TTGlyphPen(None)
    pen.moveTo((40, 0))
    pen.lineTo((40, 700))
    pen.lineTo((_ADVANCE - 40, 700))
    pen.lineTo((_ADVANCE - 40, 0))
    pen.closePath()
    box = pen.glyph()
    empty = TTGlyphPen(None).glyph()
    builder.setupGlyf({n: empty if n in (".notdef", "space") else box for n in names})
    builder.setupHorizontalMetrics({n: (_ADVANCE, 40) for n in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "FixtureBox", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    path = Path(tempfile.mkdtemp(prefix="spendsift-fixture-font-")) / "box.ttf"
    builder.save(str(path))
    return path


def _pdf() -> tuple[FPDF, _Writer]:
    pdf = FPDF(unit="pt", format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_font("FixtureBox", "", str(_font_path()))
    pdf.set_font("FixtureBox", size=_FONT_SIZE)
    return pdf, _Writer(pdf)


def _right(w: _Writer, x1: float, y: float, text: str, jitter: float = 0.0) -> None:
    """Print `text` so that it ends at x1."""
    w.words(x1 - w.width(text), y, text, jitter)


def _footer(w: _Writer, page: int, pages: int) -> None:
    w.words(270, _FOOTER_Y, f"Page {page} of {pages}")


def _rupee(value: Decimal, glyph: str = "₹") -> str:
    return f"{glyph}{indian(value)}"


# ---------------------------------------------------------------------------
# ICICI (Amazon Pay)

# Table x-offset (the date's left edge) on page 1 and page 2: the date sits
# at x≈207 on page 1 and the rows start at x≈35 on page 2.
ICICI_PAGE_X = (191.0, 35.0)
_ICICI_HEADER = "Date SerNo. Transaction Details Reward Points Amount (in {glyph})"
# Word centres measured on the real statement, page 1.
_ICICI_LABELS = (
    (114.5, "Total Amount due"),
    (248.5, "Previous Balance"),
    (339.5, "Purchases / Charges"),
    (424.0, "Cash Advances"),
    (520.0, "Payments / Credits"),
)
_ICICI_OPERATORS = ((191.0, "="), (287.0, "+"), (380.0, "+"), (472.0, "-"))
_ICICI_TOTAL_X = 112.0
_ICICI_VALUES_X = (248.0, 338.0, 428.0, 518.0)


def icici_summary(rows: list[Txn]) -> dict[str, Decimal]:
    """The figures an ICICI statement prints for these rows."""
    previous = Decimal("6400.00")
    payments = _total(rows, *_CREDIT_KINDS)
    purchases = _total(rows, "purchase", "other")
    cash = _total(rows, "cash")
    return {
        "previous_balance": previous,
        "purchases_debits": purchases,
        "cash_advances": cash,
        "payments_credits": payments,
        "total_due": previous - payments + purchases + cash,
    }


def _icici_row(w: _Writer, base: float, y: float, row: Txn, jitter: float) -> None:
    # Columns relative to the date's left edge: on page 1 the ref centres at
    # x≈252, the points at x≈450, the amount ends at x≈540 and CR at x≈548.
    w.words(base, y, row.date, jitter)
    w.words(base + 43, y + jitter, row.ref)
    w.words(base + 88, y, row.description, jitter)
    if row.points is not None:
        w.centred(base + 259, y + jitter, str(row.points))
    _right(w, base + 349, y, row.amount)
    if row.credit:
        w.words(base + 354, y + jitter, "CR")


def make_icici_pdf(
    path: Path,
    *,
    page1: list[Txn],
    page2: list[Txn] = (),  # type: ignore[assignment]
    international: list[Txn] = (),  # type: ignore[assignment]
    summary: dict[str, Decimal] | None = None,
    holder: tuple[str, str] = ICICI_HOLDER,
    card: str = ICICI_CARD,
    period: str = ICICI_PERIOD,
    jitter: float = 1.0,
    glyph: str = "₹",
) -> Path:
    """An ICICI-style statement: 3 pages, rows on pages 1-2, terms on page 3.

    `summary` overrides single printed figures (no recomputation), so a test
    can print a summary that does not reconcile. `glyph` is the character
    printed for the rupee sign: a real statement's font prints a backtick."""
    values = icici_summary([*page1, *page2, *international])
    values.update(summary or {})
    pdf, w = _pdf()
    pages = 3

    # Page 1: holder, card, period, the summary panel, then the table.
    pdf.add_page()
    w.words(35, 40, holder[0])
    w.words(35, 52, holder[1])
    w.words(35, 70, card)
    w.words(35, 90, f"Statement period : {period}")
    for centre, label in _ICICI_LABELS:
        w.centred(centre, 112, label)
    for centre, op in _ICICI_OPERATORS:
        w.centred(centre, 124, op)
    w.centred(_ICICI_TOTAL_X, 136, _rupee(values["total_due"], glyph))
    roles = (
        "previous_balance",
        "purchases_debits",
        "cash_advances",
        "payments_credits",
    )
    for centre, role in zip(_ICICI_VALUES_X, roles, strict=True):
        w.centred(centre, 148, _rupee(values[role], glyph))
    base = ICICI_PAGE_X[0]
    w.words(base, 185, _ICICI_HEADER.format(glyph=glyph))
    y = 200.0
    for row in page1:
        _icici_row(w, base, y, row, jitter)
        y += _ROW_PITCH
    if y > 800:
        raise ValueError("too many rows for the fixture's first page")
    _footer(w, 1, pages)

    # Page 2: the table again at another x, an International heading, the end.
    pdf.add_page()
    base = ICICI_PAGE_X[1]
    w.words(base, 40, _ICICI_HEADER.format(glyph=glyph))
    y = 56.0
    for row in page2:
        _icici_row(w, base, y, row, jitter)
        y += _ROW_PITCH
    if international:
        y += 4
        w.words(base, y, "International Spends")
        y += _ROW_PITCH
        for row in international:
            _icici_row(w, base, y, row, jitter)
            y += _ROW_PITCH
    w.words(base, y + 20, "Reward Points Summary")
    w.words(base, y + 34, "Points earned this month are credited by the next statement")
    _footer(w, 2, pages)

    # Page 3: terms, with a worked interest example that looks like a row but
    # sits outside any table.
    pdf.add_page()
    w.words(35, 40, "Terms and Conditions")
    w.words(35, 60, "Illustration of interest when the Total Amount due is not paid")
    w.words(35, 74, "Purchase on Sep 10, 2026 of ₹10,000.00 billed on the statement")
    w.words(35, 88, "10/09/2026 12345678901 Purchase on Sep 10 10,000.00")
    w.words(35, 102, "Interest is charged from the date of each transaction")
    _footer(w, 3, pages)

    path.write_bytes(bytes(pdf.output()))
    return path


# ---------------------------------------------------------------------------
# Axis

_AXIS_HEADER = "DATE TRANSACTION DETAILS MERCHANT CATEGORY AMOUNT (Rs.) CASHBACK EARNED"
_AXIS_FIRST_LABELS = (
    (83.0, "Total Payment Due"),
    (194.5, "Minimum Payment Due"),
    (303.0, "Statement Period"),
    (415.0, "Payment Due Date"),
    (522.5, "Statement Generation Date"),
)
_AXIS_EQUATION = (
    "Previous Balance - Payments - Credits + Purchase + Cash Advance"
    " + Other Debit & Charges =Total Payment Due"
)
_AXIS_EQUATION_X = 69.0  # "Previous" centres at x≈82
# Value centres on the equation's value line, measured: not under the labels.
_AXIS_VALUES_X = (62.0, 130.0, 178.0, 225.0, 280.0, 349.0, 420.0)
_AXIS_ROLES = (
    "previous_balance",
    "payments",
    "other_credits",
    "purchases_debits",
    "cash_advances",
    "other_charges",
    "total_due",
)


def axis_summary(rows: list[Txn]) -> dict[str, Decimal]:
    """The figures an Axis statement prints for these rows."""
    previous = Decimal("9850.00")
    payments = _total(rows, "payment")
    credits = _total(rows, "refund")
    purchases = _total(rows, "purchase")
    cash = _total(rows, "cash")
    other = _total(rows, "other")
    total = previous - payments - credits + purchases + cash + other
    return {
        "previous_balance": previous,
        "payments": payments,
        "other_credits": credits,
        "purchases_debits": purchases,
        "cash_advances": cash,
        "other_charges": other,
        "total_due": total,
        "minimum_due": (total * Decimal("0.05")).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        ),
    }


def _axis_row(w: _Writer, y: float, row: Txn, jitter: float) -> None:
    # Date centred at x≈53, description from x≈97, the amount ending just
    # before its Dr/Cr at x≈511, the cashback before its Dr/Cr at x≈579.
    w.words(37, y, row.date, jitter)
    w.words(97, y + jitter, row.description)
    w.words(380, y, row.category, jitter)
    _right(w, 503, y + jitter, row.amount)
    w.words(507.8, y, "Cr" if row.credit else "Dr")
    _right(w, 571, y, row.cashback, jitter)
    w.words(575.8, y + jitter, "Cr")


def make_axis_pdf(
    path: Path,
    *,
    page1: list[Txn],
    page2: list[Txn] = (),  # type: ignore[assignment]
    summary: dict[str, Decimal] | None = None,
    holder: tuple[str, str] = AXIS_HOLDER,
    card: str = AXIS_CARD,
    period: tuple[str, str] = ("16/09/2026", "15/10/2026"),
    due_date: str = "05/11/2026",
    jitter: float = 1.0,
) -> Path:
    """An Axis-style statement: 2 pages. `summary` overrides single printed
    figures (no recomputation)."""
    values = axis_summary([*page1, *page2])
    values.update(summary or {})
    pdf, w = _pdf()
    pages = 2

    pdf.add_page()
    w.words(40, 40, holder[0])
    w.words(40, 52, holder[1])
    w.words(40, 70, f"Card No: {card}")

    # First block: labels, then the values on the next line.
    for centre, label in _AXIS_FIRST_LABELS:
        w.centred(centre, 95, label)
    w.centred(76, 107, indian(values["total_due"]))
    w.centred(100, 107, "Dr")
    w.centred(184, 107, indian(values["minimum_due"]))
    w.centred(205, 107, "Dr")
    w.centred(278, 107, period[0])
    w.centred(300, 107, "-")
    w.centred(322, 107, period[1])
    w.centred(412, 107, due_date)
    w.centred(529, 107, period[1])

    # Second block: the issuer's equation, a note line, then the 7 values
    # (not centred under their labels) followed by note text with no amount.
    w.words(_AXIS_EQUATION_X, 130, _AXIS_EQUATION)
    w.words(69, 142, "( as on the statement generation date )")
    for centre, role in zip(_AXIS_VALUES_X, _AXIS_ROLES, strict=True):
        w.centred(centre, 154, indian(values[role]))
    w.centred(84, 154, "Dr")
    w.centred(444, 154, "Dr")
    w.words(456, 154, "Pay by the due date")

    # The card again: running straight into the credit limit (this one does
    # not count as a card number), and once more on its own line.
    w.words(40, 175, "Card Details Credit Limit Available Credit Limit")
    w.words(40, 187, f"Card No: {card} 2,00,000.00 1,82,000.00")
    w.words(40, 205, f"Card No: {card}")

    w.words(40, 225, _AXIS_HEADER)
    y = 240.0
    for row in page1:
        _axis_row(w, y, row, jitter)
        y += _ROW_PITCH
    if y > 800:
        raise ValueError("too many rows for the fixture's first page")
    _footer(w, 1, pages)

    pdf.add_page()
    w.words(40, 40, _AXIS_HEADER)
    y = 56.0
    for row in page2:
        _axis_row(w, y, row, jitter)
        y += _ROW_PITCH
    w.words(40, y + 16, "**** End of Statement ****")
    _footer(w, 2, pages)

    path.write_bytes(bytes(pdf.output()))
    return path
