from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.reports.health.ledger_view import PostingRow, all_postings
from backend.app.service.reports.networth.networth import (
    current_position,
    monthly_series,
)


def test_networth_accumulates_balances_by_month(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 1, 10),
                amount=Decimal("100000"),
                raw_narration="OPEN",
                normalized_narration="OPEN",
                source_account="Equity:Opening-Balances",
                direction=Direction.DEBIT,
                suggested_other_leg="Assets:Checking:HDFC",
            ),
            CandidateTxn(
                date=date(2026, 2, 5),
                amount=Decimal("20000"),
                raw_narration="RENT",
                normalized_narration="RENT",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                suggested_other_leg="Expenses:Rent",
            ),
        ],
    )
    out = monthly_series(all_postings(ledger))
    assert out[0] == {
        "month": "2026-01",
        "assets": "100000",
        "liabilities": "0",
        "net": "100000",
    }
    assert out[-1] == {
        "month": "2026-02",
        "assets": "80000",
        "liabilities": "0",
        "net": "80000",
    }


def test_networth_empty_ledger_is_empty(ledger, investment_ledger):
    assert monthly_series(all_postings(ledger)) == []


def test_networth_excludes_tds_receivable(ledger, investment_ledger):
    # TDS withheld posts to Assets:Receivable:TDS but is not a real asset until
    # an actual filing-settlement transaction occurs — the trend/sparkline must
    # exclude it, same as the dashboard headline.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 3),
                amount=Decimal("272000"),
                raw_narration="Salary",
                normalized_narration="SALARY",
                source_account="Assets:Checking:HDFC",
                direction=Direction.CREDIT,
                suggested_category="Income:Salary",
                suggested_other_leg=None,
            ),
            CandidateTxn(
                date=date(2026, 4, 3),
                amount=Decimal("8000"),
                raw_narration="TDS withheld",
                normalized_narration="TDS WITHHELD",
                source_account="Income:Salary",
                direction=Direction.DEBIT,
                suggested_category="Assets:Receivable:TDS",
                suggested_other_leg=None,
            ),
        ],
    )
    out = monthly_series(all_postings(ledger))
    assert out[-1]["assets"] == "272000"
    assert out[-1]["net"] == "272000"


def _row(d: date, account: str, amount: str) -> PostingRow:
    return PostingRow(d, account, Decimal(amount), "synthetic", tags=frozenset())


# Synthetic ledger: 85000 of the 125000 in assets sits in Checking.
ROWS = [
    _row(date(2026, 1, 10), "Assets:Checking:HDFC", "100000"),
    _row(date(2026, 1, 10), "Equity:Opening-Balances", "-100000"),
    _row(date(2026, 2, 5), "Assets:Checking:HDFC", "-40000"),
    _row(date(2026, 2, 5), "Assets:Investment:MutualFund", "40000"),
    _row(date(2026, 2, 20), "Assets:Checking:IDFC", "25000"),
    _row(date(2026, 2, 20), "Income:Salary", "-25000"),
    _row(date(2026, 2, 25), "Liabilities:CreditCard:ICICI", "-3000"),
    _row(date(2026, 2, 25), "Expenses:Food", "3000"),
]


def test_networth_counts_checking_balances():
    # Regression lock: a series that dropped checking would read assets 40000.
    assert monthly_series(ROWS)[-1] == {
        "month": "2026-02",
        "assets": "125000",
        "liabilities": "-3000",
        "net": "122000",
    }


def test_current_position_reports_checking_as_a_group():
    cur = current_position(ROWS)
    assert cur["by_group"] == [
        {"group": "Checking", "amount": "85000.00"},
        {"group": "Investment", "amount": "40000.00"},
    ]
    assert cur["assets"] == "125000.00"
    assert cur["liabilities"] == "-3000.00"
    assert cur["net"] == "122000.00"
    assert cur["as_of"] == "2026-02-25"
    assert cur["excluded"] == []
    assert cur["excluded_total"] == "0.00"


def test_current_position_net_matches_last_series_point():
    cur = current_position(ROWS)
    assert Decimal(cur["net"]) == Decimal(monthly_series(ROWS)[-1]["net"])
    assert sum(Decimal(g["amount"]) for g in cur["by_group"]) == Decimal(cur["assets"])


def test_current_position_excludes_prepaid_tax_and_names_it():
    rows = [
        *ROWS,
        _row(date(2026, 2, 26), "Assets:Receivable:TDS", "5000"),
        _row(date(2026, 2, 26), "Income:Salary", "-5000"),
    ]
    cur = current_position(rows)
    assert cur["assets"] == "125000.00"
    assert cur["excluded"] == [
        {
            "account": "Assets:Receivable:TDS",
            "account_display": "Assets:Receivable:TDS",
            "amount": "5000.00",
        }
    ]
    assert cur["excluded_total"] == "5000.00"
    assert all(g["group"] != "Receivable" for g in cur["by_group"])


