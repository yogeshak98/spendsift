from backend.app.models.domain import accounts


def test_checking_account_name():
    assert accounts.checking("HDFC") == "Assets:Checking:HDFC"


def test_checking_folds_wallets():
    # wallets fold into checking under their provider label
    assert accounts.checking("UPILite") == "Assets:Checking:UPILite"


def test_receivable_account_name():
    assert accounts.receivable("Kiran") == "Assets:Receivable:Kiran"


def test_investment_without_instrument():
    assert accounts.investment("FD", "HDFC") == "Assets:Investment:FD:HDFC"


def test_investment_with_instrument():
    assert (
        accounts.investment("MutualFund", "Groww", "INF209K01157")
        == "Assets:Investment:MutualFund:Groww:INF209K01157"
    )


def test_credit_card_account_name():
    assert (
        accounts.credit_card("HDFC Millennia")
        == "Liabilities:CreditCard:HDFC-Millennia"
    )


def test_loan_account_name():
    assert accounts.loan("HDFC Home") == "Liabilities:Loan:HDFC-Home"


def test_income_account_name():
    assert accounts.income("Salary") == "Income:Salary"


def test_expense_account_name():
    assert accounts.expense("Food") == "Expenses:Food"


def test_canonical_constants():
    assert accounts.CASH == "Assets:Cash"
    assert accounts.INTRANSIT == "Assets:Transfers:InTransit"
    assert accounts.EQUITY_OPENING == "Equity:Opening-Balances"
    assert accounts.EQUITY_ADJUSTMENTS == "Equity:Adjustments"


def test_seed_accounts_are_well_formed():
    for name in accounts.SEED_ACCOUNTS:
        assert name.split(":")[0] in {
            "Assets",
            "Liabilities",
            "Income",
            "Expenses",
            "Equity",
        }
