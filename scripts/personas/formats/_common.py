"""What every statement writer shares: dates, money text, the persona's
invented identity, per-bank narration styles, card cycles, deterministic
file output and the truth sidecar.

Nothing here reads the clock or the locale. Month names come from a fixed
table, every number is derived from the persona with sha256, and xlsx and
PDF metadata are pinned, so the same persona always yields the same bytes.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from fpdf import FPDF

from scripts.personas.model import Account, Event, Persona
from scripts.personas.simulate import DUE_DAY, STATEMENT_DAY

PAISA = Decimal("0.01")
RUPEE = Decimal("1")
MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip
SIDECAR_SUFFIX = ".truth.json"


# ---------------------------------------------------------------------------
# Dates and money as statements print them


def d_m_y(day: date, sep: str = "/", year4: bool = True) -> str:
    year = f"{day.year:04d}" if year4 else f"{day.year % 100:02d}"
    return f"{day.day:02d}{sep}{day.month:02d}{sep}{year}"


def d_mon_y(day: date, sep: str = "-", pad: bool = True) -> str:
    """01-Oct-2025 (sep "-") or 1 Oct 2025 (sep " ", pad False)."""
    dd = f"{day.day:02d}" if pad else str(day.day)
    return f"{dd}{sep}{MONTH_ABBR[day.month - 1]}{sep}{day.year:04d}"


def iso(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}-{day.day:02d}"


def indian(value: Decimal) -> str:
    """1234567.8 -> '12,34,567.80' (the sign is dropped: callers say Cr/Dr)."""
    whole, frac = f"{abs(value).quantize(PAISA):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{whole}.{frac}"


def plain(value: Decimal) -> str:
    """1234.5 -> '1234.50'."""
    return f"{value.quantize(PAISA):.2f}"


def as_float(value: Decimal) -> float:
    """A spreadsheet number. Two-decimal values round-trip exactly through
    the shortest float repr, so reading the cell back gives the same Decimal."""
    return float(value.quantize(PAISA))


def truncate_rupee(value: Decimal) -> Decimal:
    return value.quantize(RUPEE, rounding=ROUND_DOWN)


# ---------------------------------------------------------------------------
# The persona's invented identity


def digits(*parts: str, n: int) -> str:
    """`n` digits derived from `parts`: stable across runs and machines."""
    raw = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return str(int(raw, 16))[-n:].zfill(n)


def holder(persona: Persona) -> str:
    return persona.display_name.upper()


# city -> (area, city as printed, PIN, state). PINs and areas are public.
_CITIES = {
    "Bengaluru": ("INDIRANAGAR", "BENGALURU", "560038", "KARNATAKA"),
    "Pune": ("BANER", "PUNE", "411045", "MAHARASHTRA"),
    "Hyderabad": ("GACHIBOWLI", "HYDERABAD", "500032", "TELANGANA"),
    "Gurugram": ("SECTOR 54", "GURUGRAM", "122011", "HARYANA"),
    "Mumbai": ("POWAI", "MUMBAI", "400076", "MAHARASHTRA"),
}
_SOCIETIES = (
    "LAKESHORE RESIDENCY",
    "GREEN ORCHID TOWERS",
    "SILVER OAK ENCLAVE",
    "PALM CREST HEIGHTS",
    "RIVERSTONE COURT",
)


@dataclass(frozen=True)
class Identity:
    name: str
    lines: tuple[str, str, str]  # flat, area, city - pin
    city: str
    state: str
    pin: str


def identity(persona: Persona) -> Identity:
    area, city, pin, state = _CITIES[persona.city]
    n = int(digits(persona.slug, "flat", n=3)) % 19 + 2
    wing = "ABCD"[int(digits(persona.slug, "wing", n=2)) % 4]
    society = _SOCIETIES[int(digits(persona.slug, "society", n=2)) % len(_SOCIETIES)]
    return Identity(
        holder(persona),
        (f"FLAT {n}{wing}, {society}", f"{area}", f"{city} - {pin}"),
        city,
        state,
        pin,
    )


def masked_mobile(persona: Persona) -> str:
    return "XXXXXX" + digits(persona.slug, "mobile", n=4)


def masked_email(persona: Persona) -> str:
    first = persona.display_name.split()[0].lower()
    return f"{first[:2]}{'*' * 6}@example.com"


def customer_id(persona: Persona, account: Account, n: int = 8) -> str:
    return digits(persona.slug, account.key, "customer", n=n)


# The 4-letter IFSC prefix each institution's branches use (public).
BANK_CODE = {
    "HDFC": "HDFC",
    "IDFC": "IDFB",
    "SBI": "SBIN",
    "Kotak": "KKBK",
    "ICICI": "ICIC",
    "Axis": "UTIB",
}
# Brokers collect and pay out through a bank account of their own.
_BROKER_BANK = {"Groww": "ICIC", "Zerodha": "HDFC"}
_BROKER_NAME = {"Groww": "GROWW", "Zerodha": "ZERODHA BROKING LTD"}
# UPI handle -> the bank behind it, as SBI prints it.
_HANDLE_BANK = {
    "axisbank": "UTIB",
    "okaxis": "UTIB",
    "hdfcbank": "HDFC",
    "okhdfcbank": "HDFC",
    "icici": "ICIC",
    "okicici": "ICIC",
    "sbi": "SBIN",
    "oksbi": "SBIN",
    "ybl": "YESB",
    "apl": "UTIB",
    "airtel": "AIRP",
    "idfcbank": "IDFB",
    "kotak": "KKBK",
}
# The holder's own UPI handle at each bank.
_OWN_HANDLE = {
    "HDFC": "okhdfcbank",
    "IDFC": "idfcbank",
    "SBI": "oksbi",
    "Kotak": "kotak",
}


def bank_code(account: Account) -> str:
    if account.kind in ("broker_mf", "broker_stocks"):
        return _BROKER_BANK[account.institution]
    return BANK_CODE[account.institution]


def ifsc(persona: Persona, account: Account) -> str:
    """An IFSC-shaped branch code for the account: the institution's real
    prefix, then an invented branch number."""
    return f"{bank_code(account)}0{digits(persona.slug, account.key, 'ifsc', n=6)}"


def party(persona: Persona, account: Account) -> str:
    """Who the other side of a movement is called: the holder for their own
    bank, the broker's name for a broker."""
    if account.kind in ("broker_mf", "broker_stocks"):
        return _BROKER_NAME[account.institution]
    return holder(persona)


