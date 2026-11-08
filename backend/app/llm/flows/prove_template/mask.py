"""Identifier masking for anything shown to a model, so no e-mail, PAN or
account/card number in a statement reaches it."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# No `\b`: a PAN glued to "_" or letters (PAN_ABCDE1234F.csv, UPIABCDE1234F)
# must still be masked (ruling R11). The lookahead alone keeps a PAN-shaped
# slice of a longer token (XABCDE1234FX) unmasked.
_PAN = re.compile(r"[A-Z]{5}\d{4}[A-Z](?![A-Z0-9])")
_DIGITS = re.compile(r"\d{9,}")
# Card/account numbers printed in groups ("4399 1234 1234 0001",
# "XXXX-XXXX-1234-5678"): groups of 3-6 digits (or already-masked X) joined
# by single spaces or hyphens. Two-digit groups never take part, so dates
# (2026-03-12, 12 03 2026) and short codes (12 34 56) cannot form a run.
_GROUPED = re.compile(
    r"(?<![0-9A-Za-z])(?<!\d[.,/])"
    r"[0-9X]{3,6}(?:[ -][0-9X]{3,6})+"
    r"(?![0-9A-Za-z])(?![.,/]\d)"
)
_GROUPED_MIN = 12


def _mask_grouped(m: re.Match) -> str:
    text = m.group()
    if sum(ch.isdigit() or ch == "X" for ch in text) < _GROUPED_MIN:
        return text
    kept = 0
    out: list[str] = []
    for ch in reversed(text):
        if ch.isdigit() and kept < 4:
            kept += 1
            out.append(ch)
        elif ch.isdigit():
            out.append("X")
        else:
            out.append(ch)
    return "".join(reversed(out))


def mask(text: str) -> str:
    """Hide e-mails, PAN-shaped tokens, runs of >= 9 digits and grouped runs
    totalling >= 12 digits (account, card and reference numbers), keeping a
    run's last 4 digits, its length and its separators so layout reasoning
    still works."""
    text = _EMAIL.sub("<EMAIL>", text)
    text = _PAN.sub("<PAN>", text)
    text = _GROUPED.sub(_mask_grouped, text)
    return _DIGITS.sub(lambda m: "X" * (len(m.group()) - 4) + m.group()[-4:], text)
