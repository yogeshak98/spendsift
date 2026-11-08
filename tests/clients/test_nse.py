from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx

from backend.app.clients.amfi import FetchStatus
from backend.app.clients.nse import bhavcopy_url, fetch_bhavcopy

_CSV = (
    "TradDt,FinInstrmTp,ISIN,TckrSymb,SctySrs,ClsPric\n"
    "2026-09-03,STK,INE062A01020,SBIN,EQ,1016.10\n"
)


def _zip_bytes(name: str = "BhavCopy_NSE_CM_0_0_0_20260903_F_0000.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, _CSV)
    return buf.getvalue()


def test_url_uses_the_udiff_naming_scheme():
    assert bhavcopy_url(date(2026, 9, 3)).endswith(
        "BhavCopy_NSE_CM_0_0_0_20260903_F_0000.csv.zip"
    )


def test_200_unzips_to_csv_text_and_reports_the_trade_date():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_zip_bytes())

    res = fetch_bhavcopy(on=date(2026, 9, 3), transport=httpx.MockTransport(handler))
    assert res.status is FetchStatus.OK
    assert res.trade_date == date(2026, 9, 3)
    assert "SBIN" in (res.text or "")


def test_sends_browser_shaped_headers():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content=_zip_bytes())

    fetch_bhavcopy(on=date(2026, 9, 3), transport=httpx.MockTransport(handler))
    assert "mozilla" in seen["user-agent"].lower()
    assert "nseindia.com" in seen["referer"]


def test_404_walks_back_to_the_previous_trading_day():
    # 2026-09-05 is a Saturday and 2026-09-06 a Sunday: both 404 on the real
    # feed. Asking for Sunday must yield Thursday's file.
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(str(request.url))
        if "20260903" in str(request.url):
            return httpx.Response(200, content=_zip_bytes())
        return httpx.Response(404)

    res = fetch_bhavcopy(on=date(2026, 9, 6), transport=httpx.MockTransport(handler))
    assert res.status is FetchStatus.OK
    assert res.trade_date == date(2026, 9, 3)
    assert len(tried) == 4  # 06, 05, 04, 03


def test_gives_up_after_back_days_and_reports_offline():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    res = fetch_bhavcopy(
        on=date(2026, 9, 6), back_days=3, transport=httpx.MockTransport(handler)
    )
    assert res.status is FetchStatus.OFFLINE
    assert res.text is None


def test_transport_error_maps_to_offline_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network", request=request)

    res = fetch_bhavcopy(on=date(2026, 9, 3), transport=httpx.MockTransport(handler))
    assert res.status is FetchStatus.OFFLINE


def test_corrupt_zip_maps_to_offline_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"this is not a zip")

    res = fetch_bhavcopy(on=date(2026, 9, 3), transport=httpx.MockTransport(handler))
    assert res.status is FetchStatus.OFFLINE
