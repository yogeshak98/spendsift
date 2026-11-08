from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.reports.cashflow.cashflow import (
    available_years,
    income_statement_pnl,
    monthly_series,
    monthly_series_range,
    overview,
    statement,
)
from backend.app.service.reports.cashflow.overview import flow_overview
from backend.app.service.reports.cashflow.period import DateRange
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    TransactionRow,
    all_postings,
)


def _row(d: date) -> PostingRow:
    return PostingRow(
        date=d,
        account="Expenses:Food",
        amount=Decimal("100"),
        narration="x",
        tags=frozenset(),
    )


def test_available_years_splits_calendar_and_fy():
    # 2025-02-10 -> calendar 2025, FY 2024 (Jan-Mar belongs to prior April-start year)
    # 2025-06-10 -> calendar 2025, FY 2025
    rows = [_row(date(2025, 2, 10)), _row(date(2025, 6, 10))]
    assert available_years(rows) == {
        "calendar_years": [2025],
        "fiscal_years": [2024, 2025],
    }


def test_available_years_empty():
    assert available_years([]) == {"calendar_years": [], "fiscal_years": []}


# Fixture expense total: 35000 + 412 + 888 + 4299 = 40599; net = 280000 - 40599 = 239401.


def test_monthly_series_has_april(loaded_ledger):
    series = monthly_series(all_postings(loaded_ledger), 2026)
    assert len(series) == 12
    assert series[0]["month"] == 4
    assert series[-1]["month"] == 3
    april = next(m for m in series if m["month"] == 4)
    assert april["income"] == "280000"
    assert april["expenses"] == "40599"
    may = next(m for m in series if m["month"] == 5)
    assert may["income"] == "0"
    assert may["expenses"] == "0"


def test_monthly_series_excludes_other_fy(loaded_ledger):
    # April-2026 belongs to FY2026, not FY2025 (Apr-2025 .. Mar-2026).
    series = monthly_series(all_postings(loaded_ledger), 2025)
    assert len(series) == 12
    assert all(m["income"] == "0" and m["expenses"] == "0" for m in series)


def _purchase_candidate() -> CandidateTxn:
    return CandidateTxn(
        date=date(2026, 4, 18),
        amount=Decimal("21500.00"),
        raw_narration="Purchase — Parag Parikh Flexi Cap",
        normalized_narration="Parag Parikh Flexi Cap",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal("100.0"),
        commodity="MF122639",
        unit_cost=Decimal("215.0"),
    )


def test_investment_flow_uses_rupee_cost_not_commodity_share_count(
    ledger, investment_ledger
):
    # Post the investment-ledger detach split (2026-09-05 plan Unit 3): a
    # Groww PURCHASE now posts a coarse vehicle-level INR leg
    # (Assets:Investment:MutualFund) into main.beancount, funded from the
    # Groww wallet (Assets:Broker:Groww) — the raw commodity share-count lot
    # lives only in investments.beancount now. The main-ledger investment
    # flow must still reflect the rupee cost (21500), not any share count.
    commit_candidates(ledger, investment_ledger, [_purchase_candidate()])
    series = monthly_series(all_postings(ledger), 2026)
    april = next(m for m in series if m["month"] == 4)
    assert april["investment"] == "21500.00"


def test_investment_ledger_carries_the_raw_share_count_posting(
    ledger, investment_ledger
):
    # The split's other half: investments.beancount holds the actual lot
    # (100 units, not the 21500 rupee cost) under the per-scheme holding
    # account.
    commit_candidates(ledger, investment_ledger, [_purchase_candidate()])
    rows = all_postings(investment_ledger)
    holding = next(r for r in rows if r.account.endswith(":Groww:MF122639"))
    assert holding.amount == Decimal("100.0")


def test_pnl_columns_match_requested_fys(loaded_ledger):
    rows = all_postings(loaded_ledger)
    data = income_statement_pnl(rows, [2024, 2025, 2026])
    assert data["fys"] == [2024, 2025, 2026]
    assert [c["fy"] for c in data["columns"]] == [2024, 2025, 2026]
    assert data["columns"][-1]["label"] == "FY 2026-27"