def test_current_position_drops_groups_that_net_to_zero():
    rows = [
        *ROWS,
        _row(date(2026, 2, 1), "Assets:Investment:FD:HDFC", "10000"),
        _row(date(2026, 2, 1), "Assets:Checking:HDFC", "-10000"),
        _row(date(2026, 2, 28), "Assets:Investment:FD:HDFC", "-10000"),
        _row(date(2026, 2, 28), "Assets:Checking:HDFC", "10000"),
    ]
    groups = {g["group"]: g["amount"] for g in current_position(rows)["by_group"]}
    assert groups["Investment"] == "40000.00"

    house = [
        *ROWS,
        _row(date(2026, 2, 10), "Assets:House:X", "500"),
        _row(date(2026, 2, 10), "Assets:Checking:HDFC", "-500"),
        _row(date(2026, 2, 12), "Assets:House:X", "-500"),
        _row(date(2026, 2, 12), "Assets:Checking:HDFC", "500"),
    ]
    assert all(g["group"] != "House" for g in current_position(house)["by_group"])


def test_current_position_quantizes_to_paise():
    cur = current_position([_row(date(2026, 3, 1), "Assets:Checking:HDFC", "100.005")])
    assert cur["assets"] == "100.01"
    assert cur["by_group"] == [{"group": "Checking", "amount": "100.01"}]


def test_current_position_empty_ledger():
    assert current_position([]) == {
        "as_of": None,
        "assets": "0.00",
        "liabilities": "0.00",
        "net": "0.00",
        "by_group": [],
        "excluded": [],
        "excluded_total": "0.00",
    }


def test_current_position_assets_is_the_sum_of_the_rounded_groups():
    # Sub-paisa remainders in several groups: rounding each group and rounding
    # the raw total disagree (100.00 + 50.00 + 25.00 vs round(175.012) = 175.01).
    # The chips must add up to Assets exactly, so Assets is the chips' sum.
    rows = [
        _row(date(2026, 3, 1), "Assets:Checking:HDFC", "100.004"),
        _row(date(2026, 3, 1), "Assets:Investment:MutualFund", "50.004"),
        _row(date(2026, 3, 1), "Assets:House:X", "25.004"),
        _row(date(2026, 3, 2), "Liabilities:CreditCard:ICICI", "-10.004"),
    ]
    cur = current_position(rows)
    assert [g["amount"] for g in cur["by_group"]] == ["100.00", "50.00", "25.00"]
    assert cur["assets"] == "175.00"
    assert sum(Decimal(g["amount"]) for g in cur["by_group"]) == Decimal(cur["assets"])
    assert cur["liabilities"] == "-10.00"
    # Net is the raw total rounded once, so it equals the Dashboard's
    # cash_net_worth (the unrounded sum) after rounding to 2 dp.
    raw_net = (
        Decimal("100.004") + Decimal("50.004") + Decimal("25.004") - Decimal("10.004")
    )
    assert cur["net"] == "165.01"
    assert Decimal(cur["net"]) == raw_net.quantize(Decimal("0.01"))
    assert Decimal(cur["net"]) == Decimal(monthly_series(rows)[-1]["net"]).quantize(
        Decimal("0.01")
    )


def test_current_position_totals_several_exclusions_from_the_listed_amounts():
    rows = [
        *ROWS,
        _row(date(2026, 2, 26), "Assets:Receivable:TDS", "5000.004"),
        _row(date(2026, 2, 27), "Assets:Receivable:AdvanceTax", "-1000.004"),
    ]
    cur = current_position(rows)
    assert [(e["account"], e["amount"]) for e in cur["excluded"]] == [
        ("Assets:Receivable:AdvanceTax", "-1000.00"),
        ("Assets:Receivable:TDS", "5000.00"),
    ]
    assert cur["excluded_total"] == "4000.00"


def test_current_position_excluded_accounts_carry_a_display_name():
    rows = [_row(date(2026, 2, 26), "Assets:Receivable:TDS:SYN1", "5000")]
    cur = current_position(rows, names={"SYN1": "Synthetic Employer"})
    assert cur["excluded"] == [
        {
            "account": "Assets:Receivable:TDS:SYN1",
            "account_display": "Assets:Receivable:TDS:Synthetic Employer",
            "amount": "5000.00",
        }
    ]
