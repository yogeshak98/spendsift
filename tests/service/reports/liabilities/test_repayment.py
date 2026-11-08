from datetime import date
from decimal import Decimal

from backend.app.service.reports.liabilities import repayment
from backend.app.service.reports.health.ledger_view import PostingRow
from backend.app.service.reports.liabilities.loan_terms import LoanTerms


def _p(account, amount):
    return PostingRow(
        date=date(2026, 1, 1),
        account=account,
        amount=Decimal(amount),
        narration="x",
        tags=frozenset(),
    )


def test_loan_with_terms_produces_full_schedule():
    rows = [_p("Liabilities:Loan:Car", "-100000")]  # owe 100000
    terms = LoanTerms(
        {"Liabilities:Loan:Car": {"annual_rate_pct": "12", "term_months": 12}}
    )
    out = repayment.schedules(rows, terms, today=date(2026, 1, 15))["loans"]
    assert len(out) == 1
    loan = out[0]
    assert loan["account"] == "Liabilities:Loan:Car"
    assert loan["terms_set"] is True
    assert loan["outstanding"] == "100000"
    assert loan["term_months"] == 12
    assert len(loan["schedule"]) == 12
    assert loan["schedule"][-1]["balance"] == "0"
    assert loan["payoff_month"] == "2027-01"  # Jan 2026 + 12 months
    # all money fields are strings
    assert isinstance(loan["emi"], str)
    assert isinstance(loan["total_interest"], str)
    assert isinstance(loan["schedule"][0]["principal"], str)


def test_loan_without_terms_is_listed_unset():
    rows = [_p("Liabilities:Loan:Home", "-500000")]
    out = repayment.schedules(rows, LoanTerms({}), today=date(2026, 1, 1))["loans"]
    loan = out[0]
    assert loan["terms_set"] is False
    assert loan["outstanding"] == "500000"
    assert loan["emi"] is None
    assert loan["annual_rate_pct"] is None
    assert loan["term_months"] is None
    assert loan["payoff_month"] is None
    assert loan["schedule"] == []


def test_non_loan_liabilities_and_zero_balances_excluded():
    rows = [
        _p("Liabilities:CreditCard:HDFC", "-2000"),
        _p("Liabilities:Loan:Paid", "-500"),
        _p("Liabilities:Loan:Paid", "500"),  # nets to zero
        _p("Assets:Bank:HDFC", "-100000"),
    ]
    out = repayment.schedules(rows, LoanTerms({}), today=date(2026, 1, 1))["loans"]
    assert out == []


def test_original_principal_override_used_over_current_balance():
    rows = [_p("Liabilities:Loan:Home", "-480000")]
    terms = LoanTerms(
        {
            "Liabilities:Loan:Home": {
                "annual_rate_pct": "10",
                "term_months": 24,
                "original_principal": "500000",
            }
        }
    )
    out = repayment.schedules(rows, terms, today=date(2026, 1, 1))["loans"]
    loan = out[0]
    assert loan["outstanding"] == "480000"  # current balance, unchanged
    # schedule computed off 500000 original principal, not 480000
    assert loan["schedule"][0]["balance"] != "0"
    assert len(loan["schedule"]) == 24


# ── Terms from the account, the file or inference; the EMI pool ─────────────
POOL = "Liabilities:Loan:CardEMI"
PLAN = f"{POOL}:Acme1234-20260722"


def _at(d, account, amount):
    return PostingRow(
        date=d, account=account, amount=Decimal(amount), narration="x", tags=frozenset()
    )


def _running_plan(account=PLAN):
    """A synthetic 12000 / 15% / 12-month plan, 3 instalments paid from 2026-08."""
    from backend.app.service.reports.liabilities import amortize

    sched = amortize.schedule(Decimal(12000), Decimal(15), 12)
    rows = [_at(date(2026, 7, 22), account, "-12000")]
    for m in range(3):
        rows.append(_at(date(2026, 8 + m, 21), account, sched[m]["principal"]))
    return rows


def test_every_loan_carries_all_contract_keys():
    out = repayment.schedules(
        [_p("Liabilities:Loan:Home", "-500000")], LoanTerms({}), today=date(2026, 1, 1)
    )
    (loan,) = out["loans"]
    assert set(loan) == {
        "account",
        "label",
        "card",
        "terms_set",
        "terms_source",
        "outstanding",
        "original_principal",
        "annual_rate_pct",
        "term_months",
        "paid_instalments",
        "start_month",
        "emi",
        "total_interest",
        "payoff_month",
        "schedule",
    }
    assert loan["terms_source"] is None
    assert loan["original_principal"] == "500000"
    assert out["emi_pool"] is None


def test_inferred_plan_schedule_from_original_principal_and_payoff_from_start():
    out = repayment.schedules(
        _running_plan(),
        LoanTerms({}),
        today=date(2026, 11, 1),
        account_meta={
            PLAN: {"label": "Washer", "emi-card": "Liabilities:CreditCard:Acme1234"}
        },
        pool_account=POOL,
    )
    (loan,) = out["loans"]
    assert loan["terms_source"] == "inferred"
    assert loan["terms_set"] is True
    assert loan["original_principal"] == "12000"
    assert loan["payoff_month"] == "2027-07"
    assert loan["paid_instalments"] == 3
    assert loan["start_month"] == "2026-08"
    assert len(loan["schedule"]) == 12
    assert loan["label"] == "Washer"
    assert loan["card"] == "Liabilities:CreditCard:Acme1234"
    assert out["emi_pool"] is None  # the pool is neither open nor posted to


