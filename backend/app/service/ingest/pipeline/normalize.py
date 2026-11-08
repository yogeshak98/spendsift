from __future__ import annotations

import re

# Leading rail/instrument prefixes to drop (UPI, IMPS, NEFT, POS, ACH, ATW...).
_PREFIX = re.compile(
    r"^(?:UPI|IMPS|NEFT|RTGS|POS|ACH|ATW|MMT)[/\- ].*?(?=[A-Za-z]{3})",
    re.IGNORECASE,
)
_REF = re.compile(r"\b\d{5,}\b")  # long reference numbers
_NOISE = re.compile(r"\b(?:P2A|P2M|DR|CR|HDFC|ICICI|IDFC|payment)\b", re.IGNORECASE)
_WS = re.compile(r"\s+")


def normalize_narration(raw: str) -> str:
    text = raw.strip()
    # Keep UPI-LITE LOAD intact — it is a meaningful transfer marker, not a merchant string.
    if text.upper().startswith("UPI-LITE"):
        return _WS.sub(" ", text).upper()
    # Drop the leading rail/instrument prefix and any ref noise riding with it.
    text = _PREFIX.sub("", text)
    parts = re.split(r"[/\-]", text)
    # take the segment with the most non-noise alphabetic content as the merchant
    candidates = [
        cleaned
        for p in parts
        if re.search(r"[A-Za-z]{3,}", p)
        and (cleaned := _NOISE.sub("", _REF.sub("", p)).strip())
    ]
    merchant = max(
        candidates,
        key=lambda p: len(re.sub(r"[^A-Za-z ]", "", p)),
        default=text,
    )
    merchant = _REF.sub("", merchant)
    merchant = _NOISE.sub("", merchant)
    return _WS.sub(" ", merchant).strip().upper()
