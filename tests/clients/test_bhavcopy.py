from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.core.paths import REPO_ROOT
from backend.app.clients.bhavcopy import EquityQuote, parse_bhavcopy

_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "bhavcopy_sample.csv"


def _parsed() -> dict[str, EquityQuote]:
    return parse_bhavcopy(_FIXTURE.read_text(encoding="utf-8")).quotes


def test_parses_ticker_isin_close_and_date():
    quotes = _parsed()
    sbin = quotes["SBIN"]
    assert sbin.ticker == "SBIN"
    assert sbin.isin == "INE062A01020"
    assert sbin.close == Decimal("1016.10")
    assert sbin.date == date(2026, 9, 3)
    assert sbin.series == "EQ"


def test_close_is_decimal_never_float():
    assert isinstance(_parsed()["MARICO"].close, Decimal)


def test_etf_isin_prefixed_inf_is_kept():
    # ETFs carry INF-prefixed ISINs (mutual-fund style). They are still equities
    # on the cash segment and must not be filtered out.
    assert _parsed()["GOLDCASE"].isin == "INF0R8F01042"


def test_be_series_is_kept():
    # BE (trade-for-trade) is a real cash-segment series, not a derivative.
    assert _parsed()["GROWW"].series == "BE"


def test_rows_without_a_usable_close_are_dropped():
    quotes = _parsed()
    assert "BADNAV" not in quotes  # empty ClsPric
    assert "ZEROPX" not in quotes  # zero is not a price


def test_non_stock_instrument_types_are_dropped():
    assert "NIFTY" not in _parsed()  # FinInstrmTp=OPTIDX


def test_malformed_rows_do_not_raise():
    assert isinstance(_parsed(), dict)


def test_empty_text_returns_empty_dict():
    assert parse_bhavcopy("").quotes == {}


def test_header_only_returns_empty_dict():
    header = _FIXTURE.read_text(encoding="utf-8").splitlines()[0]
    assert parse_bhavcopy(header).quotes == {}


def test_duplicate_ticker_is_refused_not_last_wins():
    # Two STK rows share TckrSymb=DUPTIK with different SctySrs and different
    # closes. Resolving by file order would silently pick an arbitrary close;
    # the parser must refuse to price the symbol at all instead.
    text = (
        _FIXTURE.read_text(encoding="utf-8").rstrip()
        + "\n2026-09-03,2026-09-03,CM,NSE,STK,1111,INE999A01011,DUPTIK,EQ,"
        "100.00,99.90,1000\n"
        "2026-09-03,2026-09-03,CM,NSE,STK,1112,INE999A01011,DUPTIK,BE,"
        "105.00,104.90,2000\n"
    )
    result = parse_bhavcopy(text)
    assert "DUPTIK" not in result.quotes
    assert result.duplicate_tickers == frozenset({"DUPTIK"})
    # Unrelated tickers in the same file are unaffected.
    assert "SBIN" in result.quotes


def test_three_colliding_rows_still_refuse_the_ticker():
    text = (
        _FIXTURE.read_text(encoding="utf-8").rstrip()
        + "\n2026-09-03,2026-09-03,CM,NSE,STK,1111,INE999A01011,TRIPTIK,EQ,"
        "100.00,99.90,1000\n"
        "2026-09-03,2026-09-03,CM,NSE,STK,1112,INE999A01011,TRIPTIK,BE,"
        "105.00,104.90,2000\n"
        "2026-09-03,2026-09-03,CM,NSE,STK,1113,INE999A01011,TRIPTIK,EQ,"
        "110.00,109.90,3000\n"
    )
    result = parse_bhavcopy(text)
    assert "TRIPTIK" not in result.quotes
    assert result.duplicate_tickers == frozenset({"TRIPTIK"})
