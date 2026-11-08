from __future__ import annotations

from backend.app.core.errors import error_codes
from backend.app.core.errors.errors import EQUITY_QUOTES_EMPTY_WHILE_HOLDING


def test_new_code_has_the_three_display_keys():
    meta = error_codes.E1042
    assert set(meta) == {"description", "resolution", "resolvedByRefresh"}
    assert isinstance(meta["description"], str)
    assert isinstance(meta["resolution"], str)
    assert isinstance(meta["resolvedByRefresh"], bool)


def test_equity_quotes_empty_while_holding_is_422():
    err = EQUITY_QUOTES_EMPTY_WHILE_HOLDING.exc()
    assert err.code == "E1042"
    assert err.status == 422
    assert err.resolved_by_refresh is True