def test_pool_account_is_never_inferred_and_reports_unassigned():
    rows = _running_plan(POOL)[:3]
    out = repayment.schedules(
        rows,
        LoanTerms({}),
        today=date(2026, 11, 1),
        account_meta={POOL: {}},
        pool_account=POOL,
    )
    (loan,) = out["loans"]
    assert loan["account"] == POOL
    assert loan["terms_set"] is False
    total = sum((r.amount for r in rows), Decimal(0))
    assert out["emi_pool"] == {
        "account": POOL,
        "unassigned_count": 3,
        "unassigned_amount": str(-total),
    }


def test_open_pool_without_postings_reports_zero():
    out = repayment.schedules(
        [],
        LoanTerms({}),
        today=date(2026, 11, 1),
        account_meta={POOL: {}},
        pool_account=POOL,
    )
    assert out == {
        "loans": [],
        "emi_pool": {"account": POOL, "unassigned_count": 0, "unassigned_amount": "0"},
    }


def test_account_meta_terms_override_inferred():
    out = repayment.schedules(
        _running_plan(),
        LoanTerms({}),
        today=date(2026, 11, 1),
        account_meta={PLAN: {"rate-pct": Decimal("13.5"), "term-months": 9}},
        pool_account=POOL,
    )
    (loan,) = out["loans"]
    assert loan["terms_source"] == "account"
    assert (loan["annual_rate_pct"], loan["term_months"]) == ("13.5", 9)
    assert loan["paid_instalments"] is None and loan["start_month"] is None
    assert loan["original_principal"] == loan["outstanding"]
    assert loan["payoff_month"] == "2027-08"  # Nov 2026 + 9 months


# ── Inference is for card EMI plan accounts only ─────────────────────────────
def _equal_instalment_loan(account):
    """A synthetic loan whose full EMI is booked to the loan in equal amounts:
    the shape inference would read as a no-cost (0%) plan."""
    rows = [_at(date(2026, 1, 5), account, "-120000")]
    for m in range(3):
        rows.append(_at(date(2026, 2 + m, 5), account, "10000"))
    return rows


def test_equal_instalment_home_loan_is_not_inferred():
    from backend.app.service.reports.liabilities.emi_terms import infer_terms

    rows = _equal_instalment_loan("Liabilities:Loan:HomeLoan")
    assert infer_terms(rows) is not None  # inference would have guessed 0%
    out = repayment.schedules(
        rows, LoanTerms({}), today=date(2026, 5, 1), pool_account=POOL
    )
    (loan,) = out["loans"]
    assert loan["terms_source"] is None
    assert loan["terms_set"] is False
    assert loan["annual_rate_pct"] is None


def test_grandchild_of_pool_and_no_pool_are_not_inferred():
    deep = f"{PLAN}:Extra"
    out = repayment.schedules(
        _running_plan(deep), LoanTerms({}), today=date(2026, 11, 1), pool_account=POOL
    )
    (loan,) = out["loans"]
    assert loan["terms_source"] is None
    out = repayment.schedules(
        _running_plan(), LoanTerms({}), today=date(2026, 11, 1), pool_account=None
    )
    (loan,) = out["loans"]
    assert loan["terms_source"] is None


def test_home_loan_terms_still_come_from_meta_or_file():
    loan_acct = "Liabilities:Loan:HomeLoan"
    rows = _equal_instalment_loan(loan_acct)
    file_terms = LoanTerms({loan_acct: {"annual_rate_pct": "8.5", "term_months": 240}})
    out = repayment.schedules(
        rows, file_terms, today=date(2026, 5, 1), pool_account=POOL
    )
    assert out["loans"][0]["terms_source"] == "file"
    out = repayment.schedules(
        rows,
        LoanTerms({}),
        today=date(2026, 5, 1),
        account_meta={loan_acct: {"rate-pct": Decimal("8.5"), "term-months": 240}},
        pool_account=POOL,
    )
    assert out["loans"][0]["terms_source"] == "account"


# ── Closed loans and the pool's countable postings ──────────────────────────
def test_closed_loan_is_omitted():
    rows = [_p("Liabilities:Loan:Car", "-1000"), _p("Liabilities:Loan:Home", "-500")]
    out = repayment.schedules(
        rows,
        LoanTerms({}),
        today=date(2026, 1, 1),
        closed=frozenset({"Liabilities:Loan:Car"}),
    )
    assert [loan["account"] for loan in out["loans"]] == ["Liabilities:Loan:Home"]


def test_pool_counts_only_postings_with_a_txn_id():
    from backend.app.service.reports.health.ledger_view import TransactionRow

    tracked = _at(date(2026, 8, 21), POOL, "600")
    untracked = _at(date(2026, 9, 21), POOL, "400")
    txns = [
        TransactionRow(
            txn_id="t1",
            date=tracked.date,
            narration="x",
            postings=[tracked],
            year_file_hash="",
        )
    ]
    out = repayment.schedules(
        [tracked, untracked],
        LoanTerms({}),
        today=date(2026, 11, 1),
        account_meta={POOL: {}},
        pool_account=POOL,
        transactions=txns,
    )
    assert out["emi_pool"] == {
        "account": POOL,
        "unassigned_count": 1,
        "unassigned_amount": "600",
    }