def vpa(persona: Persona, account: Account) -> str:
    if account.kind in ("broker_mf", "broker_stocks"):
        return f"{account.institution.lower()}.payments@icici"
    first = persona.display_name.split()[0].lower()
    return f"{first}.{account.last4}@{_OWN_HANDLE[account.institution]}"


def handle_bank(address: str) -> str:
    return _HANDLE_BANK.get(address.rsplit("@", 1)[-1].lower(), "NA")


# ---------------------------------------------------------------------------
# Narrations: the canonical text, restyled per bank


@dataclass(frozen=True)
class Upi:
    """A canonical UPI narration taken apart. Both canonical shapes
    (`UPI/<ref>/<NAME>/<vpa>/<note>` and `UPI-<NAME>-<vpa>-<ref>-<note>`) and
    the reversal (`UPI/REV/<ref>/...`) parse to the same parts."""

    ref: str
    name: str
    vpa: str
    note: str
    reversal: bool = False


def parse_upi(event: Event) -> Upi | None:
    text, ref = event.narration, event.ref
    if not ref or not text.startswith("UPI"):
        return None
    reversal = text.startswith("UPI/REV/")
    if text.startswith("UPI/"):
        body = text.removeprefix("UPI/REV/" if reversal else "UPI/")
        got, name, address, note = body.split("/", 3)
        if got != ref:
            raise ValueError(f"UPI narration without its ref first: {text!r}")
        return Upi(ref, name, address, note, reversal)
    if text.startswith("UPI-") and f"-{ref}-" in text:
        head, note = text.removeprefix("UPI-").split(f"-{ref}-", 1)
        name, address = head.split("-", 1)
        return Upi(ref, name, address, note)
    raise ValueError(f"unrecognised UPI narration: {text!r}")


@dataclass(frozen=True)
class Move:
    """One leg of a movement between two of the persona's accounts, as the
    bank side prints it: `debit` is money leaving this account."""

    rail: str  # UPI, IMPS or NEFT
    ref: str
    debit: bool
    name: str  # the other party (the holder, or a broker)
    ifsc: str  # the other side's branch code
    code: str  # the other side's 4-letter bank code
    last4: str  # the other side's account last 4
    vpa: str  # the other side's UPI address


