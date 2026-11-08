from __future__ import annotations

from backend.app.core.errors.errors import (
    ACCOUNT_NAME_COLLISION,
    ACCOUNT_NOT_SETTLED,
    INVALID_ACCOUNT_NAME,
)


def test_invalid_account_name():
    err = INVALID_ACCOUNT_NAME.exc({"name": "bogus name"})
    assert err.code == "E1017"
    assert err.status == 422
    assert "bogus name" in err.description
    assert err.resolved_by_refresh is False


def test_account_name_collision():
    err = ACCOUNT_NAME_COLLISION.exc({"new_name": "Assets:Checking:HDFC"})
    assert err.code == "E1018"
    assert err.status == 409
    assert "Assets:Checking:HDFC" in err.description


def test_account_not_settled():
    err = ACCOUNT_NOT_SETTLED.exc(
        {"account": "Assets:Checking:Old", "residual": "1234.50 INR"}
    )
    assert err.code == "E1019"
    assert err.status == 409
    assert "1234.50 INR" in err.description
    assert "Assets:Checking:Old" in err.description
