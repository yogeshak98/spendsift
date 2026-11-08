"""Fetches the NSE UDiFF daily bhavcopy zip and returns its CSV text.

Best-effort and OFFLINE-safe, exactly like the AMFI client (amfi.py): every
transport error, bad status, and unreadable archive returns
FetchStatus.OFFLINE rather than raising, so a dead feed can never break a page
render.

Two facts verified against the live endpoint on 2026-09-06:
  * the nsearchives host serves the file with only a User-Agent and Referer --
    no cookie warm-up against www.nseindia.com is needed;
  * non-trading days return 404 (not an empty file), so we walk backwards to
    the most recent trading day.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

from backend.app.clients.amfi import FetchStatus

_BASE = "https://nsearchives.nseindia.com/content/cm"

# NSE serves these only to browser-shaped requests.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/all-reports",
}


@dataclass(frozen=True)
class BhavcopyResult:
    status: FetchStatus
    text: str | None
    trade_date: date | None


def bhavcopy_url(day: date) -> str:
    return f"{_BASE}/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"


def _first_csv(payload: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not names:
                return None
            return zf.read(names[0]).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, OSError):
        return None


def fetch_bhavcopy(
    *,
    on: date,
    back_days: int = 7,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 15.0,
) -> BhavcopyResult:
    """Fetch the bhavcopy for `on`, walking back up to `back_days` calendar days.

    Weekends and exchange holidays 404, so the walk-back is the normal path,
    not an error path. `back_days=7` covers a long holiday weekend.
    """
    offline = BhavcopyResult(status=FetchStatus.OFFLINE, text=None, trade_date=None)
    try:
        with httpx.Client(
            timeout=timeout, transport=transport, follow_redirects=True
        ) as client:
            for delta in range(back_days + 1):
                day = on - timedelta(days=delta)
                resp = client.get(bhavcopy_url(day), headers=_HEADERS)
                if resp.status_code == 404:
                    continue  # non-trading day
                if resp.status_code != 200:
                    return offline
                text = _first_csv(resp.content)
                if text is None:
                    return offline
                return BhavcopyResult(status=FetchStatus.OK, text=text, trade_date=day)
    except httpx.HTTPError:
        return offline
    return offline
