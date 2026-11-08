from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
from beancount.core import data
from beancount.core.amount import Amount

from backend.app.core.errors.errors import CodedError
from backend.app.persistence.journal import Ledger
from backend.app.persistence.nav_store import NavStore
from backend.app.service.investments.equity_prices import EquityPriceService

_CSV = (
    "TradDt,FinInstrmTp,ISIN,TckrSymb,SctySrs,ClsPric\n"
    "2026-09-03,STK,INE062A01020,SBIN,EQ,1016.10\n"
    "2026-09-03,STK,INE196A01026,MARICO,EQ,815.15\n"
    "2026-09-03,STK,INE213A01029,ONGC,EQ,234.65\n"
)


def _buy(ledger: Ledger, symbol: str, qty: str, price: str) -> None:
    account = f"Assets:Investment:Stock:{symbol}"
    meta = data.new_metadata("<test>", 0)
    cost = data.Cost(Decimal(price), "INR", date(2024, 1, 5), None)
    txn = data.Transaction(
        meta,
        date(2024, 1, 5),
        "*",
        None,
        f"buy {symbol}",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(account, Amount(Decimal(qty), symbol), cost, None, None, None),
            data.Posting(
                "Assets:Checking:HDFC",
                Amount(-Decimal(qty) * Decimal(price), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )
    ledger.append([txn])


def _open_stock_accounts(ledger: Ledger, *symbols: str) -> None:
    """Controller ruling: ensure_commodity_accounts only opens MF accounts, so
    write the Stock commodity + open directives directly, matching the shape
    the real journal uses."""
    main = ledger.main_file.read_text(encoding="utf-8")
    additions: list[str] = []
    for symbol in symbols:
        if f"commodity {symbol}" not in main:
            additions.append(f'2000-01-01 commodity {symbol}\n  name: "{symbol}"')
        account = f"Assets:Investment:Stock:{symbol}"
        if f"open {account}" not in main:
            additions.append(f'2000-01-01 open {account} {symbol} "FIFO"')
    if additions:
        ledger.main_file.write_text(
            main.rstrip() + "\n" + "\n".join(additions) + "\n", encoding="utf-8"
        )


def _ledger(tmp_path, *symbols: str) -> Ledger:
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    _open_stock_accounts(ledger, *symbols)
    for s in symbols:
        _buy(ledger, s, "10", "500.00")
    return ledger


def _service(ledger, transport) -> EquityPriceService:
    return EquityPriceService(
        ledger=ledger,
        nav_store=NavStore(ledger.storage_dir / "prices" / "equity_history.parquet"),
        transport=transport,
    )


def _ok_transport():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BhavCopy.csv", _CSV)
    payload = buf.getvalue()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    return httpx.MockTransport(handler)


def _offline_transport():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no net", request=req)

    return httpx.MockTransport(handler)


def test_held_symbols_reads_stock_accounts_from_the_main_ledger(tmp_path):
    ledger = _ledger(tmp_path, "SBIN", "MARICO")
    svc = _service(ledger, _ok_transport())
    assert svc.held_symbols() == {"SBIN", "MARICO"}


def test_refresh_writes_prices_for_held_symbols_only(tmp_path):
    ledger = _ledger(tmp_path, "SBIN")
    out = _service(ledger, _ok_transport()).refresh(on=date(2026, 9, 3))
    assert out["source"] == "fetched"
    assert out["as_of"] == "2026-09-03"
    assert [f["symbol"] for f in out["priced"]] == ["SBIN"]
    text = (ledger.journal_dir / "prices-equity.beancount").read_text(encoding="utf-8")
    assert "2026-09-03 price SBIN 1016.1000 INR" in text
    # ONGC is in the feed but not held -> not written.
    assert "ONGC" not in text
    assert ledger.validate() == []


def test_refresh_does_not_touch_the_mutual_fund_price_file(tmp_path):
    ledger = _ledger(tmp_path, "SBIN")
    (ledger.journal_dir / "prices.beancount").write_text("", encoding="utf-8")
    _service(ledger, _ok_transport()).refresh(on=date(2026, 9, 3))
    assert (ledger.journal_dir / "prices.beancount").read_text(encoding="utf-8") == ""


def test_offline_keeps_previous_prices_and_reports_offline(tmp_path):
    ledger = _ledger(tmp_path, "SBIN")
    svc = _service(ledger, _ok_transport())
    svc.refresh(on=date(2026, 9, 3))
    before = (ledger.journal_dir / "prices-equity.beancount").read_text(
        encoding="utf-8"
    )

    svc_offline = _service(ledger, _offline_transport())
    out = svc_offline.refresh(on=date(2026, 9, 4))
    assert out["source"] == "offline"
    assert out["as_of"] == "2026-09-03"  # the stored close, not today
    assert (ledger.journal_dir / "prices-equity.beancount").read_text(
        encoding="utf-8"
    ) == before


def test_feed_with_zero_overlap_reports_all_held_unpriced_without_raising(tmp_path):
    """The feed is non-empty (valid bhavcopy, real quotes) but none of them are
    for a held symbol. This is the boundary between the empty-feed raise
    (E1042) and a normal report: a non-empty feed with zero overlap must NOT
    raise -- it reports every held symbol as unpriced, same as any other
    held-but-not-in-feed symbol."""
    ledger = _ledger(tmp_path, "UTLSOLAR")  # not present in _CSV at all
    out = _service(ledger, _ok_transport()).refresh(on=date(2026, 9, 3))
    assert out["source"] == "fetched"
    assert out["as_of"] is None
    assert out["priced"] == []
    assert [f["symbol"] for f in out["unpriced"]] == ["UTLSOLAR"]
    text = (ledger.journal_dir / "prices-equity.beancount").read_text(encoding="utf-8")
    assert text == ""


def test_held_symbol_missing_from_the_feed_is_reported_unpriced(tmp_path):
    ledger = _ledger(tmp_path, "SBIN", "UTLSOLAR")
    out = _service(ledger, _ok_transport()).refresh(on=date(2026, 9, 3))
    assert [f["symbol"] for f in out["priced"]] == ["SBIN"]
    assert [f["symbol"] for f in out["unpriced"]] == ["UTLSOLAR"]


def test_empty_feed_while_holding_raises_e1042_and_keeps_the_cache(tmp_path):
    ledger = _ledger(tmp_path, "SBIN")
    svc = _service(ledger, _ok_transport())
    svc.refresh(on=date(2026, 9, 3))
    before = (ledger.journal_dir / "prices-equity.beancount").read_text(
        encoding="utf-8"
    )

    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "BhavCopy.csv", "TradDt,FinInstrmTp,ISIN,TckrSymb,SctySrs,ClsPric\n"
        )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=buf.getvalue())

    with pytest.raises(CodedError) as excinfo:
        _service(ledger, httpx.MockTransport(handler)).refresh(on=date(2026, 9, 4))
    assert excinfo.value.code == "E1042"
    assert (ledger.journal_dir / "prices-equity.beancount").read_text(
        encoding="utf-8"
    ) == before


