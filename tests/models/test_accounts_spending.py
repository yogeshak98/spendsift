from backend.app.models.domain.accounts import (
    BANK_PREFIXES,
    INVESTMENT_EXPENSE,
    is_spending_account,
)


def test_is_spending_account_excludes_investment_charges():
    assert is_spending_account("Expenses:Investment:Charges:Groww") is False
    assert is_spending_account("Expenses:Investment:StampDuty") is False
    assert is_spending_account("Expenses:Investment") is False
    assert is_spending_account("Expenses:Food") is True
    assert is_spending_account("Expenses:Food:Swiggy") is True
    # The match is on the top category, not a string prefix.
    assert is_spending_account("Expenses:InvestmentAdvice") is True
    assert is_spending_account("Income:Salary") is False
    assert is_spending_account("Assets:Checking:HDFC") is False


def test_spending_constants():
    assert INVESTMENT_EXPENSE == "Expenses:Investment"
    assert BANK_PREFIXES == ("Assets:Checking:", "Assets:Bank:")
