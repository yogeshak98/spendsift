"""Generate fictional card-statement PDFs for tests. No real data lives here."""

from __future__ import annotations

import io
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path

from fpdf import FPDF
from pypdf import PdfReader, PdfWriter

PAGE_X = (169.0, 25.0)  # table x-offset on page 1 and page 2
_FONT = 8
_GAP = 4.0  # between words; wider than pdfplumber's default x_tolerance
_ROW_PITCH = 16.0
_ROW_START = 210.0
# A second, unmasked 16-digit number (measured on HDFC): card-shaped, but not
# the card, so a card_pattern must anchor on the card's own label.
CUSTOMER_NO = "9876543210987654"


@dataclass
class Row:
    date: str
    time: str
    description: str
    amount: str
    credit: bool = False
    rewards: int | None = None
    wrap: str | None = None
    bar_apart: bool = False
    # The measured sample prints the bar glued to the date ("21/09/2026|", one
    # word) and a trailing PI marker word "l" on some rows.
    bar_glued: bool = False
    pi: bool = False
    # The measured HDFC payment row prints its description on the line above
    # and the line below the date/time/amount line (Task 6b).
    above: str | None = None
    below: str | None = None


def indian(value: Decimal) -> str:
    """1234567.8 -> '12,34,567.80'."""
    whole, frac = f"{value:.2f}".split(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join([*parts, tail])
    return f"{sign}{whole}.{frac}"


def _dec(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def _default_summary(rows: list[Row]) -> dict[str, str]:
    credits = sum((_dec(r.amount) for r in rows if r.credit), Decimal(0))
    debits = sum((_dec(r.amount) for r in rows if not r.credit), Decimal(0))
    previous = Decimal("8200.00")
    total = previous - credits + debits
    minimum = (total * Decimal("0.05")).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return {
        "previous_balance": f"C {indian(previous)}",
        "payments_credits": f"C {indian(credits)}",
        "purchases_debits": f"C {indian(debits)}",
        "finance_charges": f"C {indian(Decimal(0))}",
        "total_due": f"C {indian(total)}",
        "minimum_due": f"C {indian(minimum)}",
        "due_date": "10 Nov, 2026",
        "transactions_total": f"C {indian(debits)}",
        "period": "21 Sep, 2026 - 20 Oct, 2026",
    }


class _Writer:
    def __init__(self, pdf: FPDF) -> None:
        self.pdf = pdf

    def words(self, x: float, y: float, text: str, jitter: float = 0.0) -> float:
        """Print each word at an explicit x/y; returns the x after the last word."""
        for i, word in enumerate(text.split()):
            self.pdf.text(x, y + (jitter if i % 2 else 0.0), word)
            x += self.pdf.get_string_width(word) + _GAP
        return x

    def width(self, text: str) -> float:
        ws = text.split()
        return sum(self.pdf.get_string_width(w) for w in ws) + _GAP * (len(ws) - 1)

    def centred(self, centre: float, y: float, text: str) -> None:
        self.words(centre - self.width(text) / 2, y, text)


_CENTRES = [100.0, 235.0, 370.0, 505.0]


def _summary_block(w: _Writer, y: float, pairs: list[tuple[str, str]]) -> None:
    for centre, (label, value) in zip(_CENTRES, pairs, strict=False):
        w.centred(centre, y, label)
        w.centred(centre, y + 12, value)


# Three caption lines between the component labels and their values, so the
# values print 4 lines below the labels (measured on HDFC).
_CAPTIONS = (
    "Account Summary",
    "Figures in Indian Rupees",
    "as on the statement date",
)


def _equation_block(w: _Writer, y: float, pairs: list[tuple[str, str]]) -> None:
    """Labels, three caption lines, then the values laid out as the issuer's
    equation (`A - B + C + D =`): each value 4 lines below its label."""
    for centre, (label, _) in zip(_CENTRES, pairs, strict=True):
        w.centred(centre, y, label)
    for i, caption in enumerate(_CAPTIONS, start=1):
        w.centred(302.0, y + 10 * i, caption)
    values_y = y + 10 * (len(_CAPTIONS) + 1)
    for centre, (_, value) in zip(_CENTRES, pairs, strict=True):
        w.centred(centre, values_y, value)
    for x, op in zip((167.5, 302.5, 437.5, 565.0), "-++=", strict=True):
        w.centred(x, values_y, op)


def _row(w: _Writer, base: float, y: float, row: Row, jitter: float) -> float:
    if row.above:
        w.words(base + 85, y, row.above)
        y += 9
    date = f"{row.date}|" if row.bar_glued else row.date
    x = w.words(base, y, date, jitter)
    if row.bar_apart:
        x = w.words(x, y + jitter, "|", 0.0) + 0.0
        w.words(x, y, row.time)
    else:
        w.words(base + 50, y + jitter, row.time)
    w.words(base + 85, y, row.description, jitter)
    if row.rewards is not None:
        w.words(base + 235, y + jitter, f"+ {row.rewards}")
    amount = f"+ C {row.amount}" if row.credit else f"C {row.amount}"
    w.words(base + 280, y, amount, jitter)
    if row.pi:
        w.words(base + 360, y + jitter, "l")
    if row.wrap:
        y += 9
        w.words(base + 85, y, row.wrap)
    if row.below:
        y += 9
        w.words(base + 85, y, row.below)
    return y + _ROW_PITCH


def make_statement_pdf(
    path: Path,
    *,
    domestic: list[Row],
    international: list[Row] = (),  # type: ignore[assignment]
    summary: dict[str, str] | None = None,
    emi_lines: list[str] = (),  # type: ignore[assignment]
    holder: tuple[str, str] = ("ASHA RAO", "12 LAKE VIEW ROAD BENGALURU 560001"),
    password: str | None = None,
    jitter: float = 1.5,
    extra_lines: dict[int, list[str]] | None = None,
    card: str = "123456XXXXXX1234",
    customer_no: str | None = CUSTOMER_NO,
    whole_rupee_total: bool = False,
) -> Path:
    values = _default_summary([*domestic, *international])
    if whole_rupee_total:
        # The statement prints its total due in whole rupees, rounded down
        # (measured on HDFC); the components keep their paise.
        exact = _dec(values["total_due"].removeprefix("C "))
        values["total_due"] = f"C {indian(exact.to_integral_value(ROUND_FLOOR))}"
    values.update(summary or {})
    extra = extra_lines or {}

    pdf = FPDF(unit="pt", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_font("Helvetica", size=_FONT)
    w = _Writer(pdf)
    header = "DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI"

    # Page 1
    pdf.add_page()
    w.words(25, 40, holder[0])
    w.words(25, 52, holder[1])
    w.words(25, 64, f"Card No: {card}")
    if customer_no is not None:
        w.words(25, 76, f"Customer No: {customer_no}")
    _equation_block(
        w,
        92,
        [
            ("PREVIOUS STATEMENT DUES", values["previous_balance"]),
            ("PAYMENTS/CREDITS", values["payments_credits"]),
            ("PURCHASES/DEBIT", values["purchases_debits"]),
            ("FINANCE CHARGES", values["finance_charges"]),
        ],
    )
    _summary_block(
        w,
        146,
        [
            ("TOTAL AMOUNT DUE", values["total_due"]),
            ("MINIMUM DUE", values["minimum_due"]),
            ("DUE DATE", values["due_date"]),
        ],
    )
    w.words(25, 172, f"Billing Period {values['period']}")
    w.words(PAGE_X[0], 184, "Domestic Transactions")
    w.words(PAGE_X[0], 196, header)
    y = _ROW_START
    for row in domestic:
        y = _row(w, PAGE_X[0], y, row, jitter)
    if y > 780:
        raise ValueError("too many domestic rows for the fixture's first page")
    y = 790.0
    for line in extra.get(0, []):
        w.words(25, y, line)
        y += 10
    w.words(270, 825, "Page 1 of 2")

    # Page 2
    pdf.add_page()
    w.words(PAGE_X[1], 40, header)
    w.words(PAGE_X[1], 60, "International Transactions")
    y = 80.0
    for row in international:
        y = _row(w, PAGE_X[1], y, row, jitter)
    w.words(
        PAGE_X[1], y + 6, f"TRANSACTIONS TOTAL AMOUNT {values['transactions_total']}"
    )
    y += 40
    w.words(PAGE_X[1], y, "EMI / Personal Loan on Credit Card")
    for line in emi_lines:
        y += 12
        w.words(PAGE_X[1], y, line)
    y += 24
    for line in extra.get(1, []):
        w.words(PAGE_X[1], y, line)
        y += 12
    w.words(270, 825, "Page 2 of 2")

    raw = bytes(pdf.output())
    if password is not None:
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(raw)))
        writer.encrypt(user_password=password, algorithm="AES-256")
        out = io.BytesIO()
        writer.write(out)
        raw = out.getvalue()
    path.write_bytes(raw)
    return path