def test_status_after_a_refresh_reports_the_stored_close(tmp_path):
    ledger = _ledger(tmp_path, "SBIN")
    svc = _service(ledger, _ok_transport())
    svc.refresh(on=date(2026, 9, 3))
    out = svc.status()
    assert out["as_of"] == "2026-09-03"
    assert [f["symbol"] for f in out["priced"]] == ["SBIN"]


def test_status_with_no_history_reports_never(tmp_path):
    ledger = _ledger(tmp_path, "SBIN")
    assert _service(ledger, _ok_transport()).status()["source"] == "never"


def _duplicate_transport():
    import io
    import zipfile

    csv_text = (
        "TradDt,FinInstrmTp,ISIN,TckrSymb,SctySrs,ClsPric\n"
        "2026-09-03,STK,INE062A01020,SBIN,EQ,1016.10\n"
        "2026-09-03,STK,INE999A01011,DUPTIK,EQ,100.00\n"
        "2026-09-03,STK,INE999A01011,DUPTIK,BE,105.00\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BhavCopy.csv", csv_text)
    payload = buf.getvalue()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    return httpx.MockTransport(handler)


def test_held_symbol_with_duplicate_bhavcopy_rows_is_refused_not_priced(
    tmp_path, caplog
):
    # DUPTIK collides in the feed (two SctySrs rows). It must never be priced
    # off an arbitrary one of the two closes -- it is reported unpriced, same
    # as any other symbol the feed doesn't resolve, and the collision is
    # logged rather than swallowed.
    ledger = _ledger(tmp_path, "SBIN", "DUPTIK")
    with caplog.at_level("WARNING", logger="backend"):
        out = _service(ledger, _duplicate_transport()).refresh(on=date(2026, 9, 3))
    assert [f["symbol"] for f in out["priced"]] == ["SBIN"]
    assert [f["symbol"] for f in out["unpriced"]] == ["DUPTIK"]
    text = (ledger.journal_dir / "prices-equity.beancount").read_text(encoding="utf-8")
    assert "DUPTIK" not in text
    assert any("DUPTIK" in record.message for record in caplog.records)