def test_pnl_income_section_positive_and_flipped(loaded_ledger):
    data = income_statement_pnl(all_postings(loaded_ledger), [2024, 2025, 2026])
    income = next(s for s in data["sections"] if s["key"] == "income")
    assert income["label"] == "Revenue"
    salary = next(
        li for li in income["line_items"] if li["category"] == "Income:Salary"
    )
    assert salary["amounts"] == ["0", "0", "280000"]  # positive, Apr-2026 in FY2026 col
    assert income["totals"] == ["0", "0", "280000"]


def test_pnl_expense_section_grouped_by_top_category(loaded_ledger):
    data = income_statement_pnl(all_postings(loaded_ledger), [2026])
    exp = next(s for s in data["sections"] if s["key"] == "expenses")
    cats = {li["category"]: li["amounts"][0] for li in exp["line_items"]}
    assert cats["Expenses:Rent"] == "35000"
    assert cats["Expenses:Food"] == "1300"  # 412 + 888 folded into top category
    assert cats["Expenses:Shopping"] == "4299"
    assert exp["totals"] == ["40599"]


def test_pnl_expense_line_items_sorted_desc_by_total(loaded_ledger):
    data = income_statement_pnl(all_postings(loaded_ledger), [2026])
    exp = next(s for s in data["sections"] if s["key"] == "expenses")
    cats = [li["category"] for li in exp["line_items"]]
    assert cats == ["Expenses:Rent", "Expenses:Shopping", "Expenses:Food"]


def test_pnl_net_is_income_minus_expenses_per_column(loaded_ledger):
    data = income_statement_pnl(all_postings(loaded_ledger), [2025, 2026])
    assert data["net"] == ["0", "239401"]


def test_pnl_empty_fy_columns_are_zero_strings(loaded_ledger):
    data = income_statement_pnl(all_postings(loaded_ledger), [2024])
    income = next(s for s in data["sections"] if s["key"] == "income")
    # FY2024 has no activity in the fixture; line items with zero-everywhere are dropped,
    # so the section may be empty, but totals/net still carry one "0" per column.
    assert income["totals"] == ["0"]
    assert data["net"] == ["0"]


def test_monthly_series_carries_five_series_and_balance(loaded_ledger):
    series = monthly_series(all_postings(loaded_ledger), 2026)
    assert len(series) == 12
    april = next(m for m in series if m["month"] == 4)
    # Existing fixture: Salary 280000 (Income), Rent 35000 + Food 1300 +
    # Shopping 4299 = 40599 (Expenses). No liabilities/investment/tax postings.
    assert april["income"] == "280000"
    assert april["expenses"] == "40599"
    assert april["liabilities"] == "0"
    assert april["investment"] == "0"
    assert april["tax"] == "0"
    # Every month exposes all five series + balance as strings.
    for m in series:
        for key in (
            "income",
            "expenses",
            "liabilities",
            "investment",
            "tax",
            "balance",
        ):
            assert isinstance(m[key], str)


def test_monthly_series_balance_is_running_liquid_cumulative():
    # Liquid = Assets:Checking:* + Assets:Cash*. Build a tiny synthetic ledger.
    rows = [
        # Opening liquid balance BEFORE the FY (Mar 2026 belongs to FY2025).
        PostingRow(
            date(2026, 3, 31),
            "Assets:Checking:HDFC",
            Decimal("1000"),
            "open",
            frozenset(),
        ),
        # Inside FY2026:
        PostingRow(
            date(2026, 4, 10), "Assets:Checking:HDFC", Decimal("500"), "in", frozenset()
        ),
        PostingRow(date(2026, 5, 10), "Assets:Cash", Decimal("200"), "in", frozenset()),
        PostingRow(
            date(2026, 6, 10),
            "Assets:Checking:HDFC",
            Decimal("-300"),
            "out",
            frozenset(),
        ),
        # A non-liquid posting must NOT move the balance line.
        PostingRow(
            date(2026, 6, 10),
            "Assets:Investment:Equity",
            Decimal("9999"),
            "inv",
            frozenset(),
        ),
    ]
    series = monthly_series(rows, 2026)
    by_month = {m["month"]: m for m in series}
    assert by_month[4]["balance"] == "1500"  # opening 1000 + 500 Apr
    assert by_month[5]["balance"] == "1700"  # + 200 cash May
    assert by_month[6]["balance"] == "1400"  # - 300 Jun; investment ignored
    assert by_month[3]["balance"] == "1400"  # carries forward to Mar
    assert by_month[6]["investment"] == "9999"