class BankStyle:
    """How one bank prints each kind of row. A subclass overrides the forms
    its bank uses; anything not restyled keeps the canonical narration."""

    def upi(self, u: Upi, debit: bool) -> str:
        raise NotImplementedError

    def neft(self, m: Move) -> str:
        raise NotImplementedError

    def imps(self, m: Move) -> str:
        raise NotImplementedError

    def upi_move(self, m: Move) -> str:
        return self.upi(Upi(m.ref, m.name, m.vpa, self.move_note(m)), m.debit)

    def move_note(self, m: Move) -> str:
        return "SELF TRANSFER" if m.name not in _BROKER_NAME.values() else "ADD FUNDS"

    def card_bill(self, card: Account, ref: str) -> str:
        raise NotImplementedError

    def payout(self, m: Move) -> str:
        """A broker paying redemption proceeds in (NEFT, a credit)."""
        return self.neft(m)

    def render(self, e: Event, persona: Persona) -> str:
        kind = e.kind
        if kind in ("transfer_out", "transfer_in", "platform_funding"):
            return self._move(e, persona)
        if kind == "mf_redemption_payout":
            m = self._leg(e, persona, rail="NEFT")
            return self.payout(m)
        if kind == "card_bill_payment":
            card = persona.account(e.counter_account_key or "")
            return self.card_bill(card, e.ref or "")
        upi = parse_upi(e)
        if upi is not None:
            return self.upi(upi, e.amount < 0)
        return e.narration

    def _leg(self, e: Event, persona: Persona, rail: str) -> Move:
        other = persona.account(e.counter_account_key or "")
        return Move(
            rail=rail,
            ref=e.ref or "",
            debit=e.amount < 0,
            name=party(persona, other),
            ifsc=ifsc(persona, other),
            code=bank_code(other),
            last4=other.last4,
            vpa=vpa(persona, other),
        )

    def _move(self, e: Event, persona: Persona) -> str:
        rail = e.narration.split("/", 1)[0]
        m = self._leg(e, persona, rail)
        if rail == "NEFT":
            return self.neft(m)
        if rail == "IMPS":
            return self.imps(m)
        if rail == "UPI":
            return self.upi_move(m)
        raise ValueError(f"unknown rail in {e.narration!r}")


def card_text(e: Event, payment: str) -> str:
    """A card row: purchases and refunds as `<MERCHANT> <CITY> IN`; the
    issuer's own wording for a payment received; the rest as simulated."""
    if e.kind == "card_payment_received":
        return payment.format(ref=e.ref or "")
    if e.kind in ("spend", "refund") and not e.narration.endswith(" IN"):
        return f"{e.narration} IN"
    return e.narration


# ---------------------------------------------------------------------------
# Rows, the truth sidecar and statement periods


@dataclass(frozen=True)
class Row:
    """One event and its position in the account's event list (`seq`)."""

    seq: int
    event: Event


def numbered(events: list[Event]) -> list[Row]:
    return [Row(i, e) for i, e in enumerate(events)]


@dataclass
class Sidecar:
    """The ordered mapping from rendered rows to truth events, for the
    checker. Build output only: never committed, never given to the agent."""

    persona: Persona
    account: Account
    file: Path
    rows: list[dict[str, Any]] = field(default_factory=list)
    unrendered: list[dict[str, Any]] = field(default_factory=list)

    def add(self, row: Row, text: str, **locator: Any) -> None:
        self.rows.append(
            {"locator": locator, "text": text, "seq": row.seq, **_event(row.event)}
        )

    def skip(self, row: Row, reason: str) -> None:
        self.unrendered.append({"seq": row.seq, "reason": reason, **_event(row.event)})

    def write(self) -> Path:
        path = self.file.with_name(self.file.name + SIDECAR_SUFFIX)
        doc = {
            "schema": 1,
            "persona": self.persona.slug,
            "account_key": self.account.key,
            "kind": self.account.kind,
            "institution": self.account.institution,
            "file": self.file.name,
            "rows": self.rows,
            "unrendered": self.unrendered,
        }
        text = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")
        return path


def _event(e: Event) -> dict[str, Any]:
    return {
        "date": iso(e.date),
        "amount": plain(e.amount),
        "kind": e.kind,
        "ref": e.ref,
        "pair_id": e.pair_id,
        "narration": e.narration,
    }


def statement_name(account: Account, day: date, suffix: str) -> str:
    return f"{account.key}_{account.last4}_{iso(day)}{suffix}"


@dataclass(frozen=True)
class CardStatement:
    """One card statement: rows after the previous statement date up to and
    including `statement_date`. Balances use the ledger sign (owed < 0)."""

    start: date
    statement_date: date
    due_date: date
    opening: Decimal
    rows: list[Row]

    @property
    def debits(self) -> Decimal:
        return -sum(
            (r.event.amount for r in self.rows if r.event.amount < 0), Decimal(0)
        )

    @property
    def credits(self) -> Decimal:
        return sum(
            (r.event.amount for r in self.rows if r.event.amount > 0), Decimal(0)
        )

    @property
    def closing(self) -> Decimal:
        return self.opening - self.debits + self.credits

    @property
    def total_due(self) -> Decimal:
        """What the statement prints as owed (positive when owed)."""
        return -self.closing

    @property
    def minimum_due(self) -> Decimal:
        if self.total_due <= 0:
            return Decimal(0)
        five = (self.total_due * Decimal("0.05")).quantize(PAISA, ROUND_HALF_UP)
        return min(self.total_due, max(five, Decimal(200)))


