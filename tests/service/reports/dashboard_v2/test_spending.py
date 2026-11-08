"""Dashboard spending: one-time detection, averages, drift, month boundaries.

Report date 2026-10-15, so the 12 complete months are Oct 2025 .. Sep 2026
and October 2026 is incomplete. The usual-row window is Oct 2025 .. 15 Oct
2026.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.service.reports.dashboard_v2 import spending as sp
from backend.app.service.reports.dashboard_v2.cash_flow import cash_flow_summary
from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow
from backend.app.service.reports.networth.liquidity import average_monthly_spend

REPORT = date(2026, 10, 15)
CHK = "Assets:Checking:Acme"
CARD = "Liabilities:CreditCard:Acme1234"


def _txn(
    d: date, narration: str, debit: str, credit: str, amount: str
) -> TransactionRow:
    amt = Decimal(amount)
    return TransactionRow(
        txn_id=f"{d.isoformat()}-{narration}",
        date=d,
        narration=narration,
        postings=[
            PostingRow(d, debit, amt, narration, frozenset()),
            PostingRow(d, credit, -amt, narration, frozenset()),
        ],
        year_file_hash="x",
    )


def _months_back(n: int, day: int = 5) -> date:
    """`day` of the month `n` months before October 2026."""
    idx = 2026 * 12 + 9 - n
    return date(idx // 12, idx % 12 + 1, day)


# Rent, monthly (recurring): the 6 oldest complete months 10,000, the next 3
# 20,000, the last 3 40,000. Rent alone averages 40,000 / 30,000 / 20,000 over
# 3 / 6 / 12 months.
RENT = [
    _txn(_months_back(n), "RENT", "Expenses:Rent", CHK, amt)
    for n, amt in zip(
        range(12, 0, -1), ["10000"] * 6 + ["20000"] * 3 + ["40000"] * 3, strict=True
    )
]
# Claude, monthly: 1,900 on the 5th of each complete month and of October.
CLAUDE = [
    _txn(
        _months_back(n),
        "CLAUDE.AI SUBSCRIPTION",
        "Expenses:Subscriptions",
        CARD,
        "1900",
    )
    for n in range(12, -1, -1)
]
# LeetCode, yearly, paid twice 12 months apart. The category's usual row is
# Claude's 1,900, so 13,000 (6.8x) would stand out: only being recurring keeps
# it out of the one-time spends.
LEETCODE = [
    _txn(date(y, 9, 10), "LEETCODE PREMIUM", "Expenses:Subscriptions", CARD, "13000")
    for y in (2025, 2026)
]
# Food orders, three a month (500, 600, 700) with a different narration each
# time, so never recurring; ordinary in size, so never one-time.
FOOD = [
    _txn(
        _months_back(n, day),
        f"SWIGGY ORDER {n}-{day}",
        "Expenses:Food",
        CARD,
        amount,
    )
    for n in range(12, -1, -1)
    for day, amount in ((8, "500"), (12, "600"), (16, "700"))
]
# A bigger order: 1,500 is 2.5x the Food usual (600). Not one-time.
BIG_ORDER = _txn(date(2026, 9, 20), "ZOMATO ORDER 991", "Expenses:Food", CARD, "1500")
# A dinner party: 2,500 is 4.2x the Food usual (600). One-time.
PARTY = _txn(date(2026, 8, 22), "DINNER PARTY", "Expenses:Food", CARD, "2500")
# The bike, once, in Sep 2026: Bike has under 3 rows, so it is judged against
# the median of all spending rows (700): 85.7x. One-time.
BIKE = _txn(date(2026, 9, 22), "ROYAL ENFIELD", "Expenses:Bike", CARD, "60000")
# October is incomplete on the report date: never in the averages.
OCTOBER = _txn(date(2026, 10, 2), "RENT", "Expenses:Rent", CHK, "99999")
# Investing in a complete month: a SIP and its stamp duty, neither is spending.
SIP = _txn(date(2026, 8, 10), "SIP", "Assets:Investment:MutualFund:X", CHK, "50000")
STAMP = _txn(
    date(2026, 8, 10), "STAMP DUTY", "Expenses:Investment:Charges", CHK, "5000"
)

TXNS = [*RENT, *CLAUDE, *LEETCODE, *FOOD, BIG_ORDER, PARTY, BIKE, OCTOBER, SIP, STAMP]
ROWS = [p for t in TXNS for p in t.postings]


def _rows(txns: list[TransactionRow]) -> list[PostingRow]:
    return [p for t in txns for p in t.postings]


def _result(report: date = REPORT):
    return sp.spending(ROWS, report, sp.one_time_rule(ROWS, report))


def _items(txns: list[TransactionRow] = TXNS) -> list[dict]:
    rule = sp.one_time_rule(_rows(txns), REPORT)
    return sp.one_time_spends(txns, REPORT, rule, {})["items"]


def _amounts(variant: dict) -> tuple:
    return tuple(variant[f"avg_{n}"]["amount"] for n in (3, 6, 12))


def _month(key: str) -> dict:
    return next(m for m in _result()["months"] if m["month"] == key)


def test_one_time_spends_are_exactly_the_bike_and_the_party():
    assert [i["name"] for i in _items()] == ["ROYAL ENFIELD", "DINNER PARTY"]


def test_yearly_subscription_paid_twice_is_recurring_and_stays_in():
    assert "LEETCODE PREMIUM" not in {i["name"] for i in _items()}
    sep = _month("2026-09")
    # rent 40,000 + Claude 1,900 + food 1,800 + LeetCode 13,000 + big order
    # 1,500 + bike 60,000; only the bike is one-time.
    assert sep["all"] == "118200.00"
    assert sep["one_time"] == "60000.00"
    assert sep["without_one_time"] == "58200.00"


def test_monthly_subscription_is_recurring_not_one_time():
    assert "CLAUDE.AI SUBSCRIPTION" not in {i["name"] for i in _items()}


def test_ordinary_non_recurring_food_orders_are_not_one_time():
    names = {i["name"] for i in _items()}
    assert not any(n.startswith(("SWIGGY", "ZOMATO")) for n in names)


def test_bike_is_judged_against_all_spending_when_its_category_is_new():
    bike = _items()[0]
    assert bike["name"] == "ROYAL ENFIELD"
    assert (bike["category"], bike["account"], bike["paid_from"]) == (
        "Expenses:Bike",
        "Expenses:Bike",
        CARD,
    )
    assert bike["amount"] == "60000.00"
    assert (bike["usual"], bike["usual_basis"]) == ("700.00", "all_spending")
    assert bike["multiple_of_usual"] == "85.7"


def test_party_stands_out_against_its_own_category():
    party = _items()[1]
    assert (party["usual"], party["usual_basis"]) == ("600.00", "category")
    assert party["multiple_of_usual"] == "4.2"


def test_averages_and_drift_differ_across_windows():
    res = _result()
    # Without one-offs: rent 40k/30k/20k, Claude 1,900 and food 1,800 a month,
    # plus LeetCode 13,000 and the 1,500 order in Sep (in all three windows).
    core = res["without_one_time"]
    # 145,600 / 3, 216,700 / 6, 298,900 / 12
    assert _amounts(core) == ("48533.33", "36116.67", "24908.33")
    assert (core["lifestyle_drift_pct"], core["drift_basis"]) == ("94.8", "ok")
    # All spending adds the bike (60,000) and the party (2,500).
    every = res["all"]
    assert _amounts(every) == ("69366.67", "46533.33", "30116.67")
    assert every["lifestyle_drift_pct"] == "130.3"
    # Every month of the window has spending.
    assert [every[f"avg_{n}"]["months_counted"] for n in (3, 6, 12)] == [3, 6, 12]


def test_three_month_average_is_the_months_of_cover_pace():
    expected = average_monthly_spend(ROWS, REPORT)
    assert expected is not None
    assert _result()["all"]["avg_3"]["amount"] == str(
        expected.quantize(Decimal("0.01"))
    )


def test_incomplete_report_month_is_left_out():
    res = _result()
    assert [m["month"] for m in res["months"]][-1] == "2026-09"
    assert len(res["months"]) == 12
    # The 99,999 October rent would lift the 3-month average if counted.
    assert res["without_one_time"]["avg_3"]["amount"] == "48533.33"


def test_report_on_the_first_counts_the_month_just_ended():
    res = _result(date(2026, 11, 1))
    assert res["months"][-1]["month"] == "2026-10"
    # October: the 99,999 rent, Claude 1,900 and food 1,800.
    assert res["months"][-1]["all"] == "103699.00"


def test_investing_is_not_spending():
    # Rent 40,000 + Claude 1,900 + food 1,800 + party 2,500; the SIP and the
    # stamp duty are not spending.
    assert _month("2026-08")["all"] == "46200.00"


def test_young_ledger_says_how_many_months_it_averaged_and_has_no_drift():
    rows = _rows(RENT[-2:])  # Aug and Sep 2026 only
    res = sp.spending(rows, REPORT, sp.one_time_rule(rows, REPORT))
    every = res["all"]
    # Not diluted by the ten empty months, and says it covers two.
    assert every["avg_12"] == {"amount": "40000.00", "months_counted": 2}
    assert every["avg_3"] == {"amount": "40000.00", "months_counted": 2}
    # A 2-month "12-month pace" is no basis for drift.
    assert every["lifestyle_drift_pct"] is None
    assert every["drift_basis"] == "too_few_months"
    # Two equal rents do not stand out against each other.
    assert res["without_one_time"] == res["all"]


def test_drift_needs_six_counted_months():
    six = _rows(RENT[-6:])  # Apr..Sep 2026
    res = sp.spending(six, REPORT, sp.one_time_rule(six, REPORT))
    assert res["all"]["avg_12"]["months_counted"] == 6
    assert res["all"]["drift_basis"] == "ok"
    # 3 x 40,000 / 3 against (3 x 20,000 + 3 x 40,000) / 6 = 30,000.
    assert res["all"]["lifestyle_drift_pct"] == "33.3"


def test_empty_ledger_has_no_averages():
    res = sp.spending([], REPORT, sp.one_time_rule([], REPORT))
    none = {"amount": None, "months_counted": 0}
    assert res["all"] == {
        "avg_3": none,
        "avg_6": none,
        "avg_12": none,
        "lifestyle_drift_pct": None,
        "drift_basis": "no_spending",
    }


def test_one_time_window_is_report_month_and_two_before():
    assert sp.one_time_window_start(REPORT) == date(2026, 8, 1)
    assert sp.one_time_window_start(date(2026, 2, 3)) == date(2025, 12, 1)
    sofa = _txn(date(2026, 7, 31), "SOFA", "Expenses:Home", CHK, "30000")
    assert "SOFA" not in {i["name"] for i in _items([*TXNS, sofa])}


def test_refund_is_never_one_time():
    refund = _txn(
        date(2026, 9, 25), "ROYAL ENFIELD REFUND", CARD, "Expenses:Bike", "60000"
    )
    assert "ROYAL ENFIELD REFUND" not in {i["name"] for i in _items([*TXNS, refund])}


def test_card_bill_payments_are_not_spending():
    clearing = "Assets:Transfer:CreditCard"
    txns = [
        # A 10,000 card spend and its bill paid straight from the bank.
        _txn(date(2026, 9, 5), "SHOP A", "Expenses:Shopping", CARD, "10000"),
        _txn(date(2026, 9, 25), "CARD BILL", CARD, CHK, "10000"),
        # A 5,000 card spend and its bill paid via the clearing account.
        _txn(date(2026, 8, 5), "SHOP B", "Expenses:Shopping", CARD, "5000"),
        _txn(date(2026, 8, 25), "CARD BILL VIA CLEARING", clearing, CHK, "5000"),
        _txn(date(2026, 8, 27), "PAYMENT RECEIVED", CARD, clearing, "5000"),
    ]
    rows = _rows(txns)
    res = sp.spending(rows, REPORT, sp.one_time_rule(rows, REPORT))
    by_month = {m["month"]: m["all"] for m in res["months"]}
    assert (by_month["2026-08"], by_month["2026-09"]) == ("5000.00", "10000.00")
    last_12 = cash_flow_summary(rows, REPORT)[2]
    assert last_12["spent"] == "15000.00"