def test_monthly_series_liabilities_repayment_is_positive_flow():
    # A principal repayment debits Liabilities (positive amount into the account,
    # reducing the negative balance). Report it as a positive outflow bar.
    rows = [
        PostingRow(
            date(2026, 7, 5),
            "Liabilities:Loan:HDFC",
            Decimal("2500"),
            "emi",
            frozenset(),
        ),
    ]
    series = monthly_series(rows, 2026)
    july = next(m for m in series if m["month"] == 7)
    assert july["liabilities"] == "2500"


def test_monthly_series_tax_paid_is_outflow_and_excluded_from_expenses():
    # A real tax PAYMENT: Expenses:Taxes leg + the balancing liquid debit.
    rows = [
        PostingRow(
            date(2026, 8, 1),
            "Expenses:Taxes:AdvanceTax",
            Decimal("12000"),
            "advtax",
            frozenset(),
        ),
        PostingRow(
            date(2026, 8, 1),
            "Assets:Checking:HDFC",
            Decimal("-12000"),
            "advtax",
            frozenset(),
        ),
        PostingRow(
            date(2026, 8, 2), "Expenses:Food", Decimal("400"), "food", frozenset()
        ),
    ]
    series = monthly_series(rows, 2026)
    aug = next(m for m in series if m["month"] == 8)
    # Tax-paid surfaces in the tax series...
    assert aug["tax"] == "12000"
    # ...and is NOT double-counted inside expenses.
    assert aug["expenses"] == "400"
    # Tax PAID is a true liquid outflow: the balance line dips by 12000.
    assert aug["balance"] == "-12000"


def test_monthly_series_tds_withheld_does_not_move_balance():
    # GROSS-with-TDS salary: 100000 gross, 20000 TDS withheld, 80000 to bank.
    rows = [
        PostingRow(
            date(2026, 9, 5), "Income:Salary", Decimal("-100000"), "salary", frozenset()
        ),
        PostingRow(
            date(2026, 9, 5),
            "Income:Salary:TDS",
            Decimal("20000"),
            "salary",
            frozenset(),
        ),
        PostingRow(
            date(2026, 9, 5),
            "Assets:Checking:HDFC",
            Decimal("80000"),
            "salary",
            frozenset(),
        ),
    ]
    series = monthly_series(rows, 2026)
    sep = next(m for m in series if m["month"] == 9)
    # TDS withheld appears in the Tax series...
    assert sep["tax"] == "20000"
    # ...income stays NET of TDS (what the bank actually received).
    assert sep["income"] == "80000"
    # CRITICAL: withheld TDS NEVER touched the bank -> balance reflects only the
    # 80000 deposit, NOT a 20000 outflow.
    assert sep["balance"] == "80000"


def test_monthly_series_tax_sums_tds_and_payment_within_a_month():
    rows = [
        # TDS withheld from interest (gross 5000, TDS 500, 4500 to bank)
        PostingRow(
            date(2026, 10, 1), "Income:Interest:FD", Decimal("-5000"), "fd", frozenset()
        ),
        PostingRow(
            date(2026, 10, 1),
            "Income:Interest:FD:TDS",
            Decimal("500"),
            "fd",
            frozenset(),
        ),
        PostingRow(
            date(2026, 10, 1),
            "Assets:Checking:HDFC",
            Decimal("4500"),
            "fd",
            frozenset(),
        ),
        # Advance-tax payment 3000 the same month
        PostingRow(
            date(2026, 10, 9),
            "Expenses:Taxes:AdvanceTax",
            Decimal("3000"),
            "advtax",
            frozenset(),
        ),
        PostingRow(
            date(2026, 10, 9),
            "Assets:Checking:HDFC",
            Decimal("-3000"),
            "advtax",
            frozenset(),
        ),
    ]
    series = monthly_series(rows, 2026)
    oct_ = next(m for m in series if m["month"] == 10)
    # tax = TDS 500 + payment 3000
    assert oct_["tax"] == "3500"
    # income net of TDS = 4500
    assert oct_["income"] == "4500"
    # balance moved by deposit 4500 - payment 3000 = 1500 (TDS never hit bank)
    assert oct_["balance"] == "1500"


