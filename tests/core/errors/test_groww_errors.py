from __future__ import annotations

from backend.app.core.errors import error_codes
from backend.app.core.errors.errors import (
    SCHEME_MASTER_MISSING,
    UNRESOLVED_SCHEMES,
)


def test_new_codes_have_the_three_display_keys():
    for meta in (error_codes.E1011, error_codes.E1012):
        assert set(meta) == {"description", "resolution", "resolvedByRefresh"}
        assert isinstance(meta["description"], str)
        assert isinstance(meta["resolution"], str)
        assert isinstance(meta["resolvedByRefresh"], bool)


def test_scheme_master_missing_is_422_with_path_param():
    err = SCHEME_MASTER_MISSING.exc({"path": "/data/amfi_cache.csv"})
    assert err.code == "E1011"
    assert err.status == 422
    assert "/data/amfi_cache.csv" in err.description


def test_unresolved_schemes_is_422_with_candidate_ids_param():
    err = UNRESOLVED_SCHEMES.exc({"candidate_ids": "a, b"})
    assert err.code == "E1012"
    assert err.status == 422
    assert "a, b" in err.description


def test_unreconciled_stock_order_is_422_with_both_params():
    from backend.app.core.errors.errors import UNRECONCILED_STOCK_ORDER

    assert set(error_codes.E1043) == {
        "description",
        "resolution",
        "resolvedByRefresh",
    }
    err = UNRECONCILED_STOCK_ORDER.exc({"candidate_ids": "a, b", "tolerance": "0.01"})
    assert err.code == "E1043"
    assert err.status == 422
    assert "a, b" in err.description
    assert "0.01" in err.description


def test_lot_not_bookable_is_422_with_details():
    from backend.app.core.errors.errors import LOT_NOT_BOOKABLE

    assert set(error_codes.E1044) == {
        "description",
        "resolution",
        "resolvedByRefresh",
    }
    err = LOT_NOT_BOOKABLE.exc({"details": "abc (symbol 'M&MFIN' is illegal)"})
    assert err.code == "E1044"
    assert err.status == 422
    assert "M&MFIN" in err.description
