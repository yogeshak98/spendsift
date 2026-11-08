"""Structural redaction of free text shown to a model.

A statement's holder name, address and PIN code must never reach the model.
Rather than hunt for names, every non-transaction line keeps only what is
recognisably statement structure: amounts, dates, known statement words and
punctuation. Everything else is replaced by a token. The vocabulary holds no
person or place names and errs towards redacting.

Month words are not vocabulary: April, June and May are first names, and
August Kranti Marg or 21 March Road are streets. A month word, and the day
and year beside it, stay in the clear only when together they make one of
the two month forms DATE_TEXT reads, and both need a 4-digit year:
day-first `21 Sep, 2026` / `21 September 2026` (a day 1-31, the month with
an optional comma, a year) and month-first `September 22, 2026` /
`Sep 22 2026` (the month, a day 1-31 with an optional comma, a year). So a
house number before a month (`12 AUGUST KRANTI MARG`) is not a date, and
neither is a month beside a lone day or a lone year; all of it is masked.

Card numbers: any run of tokens with the card shape (`card_shaped`,
pdf_words.py) is shown as `XXXXXXXXXXXX` + its last 4 digits, masked or
not. That includes an unmasked number of 16 or more digits (a customer or
loan number), whose last 4 then show (ruling P15): showing the last 4 is the
usual masking convention for a card, and the prover needs them to write a
`card_pattern`. Its other digits, and the BIN of a masked card, never show.
"""

from __future__ import annotations

import re
import string

from backend.app.llm.flows.prove_template.mask import mask
from backend.app.service.ingest.parsers.pdf_words import (
    AMOUNT_WORD,
    MONTHS,
    card_shaped,
    normalise_card_token,
)

REDACTED = "▢"  # U+25A2

_NUM_DATE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")
_MON_DATE = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})$")
_DATE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$|^\d{1,2}-[A-Za-z]{3}-\d{2,4}$")
_INT = re.compile(r"^\d+$")
_DAY = re.compile(r"^(\d{1,2})$")
_DAY_COMMA = re.compile(r"^(\d{1,2}),?$")
_YEAR = re.compile(r"^(?:19|20)\d{2},?$")
_MONTH_WORD = re.compile(r"^([A-Za-z]+)$")
_MONTH_COMMA = re.compile(r"^([A-Za-z]+),?$")
_MASKED_ID = re.compile(r"^X{2,}\d{0,4}$")
_PLACEHOLDER = re.compile(r"^<(?:EMAIL|PAN)>$")
_GLYPHS = frozenset({"C", "₹", "Rs.", "INR", "+", "|", "&", "-", "/"})

_MONTHS = MONTHS | {"SEPT"}

STATEMENT_VOCAB: frozenset[str] = frozenset(
    {
        "PREVIOUS", "STATEMENT", "DUES", "DUE", "PAYMENTS", "PAYMENT", "CREDITS",
        "CREDIT", "PURCHASES", "PURCHASE", "DEBIT", "DEBITS", "FINANCE", "CHARGES",
        "CHARGE", "TOTAL", "AMOUNT", "AMT", "MINIMUM", "DATE", "LIMIT", "AVAILABLE",
        "CASH", "BILLING", "PERIOD", "TRANSACTIONS", "TRANSACTION", "DESCRIPTION",
        "REWARDS", "PI", "DOMESTIC", "INTERNATIONAL", "TIME", "PAGE", "OF", "EMI",
        "LOAN", "PERSONAL", "ON", "GST", "GSTN", "POINTS", "BALANCE", "BAL",
        "OPENING", "CLOSING", "CARD", "NUMBER", "NO", "ACCOUNT", "SUMMARY", "PAST",
        "IF", "ANY", "OVERLIMIT", "CURRENT", "MONTH", "MONTHS", "CUSTOMER", "CR",
        "DR",
        # Summary and table-header words measured on ICICI, HDFC and Axis
        # statements (Task 6b), so a prover can anchor on them.
        "ADVANCE", "ADVANCES", "OTHER", "DETAILS", "MERCHANT", "CATEGORY",
        "REWARD", "SPENDS", "SPEND", "SERNO", "SER", "SERIAL", "REFERENCE", "REF",
        "PARTICULARS", "CASHBACK", "EARNED", "GENERATION", "OUTSTANDING",
        "INTEREST", "FEE", "FEES", "TAX", "IGST", "CGST", "SGST",
        # No month words: they show only inside a date (_date_tokens).
    }
)  # fmt: skip