# --- Spending excludes Expenses:Investment (MF charge plugs) ------------------


def _p(d, account, amount, narration="x", inr=None):
    return PostingRow(
        date=d,
        account=account,
        amount=Decimal(amount),
        narration=narration,
        tags=frozenset(),
        inr_amount=Decimal(inr if inr is not None else amount),
    )


def _mf_buy(d):
    return [
        _p(d, "Assets:Investment:MutualFund", "600036.2856"),
        _p(d, "Assets:Checking:Synth", "-599970"),
        _p(d, "Expenses:Investment:Charges:Synth", "-66.2856"),
    ]


def test_monthly_series_investment_includes_charges():
    series = monthly_series(_mf_buy(date(2026, 8, 5)), 2026)
    aug = next(m for m in series if m["month"] == 8)
    assert aug["investment"] == "599970.0000"
    assert aug["expenses"] == "0"


def test_income_statement_pnl_excludes_investment_charges():
    rows = [*_mf_buy(date(2026, 8, 5)), _p(date(2026, 8, 6), "Expenses:Food", "100")]
    pnl = income_statement_pnl(rows, [2026])
    expenses = next(s for s in pnl["sections"] if s["key"] == "expenses")
    assert [li["category"] for li in expenses["line_items"]] == ["Expenses:Food"]
    assert expenses["totals"] == ["100"]


def test_cashflow_sums_inr_amount_for_commodity_postings():
    d = date(2026, 8, 5)
    rows = [
        _p(d, "Expenses:Gifts", "2", inr="200"),
        _p(d, "Income:Bonus", "-3", inr="-300"),
    ]
    aug = next(m for m in monthly_series(rows, 2026) if m["month"] == 8)
    assert (aug["income"], aug["expenses"]) == ("300", "200")


# --- Cash flow over a date range (Phase 1-3) -----------------------------------

_LEGACY_MONTH_KEYS = {
    "month",
    "income",
    "expenses",
    "liabilities",
    "investment",
    "tax",
    "balance",
}


def _salary(d):
    return [
        _p(d, "Income:Salary", "-100000", "Synth Payroll"),
        _p(d, "Income:Salary:TDS", "10000", "Synth Payroll"),
        _p(d, "Assets:Checking:Synth", "90000", "Synth Payroll"),
    ]


def _paid(d, account, amount, narration="x"):
    return [
        _p(d, account, amount, narration),
        _p(d, "Assets:Checking:Synth", str(-Decimal(amount)), narration),
    ]


def _seeded():
    aug = date(2026, 8, 5)
    return [
        *_salary(date(2026, 8, 1)),
        *_paid(aug, "Expenses:Rent", "25000"),
        *_paid(aug, "Expenses:Food", "5000"),
        _p(aug, "Assets:Investment:MutualFund:Synth", "20000"),
        _p(aug, "Assets:Checking:Synth", "-19995"),
        _p(aug, "Expenses:Investment:Charges:Synth", "-5"),
    ]


AUG = DateRange(date(2026, 8, 1), date(2026, 8, 31))


def test_cashflow_overview_totals_and_savings_rate():
    out = overview(_seeded(), AUG)
    assert out["totals"] == {
        "income": "90000.00",
        "expenses": "30000.00",
        "invested": "19995.00",
        "saved": "60000.00",
        "savings_rate": "66.7",
    }
    assert (out["start"], out["end"]) == ("2026-08-01", "2026-08-31")
    (month,) = out["by_month"]
    assert (month["year"], month["month"], month["label"]) == (2026, 8, "Aug")
    assert (month["income"], month["investment"], month["tax"]) == (
        "90000.00",
        "19995.00",
        "10000.00",
    )


