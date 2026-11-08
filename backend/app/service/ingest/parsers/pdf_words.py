"""One definition of statement words, shared by the PDF parser and the peek.

Amount words, month words, date text, the "this line opens with a date" rule
and the card-number shape live here and nowhere else. The parser reads values
with these definitions and the agent's redacted view keeps exactly the same
text, so what the agent sees and what code reads agree.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from backend.app.service.ingest.parsers.pdf_reader import PdfLine

# ---------------------------------------------------------------------------
# Amounts

# The currency prefixes a value may be glued to: the rupee sign, Rs./Rs, INR,
# and two font encodings of the rupee sign measured on real statements (`C`
# on HDFC, a backtick on ICICI). One alternation, for both regexes below.
_CURRENCY = r"₹|Rs\.?|INR|C|`"
# Fullmatch semantics. A glued suffix (9,999.99CR) is deliberately not an amount.
AMOUNT_WORD = re.compile(rf"^({_CURRENCY})?[+-]?[\d,]*\d\.\d{{2}}$")
_PREFIX = re.compile(rf"^(?:{_CURRENCY})")


def parse_money(text: str) -> Decimal | None:
    """The value of an amount, tolerating a currency prefix, commas and spaces."""
    cleaned = _PREFIX.sub("", re.sub(r"[,\s]", "", str(text)))
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def parse_amount_word(text: str) -> Decimal | None:
    """The value of `text` when it is exactly one amount word, else None."""
    if not AMOUNT_WORD.fullmatch(text):
        return None
    return parse_money(text)


# ---------------------------------------------------------------------------
# Months and dates

_SHORT = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT")
_FULL = (
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
    "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
)  # fmt: skip
MONTHS: frozenset[str] = frozenset({*_SHORT, "NOV", "DEC", *_FULL})

_MONTH_ALTERNATES = "|".join(str(m) for m in sorted(MONTHS, key=len, reverse=True))
_MONTH = f"(?:{_MONTH_ALTERNATES})(?![A-Za-z])"

# The date forms a summary may print: 21 Sep, 2026 | 21 September 2026 |
# Sep 22, 2026 | September 22, 2026 | 21/09/2026.
DATE_TEXT = re.compile(
    rf"(?<!\d)\d{{1,2}}[ /-](?i:{_MONTH}),? \d{{4}}"
    rf"|(?i:(?<![A-Za-z]){_MONTH}) \d{{1,2}},? \d{{4}}"
    r"|(?<!\d)\d{2}/\d{2}/\d{4}"
)

_LEADING_DATE = re.compile(
    r"^\s*(?:"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    rf"|\d{{1,2}}[ /-](?i:{_MONTH})[ ,/-]"
    rf"|(?i:{_MONTH})\.? \d{{1,2}}(?!\d)"
    r")"
)


def starts_with_date(text: str) -> bool:
    """The line opens with a numeric date or a real month name (day-first or
    month-first). "12 MAY STREET" does; "1 THE MALL" and "24 HRS" do not."""
    return _LEADING_DATE.match(text) is not None


# ---------------------------------------------------------------------------
# Card numbers

# At least 15 characters of [0-9X*] (separators not counted) with single
# space or hyphen separators, ending in four digits. Shorter masked numbers
# (a mobile, a bank account) are not cards. The mask-or-16-digits rule is
# applied by _is_card.
CARD_MIN_CHARS = 15
_CARD_CHARS = frozenset("0123456789X*")
CARD_SHAPE = re.compile(
    rf"(?<![0-9X*])(?:[0-9X*][ -]?){{{CARD_MIN_CHARS - 4},}}\d{{4}}"
    r"(?![0-9X*])(?![ -][0-9X*])"
)


def _is_card(text: str) -> bool:
    chars = re.sub(r"[ -]", "", text)
    return len(chars) >= CARD_MIN_CHARS and (
        "X" in chars or "*" in chars or (chars.isdigit() and len(chars) >= 16)
    )


def card_shaped(text: str) -> bool:
    """The whole string has the card shape."""
    return CARD_SHAPE.fullmatch(text) is not None and _is_card(text)


def normalise_card_token(token: str) -> str | None:
    """XXXXXXXXXXXX + the last 4 digits when `token` is card-shaped, else None."""
    if not card_shaped(token):
        return None
    return "X" * 12 + token[-4:]


def _trailing_card(text: str, start: int, end: int) -> bool:
    """Some suffix of text[start:end] that starts at a run boundary (a
    [0-9X*] character not right after another) is card-shaped. Tried longest
    first; a suffix starting mid-run never counts."""
    for i in range(start, end):
        if text[i] not in _CARD_CHARS or (i > 0 and text[i - 1] in _CARD_CHARS):
            continue
        if card_shaped(text[i:end]):
            return True
    return False


def _run_ends(text: str, end: int) -> bool:
    """No card character follows `end`, directly or after one separator."""
    nxt, after = text[end : end + 1], text[end + 1 : end + 2]
    if nxt in _CARD_CHARS:
        return False
    return not (nxt in (" ", "-") and after in _CARD_CHARS)


def find_card_last4(
    lines: list[PdfLine], card_pattern: str
) -> tuple[set[str], list[int]]:
    """(distinct last4 of matches whose trailing run is a card, indices of
    lines holding card-shaped text at all, matched by the pattern or not).

    A match counts when it ends with its 4-digit `last4` group, the card run
    ends there too (no card character follows, directly or after one
    separator), and its longest suffix that starts at a run boundary is
    card-shaped, so a label before the
    number (`Card No: ...`) is allowed, while a year, branch code or phone
    number is not. A pattern without a `last4` group, or an invalid one,
    finds no last4."""
    card_lines = [
        line.index
        for line in lines
        if any(_is_card(m.group()) for m in CARD_SHAPE.finditer(line.text))
    ]
    try:
        pattern = re.compile(card_pattern)
    except re.error:
        return set(), card_lines
    if "last4" not in pattern.groupindex:
        return set(), card_lines
    found: set[str] = set()
    for line in lines:
        for m in pattern.finditer(line.text):
            last4 = m.group("last4")
            if (
                last4 is not None
                and re.fullmatch(r"\d{4}", last4)
                and m.end("last4") == m.end()
                and _run_ends(line.text, m.end())
                and _trailing_card(line.text, m.start(), m.end())
            ):
                found.add(last4)
    return found, card_lines
