import pytest

from backend.app.core.errors.ledger import LedgerValidationError


def test_carries_messages():
    err = LedgerValidationError(["unbalanced", "no such account"])
    assert err.messages == ["unbalanced", "no such account"]
    assert "unbalanced" in str(err)


def test_is_an_exception():
    with pytest.raises(LedgerValidationError):
        raise LedgerValidationError(["boom"])
