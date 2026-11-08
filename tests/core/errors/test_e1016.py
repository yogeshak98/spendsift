from __future__ import annotations

from backend.app.core.errors.errors import (
    INVESTMENT_VALUATION_INCONSISTENT,
    CodedError,
)


def test_e1016_renders_coded_error():
    exc = INVESTMENT_VALUATION_INCONSISTENT.exc({"symbol": "MF123"})
    assert isinstance(exc, CodedError)
    assert exc.code == "E1016"
    assert exc.status == 422
    assert "MF123" in exc.description
    assert exc.resolution
