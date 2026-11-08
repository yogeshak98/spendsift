"""Read a PDF statement into numbered text lines; unlock it in memory.

Only text extraction lives here. Nothing in this module interprets amounts or
dates, and no library exception text or password ever leaves it: every failure
is a coded error carrying one of three fixed reasons.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from backend.app.core.errors.errors import (
    PDF_PASSWORD_WRONG,
    PDF_UNREADABLE,
    CodedError,
)

if TYPE_CHECKING:
    from pypdf import PdfReader

# pdfplumber / pypdf are imported inside the functions that use them: they
# cost ~0.1s+ at import and are only needed when a PDF is actually read, so
# keeping them off the module top level keeps app startup fast.

LINE_TOLERANCE: float = 3.0  # points; words whose tops differ by <= this join one line

_DAMAGED = "the file is damaged"
_NO_TEXT = "it has no selectable text"
_LOCKED = "it is password-protected"


@dataclass(frozen=True)
class PdfWord:
    text: str
    x0: float
    x1: float


@dataclass(frozen=True)
class PdfLine:
    index: int  # 0-based across the whole document
    page: int  # 0-based
    text: str  # words joined by single spaces, left to right
    words: tuple[PdfWord, ...]
    spans: tuple[tuple[int, int], ...]  # (start, end) char span of each word in text


def _unreadable(filename: str, reason: str) -> CodedError:
    return PDF_UNREADABLE.exc({"filename": filename, "reason": reason})


def is_pdf(raw: bytes) -> bool:
    return raw[:5] == b"%PDF-"


def _open(raw: bytes, filename: str) -> PdfReader:
    from pypdf import PdfReader

    try:
        return PdfReader(io.BytesIO(raw))
    except Exception:
        raise _unreadable(filename, _DAMAGED) from None


def is_encrypted(raw: bytes, *, filename: str = "the file") -> bool:
    """True only if the empty password does not open the document."""
    from pypdf import PasswordType

    reader = _open(raw, filename)
    try:
        if not reader.is_encrypted:
            return False
        return reader.decrypt("") == PasswordType.NOT_DECRYPTED
    except Exception:
        raise _unreadable(filename, _DAMAGED) from None


def unlock(raw: bytes, password: str, *, filename: str = "the file") -> bytes:
    """Return decrypted PDF bytes. The password is used once and never echoed."""
    from pypdf import PasswordType, PdfWriter

    reader = _open(raw, filename)
    try:
        if not reader.is_encrypted:
            return raw
        if reader.decrypt(password) == PasswordType.NOT_DECRYPTED:
            raise PDF_PASSWORD_WRONG.exc({"filename": filename}) from None
        writer = PdfWriter(clone_from=reader)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except CodedError:
        raise
    except Exception:
        raise _unreadable(filename, _DAMAGED) from None


def _cluster(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    first_top = 0.0
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and word["top"] - first_top <= LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
            first_top = word["top"]
    return lines


def read_lines(path: Path) -> list[PdfLine]:
    filename = path.name
    try:
        raw = path.read_bytes()
    except OSError:
        raise _unreadable(filename, _DAMAGED) from None
    if not is_pdf(raw):
        raise _unreadable(filename, _DAMAGED)
    if is_encrypted(raw, filename=filename):
        raise _unreadable(filename, _LOCKED)

    import pdfplumber

    result: list[PdfLine] = []
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page_no, page in enumerate(pdf.pages):
                for group in _cluster(page.extract_words()):
                    group.sort(key=lambda w: w["x0"])
                    words = tuple(
                        PdfWord(w["text"], float(w["x0"]), float(w["x1"]))
                        for w in group
                    )
                    spans: list[tuple[int, int]] = []
                    pos = 0
                    for word in words:
                        spans.append((pos, pos + len(word.text)))
                        pos += len(word.text) + 1
                    result.append(
                        PdfLine(
                            index=len(result),
                            page=page_no,
                            text=" ".join(w.text for w in words),
                            words=words,
                            spans=tuple(spans),
                        )
                    )
    except Exception:
        raise _unreadable(filename, _DAMAGED) from None
    if not result:
        raise _unreadable(filename, _NO_TEXT)
    return result
