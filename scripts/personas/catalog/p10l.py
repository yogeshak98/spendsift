"""₹10L: three years into a first job in Bengaluru. One bank, one card, SIPs on
Groww; rents a co-living room, rides a scooter, one weekend trip to Coorg."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from scripts.personas.catalog._common import fuel_fills, sms_alert_charges
from scripts.personas.instruments import mf_buy, sip, trading_day
from scripts.personas.merchants import (
    MERCHANTS,
    narrations,
    pattern,
    recurring,
    trip_spend,
)
from scripts.personas.model import (
    Account,
    CardBillPlan,
    CashbackPlan,
    Income,
    Persona,
    RefundPlan,
    Trip,
)

D = Decimal
CITY = "Bengaluru"
_card = "BANGALORE"
_fuel, _fuel_fees = fuel_fills("icici", "HPCL", "500", 9, _card)

PERSONA = Persona(
    slug="p10l",
    display_name="Ritika Sundaram",
    city=CITY,
    employer="Lumenfield Software Pvt Ltd",
    net_worth_target=D("1000000"),
    seed=101001,
    accounts=[
        Account("hdfc", "bank", "HDFC", "Savings", "50100000012651", D("579412.36")),
        Account(
            "icici", "card", "ICICI", "Amazon Pay", "4000001200007315", D("-14286.40")
        ),
        Account("groww", "broker_mf", "Groww", "Mutual Funds", "G100002604", D("0")),
    ],
    income=[
        Income(
            "hdfc",
            "salary",
            D("108450.00"),
            "NEFT CR-N{ref}-LUMENFIELD SOFTWARE PVT LTD-SALARY {month}",
            "Income:Salary",
        ),
        Income(
            "hdfc",
            "interest",
            D("2184.00"),
            "CREDIT INTEREST CAPITALISED {month}",
            "Income:Interest",
        ),
    ],
    recurring=[
        recurring("hdfc", "STANZA LIVING", "16500", 3, "upi", _card),
        recurring("hdfc", "AIRTEL", "349", 20, "upi", _card),
        recurring("icici", "SPOTIFY", "119", 6, "card", _card, ("subscription",)),
        recurring("icici", "NETFLIX", "199", 14, "card", _card, ("subscription",)),
        _fuel,
    ],
    spends=[
        pattern("icici", "SWIGGY", 7, "card", _card, ("149", "640")),
        pattern("icici", "ZOMATO", 3, "card", _card, ("180", "720")),
        pattern("icici", "UBER", 4, "card", _card, ("92", "460")),
        pattern("icici", "AMAZON", 2, "card", _card, ("249", "3200")),
        pattern("icici", "BLINKIT", 3, "card", _card, ("120", "860")),
        pattern("icici", "BOOKMYSHOW", 1, "card", _card, ("260", "980")),
        pattern("icici", "APOLLO PHARMACY", 1, "card", _card, ("118", "940")),
        pattern("hdfc", "RAPIDO", 8, "upi", _card),
        pattern("hdfc", "ZEPTO", 3, "upi", _card, ("99", "640")),
        pattern("hdfc", "CHAAYOS", 4, "upi", _card),
        pattern("hdfc", "BESCOM", 1, "upi", _card, ("410", "980")),
        pattern("hdfc", "DMART", 1, "upi", _card, ("640", "2900")),
    ],
    card_bills=[CardBillPlan("icici", "hdfc", days_early=2)],
    refunds=[
        RefundPlan(
            "icici",
            "MYNTRA",
            MERCHANTS["MYNTRA"].category,
            D("2149.00"),
            date(2025, 11, 18),
            date(2025, 11, 27),
            narrations("MYNTRA", "card", _card)[0],
            "REFUND MYNTRA DESIGNS PVT LTD BANGALORE",
        ),
    ],
    trips=[
        Trip(
            "trip-coorg-2025",
            date(2025, 12, 12),
            date(2025, 12, 14),
            (
                trip_spend("hdfc", "REDBUS", 1, "upi", _card, ("1180", "1890")),
                trip_spend("icici", "MAKEMYTRIP", 1, "card", _card, ("3200", "6800")),
                trip_spend("icici", "CAFE COFFEE DAY", 2, "card", _card),
            ),
        ),
    ],
    fees=[
        *_fuel_fees,
        *sms_alert_charges("hdfc", date(2025, 10, 8), date(2026, 1, 8)),
    ],
    cashbacks=[
        CashbackPlan(
            "hdfc",
            date(2025, 11, 6),
            D("47.00"),
            "UPI-GOOGLE PAY-goog-payment@okaxis-{ref}-REWARD",
        ),
        CashbackPlan(
            "hdfc",
            date(2026, 2, 11),
            D("21.00"),
            "UPI-GOOGLE PAY-goog-payment@okaxis-{ref}-REWARD",
        ),
    ],
    mf_orders=[
        *sip("groww", "120716", "5000", 5, "hdfc"),
        *sip("groww", "122639", "5000", 10, "hdfc"),
        *sip("groww", "120503", "2000", 15, "hdfc"),
        mf_buy("groww", "122639", "10000", trading_day(2026, 2, 2), "hdfc"),
    ],
)
