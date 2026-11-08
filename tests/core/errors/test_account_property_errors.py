import pytest

from backend.app.core.errors import errors

CASES = [
    (
        "ACCOUNT_PROPERTY_INVALID",
        "E1082",
        422,
        {"field": "statement_day", "value": "32", "reason": "must be 1 to 31"},
    ),
    (
        "ACCOUNT_PROPERTY_NOT_APPLICABLE",
        "E1083",
        422,
        {"account": "Assets:Checking:Bank", "expected": "Liabilities:CreditCard:*"},
    ),
    ("ACCOUNT_CLOSED", "E1084", 409, {"account": "Liabilities:CreditCard:Old"}),
    ("EMI_SPLIT_STALE", "E1085", 409, {"txn_id": "t-42"}),
    (
        "EMI_SPLIT_INVALID",
        "E1086",
        422,
        {"reason": "Liabilities:Loan:Other is not a direct child of the EMI account"},
    ),
    (
        "ACCOUNT_RENAME_BREAKS_TEMPLATE",
        "E1094",
        409,
        {
            "account": "Liabilities:CreditCard:Synth",
            "new_name": "Liabilities:Loan:Synth",
            "template": "the PDF import template for SYNTH card 1234"
            " (0123456789abcdef.yaml)",
            "file": "0123456789abcdef.yaml",
            "reason": "default_account: default_account 'Liabilities:Loan:Synth'"
            " must be a card account under Liabilities:CreditCard:",
        },
    ),
    (
        "ACCOUNT_RENAME_PENDING_MERGE",
        "E1095",
        409,
        {"account": "Expenses:Synth", "files": "synth.ledger"},
    ),
]


@pytest.mark.parametrize(("name", "code", "status", "params"), CASES)
def test_account_property_error_catalog(name, code, status, params):
    exc = getattr(errors, name).exc(params, data=params)
    assert (exc.code, exc.status) == (code, status)
    assert exc.description and exc.resolution
    assert "{" not in exc.description + exc.resolution
    for value in params.values():
        assert value in exc.description
    assert exc.data == params
