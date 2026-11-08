from backend.app.service.ingest.templates.fingerprint import (
    fingerprint_headers,
    autoguess_roles,
)


def test_fingerprint_is_stable_and_order_sensitive():
    a = fingerprint_headers(["Date", "Narration", "Withdrawal", "Deposit"])
    b = fingerprint_headers(["date", " narration ", "WITHDRAWAL", "deposit"])
    assert a == b  # case/space-insensitive
    assert a != fingerprint_headers(["Narration", "Date", "Withdrawal", "Deposit"])


def test_autoguess_roles_hdfc_like():
    headers = [
        "Date",
        "Narration",
        "Chq/Ref No",
        "Withdrawal Amt",
        "Deposit Amt",
        "Balance",
    ]
    roles = autoguess_roles(headers)
    assert roles["date"] == "Date"
    assert roles["narration"] == "Narration"
    assert roles["debit"] == "Withdrawal Amt"
    assert roles["credit"] == "Deposit Amt"


def test_stock_header_is_recognised():
    from backend.app.service.ingest.templates.fingerprint import is_groww_stock_header

    headers = [
        "Stock name",
        "Symbol",
        "ISIN",
        "Type",
        "Quantity",
        "Value",
        "Exchange",
        "Exchange Order Id",
        "Execution date and time",
        "Order status",
    ]
    assert is_groww_stock_header(headers) is True


def test_mf_header_is_not_mistaken_for_a_stock_header():
    # Both are Groww exports; dispatching on the wrong one would parse an MF
    # file as stocks and write lots to the wrong vehicle account.
    from backend.app.service.ingest.templates.fingerprint import (
        is_groww_stock_header,
        is_groww_header,
    )

    mf = ["Scheme Name", "Transaction Type", "Units", "NAV", "Amount", "Date"]
    assert is_groww_stock_header(mf) is False
    assert is_groww_header(mf) is True


def test_a_header_carrying_both_exports_columns_satisfies_both_predicates():
    # Both predicates use issubset and tolerate extra columns, so a header
    # containing the union of both exports' columns satisfies both — the
    # required-key sets are disjoint, but the predicates are not mutually
    # exclusive on all inputs. This is not a real Groww export; it exists to
    # pin the actual behaviour and explain why detect() must test the stock
    # predicate before the MF predicate to avoid misdispatch.
    from backend.app.service.ingest.templates.fingerprint import (
        is_groww_stock_header,
        is_groww_header,
    )

    union_headers = [
        "Stock name",
        "Symbol",
        "ISIN",
        "Type",
        "Quantity",
        "Value",
        "Exchange",
        "Exchange Order Id",
        "Execution date and time",
        "Order status",
        "Scheme Name",
        "Transaction Type",
        "Units",
        "NAV",
        "Amount",
        "Date",
    ]
    assert is_groww_stock_header(union_headers) is True
    assert is_groww_header(union_headers) is True


def test_autoguess_detects_single_amount_plus_sign_column():
    # The Axis shape: one amount column, one Debit/Credit sign column.
    roles = autoguess_roles(
        ["Date", "Transaction Details", "Amount (INR)", "Debit/Credit"]
    )
    assert roles["amount"] == "Amount (INR)"
    assert roles["sign"] == "Debit/Credit"
    # Mutually exclusive with the two-column shape: a caller that still read
    # debit/credit would point both legs at a column of the text "Debit".
    assert roles["debit"] is None
    assert roles["credit"] is None


def test_autoguess_detects_the_spaced_sign_column_and_a_bare_amt_header():
    # The HDFC credit-card shape.
    roles = autoguess_roles(
        [
            "Transaction type",
            "Primary / Addon Customer Name",
            "DATE",
            "Description",
            "AMT",
            "Debit / Credit",
        ]
    )
    assert roles["amount"] == "AMT"
    assert roles["sign"] == "Debit / Credit"
    assert roles["debit"] is None and roles["credit"] is None


def test_autoguess_keeps_two_distinct_columns_as_debit_and_credit():
    # The IDFC shape. "Debit" alone must not match the credit pattern, nor
    # vice versa, or this regresses to the sign-column branch.
    roles = autoguess_roles(
        [
            "Transaction Date",
            "Value Date",
            "Particulars",
            "Cheque No.",
            "Debit",
            "Credit",
            "Balance",
        ]
    )
    assert roles["debit"] == "Debit"
    assert roles["credit"] == "Credit"
    assert roles["amount"] is None and roles["sign"] is None


def test_autoguess_does_not_mistake_withdrawal_amt_for_an_amount_column():
    # The HDFC bank shape. Both leg headers contain "Amt", so amount
    # detection must be GATED on having found a sign column first.
    roles = autoguess_roles(
        [
            "Date",
            "Narration",
            "Chq./Ref.No.",
            "Value Dt",
            "Withdrawal Amt.",
            "Deposit Amt.",
            "Closing Balance",
        ]
    )
    assert roles["debit"] == "Withdrawal Amt."
    assert roles["credit"] == "Deposit Amt."
    assert roles["amount"] is None and roles["sign"] is None


def test_autoguess_falls_back_to_two_column_when_no_amount_column_is_found():
    # A sign column with nothing to sign is not the single-amount shape.
    # Asserting the FULL shape, not just amount/sign: the earlier version of
    # this test checked only those two keys, and so passed while the fallback
    # returned the same both-matching header for debit AND credit.
    roles = autoguess_roles(["Date", "Description", "Debit/Credit"])
    assert roles == {
        "date": "Date",
        "narration": "Description",
        "debit": None,
        "credit": None,
        "amount": None,
        "sign": None,
    }


def test_autoguess_never_points_both_money_legs_at_one_column():
    # A single both-matching column and no separate amount column. `_first`
    # resolves the SAME header for debit and credit, and parsers/csv_xls.py tests
    # `if debit:` first -- so every row, salary credits included, books as a
    # debit. One column cannot be both legs: guess nothing and let the wizard
    # ask, rather than return a guess that is known to be wrong.
    roles = autoguess_roles(
        ["Date", "Narration", "Withdrawal/Deposit Amount", "Balance"]
    )
    assert roles["debit"] is None
    assert roles["credit"] is None
    assert roles["amount"] is None and roles["sign"] is None


def test_autoguess_does_not_claim_a_value_date_as_the_amount_column():
    # "Value Date" is a date. With a sign column present to open the amount
    # search, `\bvalue\b` made the statement's second DATE column the amount
    # column -- a date read as money, which is a wrong figure rather than a
    # missing one.
    roles = autoguess_roles(
        [
            "Transaction Date",
            "Value Date",
            "Particulars",
            "Debit/Credit",
            "Balance",
        ]
    )
    assert roles["amount"] is None
    assert roles["sign"] is None  # no amount column -> not the single shape


def test_autoguess_always_returns_all_six_keys():
    for headers in (
        [],
        ["Date"],
        ["Date", "Narration", "Amount (INR)", "Debit/Credit"],
    ):
        assert set(autoguess_roles(headers)) == {
            "date",
            "narration",
            "debit",
            "credit",
            "amount",
            "sign",
        }
