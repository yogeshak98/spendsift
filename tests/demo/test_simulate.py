"""The simulator's truth obeys the money rules every statement writer and the
checker rely on, on a tiny invented persona (2 banks, 1 card, 1 MF broker)."""

from __future__ import annotations

import dataclasses
import math
import random
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

import pytest

from backend.app.models.storage.merge_booking import MergeBookingConfig
from backend.app.service.ingest.pipeline.charges import (
    EXACT_NAV_NOT_ROUND,
    exact_nav_purchase,
)
from scripts.personas.model import (
    PERIOD_END,
    PERIOD_START,
    Account,
    CardBillPlan,
    FeePlan,
    Income,
    MfOrderPlan,
    Persona,
    Recurring,
    RefundPlan,
    SpendPattern,
    TransferPlan,
    Trip,
    TripSpend,
)
from scripts.personas.simulate import DUST_UNITS, mf_purchase, simulate

D = Decimal
SCHEME = "120716"  # a public AMFI scheme code; the NAV below is a stub


def stub_nav(code: str, on: date) -> Decimal:
    """A deterministic, non-round NAV that moves day to day."""
    assert code == SCHEME
    return (D("187.4521") + D("0.0137") * (on - PERIOD_START).days).quantize(
        D("0.0001")
    )


def no_prices(symbol: str, on: date) -> Decimal:
    raise AssertionError("the tiny persona holds no stocks")


TINY = Persona(
    slug="tiny",
    display_name="Fictive Tester",
    city="Bengaluru",
    employer="Fictive Systems Pvt Ltd",
    net_worth_target=D("500000"),
    seed=4242,
    accounts=[
        Account("hdfc", "bank", "HDFC", "Savings", "50100000004417", D("84250.35")),
        Account("idfc", "bank", "IDFC", "Savings", "10000000002931", D("152000.00")),
        Account(
            "icici", "card", "ICICI", "Amazon Pay", "4000000000002829", D("-12480.50")
        ),
        Account("groww", "broker_mf", "Groww", "Mutual Funds", "FICTIVE01", D("0")),
    ],
    income=[
        Income(
            "hdfc",
            "salary",
            D("98400.00"),
            "NEFT CR-FICT0000001-FICTIVE SYSTEMS PVT LTD-SALARY {month}",
            "Income:Salary",
        ),
        Income(
            "idfc",
            "interest",
            D("1187.00"),
            "INTEREST CREDIT {month}",
            "Income:Interest",
        ),
    ],
    recurring=[
        Recurring(
            "icici",
            "NETFLIX",
            D("649.00"),
            8,
            "NETFLIX.COM MUMBAI",
            "Expenses:Entertainment",
            ("subscription",),
        ),
    ],
    spends=[
        SpendPattern(
            "icici",
            "SWIGGY",
            "Expenses:Food",
            6,
            (D("180"), D("720")),
            ("SWIGGY BANGALORE", "SWIGGY INSTAMART BANGALORE"),
        ),
        SpendPattern(
            "hdfc",
            "BIGBASKET",
            "Expenses:Groceries",
            3,
            (D("450"), D("2600")),
            ("UPI-BIGBASKET-bigbasket@hdfcbank-{ref}-UPI",),
        ),
    ],
    transfers=[
        TransferPlan("hdfc", "idfc", D("25000.00"), date(2025, 10, 10), lag_days=1),
        TransferPlan("hdfc", "idfc", D("15000.00"), date(2025, 12, 20), rail="UPI"),
        TransferPlan(
            "idfc", "hdfc", D("8000.00"), date(2026, 2, 13), lag_days=1, rail="NEFT"
        ),
    ],
    card_bills=[CardBillPlan("icici", "hdfc", days_early=1)],
    refunds=[
        RefundPlan(
            "icici",
            "AMAZON",
            "Expenses:Shopping",
            D("1899.00"),
            date(2025, 11, 3),
            date(2025, 11, 12),
            "AMAZON PAY INDIA BANGALORE",
            "REFUND AMAZON PAY INDIA BANGALORE",
        ),
    ],
    mf_orders=[
        MfOrderPlan(
            "groww",
            SCHEME,
            "Fictive Flexi Cap Fund Direct Growth",
            "buy",
            date(2025, 10, 7) + timedelta(days=31 * i),
            amount=D("5000"),
            funded_from="hdfc",
        )
        for i in range(6)
    ],
    trips=[
        Trip(
            "trip-goa-2026",
            date(2026, 1, 23),
            date(2026, 1, 26),
            (
                TripSpend(
                    "icici",
                    "MAKEMYTRIP",
                    "Expenses:Travel",
                    1,
                    (D("6000"), D("9000")),
                    ("MAKEMYTRIP INDIA PVT LTD GURGAON",),
                ),
                TripSpend(
                    "icici",
                    "ZOMATO",
                    "Expenses:Food",
                    3,
                    (D("400"), D("1800")),
                    ("ZOMATO GOA",),
                ),
            ),
        ),
    ],
    fees=[
        FeePlan(
            "icici",
            date(2025, 12, 2),
            D("750.00"),
            "LATE PAYMENT FEE",
            "Expenses:Interest",
            gst_narration="IGST @18% ON LATE PAYMENT FEE",
        ),
    ],
)


