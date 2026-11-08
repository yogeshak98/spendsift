from datetime import date
from decimal import Decimal

from backend.app.service.reports.investments.investments import holdings
from backend.app.service.reports.health.ledger_view import PostingRow


def _row(account: str, amount: str) -> PostingRow:
    return PostingRow(
        date=date(2026, 1, 1),
        account=account,
        amount=Decimal(amount),
        narration="",
        tags=frozenset(),
    )


def test_holdings_parses_vehicle_issuer_instrument_and_sums_per_account():
    rows = [
        _row("Assets:Investment:MutualFund:Groww:ParagParikh", "10000"),
        _row("Assets:Investment:MutualFund:Groww:ParagParikh", "5000"),
        _row("Assets:Checking:HDFC", "999999"),  # not an investment account
    ]
    out = holdings(rows)
    assert out["holdings"] == [
        {
            "account": "Assets:Investment:MutualFund:Groww:ParagParikh",
            "account_display": "Assets:Investment:MutualFund:Groww:ParagParikh",
            "vehicle": "MutualFund",
            "issuer": "Groww",
            "instrument": "ParagParikh",
            "instrument_display": "ParagParikh",
            "amount": "15000",
        }
    ]
    assert out["total"] == "15000"


def test_holdings_resolves_scheme_code_to_fund_name():
    # Same contract as /api/transactions and the dashboard's Accounts card —
    # a holding's instrument leaf must resolve to the fund name when a scheme
    # code -> name mapping is available, not leak the raw code.
    rows = [_row("Assets:Investment:MutualFund:Groww:MF122639", "10000")]
    out = holdings(rows, names={"MF122639": "Parag Parikh Flexi Cap Fund"})
    holding = out["holdings"][0]
    assert holding["account"] == "Assets:Investment:MutualFund:Groww:MF122639"
    assert holding["account_display"] == (
        "Assets:Investment:MutualFund:Groww:Parag Parikh Flexi Cap Fund"
    )
    assert holding["instrument"] == "MF122639"
    assert holding["instrument_display"] == "Parag Parikh Flexi Cap Fund"


def test_holdings_instrument_is_none_for_three_segment_accounts():
    rows = [_row("Assets:Investment:RealEstate:Home", "5000000")]
    out = holdings(rows)
    assert out["holdings"][0]["issuer"] == "Home"
    assert out["holdings"][0]["instrument"] is None


def test_holdings_excludes_non_positive_balances():
    rows = [
        _row("Assets:Investment:Equity:Zerodha:Infy", "20000"),
        _row("Assets:Investment:Equity:Zerodha:Infy", "-20000"),  # fully redeemed -> 0
        _row("Assets:Investment:MutualFund:Groww:Mirae", "8000"),
    ]
    out = holdings(rows)
    accounts = [h["account"] for h in out["holdings"]]
    assert "Assets:Investment:Equity:Zerodha:Infy" not in accounts
    assert accounts == ["Assets:Investment:MutualFund:Groww:Mirae"]
    assert out["total"] == "8000"


def test_holdings_rollups_sum_to_total_and_sort_desc():
    rows = [
        _row("Assets:Investment:MutualFund:Groww:Mirae", "8000"),
        _row("Assets:Investment:MutualFund:Groww:Parag", "15000"),
        _row("Assets:Investment:Equity:Zerodha:Infy", "20000"),
        _row("Assets:Investment:RealEstate:Home", "5000000"),
    ]
    out = holdings(rows)
    total = Decimal(out["total"])
    assert total == Decimal("5043000")
    assert sum(Decimal(v["amount"]) for v in out["by_vehicle"]) == total
    assert sum(Decimal(v["amount"]) for v in out["by_issuer"]) == total
    # sorted by amount desc
    vehicle_amts = [Decimal(v["amount"]) for v in out["by_vehicle"]]
    assert vehicle_amts == sorted(vehicle_amts, reverse=True)
    holding_amts = [Decimal(h["amount"]) for h in out["holdings"]]
    assert holding_amts == sorted(holding_amts, reverse=True)
    # by_issuer keeps vehicle + issuer, MutualFund:Groww folds both schemes
    groww = next(v for v in out["by_issuer"] if v["issuer"] == "Groww")
    assert groww == {"vehicle": "MutualFund", "issuer": "Groww", "amount": "23000"}


def test_holdings_classifies_missing_segments_as_unknown_without_crashing():
    rows = [_row("Assets:Investment:Crypto", "30000")]  # no issuer segment
    out = holdings(rows)
    h = out["holdings"][0]
    assert h["vehicle"] == "Crypto"
    assert h["issuer"] == "Unknown"
    assert h["instrument"] is None


def test_holdings_excludes_internal_clearing_cash_subaccount():
    # Assets:Investment:MutualFund:<Issuer>:Cash is the vehicle-level clearing
    # account used to net broker-cash movements, not a real holding.
    rows = [
        _row("Assets:Investment:MutualFund:Groww:Cash", "5000"),
        _row("Assets:Investment:MutualFund:Groww:ParagParikh", "10000"),
    ]
    out = holdings(rows)
    accounts = [h["account"] for h in out["holdings"]]
    assert "Assets:Investment:MutualFund:Groww:Cash" not in accounts
    assert accounts == ["Assets:Investment:MutualFund:Groww:ParagParikh"]
    assert out["total"] == "10000"


def test_holdings_empty_when_no_investment_accounts():
    out = holdings([_row("Assets:Checking:HDFC", "1000")])
    assert out == {"total": "0", "holdings": [], "by_vehicle": [], "by_issuer": []}