def _bare(token: str) -> str:
    return token.upper().strip(string.punctuation)


def _valid_date(token: str) -> bool:
    token = token.removesuffix("|")
    m = _NUM_DATE.match(token)
    if m:
        d, mo, y = m.groups()
        return 1 <= int(d) <= 31 and 1 <= int(mo) <= 12 and len(y) in (2, 4)
    m = _MON_DATE.match(token)
    if m:
        d, mon, y = m.groups()
        return 1 <= int(d) <= 31 and mon.upper() in _MONTHS and len(y) in (2, 4)
    return False


def is_transaction_shaped(text: str) -> bool:
    """A valid leading date AND an amount token on the line. The amount
    requirement keeps dash-separated house numbers ("12-3-45 ...") out."""
    tokens = text.split(" ")
    return _valid_date(tokens[0]) and any(AMOUNT_WORD.fullmatch(t) for t in tokens[1:])


def _in_vocab(token: str) -> bool:
    parts = [p for p in _bare(token).split("/") if p]
    return bool(parts) and all(p in STATEMENT_VOCAB for p in parts)


_MAX_CARD_TOKENS = 8
_TRAILING = ",.;:"


def _normalise_cards(tokens: list[str]) -> list[str]:
    """Replace each run of tokens that has the card shape with one
    XXXXXXXXXXXX<last4> token, before anything else could hide its last 4."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        for j in range(min(len(tokens), i + _MAX_CARD_TOKENS), i, -1):
            run = " ".join(tokens[i:j])
            stripped = run.rstrip(_TRAILING)
            tail = run[len(stripped) :]
            if card_shaped(stripped):
                out.append((normalise_card_token(stripped) or "") + tail)
                i = j
                break
        else:
            out.append(tokens[i])
            i += 1
    return out


def _is_day(pattern: re.Pattern[str], token: str) -> bool:
    m = pattern.match(token)
    return m is not None and 1 <= int(m.group(1)) <= 31


def _is_month(pattern: re.Pattern[str], token: str) -> bool:
    m = pattern.match(token)
    return m is not None and m.group(1).upper() in _MONTHS


def _date_tokens(tokens: list[str]) -> set[int]:
    """Indices of the tokens that form a month date DATE_TEXT reads: day,
    month, year (`21 Sep, 2026`) or month, day, year (`Sep 22, 2026`). A
    month without both a day and a year beside it is not a date."""
    keep: set[int] = set()
    for i in range(len(tokens) - 2):
        a, b, c = tokens[i : i + 3]
        if not _YEAR.match(c):
            continue
        if _is_day(_DAY, a) and _is_month(_MONTH_COMMA, b):
            keep.update((i, i + 1, i + 2))  # day-first
        elif _is_month(_MONTH_WORD, a) and _is_day(_DAY_COMMA, b):
            keep.update((i, i + 1, i + 2))  # month-first
    return keep


def redact_free_text(text: str) -> str:
    tokens = _normalise_cards(text.split(" "))
    dates = _date_tokens(tokens)
    out: list[str] = []
    for i, tok in enumerate(tokens):
        if tok == "":
            out.append(tok)
        elif AMOUNT_WORD.fullmatch(tok) or _DATE.match(tok) or i in dates:
            out.append(tok)
        elif tok in _GLYPHS or (len(tok) == 1 and not tok.isalnum()):
            out.append(tok)
        elif _PLACEHOLDER.match(tok) or _MASKED_ID.match(_bare_keep(tok)):
            out.append(tok)
        elif (
            len(tok) == 4
            and _INT.match(tok)
            and _MASKED_ID.match(_bare_keep(tokens[i - 1]) if i else "")
        ):
            out.append(tok)  # last four digits of an already-masked card number
        elif _in_vocab(tok):
            out.append(tok)
        elif _INT.match(tok):
            out.append("#" * len(tok))
        else:
            out.append(REDACTED)
    return " ".join(out)


def _bare_keep(token: str) -> str:
    return token.upper().strip(":,.;")


def redact_label(label: str) -> str:
    """A summary label as the model may see it."""
    return redact_free_text(mask(str(label)))
