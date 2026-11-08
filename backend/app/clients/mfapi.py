"""Client for mfapi.in per-scheme historical NAV endpoint.

GET https://api.mfapi.in/mf/{scheme_code}

No pyarrow. No FastAPI. Returns list[NavRow]; raises by code on failure.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx

from backend.app.core.errors.errors import MFAPI_BAD_RESPONSE, MFAPI_UNAVAILABLE
from backend.app.models.domain.nav import NavRow

_BASE = "https://api.mfapi.in/mf"
_DATE_FMT = "%d-%m-%Y"


def _parse_row(code: str, record: dict) -> NavRow | None:
    date_str = record.get("date", "")
    nav_str = record.get("nav", "")
    try:
        d = datetime.strptime(date_str.strip(), _DATE_FMT).date()
        nav = Decimal(nav_str.strip())
        if nav <= 0:
            return None
    except (ValueError, InvalidOperation):
        return None
    return NavRow(scheme_code=code, nav=nav, date=d)


def fetch_history(code: str, *, transport=None) -> list[NavRow]:
    """Fetch all historical NAVs for scheme_code from mfapi.in.

    transport: optional duck-typed HTTP client (for tests). Defaults to httpx.
    Raises MFAPI_UNAVAILABLE (E1030) on network error, MFAPI_BAD_RESPONSE (E1031)
    on bad payload.
    """
    url = f"{_BASE}/{code}"
    client = transport or httpx
    try:
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        raise MFAPI_UNAVAILABLE.exc({"reason": str(exc)}) from exc

    if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
        raise MFAPI_BAD_RESPONSE.exc({"code": code})

    data = payload.get("data")
    if not isinstance(data, list):
        raise MFAPI_BAD_RESPONSE.exc({"code": code})

    rows = [_parse_row(code, r) for r in data]
    return [r for r in rows if r is not None]