@pytest.fixture(scope="module")
def truth():
    return simulate(TINY, nav=stub_nav, prices=no_prices)


def by_kind(truth, kind):
    return [e for e in truth.events if e.kind == kind]


def test_transfer_creates_two_events_sharing_pair_id_and_ref(truth):
    outs = by_kind(truth, "transfer_out")
    ins = {e.pair_id: e for e in by_kind(truth, "transfer_in")}
    assert len(outs) == len(TINY.transfers) == len(ins)
    for out in outs:
        leg = ins[out.pair_id]
        assert out.pair_id is not None and out.ref is not None
        assert leg.ref == out.ref
        assert leg.amount == -out.amount and out.amount < 0
        assert (leg.date - out.date).days in (0, 1)
        assert out.counter_account_key == leg.account_key
        assert leg.counter_account_key == out.account_key


def test_card_bill_pays_the_previous_statement_balance(truth):
    card = TINY.account("icici")
    card_events = [e for e in truth.events if e.account_key == "icici"]
    payments = by_kind(truth, "card_bill_payment")
    received = {e.pair_id: e for e in by_kind(truth, "card_payment_received")}
    # The opening balance is the September statement, paid in October.
    statements = [(date(2025, 9, 15), card.opening_balance)]
    for month in (10, 11, 12, 1, 2, 3):
        stmt = date(2025 if month >= 10 else 2026, month, 15)
        total = card.opening_balance + sum(
            (e.amount for e in card_events if e.date <= stmt), D(0)
        )
        statements.append((stmt, total))
    # The March statement is due in April, outside the period: 6 payments.
    assert len(payments) == 6
    for (stmt, total), pay in zip(statements, payments, strict=False):
        due = date(stmt.year + stmt.month // 12, stmt.month % 12 + 1, 5)
        assert pay.date == due - timedelta(days=1)
        assert pay.account_key == "hdfc" and pay.counter_account_key == "icici"
        assert pay.amount == total < 0
        leg = received[pay.pair_id]
        assert leg.account_key == "icici" and leg.amount == -total
        assert leg.date == pay.date and leg.ref == pay.ref
    assert [c.balance for c in truth.cycles] == [t for _, t in statements[1:]]
    assert all(c.statement_date.day == 15 and c.due_date.day == 5 for c in truth.cycles)


def test_refund_points_at_its_purchase(truth):
    refunds = by_kind(truth, "refund")
    assert len(refunds) == 1
    refund = refunds[0]
    [purchase] = [e for e in by_kind(truth, "spend") if e.pair_id == refund.pair_id]
    assert refund.pair_id is not None
    assert purchase.amount == -refund.amount and refund.amount > 0
    assert purchase.merchant == refund.merchant == "AMAZON"
    assert refund.expected_category == purchase.expected_category
    assert purchase.date < refund.date


def test_mf_buy_amount_is_round_and_units_from_nav(truth):
    buys = by_kind(truth, "mf_buy")
    assert len(buys) == 6
    for buy in buys:
        cash = -buy.amount
        assert cash > 0 and cash % 100 == 0
        nav = stub_nav(SCHEME, buy.date)
        stamp = (cash * D("0.00005")).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        assert buy.price == nav and buy.charges == stamp
        assert buy.units == ((cash - stamp) / nav).quantize(
            D("0.001"), rounding=ROUND_HALF_UP
        )
        assert buy.commodity == SCHEME
        funding = [
            e
            for e in by_kind(truth, "platform_funding")
            if e.date == buy.date and e.counter_account_key == "groww"
        ]
        assert [e.amount for e in funding] == [-cash]
    assert truth.holdings["groww"][SCHEME] == sum((b.units for b in buys), D(0))


@pytest.mark.parametrize(
    ("cash", "nav"),
    [
        (D("5000"), D("187.4521")),
        (D("100"), D("150.0000")),  # half-up overshoots the cash: step down
        (D("100"), D("3456.7891")),
        (D("1000"), D("9.8765")),
        (D("15000"), D("52.3456")),
        (D("250000"), D("1234.5678")),
        (D("1980000"), D("88.0001")),  # the largest order the app can snap
    ],
)
def test_mf_purchase_agrees_with_the_apps_exact_nav_booking(cash, nav):
    _assert_app_books_the_same(cash, nav)


def _sweep_cases(n: int = 100, seed: int = 20251101) -> list[tuple[Decimal, Decimal]]:
    """Seeded random orders across the range the personas may use: cash in
    whole hundreds from 100 to 19,80,000, NAV from 10 to 2000 at 4 dp.

    Cash is drawn log-uniformly, so small orders, where a high NAV makes the
    half-up units overshoot the cash and the step-down applies, are about as
    common as large ones."""
    rng = random.Random(seed)
    cases = []
    for _ in range(n):
        hundreds = round(10 ** rng.uniform(0, math.log10(19800)))
        nav = D(rng.randrange(10 * 10**4, 2000 * 10**4 + 1)) / 10**4
        cases.append((D(hundreds * 100), nav))
    return cases


def test_the_sweep_reaches_the_step_down():
    stepped = [
        (cash, nav)
        for cash, nav in _sweep_cases()
        if ((cash - mf_purchase(cash, nav)[0]) / nav).quantize(
            D("0.001"), rounding=ROUND_HALF_UP
        )
        * nav
        > cash
    ]
    assert len(stepped) >= 5


@pytest.mark.parametrize(("cash", "nav"), _sweep_cases())
def test_mf_purchase_matches_the_app_on_a_random_sweep(cash, nav):
    _assert_app_books_the_same(cash, nav)


def _assert_app_books_the_same(cash: Decimal, nav: Decimal) -> None:
    stamp, units = mf_purchase(cash, nav)
    booked, reason = _book_like_the_app(cash, stamp, units, nav)
    assert reason is None and booked is not None
    assert booked.cash == cash and booked.units == units
    # The app books the stamp in full unless the units' cost overshoots the
    # cash after it, when the overshoot comes out of the stamp leg.
    remainder = cash - stamp - units * nav
    stamp_leg = stamp if remainder >= 0 else stamp + remainder
    expected = {STAMP_ACCOUNT: stamp_leg} if stamp_leg > 0 else {}
    assert {k: v for k, v in booked.legs.items() if k == STAMP_ACCOUNT} == expected


STAMP_ACCOUNT = MergeBookingConfig().charge_stamp_duty_account


def _book_like_the_app(cash, stamp, units, nav):
    """Book what the broker's export prints (Amount net of stamp, cut to the
    rupee; units to 2 dp) through the app's exact-NAV purchase path."""
    export_amount = (cash - stamp).quantize(D("1"), rounding=ROUND_DOWN)
    export_units = units.quantize(D("0.01"), rounding=ROUND_HALF_UP)
    return exact_nav_purchase(export_amount, export_units, nav, MergeBookingConfig())


class AppCannotSnap(Exception):
    """The app refused the order as not round: the known snap-limit gap."""


@pytest.mark.xfail(
    strict=True,
    raises=AppCannotSnap,
    reason="app snaps an MF order only while stamp duty is under 99.01 rupees: "
    "from 19,80,100 up the truncated Amount falls below the next 100 boundary",
)
def test_mf_purchase_above_the_snap_limit_also_agrees_with_the_app():
    cash, nav = D("1980100"), D("88.0001")
    stamp, units = mf_purchase(cash, nav)
    booked, reason = _book_like_the_app(cash, stamp, units, nav)
    # Only the not-round refusal is the expected failure; any other reason
    # (or a booking that disagrees) is a real failure, not this gap.
    if reason == EXACT_NAV_NOT_ROUND:
        raise AppCannotSnap(reason)
    assert reason is None and booked is not None and booked.cash == cash


def test_closing_equals_opening_plus_events(truth):
    for acct in TINY.accounts:
        moved = sum((e.amount for e in truth.events if e.account_key == acct.key), D(0))
        assert truth.closing[acct.key] == acct.opening_balance + moved, acct.key
    cost = sum((e.units * e.price for e in by_kind(truth, "mf_buy")), D(0))
    expected = sum(truth.closing.values(), D(0)) + cost
    assert truth.net_worth == expected.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def test_expense_amounts_positive(truth):
    expenses = [e for e in truth.events if e.kind in ("spend", "fee", "gst_on_fee")]
    assert {e.kind for e in expenses} == {"spend", "fee", "gst_on_fee"}
    for e in expenses:
        assert e.amount < 0 and e.expense_amount is not None and e.expense_amount > 0
        assert e.expected_category is not None
        assert e.expected_category.startswith("Expenses:")
    [fee] = by_kind(truth, "fee")
    [gst] = by_kind(truth, "gst_on_fee")
    assert gst.amount == (fee.amount * D("0.18")).quantize(
        D("0.01"), rounding=ROUND_HALF_UP
    )
    assert gst.expected_category == fee.expected_category and gst.date == fee.date


def test_salary_lands_on_the_last_working_day_of_each_month(truth):
    days = [e.date for e in by_kind(truth, "salary")]
    assert days == [
        date(2025, 10, 31),
        date(2025, 11, 28),
        date(2025, 12, 31),
        date(2026, 1, 30),
        date(2026, 2, 27),
        date(2026, 3, 31),
    ]
    interest = [e.date for e in by_kind(truth, "interest")]
    assert interest == [date(2025, 12, 31), date(2026, 3, 31)]


def test_trip_spends_carry_the_trip_tag_and_stay_in_its_window(truth):
    trip = TINY.trips[0]
    tagged = [e for e in truth.events if trip.tag in e.expected_tags]
    assert len(tagged) == 4
    assert all(trip.start <= e.date <= trip.end for e in tagged)
    netflix = [e for e in truth.events if e.merchant == "NETFLIX"]
    assert len(netflix) == 6 and all(
        e.expected_tags == ("subscription",) for e in netflix
    )


def test_every_event_is_inside_the_period_and_in_date_order(truth):
    dates = [e.date for e in truth.events]
    assert dates == sorted(dates)
    assert PERIOD_START <= dates[0] and dates[-1] <= PERIOD_END


def test_same_seed_same_truth(truth):
    assert simulate(TINY, nav=stub_nav, prices=no_prices) == truth


def test_different_seed_different_truth(truth):
    other = simulate(
        dataclasses.replace(TINY, seed=TINY.seed + 1), nav=stub_nav, prices=no_prices
    )
    assert other.events != truth.events
    assert other.closing != truth.closing


def test_mf_sell_consumes_lots_first_in_first_out_and_dust_redeems_all():
    persona = Persona(
        slug="seller",
        display_name="Fictive Seller",
        city="Pune",
        employer="Fictive Labs",
        net_worth_target=D("100000"),
        seed=7,
        accounts=[
            Account("bank", "bank", "HDFC", "Savings", "50100000009921", D("100000")),
            Account("groww", "broker_mf", "Groww", "Mutual Funds", "FICTIVE02", D("0")),
        ],
        mf_orders=[
            MfOrderPlan(
                "groww",
                SCHEME,
                "Fund",
                "buy",
                date(2025, 10, 6),
                amount=D("10000"),
                funded_from="bank",
            ),
            MfOrderPlan(
                "groww",
                SCHEME,
                "Fund",
                "buy",
                date(2025, 11, 6),
                amount=D("10000"),
                funded_from="bank",
            ),
        ],
    )
    base = simulate(persona, nav=stub_nav, prices=no_prices)
    first, second = by_kind(base, "mf_buy")
    held = first.units + second.units

    sell_day = date(2026, 1, 9)
    partial = dataclasses.replace(
        persona,
        mf_orders=[
            *persona.mf_orders,
            MfOrderPlan(
                "groww",
                SCHEME,
                "Fund",
                "sell",
                sell_day,
                units=first.units + D("1.000"),
            ),
        ],
    )
    truth = simulate(partial, nav=stub_nav, prices=no_prices)
    [sell] = by_kind(truth, "mf_sell")
    sell_nav = stub_nav(SCHEME, sell_day)
    assert sell.amount == ((first.units + 1) * sell_nav).quantize(
        D("0.01"), rounding=ROUND_HALF_UP
    )
    assert sell.price == sell_nav and sell.units == -(first.units + 1)
    left = second.units - 1
    assert truth.holdings["groww"][SCHEME] == left
    cost_left = left * second.price  # FIFO: the first lot went first
    expected = sum(truth.closing.values(), D(0)) + cost_left
    assert truth.net_worth == expected.quantize(D("0.01"), rounding=ROUND_HALF_UP)

    dust = dataclasses.replace(
        persona,
        mf_orders=[
            *persona.mf_orders,
            MfOrderPlan(
                "groww", SCHEME, "Fund", "sell", sell_day, units=held - D("0.004")
            ),
        ],
    )
    truth = simulate(dust, nav=stub_nav, prices=no_prices)
    [sell] = by_kind(truth, "mf_sell")
    assert sell.units == -(held - D("0.004"))
    assert truth.holdings["groww"].get(SCHEME, D(0)) == 0
    assert truth.net_worth == sum(truth.closing.values(), D(0)).quantize(D("0.01"))


def test_overdrawn_bank_is_rejected():
    broke = dataclasses.replace(
        TINY,
        transfers=[TransferPlan("hdfc", "idfc", D("900000"), date(2025, 10, 2))],
    )
    with pytest.raises(ValueError, match="hdfc"):
        simulate(broke, nav=stub_nav, prices=no_prices)


def test_mf_buy_must_be_a_round_hundred():
    odd = dataclasses.replace(
        TINY,
        mf_orders=[
            MfOrderPlan(
                "groww",
                SCHEME,
                "Fund",
                "buy",
                date(2025, 10, 7),
                amount=D("5050"),
                funded_from="hdfc",
            ),
        ],
    )
    with pytest.raises(ValueError, match="multiple of 100"):
        simulate(odd, nav=stub_nav, prices=no_prices)


def test_mf_sell_leaving_units_redeems_all_but_that_many():
    """`leave_units` sells whatever is held less a remainder, so a persona can
    state "redeem all but 0.004 units" whatever NAVs the lookup returns."""
    buys = [
        MfOrderPlan(
            "groww",
            SCHEME,
            "Fund",
            "buy",
            date(2025, 10, 6) + timedelta(days=30 * i),
            amount=D("10000"),
            funded_from="hdfc",
        )
        for i in range(2)
    ]

    def run(leave: str):
        persona = dataclasses.replace(
            TINY,
            mf_orders=[
                *buys,
                MfOrderPlan(
                    "groww",
                    SCHEME,
                    "Fund",
                    "sell",
                    date(2026, 1, 9),
                    leave_units=D(leave),
                ),
            ],
        )
        return simulate(persona, nav=stub_nav, prices=no_prices)

    dust = run("0.004")
    held = sum((b.units for b in by_kind(dust, "mf_buy")), D(0))
    [sell] = by_kind(dust, "mf_sell")
    assert sell.units == -(held - D("0.004"))
    assert D("0.004") <= DUST_UNITS
    assert SCHEME not in dust.holdings["groww"]

    kept = run("1.500")
    [sell] = by_kind(kept, "mf_sell")
    assert sell.units == -(held - D("1.500"))
    assert kept.holdings["groww"][SCHEME] == D("1.500")

    both = dataclasses.replace(
        TINY,
        mf_orders=[
            *buys,
            MfOrderPlan(
                "groww",
                SCHEME,
                "Fund",
                "sell",
                date(2026, 1, 9),
                units=D("1"),
                leave_units=D("1"),
            ),
        ],
    )
    with pytest.raises(ValueError, match="units or leave_units"):
        simulate(both, nav=stub_nav, prices=no_prices)


@pytest.mark.parametrize(
    ("sold_on", "paid_on"),
    [
        (date(2026, 1, 8), date(2026, 1, 12)),  # Thu: over the weekend to Mon
        (date(2025, 12, 24), date(2025, 12, 29)),  # Wed: Christmas is skipped
    ],
)
def test_mf_redemption_is_paid_out_to_the_bank_two_trading_days_later(sold_on, paid_on):
    buy = MfOrderPlan(
        "groww",
        SCHEME,
        "Fund",
        "buy",
        date(2025, 10, 6),
        amount=D("10000"),
        funded_from="hdfc",
    )
    sell = MfOrderPlan(
        "groww",
        SCHEME,
        "Fund",
        "sell",
        sold_on,
        units=D("10.000"),
        payout_to="idfc",
    )
    truth = simulate(
        dataclasses.replace(TINY, mf_orders=[buy, sell]),
        nav=stub_nav,
        prices=no_prices,
    )
    [redeemed] = by_kind(truth, "mf_sell")
    out, inn = by_kind(truth, "mf_redemption_payout")
    assert (out.account_key, inn.account_key) == ("groww", "idfc")
    assert out.date == inn.date == paid_on
    assert inn.amount == redeemed.amount == -out.amount > 0
    assert out.pair_id == inn.pair_id == redeemed.pair_id is not None
    assert out.ref == inn.ref and "REDEMPTION" in inn.narration
    assert truth.closing["groww"] == 0


def _sell_plan(on: date, **kw) -> MfOrderPlan:
    return MfOrderPlan("groww", SCHEME, "Fund", "sell", on, **kw)


def _buy_plan(**kw) -> MfOrderPlan:
    return MfOrderPlan(
        "groww",
        SCHEME,
        "Fund",
        "buy",
        date(2025, 10, 6),
        amount=D("10000"),
        funded_from="hdfc",
        **kw,
    )


def test_a_payout_falling_after_the_period_is_rejected():
    # Mon 30 Mar 2026 pays out on Wed 1 Apr, after PERIOD_END.
    late = _sell_plan(date(2026, 3, 30), units=D("10.000"), payout_to="idfc")
    persona = dataclasses.replace(TINY, mf_orders=[_buy_plan(), late])
    with pytest.raises(ValueError, match="payout of the .* after the period"):
        simulate(persona, nav=stub_nav, prices=no_prices)


def test_payout_to_on_a_buy_is_rejected():
    persona = dataclasses.replace(TINY, mf_orders=[_buy_plan(payout_to="idfc")])
    with pytest.raises(ValueError, match="payout_to is only for a sell"):
        simulate(persona, nav=stub_nav, prices=no_prices)
