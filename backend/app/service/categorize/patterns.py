"""Turn a normalised narration into a learnable merchant pattern."""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from backend.app.persistence.seeds import LEARN_WORDS

REASON_EMPTY = "no merchant words"
REASON_SHORT = "shorter than 4 characters"
REASON_GENERIC = "only generic words"


@dataclass(frozen=True)
class LearnWords:
    fee_phrases: tuple[str, ...]
    generic: frozenset[str]
    places: frozenset[str]
    upi_aggregators: frozenset[str] = frozenset()


def _load() -> LearnWords:
    data = yaml.safe_load(LEARN_WORDS.read_text(encoding="utf-8"))
    return LearnWords(
        fee_phrases=tuple(str(p).upper() for p in data["fee_phrases"]),
        generic=frozenset(str(w).upper() for w in data["generic"]),
        places=frozenset(str(w).upper() for w in data["places"]),
        upi_aggregators=frozenset(
            str(w).upper() for w in data.get("upi_aggregators") or ()
        ),
    )


WORDS = _load()

_TOKEN = re.compile(r"[^\s,]+")
_FEE_RES = tuple(
    re.compile(r"\b" + r"\s+".join(re.escape(w) for w in p.split()) + r"\b")
    for p in WORDS.fee_phrases
)


def learn_pattern(normalized_narration: str) -> str:
    """Contiguous upper-case slice of the input: merchant words only."""
    toks = list(_TOKEN.finditer(normalized_narration))
    i = 0
    while i < len(toks) and (
        any(c.isdigit() for c in toks[i].group()) or toks[i].group() in ("#", "@")
    ):
        i += 1
    run = []
    for t in toks[i:]:
        if any(c.isdigit() for c in t.group()):
            break
        run.append(t)
    while run and run[-1].group().strip(".*") in WORDS.places:
        run.pop()
    if not run:
        return ""
    return normalized_narration[run[0].start() : run[-1].end()]


def learnability(pattern: str) -> str | None:
    if not pattern.strip():
        return REASON_EMPTY
    if len(pattern.replace(" ", "")) < 4:
        return REASON_SHORT
    if all(w in WORDS.generic for w in re.findall(r"\w+", pattern)):
        return REASON_GENERIC
    return None


def is_fee_narration(raw_narration: str) -> bool:
    up = raw_narration.upper()
    return any(rx.search(up) for rx in _FEE_RES)


_UPI = re.compile(r"^UPI[/-]", re.IGNORECASE)
_UPI_RAIL = frozenset({"DR", "CR", "P2A", "P2M"})
_CARD_SEP = " - "
_WORD = re.compile(r"[A-Z0-9]+")


def _merchant_words(text: str) -> list[str]:
    """The alphanumeric words of `text`, upper-cased, without any word holding
    a digit (references, dates, amounts, card digits), and with trailing place
    words (a city) dropped."""
    words = [w for w in _WORD.findall(text.upper()) if not any(c.isdigit() for c in w)]
    while words and words[-1] in WORDS.places:
        words.pop()
    return words


def is_upi(raw_narration: str) -> bool:
    text = raw_narration.strip()
    return bool(_UPI.match(text)) and not text.upper().startswith("UPI-LITE")


def _is_aggregator(payee: str) -> bool:
    return any(payee == a or payee.startswith(a + " ") for a in WORDS.upi_aggregators)


def upi_merchant(raw_narration: str) -> tuple[str, str | None] | None:
    """(payee, full VPA lower-cased) of a UPI narration, else None.

    Two forms: "UPI/<DR|CR|P2A|P2M>/<ref>/<payee>/<bank>/<vpa>/<remark>" and
    "UPI-<payee>-<vpa>-<ifsc>-<ref>-<remark>". Only the rail words and ONE
    all-digit reference are skipped to reach the payee slot; a payee slot that
    is blank or a phone number gives None -- the next slot is a bank code,
    never a payee. The VPA is the first part holding "@" (None when there is
    none). The remark is free text and is never read.
    """
    text = raw_narration.strip()
    if not is_upi(text):
        return None
    parts = [p.strip() for p in text.split(text[3])][1:]
    i = 0
    if text[3] == "/":
        while i < len(parts) and parts[i].upper() in _UPI_RAIL:
            i += 1
        if i < len(parts) and parts[i].isdigit():
            i += 1  # the reference
    if i >= len(parts):
        return None
    payee_part, vpa = parts[i], None
    if "@" in payee_part:
        vpa, payee_part = payee_part, payee_part.partition("@")[0]
    payee = " ".join(_merchant_words(payee_part))
    if not payee:
        return None  # blank, or a phone number: no payee name to key on
    if vpa is None:
        vpa = next((p for p in parts[i + 1 :] if "@" in p), None)
    return payee, (vpa.lower() if vpa else None)


def card_descriptor(raw_narration: str) -> str | None:
    """The descriptor after " - " in a card narration ("IND*AMAZON.IN -
    GROCER" gives "GROCER"), without digits or a trailing city; None when
    the narration has no " - "."""
    if _CARD_SEP not in raw_narration:
        return None
    return " ".join(_merchant_words(raw_narration.split(_CARD_SEP, 1)[1]))


def merchant_key(raw_narration: str, normalized_narration: str) -> tuple | None:
    """What two rows must share to be the same merchant for the review spread,
    or None when the row names no merchant to spread on.

    - UPI: the payee name and the full VPA. The learn pattern is not used: the
      normaliser keeps the longest segment, often the free-text remark. None
      when the payee is blank or a phone number; when there is no VPA (a
      payee name alone merges different people: banks print first names);
      and for a payment app or aggregator (`upi_aggregators`) whatever its
      VPA, since one collective VPA ("AMAZONUPI@APL", "CRED.CLUB@AXISB")
      carries every merchant and bill paid through it.
    - Card style ("MERCHANT - DESCRIPTOR"): the learn pattern and the
      descriptor, so "- BILL P" and "- GROCER" differ.
    - Anything else: the learn pattern and the raw narration's words without
      digits or a trailing city, so NEFT/IMPS payees sharing bank
      boilerplate differ while "X BANGALORE" and "X BENGALURU" match.
    """
    if is_upi(raw_narration):
        upi = upi_merchant(raw_narration)
        if upi is None:
            return None
        payee, vpa = upi
        if vpa is None or _is_aggregator(payee):
            return None
        return ("upi", payee, vpa)
    pattern = learn_pattern(normalized_narration)
    descriptor = card_descriptor(raw_narration)
    if descriptor is not None:
        return ("card", pattern, descriptor)
    return ("plain", pattern, tuple(_merchant_words(raw_narration)))


UPI_RULE_PREFIX = "UPI:"


def upi_identity(raw_narration: str) -> str | None:
    """The learned-rule pattern of a UPI row, "UPI:<PAYEE>|<VPA>", upper-cased
    as rules are stored; None for a row that is not UPI or names no merchant
    (`merchant_key` gives None: no VPA, a phone-number or blank payee, or an
    aggregator).

    The same identity the review spread keys on, so a rule learned from a
    row fires on the payments the spread would treat as that merchant. Such
    a rule is stored marked `match: identity` (see `LearnedRules`)."""
    if not is_upi(raw_narration):
        return None
    key = merchant_key(raw_narration, "")
    if key is None:
        return None
    _, payee, vpa = key
    return f"{UPI_RULE_PREFIX}{payee}|{vpa}".upper()
