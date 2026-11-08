"""₹50L: a research scientist in Hyderabad. Three banks: salary, a
high-interest savings account, and a home-town savings account she sends money
home from. Two cards, Groww for funds and stocks, one trip to Kerala.

Two equal transfers between the same two banks on adjacent days, the first
arriving a day late: both credits land on the same day for the same amount,
and only the reference tells them apart."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from scripts.personas.catalog._common import fuel_fills, sms_alert_charges
from scripts.personas.instruments import MONTHS, mf_buy, sip, stock, trading_day
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
CITY = "Hyderabad"
_card = "HYDERABAD"
_fuel, _fuel_fees = fuel_fills("axis", "INDIAN OIL", "3000", 11, _card)

PERSONA = Persona(
    slug="p50l",
    display_name="Meghna Rathore",
    city=CITY,
    employer="Seravel Biotech Pvt Ltd",
    net_worth_target=D("5000000"),
    seed=505005,
    accounts=[
        Account("hdfc", "bank", "HDFC", "Savings", "50100000041468", D("1192418.64")),
        Account("idfc", "bank", "IDFC", "Savings", "10000000052937", D("2390000.00")),
        Account("sbi", "bank", "SBI", "Savings", "30000000063084", D("348214.90")),
        Account(
            "icici", "card", "ICICI", "Sapphiro", "4000007600004659", D("-38412.77")
        ),
        Account("axis", "card", "Axis", "Atlas", "4000007300000518", D("-17260.05")),
        Account("groww", "broker_mf", "Groww", "Mutual Funds", "G100005863", D("0")),
        Account("growwst", "broker_stocks", "Groww", "Stocks", "G100006290", D("0")),
    ],
    income=[
        Income(
            "hdfc",
            "salary",
            D("286750.00"),
            "NEFT CR-N{ref}-SERAVEL BIOTECH PVT LTD-SALARY {month}",
            "Income:Salary",
        ),
        Income(
            "idfc",
            "interest",
            D("39810.00"),
            "INTEREST CREDIT FOR {month}",
            "Income:Interest",
        ),
        Income(
            "sbi",
            "interest",
            D("2468.00"),
            "CREDIT INTEREST {month}",
            "Income:Interest",
        ),
    ],
    recurring=[
        recurring("hdfc", "NOBROKER", "48000", 5, "upi", _card),
        recurring("hdfc", "AIRTEL", "1178.82", 18, "upi", _card),
        recurring("axis", "NETFLIX", "649", 9, "card", _card, ("subscription",)),
        recurring("icici", "SPOTIFY", "119", 23, "card", _card, ("subscription",)),
        _fuel,
    ],
    spends=[
        pattern("icici", "SWIGGY", 10, "card", _card),
        pattern("icici", "ZOMATO", 5, "card", _card),
        pattern("icici", "AMAZON", 3, "card", _card),
        pattern("icici", "UBER", 7, "card", _card),
        pattern("icici", "PVR", 1, "card", _card),
        pattern("icici", "NATURES BASKET", 2, "card", _card),
        pattern("axis", "FLIPKART", 2, "card", _card),
        pattern("axis", "MYNTRA", 1, "card", _card),
        pattern("axis", "STARBUCKS", 3, "card", _card),
        pattern("axis", "DECATHLON", 1, "card", _card, ("699", "4200")),
        pattern("hdfc", "BIGBASKET", 3, "upi", _card),
        pattern("hdfc", "BLINKIT", 4, "upi", _card),
        pattern("hdfc", "OLA", 3, "upi", _card),
        pattern("hdfc", "TGSPDCL", 1, "upi", _card),
        pattern("hdfc", "CULTFIT", 1, "upi", _card, ("1499", "2499")),
        pattern("sbi", "PHARMEASY", 1, "upi", _card),
    ],
    transfers=[
        *(
            TransferPlan("hdfc", "idfc", D("60000.00"), date(y, m, 3), rail="IMPS")
            for y, m in MONTHS
        ),
        # The collision: equal amounts, same pair, adjacent days.
        TransferPlan(
            "hdfc", "idfc", D("25000.00"), date(2025, 11, 14), lag_days=1, rail="UPI"
        ),
        TransferPlan("hdfc", "idfc", D("25000.00"), date(2025, 11, 15), rail="UPI"),
        TransferPlan("hdfc", "sbi", D("15000.00"), date(2025, 12, 5), rail="NEFT"),
        TransferPlan("hdfc", "sbi", D("15000.00"), date(2026, 3, 6), rail="NEFT"),
        TransferPlan(
            "sbi", "idfc", D("40000.00"), date(2026, 2, 9), lag_days=1, rail="IMPS"
        ),
        TransferPlan("idfc", "growwst", D("250000.00"), date(2025, 10, 8), rail="UPI"),
    ],
    card_bills=[
        CardBillPlan("icici", "hdfc", days_early=2),
        CardBillPlan("axis", "idfc", days_early=0),
    ],
    refunds=[
        RefundPlan(
            "icici",
            "AMAZON",
            MERCHANTS["AMAZON"].category,
            D("5299.00"),
            date(2025, 12, 2),
            date(2025, 12, 11),
            narrations("AMAZON", "card", _card)[1],
            "REFUND AMAZON PAY INDIA PRIVATE BANGALORE",
        ),
        RefundPlan(
            "axis",
            "MYNTRA",
            MERCHANTS["MYNTRA"].category,
            D("1674.00"),
            date(2026, 2, 20),
            date(2026, 3, 2),
            narrations("MYNTRA", "card", _card)[1],
            "REFUND WWW MYNTRA COM BANGALORE IN",
        ),
    ],
    trips=[
        Trip(
            "trip-kerala-2025",
            date(2025, 11, 21),
            date(2025, 11, 24),
            (
                trip_spend("icici", "INDIGO", 1, "card", _card, ("8400", "13900")),
                trip_spend("icici", "MAKEMYTRIP", 1, "card", _card, ("12000", "18500")),
                trip_spend("axis", "ZOMATO", 3, "card", "KOCHI", ("380", "1600")),
                trip_spend("axis", "UBER", 2, "card", "KOCHI", ("240", "880")),
            ),
        ),
    ],
    fees=[
        *_fuel_fees,
        *sms_alert_charges("hdfc", date(2025, 10, 8), date(2026, 1, 8)),
        *sms_alert_charges("sbi", date(2025, 12, 12), date(2026, 3, 12)),
    ],
    cashbacks=[
        CashbackPlan("icici", date(2025, 12, 20), D("250.00"), "CASHBACK CREDITED"),
        CashbackPlan(
            "hdfc",
            date(2026, 1, 23),
            D("63.00"),
            "UPI-GOOGLE PAY-goog-payment@okaxis-{ref}-REWARD",
        ),
    ],
    mf_orders=[
        *sip("groww", "120716", "20000", 5, "idfc"),
        *sip("groww", "122639", "20000", 10, "idfc"),
        *sip("groww", "125497", "10000", 15, "idfc"),
        *sip("groww", "118989", "10000", 20, "idfc"),
        mf_buy("groww", "118834", "150000", trading_day(2026, 1, 6), "hdfc"),
    ],
    stock_orders=[
        stock("growwst", "TCS", "buy", 15, trading_day(2025, 10, 13)),
        stock("growwst", "ICICIBANK", "buy", 40, trading_day(2025, 11, 12)),
        stock("growwst", "SBIN", "buy", 60, trading_day(2025, 12, 10)),
        stock("growwst", "RELIANCE", "buy", 30, trading_day(2026, 1, 14)),
        stock("growwst", "SBIN", "sell", 20, trading_day(2026, 3, 11)),
    ],
)
