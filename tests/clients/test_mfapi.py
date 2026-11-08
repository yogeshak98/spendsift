from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backend.app.core.paths import REPO_ROOT
from backend.app.clients.mfapi import fetch_history

_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "mfapi_sample.json"


class _MockTransport:
    def __init__(self, body: str, status: int = 200, raise_on_get: bool = False):
        self._body = body
        self._status = status
        self._raise_on_get = raise_on_get

    def get(self, url: str) -> MagicMock:
        if self._raise_on_get:
            raise OSError("Connection refused")
        resp = MagicMock()
        resp.status_code = self._status
        resp.json.return_value = json.loads(self._body)
        if self._status != 200:
            resp.raise_for_status.side_effect = Exception(f"HTTP {self._status}")
        else:
            resp.raise_for_status.return_value = None
        return resp


def test_fetch_history_parses_valid_response():
    transport = _MockTransport(_FIXTURE.read_text())
    rows = fetch_history("122639", transport=transport)
    assert len(rows) == 4  # N/A row skipped
    dates = [r.date for r in rows]
    assert date(2026, 6, 30) in dates
    assert date(2024, 3, 15) in dates
    assert all(isinstance(r.nav, Decimal) for r in rows)
    assert all(r.scheme_code == "122639" for r in rows)


def test_fetch_history_skips_invalid_nav():
    transport = _MockTransport(_FIXTURE.read_text())
    rows = fetch_history("122639", transport=transport)
    assert all(n > 0 for n in [r.nav for r in rows])


def test_fetch_history_raises_on_http_error():
    transport = _MockTransport("{}", status=500)
    with pytest.raises(Exception) as exc_info:
        fetch_history("122639", transport=transport)
    assert "E1030" in str(exc_info.value)


def test_fetch_history_raises_on_network_error():
    transport = _MockTransport("{}", raise_on_get=True)
    with pytest.raises(Exception) as exc_info:
        fetch_history("122639", transport=transport)
    assert "E1030" in str(exc_info.value)


def test_fetch_history_raises_on_bad_status_field():
    bad = json.dumps({"status": "FAILED", "data": []})
    transport = _MockTransport(bad)
    with pytest.raises(Exception) as exc_info:
        fetch_history("122639", transport=transport)
    assert "E1031" in str(exc_info.value)


def test_fetch_history_raises_on_missing_data_key():
    bad = json.dumps({"status": "SUCCESS"})
    transport = _MockTransport(bad)
    with pytest.raises(Exception) as exc_info:
        fetch_history("122639", transport=transport)
    assert "E1031" in str(exc_info.value)