def test_cashflow_overview_savings_rate_null_without_income():
    out = overview(_paid(date(2026, 8, 5), "Expenses:Food", "100"), AUG)
    assert out["totals"]["savings_rate"] is None
    assert out["totals"]["saved"] == "-100.00"


def test_cashflow_overview_empty_range_is_zero_state():
    out = overview([], AUG)
    assert out["totals"] == {
        "income": "0.00",
        "expenses": "0.00",
        "invested": "0.00",
        "saved": "0.00",
        "savings_rate": None,
    }
    assert out["statement"] == {"income": [], "expenses": [], "net": "0.00"}
    assert len(out["by_month"]) == 1


def test_cashflow_overview_expenses_include_tax_paid():
    rows = [*_paid(date(2026, 8, 5), "Expenses:Taxes:Advance", "700")]
    out = overview(rows, AUG)
    assert out["totals"]["expenses"] == "700.00"
    (month,) = out["by_month"]
    assert (month["expenses"], month["tax"]) == ("0.00", "700.00")


def test_cashflow_overview_expenses_match_expense_overview():
    rows = _seeded()
    txns = [
        TransactionRow(f"t{i}", r.date, r.narration, [r], "h")
        for i, r in enumerate(rows)
    ]
    expense = flow_overview(txns, "expense", AUG, "all", None)
    assert overview(rows, AUG)["totals"]["expenses"] == expense["headline"]["net"]


def test_cashflow_overview_statement_sums_to_totals():
    rows = [*_seeded(), *_paid(date(2026, 8, 9), "Expenses:Food:Cafe", "250.50")]
    rows.append(_p(date(2026, 8, 9), "Income:Interest", "-12.25"))
    out = overview(rows, AUG)
    st, totals = out["statement"], out["totals"]
    assert sum(Decimal(x["amount"]) for x in st["income"]) == Decimal(totals["income"])
    assert sum(Decimal(x["amount"]) for x in st["expenses"]) == Decimal(
        totals["expenses"]
    )
    assert st["net"] == totals["saved"]
    assert [x["category"] for x in st["expenses"]] == ["Expenses:Rent", "Expenses:Food"]
    assert st["expenses"][1]["amount"] == "5250.50"


def test_statement_excludes_investment_charges():
    st = statement(_seeded(), AUG)
    assert all(x["category"] != "Expenses:Investment" for x in st["expenses"])


def test_monthly_series_range_clips_partial_months_and_opens_balance():
    rows = [
        *_paid(date(2026, 8, 10), "Expenses:Food", "100"),  # before the range
        *_paid(date(2026, 8, 20), "Expenses:Food", "40"),
        *_paid(date(2026, 10, 20), "Expenses:Food", "7"),  # after the range
        _p(date(2026, 1, 1), "Assets:Checking:Synth", "1000"),
    ]
    r = DateRange(date(2026, 8, 15), date(2026, 10, 14))
    series = monthly_series_range(rows, r)
    assert [(m["year"], m["month"]) for m in series] == [
        (2026, 8),
        (2026, 9),
        (2026, 10),
    ]
    assert [m["expenses"] for m in series] == ["40", "0", "0"]
    # The opening balance holds the liquid net before 15 Aug: 1000 - 100.
    assert [m["balance"] for m in series] == ["860", "860", "860"]
    assert [m["label"] for m in series] == ["Aug", "Sep", "Oct"]


def test_monthly_series_range_labels_carry_year_across_calendar_years():
    r = DateRange(date(2025, 12, 1), date(2026, 1, 31))
    assert [m["label"] for m in monthly_series_range([], r)] == ["Dec 25", "Jan 26"]


def test_monthly_series_fy_shape_unchanged(loaded_ledger):
    series = monthly_series(all_postings(loaded_ledger), 2026)
    assert len(series) == 12
    assert all(set(m) == _LEGACY_MONTH_KEYS for m in series)
    assert [m["month"] for m in series] == [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]


