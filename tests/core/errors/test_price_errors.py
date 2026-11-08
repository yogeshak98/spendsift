from __future__ import annotations

from backend.app.core.errors import error_codes
from backend.app.core.errors.errors import (
    EMPTY_DUMP_WHILE_HOLDING,
    MFAPI_BAD_RESPONSE,
    MFAPI_UNAVAILABLE,
    NAV_STORE_CORRUPT,
)


def test_new_codes_have_the_three_display_keys():
    for meta in (error_codes.E1014, error_codes.E1015):
        assert set(meta) == {"description", "resolution", "resolvedByRefresh"}
        assert isinstance(meta["description"], str)
        assert isinstance(meta["resolution"], str)
        assert isinstance(meta["resolvedByRefresh"], bool)


def test_nav_store_corrupt_is_422_with_path_param():
    err = NAV_STORE_CORRUPT.exc({"path": "/data/prices/nav_history.parquet"})
    assert err.code == "E1014"
    assert err.status == 422
    assert "/data/prices/nav_history.parquet" in err.description


def test_empty_dump_while_holding_is_422_paramless():
    err = EMPTY_DUMP_WHILE_HOLDING.exc()
    assert err.code == "E1015"
    assert err.status == 422
    assert err.description == error_codes.E1015["description"]


def test_mfapi_unavailable_is_502():
    err = MFAPI_UNAVAILABLE.exc({"reason": "Connection timeout"})
    assert err.code == "E1030"
    assert err.status == 502
    assert "Connection timeout" in err.description


def test_mfapi_bad_response_is_502():
    err = MFAPI_BAD_RESPONSE.exc({"code": "122639"})
    assert err.code == "E1031"
    assert err.status == 502
    assert "122639" in err.description
