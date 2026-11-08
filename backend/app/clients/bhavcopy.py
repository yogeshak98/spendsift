"""Pure parser for the NSE UDiFF daily bhavcopy (cash segment).

The real file is a 34-column CSV inside a zip; this module sees only the
already-extracted text. Columns are addressed **by name**, never by index —
NSE has added columns before and will again.

Six columns matter:
    TradDt        trade date, ISO (2026-09-03)
    FinInstrmTp   instrument type; only "STK" is the cash segment
    ISIN          the stable identity (tickers get reused after delisting)
    TckrSymb      the symbol our journal's commodities are named after
    SctySrs       series: EQ, BE, ... (informational, not a filter)
    ClsPric       closing price

No IO and no framework imports — the sibling of navall.py.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Only the cash segment. Derivative rows (OPTIDX, FUTSTK, ...) share the file
# on some NSE products and must never be mistaken for a holding's price.
_CASH_SEGMENT = "STK"


@dataclass(frozen=True)
class EquityQuote:
    ticker: str
    isin: str
    close: Decimal
    date: date
    series: str


@dataclass(frozen=True)
class ParsedBhavcopy:
    quotes: dict[str, EquityQuote]
    # Tickers that appeared more than once in the file (e.g. two SctySrs rows
    # for the same TckrSymb). Refused entirely -- see parse_bhavcopy.
    duplicate_tickers: frozenset[str]


def _parse_close(value: str) -> Decimal | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        price = Decimal(text)
    except InvalidOperation:
        return None
    # A zero close is a non-trading placeholder, not a price.
    return price if price > 0 else None


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_bhavcopy(text: str) -> ParsedBhavcopy:
    """Ticker -> EquityQuote for every priced cash-segment row.

    A `TckrSymb` that appears more than once in the file (e.g. one row per
    `SctySrs`, both `FinInstrmTp=STK`) is ambiguous: nothing here says which
    close is the one that should price a holding. Rather than let dict-build
    order silently pick "whichever row came last", every row for a colliding
    ticker is refused -- the symbol ends up in neither `quotes` nor priced,
    and the collision is reported in `duplicate_tickers` so the caller can
    log it instead of the discrepancy vanishing unnoticed.
    """
    if not text.strip():
        return ParsedBhavcopy(quotes={}, duplicate_tickers=frozenset())
    out: dict[str, EquityQuote] = {}
    duplicates: set[str] = set()
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("FinInstrmTp") or "").strip() != _CASH_SEGMENT:
            continue
        ticker = (row.get("TckrSymb") or "").strip()
        isin = (row.get("ISIN") or "").strip()
        if not ticker or not isin:
            continue
        close = _parse_close(row.get("ClsPric") or "")
        traded = _parse_date(row.get("TradDt") or "")
        if close is None or traded is None:
            continue
        if ticker in duplicates:
            continue  # already refused; further rows change nothing
        if ticker in out:
            duplicates.add(ticker)
            del out[ticker]
            continue
        out[ticker] = EquityQuote(
            ticker=ticker,
            isin=isin,
            close=close,
            date=traded,
            series=(row.get("SctySrs") or "").strip(),
        )
    return ParsedBhavcopy(quotes=out, duplicate_tickers=frozenset(duplicates))