def test_rounded_statement_lines_sum_to_rounded_totals():
    d = date(2026, 8, 5)
    rows = [
        _p(d, "Income:A", "-0.005"),
        _p(d, "Income:B", "-0.005"),
        _p(d, "Expenses:Food", "0.005"),
        _p(d, "Expenses:Rent", "0.005"),
    ]
    out = overview(rows, AUG)
    st, totals = out["statement"], out["totals"]
    assert totals["income"] == totals["expenses"] == "0.01"
    assert sum(Decimal(x["amount"]) for x in st["income"]) == Decimal("0.01")
    assert sum(Decimal(x["amount"]) for x in st["expenses"]) == Decimal("0.01")
    assert Decimal(totals["saved"]) == Decimal(totals["income"]) - Decimal(
        totals["expenses"]
    )
    assert st["net"] == totals["saved"]


def test_rounded_saved_is_income_minus_expenses():
    d = date(2026, 8, 5)
    rows = [_p(d, "Income:A", "-0.006"), _p(d, "Expenses:Food", "0.004")]
    totals = overview(rows, AUG)["totals"]
    assert (totals["income"], totals["expenses"]) == ("0.01", "0.00")
    assert totals["saved"] == "0.01"


# --- overview `series` (Heatmap | Bars) ---------------------------------------


def test_cashflow_overview_month_series_is_daily_and_zero_filled():
    out = overview(_seeded(), AUG)
    assert out["granularity"] == "day"
    assert len(out["series"]) == 31
    first, fifth = out["series"][0], out["series"][4]
    assert first == {
        "key": "2026-08-01",
        "label": "1 Aug",
        "income": "90000.00",
        "expenses": "0.00",
        "invested": "0.00",
        "net": "90000.00",
    }
    # Investments are not expenses; net is income - expenses.
    assert fifth == {
        "key": "2026-08-05",
        "label": "5 Aug",
        "income": "0.00",
        "expenses": "30000.00",
        "invested": "19995.00",
        "net": "-30000.00",
    }
    assert {p["net"] for p in out["series"][5:]} == {"0.00"}


def test_cashflow_overview_fy_series_is_monthly_and_sums_to_totals():
    out = overview(
        [*_seeded(), *_paid(date(2027, 1, 9), "Expenses:Taxes:Advance", "700")],
        DateRange(date(2026, 4, 1), date(2027, 3, 31)),
    )
    assert out["granularity"] == "month"
    keys = [p["key"] for p in out["series"]]
    assert (len(keys), keys[0], keys[-1]) == (12, "2026-04", "2027-03")
    assert out["series"][0]["label"] == "Apr 26"
    jan = next(p for p in out["series"] if p["key"] == "2027-01")
    # Tax paid is spending here, as in totals.expenses.
    assert (jan["expenses"], jan["net"]) == ("700.00", "-700.00")
    for field in ("income", "expenses", "invested"):
        total = sum((Decimal(p[field]) for p in out["series"]), Decimal(0))
        assert total == Decimal(out["totals"][field])
    net = sum((Decimal(p["net"]) for p in out["series"]), Decimal(0))
    assert net == Decimal(out["totals"]["saved"])


def test_cashflow_overview_series_92_days_is_daily_93_monthly():
    d92 = DateRange(date(2026, 8, 1), date(2026, 10, 31))
    d93 = DateRange(date(2026, 8, 1), date(2026, 11, 1))
    assert overview([], d92)["granularity"] == "day"
    assert len(overview([], d92)["series"]) == 92
    assert overview([], d93)["granularity"] == "month"
    assert [p["label"] for p in overview([], d93)["series"]] == [
        "Aug",
        "Sep",
        "Oct",
        "Nov",
    ]


def test_cashflow_overview_series_net_of_tds_and_refunds():
    d = date(2026, 8, 3)
    rows = [
        *_salary(d),
        *_paid(d, "Expenses:Food", "500"),
        *_paid(d, "Expenses:Food", "-200"),  # a refund
    ]
    point = overview(rows, AUG)["series"][2]
    assert (point["income"], point["expenses"], point["net"]) == (
        "90000.00",
        "300.00",
        "89700.00",
    )
