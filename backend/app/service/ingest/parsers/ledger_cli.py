from __future__ import annotations

import re
from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from backend.app.models.domain.ledger_cli import (
    ParsedLedger,
    ParsedPosting,
    ParsedTxn,
    UnparsedLine,
)


_HEADER = re.compile(r"^(\d{4})/(\d{2})/(\d{2})\s+(.*)$")
# account  <2+ spaces or tab>  amount-expression
_POSTING_SPLIT = re.compile(r"^(\S.*?)(?:\s{2,}|\t)(.*)$")
# [-]NUMBER COMMODITY  (optional @ PRICE CURRENCY)
_AMOUNT = re.compile(
    r"^(?P<num>-?[\d,]+(?:\.\d+)?)\s+(?P<comm>\S+)"
    r"(?:\s+@\s+(?P<price>-?[\d,]+(?:\.\d+)?)\s+(?P<cur>\S+))?$"
)
# A full-line comment / deferred directive marker.
_COMMENT_PREFIXES = (";", "#", "%")
_DEFERRED = ("include", "account", "commodity", "comment", "end comment", "P ")


def _dec(raw: str) -> Decimal:
    return Decimal(raw.replace(",", ""))


def _split_comment(text: str) -> tuple[str, str | None]:
    """Return (text-before-first-`;`, comment-or-None). Comment is stripped."""
    idx = text.find(";")
    if idx == -1:
        return text.rstrip(), None
    return text[:idx].rstrip(), text[idx + 1 :].strip() or None


def _parse_posting(line: str) -> ParsedPosting | None:
    """A posting line (already known indented). None if it is not a valid posting."""
    body, comment = _split_comment(line.strip())
    m = _POSTING_SPLIT.match(body)
    if m is None:
        # Account with no amount column at all -> elided posting.
        account = body.strip()
        if not account:
            return None
        return ParsedPosting(account, None, None, None, None, comment)
    account = m.group(1).strip()
    expr = m.group(2).strip()
    if not expr:
        return ParsedPosting(account, None, None, None, None, comment)
    am = _AMOUNT.match(expr)
    if am is None:
        return None
    try:
        amount = _dec(am.group("num"))
        cost = _dec(am.group("price")) if am.group("price") else None
    except InvalidOperation:
        return None
    return ParsedPosting(
        account=account,
        amount=amount,
        commodity=am.group("comm"),
        cost=cost,
        cost_currency=am.group("cur"),
        comment=comment,
    )


def is_ledger_file(text: str) -> bool:
    """True if `text` parses as a ledger/hledger journal rather than a bank
    statement: at least one transaction, and unparsed lines do not outnumber
    parsed transactions (a CSV statement yields zero balanced-posting txns)."""
    parsed = parse_ledger(text)
    return len(parsed.transactions) >= 1 and len(parsed.unparsed) <= len(
        parsed.transactions
    )


def parse_ledger(text: str) -> ParsedLedger:
    transactions: list[ParsedTxn] = []
    unparsed: list[UnparsedLine] = []
    ignored: list[UnparsedLine] = []

    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line_no = i + 1
        stripped = raw.strip()

        if not stripped:
            i += 1
            continue
        if stripped.startswith(_COMMENT_PREFIXES):
            ignored.append(UnparsedLine(line_no, raw, "comment line"))
            i += 1
            continue
        directive = next((d for d in _DEFERRED if stripped.startswith(d)), None)
        if directive is not None:
            ignored.append(
                UnparsedLine(
                    line_no, raw, f"unsupported directive ({directive.strip()})"
                )
            )
            i += 1
            continue

        header = _HEADER.match(raw)
        if header is None:
            unparsed.append(UnparsedLine(line_no, raw, "expected a YYYY/MM/DD header"))
            i += 1
            continue

        year, month, day, desc = header.groups()
        try:
            parsed_date = date_type(int(year), int(month), int(day))
        except ValueError:
            unparsed.append(
                UnparsedLine(
                    line_no, raw, "invalid calendar date — expected a YYYY/MM/DD header"
                )
            )
            i += 1
            continue
        narration, _ = _split_comment(desc)
        txn_line = line_no
        postings: list[ParsedPosting] = []
        bad = False
        reason = ""
        i += 1
        while i < n and (lines[i].startswith((" ", "\t"))) and lines[i].strip():
            posting = _parse_posting(lines[i])
            if posting is None:
                bad, reason = True, f"unparsable posting on line {i + 1}"
            else:
                postings.append(posting)
            i += 1

        elided = [p for p in postings if p.amount is None]
        if bad:
            block = "\n".join(lines[txn_line - 1 : i])
            unparsed.append(UnparsedLine(txn_line, block, reason))
            continue
        if len(elided) > 1:
            block = "\n".join(lines[txn_line - 1 : i])
            unparsed.append(
                UnparsedLine(txn_line, block, "more than one elided posting")
            )
            continue

        transactions.append(
            ParsedTxn(
                date=parsed_date,
                narration=narration,
                postings=postings,
                source_line=txn_line,
            )
        )

    return ParsedLedger(
        transactions=transactions,
        unparsed=unparsed,
        ignored_lines=len(ignored),
        ignored=ignored,
    )
