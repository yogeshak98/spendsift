"""₹30L: a data engineer in Pune. Two banks (salary and a high-interest
savings account), two cards, Groww for funds and stocks; rents a flat, drives
to work, and takes two trips: Goa over New Year, Jaipur in February."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from scripts.personas.catalog._common import fuel_fills, sms_alert_charges
from scripts.personas.instruments import MONTHS, sip, stock, trading_day
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
    TransferPlan,
    Trip,
)

D = Decimal
CITY = "Pune"
_card = "PUNE"
_fuel, _fuel_fees = fuel_fills("icici", "INDIAN OIL", "2000", 16, _card)

PERSONA = Persona(
    slug="p30l",
    display_name="Siddharth Rane",
    city=CITY,
    employer="Corvane Analytics Pvt Ltd",
    net_worth_target=D("3000000"),
    seed=303003,
    accounts=[
        Account("hdfc", "bank", "HDFC", "Savings", "50100000027462", D("861304.55")),
        Account("idfc", "bank", "IDFC", "Savings", "10000000035519", D("1392500.00")),
        Account(
            "icici", "card", "ICICI", "Amazon Pay", "4000001400006807", D("-21734.18")
        ),
        Account("axis", "card", "Axis", "Flipkart", "4000002100009054", D("-9418.62")),
        Account("groww", "broker_mf", "Groww", "Mutual Funds", "G100003371", D("0")),
        Account("growwst", "broker_stocks", "Groww", "Stocks", "G100004728", D("0")),
    ],
    income=[
        Income(
            "hdfc",
            "salary",
            D("204300.00"),
            "NEFT CR-N{ref}-CORVANE ANALYTICS PVT LTD-SALARY {month}",
            "Income:Salary",
        ),
        Income(
            "idfc",
            "interest",
            D("21870.00"),
            "INTEREST CREDIT FOR {month}",
            "Income:Interest",
        ),
    ],
    recurring=[
        recurring("hdfc", "NOBROKER", "32000", 4, "upi", _card),
        recurring("hdfc", "ACT FIBERNET", "1178.82", 12, "upi", _card),
        recurring("icici", "NETFLIX", "649", 8, "card", _card, ("subscription",)),
        recurring("axis", "SPOTIFY", "179", 21, "card", _card, ("subscription",)),
        _fuel,
    ],
    spends=[
        pattern("icici", "SWIGGY", 9, "card", _card),
        pattern("icici", "ZOMATO", 4, "card", _card),
        pattern("icici", "AMAZON", 3, "card", _card, ("249", "5400")),
        pattern("icici", "UBER", 6, "card", _card),
        pattern("icici", "BOOKMYSHOW", 1, "card", _card),
        pattern("icici", "STARBUCKS", 2, "card", _card),
        pattern("axis", "FLIPKART", 2, "card", _card, ("299", "5200")),
        pattern("axis", "MYNTRA", 1, "card", _card),
        pattern("axis", "DMART", 2, "card", _card),
        pattern("axis", "NYKAA", 1, "card", _card),
        pattern("hdfc", "ZEPTO", 5, "upi", _card),
        pattern("hdfc", "BLINKIT", 3, "upi", _card),
        pattern("hdfc", "RAPIDO", 4, "upi", _card),
        pattern("hdfc", "MSEDCL", 1, "upi", _card, ("780", "2460")),
        pattern("hdfc", "APOLLO PHARMACY", 1, "upi", _card),
        pattern("hdfc", "JIO", 1, "upi", _card, ("299", "449")),
    ],
    transfers=[
        *(
            TransferPlan(
                "hdfc",
                "idfc",
                D("45000.00"),
                date(y, m, 2),
                lag_days=m % 2,
                rail="IMPS" if m % 2 else "UPI",
            )
            for y, m in MONTHS
        ),
        TransferPlan("idfc", "hdfc", D("20000.00"), date(2026, 1, 9), rail="NEFT"),
        TransferPlan("idfc", "growwst", D("140000.00"), date(2025, 11, 6), rail="UPI"),
    ],
    card_bills=[
        CardBillPlan("icici", "hdfc", days_early=1),
        CardBillPlan("axis", "idfc", days_early=3),
    ],
    refunds=[
        RefundPlan(
            "icici",
            "AMAZON",
            MERCHANTS["AMAZON"].category,
            D("3499.00"),
            date(2025, 10, 24),
            date(2025, 11, 3),
            narrations("AMAZON", "card", _card)[0],
            "REFUND AMAZON PAY INDIA PRIVATE BANGALORE",
        ),
        RefundPlan(
            "axis",
            "FLIPKART",
            MERCHANTS["FLIPKART"].category,
            D("1849.00"),
            date(2026, 1, 9),
            date(2026, 1, 19),
            narrations("FLIPKART", "card", _card)[0],
            "REFUND FLIPKART INTERNET PVT LTD BANGALORE",
        ),
    ],
    trips=[
        Trip(
            "trip-goa-2025",
            date(2025, 12, 26),
            date(2025, 12, 29),
            (
                trip_spend("icici", "INDIGO", 1, "card", _card, ("7800", "12400")),
                trip_spend("icici", "MAKEMYTRIP", 1, "card", _card, ("9000", "16000")),
                trip_spend("icici", "ZOMATO", 3, "card", "GOA", ("420", "1850")),
            ),
        ),
        Trip(
            "trip-jaipur-2026",
            date(2026, 2, 13),
            date(2026, 2, 15),
            (
                trip_spend("hdfc", "IRCTC", 1, "upi", _card, ("1800", "3200")),
                trip_spend("axis", "OYO", 1, "card", _card, ("2600", "4800")),
                trip_spend("axis", "ZOMATO", 2, "card", "JAIPUR", ("380", "1400")),
            ),
        ),
    ],
    fees=[
        *_fuel_fees,
        *sms_alert_charges("hdfc", date(2025, 10, 8), date(2026, 1, 8)),
    ],
    cashbacks=[
        CashbackPlan("axis", on, D(amount), "CASHBACK EARNED ON STATEMENT")
        for on, amount in (
            (date(2025, 11, 18), "212.45"),
            (date(2025, 12, 17), "318.60"),
            (date(2026, 1, 19), "146.25"),
            (date(2026, 2, 17), "401.90"),
        )
    ],
    mf_orders=[
        *sip("groww", "120716", "10000", 5, "hdfc"),
        *sip("groww", "122639", "15000", 10, "hdfc"),
        *sip("groww", "118989", "7500", 15, "hdfc"),
        *sip("groww", "120503", "5000", 20, "hdfc", MONTHS[2:]),
    ],
    stock_orders=[
        stock("growwst", "INFY", "buy", 20, trading_day(2025, 11, 10)),
        stock("growwst", "ITC", "buy", 120, trading_day(2025, 12, 8)),
        stock("growwst", "HDFCBANK", "buy", 30, trading_day(2026, 1, 12)),
        stock("growwst", "ITC", "sell", 40, trading_day(2026, 3, 9)),
    ],
)