def _month_after(day: date, dom: int) -> date:
    y, m = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(y, m, dom)


def card_statements(
    account: Account, events: list[Event], period: tuple[date, date]
) -> list[CardStatement]:
    """Every statement whose cycle overlaps the period: one per 15th inside
    it, then the next one, which carries the activity after the last 15th
    (the simulated world ends with the period, so it holds no later rows)."""
    start, end = period
    stmt = (start.replace(day=1) - timedelta(days=1)).replace(day=STATEMENT_DAY)
    rows, opening, out = numbered(events), account.opening_balance, []
    while stmt < end:
        prev, stmt = stmt, _month_after(stmt, STATEMENT_DAY)
        cycle = [r for r in rows if prev < r.event.date <= stmt]
        statement = CardStatement(
            prev + timedelta(days=1),
            stmt,
            _month_after(stmt, DUE_DAY),
            opening,
            cycle,
        )
        out.append(statement)
        opening = statement.closing
    if sum(len(s.rows) for s in out) != len(rows):
        raise ValueError(f"{account.key}: card rows outside every statement")
    return out


def credit_limit(account: Account, statements: list[CardStatement]) -> Decimal:
    """A limit comfortably above the card's largest bill, in 50,000 steps."""
    peak = max(
        [abs(account.opening_balance), *(s.debits for s in statements)],
        default=Decimal(0),
    )
    steps = int((peak * 4 / 50000).to_integral_value(rounding=ROUND_CEILING))
    return Decimal(max(steps, 2) * 50000)


def clock(*parts: str, open_: tuple[int, int] = (9, 15)) -> tuple[int, int, int]:
    """A stable (hour, minute, second) between `open_` and 15:29."""
    span = (15 * 60 + 29) - (open_[0] * 60 + open_[1])
    n = int(digits(*parts, n=8))
    minute = open_[0] * 60 + open_[1] + n % span
    return minute // 60, minute % 60, n // span % 60


# ---------------------------------------------------------------------------
# Deterministic files

# Every zip entry and every document timestamp is pinned to the statement's
# own date at this time of day, never the time of the build.
_STAMP_TIME: tuple[int, int, int] = (9, 0, 0)


def stamp(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, *_STAMP_TIME, tzinfo=timezone.utc)


_CORE_DATE = re.compile(
    rb"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:(created|modified)>)"
)


def save_xlsx(workbook: Any, path: Path, day: date) -> Path:
    """Save an openpyxl workbook with its core-properties dates and every zip
    entry's timestamp pinned to `day`, so rebuilding is byte-identical."""
    when = stamp(day)
    workbook.properties.creator = "SpendSift demo personas"
    workbook.properties.created = when.replace(tzinfo=None)
    buffer = io.BytesIO()
    workbook.save(buffer)
    pinned = when.strftime("%Y-%m-%dT%H:%M:%SZ").encode("ascii")
    hour, minute, second = _STAMP_TIME
    zip_time = (day.year, day.month, day.day, hour, minute, second)
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as src,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                data = _CORE_DATE.sub(rb"\g<1>" + pinned + rb"\g<3>", data)
            entry = zipfile.ZipInfo(info.filename, date_time=zip_time)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = info.external_attr
            dst.writestr(entry, data)
    path.write_bytes(out.getvalue())
    return path


def new_pdf(day: date, title: str) -> FPDF:
    """An A4 PDF in points with its creation date, producer, creator and
    title pinned (fpdf derives the file ID from these and the content)."""
    pdf = FPDF(unit="pt", format="A4")
    pdf.set_creation_date(stamp(day))
    pdf.set_producer("SpendSift demo personas")
    pdf.set_creator("SpendSift demo personas")
    pdf.set_title(title)
    pdf.set_auto_page_break(False)
    pdf.set_font("Helvetica", size=8)
    return pdf


def text_right(pdf: FPDF, x1: float, y: float, text: str) -> None:
    pdf.text(x1 - pdf.get_string_width(text), y, text)


def text_centred(pdf: FPDF, centre: float, y: float, text: str) -> None:
    pdf.text(centre - pdf.get_string_width(text) / 2, y, text)


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    import csv

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8", newline="")
    return path
