"""₹1Cr: an engineering director in Gurugram. Four banks, three cards, Groww
for funds and stocks and Zerodha for a larger stock book. Moves money across
every pair of banks, redeems part of a mid-cap fund in March, and takes two
trips."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from scripts.personas.catalog._common import (
    bank_charge,
    fuel_fills,
    sms_alert_charges,
)
from scripts.personas.instruments import (
    MONTHS,
    mf_buy,
    mf_sell,
    sip,
    stock,
    trading_day,
)
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
CITY = "Gurugram"
_card = "GURGAON"
_fuel, _fuel_fees = fuel_fills("icici", "INDIAN OIL", "4000", 7, _card)

PERSONA = Persona(
    slug="p1cr",
    display_name="Arjun Malhan",
    city=CITY,
    employer="Quorvia Fintech Pvt Ltd",
    net_worth_target=D("10000000"),
    seed=100010,
    accounts=[
        Account("hdfc", "bank", "HDFC", "Savings", "50100000071157", D("1684205.31")),
        Account("idfc", "bank", "IDFC", "Savings", "10000000081265", D("5060000.00")),
        Account("sbi", "bank", "SBI", "Savings", "30000000091373", D("612488.06")),
        Account("kotak", "bank", "Kotak", "Savings", "1200001481", D("925340.72")),
        Account(
            "icici", "card", "ICICI", "Sapphiro", "4000001800001599", D("-46210.38")
        ),
        Account("axis", "card", "Axis", "Atlas", "4000002500002604", D("-28807.90")),
        Account(
            "hdfccc", "card", "HDFC", "Millennia", "4000007100002712", D("-19644.15")
        ),
        Account("groww", "broker_mf", "Groww", "Mutual Funds", "G100007915", D("0")),
        Account("growwst", "broker_stocks", "Groww", "Stocks", "G100008547", D("0")),
        Account("zerodha", "broker_stocks", "Zerodha", "Kite", "DM10829", D("0")),
    ],
    income=[
        Income(
            "hdfc",
            "salary",
            D("435600.00"),
            "NEFT CR-N{ref}-QUORVIA FINTECH PVT LTD-SALARY {month}",
            "Income:Salary",
        ),
        Income(
            "idfc",
            "interest",
            D("71540.00"),
            "INTEREST CREDIT FOR {month}",
            "Income:Interest",
        ),
        Income(
            "sbi",
            "interest",
            D("4318.00"),
            "CREDIT INTEREST {month}",
            "Income:Interest",
        ),
        Income(
            "kotak",
            "interest",
            D("6882.00"),
            "INT.PD:{ref}:{month}",
            "Income:Interest",
        ),
    ],
    recurring=[
        recurring("hdfc", "NOBROKER", "85000", 2, "upi", _card),
        recurring("hdfc", "AIRTEL", "1766.82", 19, "upi", _card),
        recurring("hdfccc", "NETFLIX", "649", 10, "card", _card, ("subscription",)),
        recurring("axis", "SPOTIFY", "179", 24, "card", _card, ("subscription",)),
        _fuel,
    ],
    spends=[
        pattern("icici", "SWIGGY", 11, "card", _card),
        pattern("icici", "ZOMATO", 6, "card", _card),
        pattern("icici", "UBER", 8, "card", _card),
        pattern("icici", "BOOKMYSHOW", 2, "card", _card),
        pattern("axis", "AMAZON", 3, "card", _card),
        pattern("axis", "STARBUCKS", 3, "card", _card),
        pattern("axis", "NATURES BASKET", 2, "card", _card),
        pattern("hdfccc", "FLIPKART", 2, "card", _card),
        pattern("hdfccc", "MYNTRA", 1, "card", _card),
        pattern("hdfccc", "CROMA", 1, "card", _card, ("1299", "9800")),
        pattern("hdfccc", "DOMINOS", 2, "card", _card),
        pattern("hdfc", "BLINKIT", 5, "upi", _card),
        pattern("hdfc", "ZEPTO", 3, "upi", _card),
        pattern("hdfc", "DHBVN", 1, "upi", _card),
        pattern("hdfc", "CULTFIT", 1, "upi", _card),
        pattern("kotak", "APOLLO PHARMACY", 2, "upi", _card),
        pattern("sbi", "IRCTC", 1, "upi", _card, ("620", "2400")),
    ],
    transfers=[
        *(
            TransferPlan("hdfc", "idfc", D("150000.00"), date(y, m, 3), lag_days=m % 2)
            for y, m in MONTHS
        ),
        TransferPlan("hdfc", "kotak", D("200000.00"), date(2025, 10, 6), rail="NEFT"),
        TransferPlan("hdfc", "sbi", D("25000.00"), date(2025, 11, 7), rail="UPI"),
        TransferPlan("idfc", "kotak", D("300000.00"), date(2025, 12, 9), lag_days=1),
        TransferPlan("idfc", "sbi", D("50000.00"), date(2026, 1, 13)),
        TransferPlan(
            "sbi", "kotak", D("30000.00"), date(2026, 2, 18), lag_days=1, rail="NEFT"
        ),
        TransferPlan("kotak", "zerodha", D("600000.00"), date(2025, 10, 7)),
        TransferPlan("idfc", "growwst", D("260000.00"), date(2025, 11, 4), rail="UPI"),
    ],
    card_bills=[
        CardBillPlan("icici", "hdfc", days_early=1),
        CardBillPlan("axis", "idfc", days_early=2),
        CardBillPlan("hdfccc", "hdfc", days_early=4),
    ],
    refunds=[
        RefundPlan(
            "hdfccc",
            "FLIPKART",
            MERCHANTS["FLIPKART"].category,
            D("4399.00"),
            date(2025, 10, 27),
            date(2025, 11, 6),
            narrations("FLIPKART", "card", _card)[1],
            "REFUND WWW FLIPKART COM BANGALORE IN",
        ),
        RefundPlan(
            "sbi",
            "IRCTC",
            MERCHANTS["IRCTC"].category,
            D("2315.60"),
            date(2026, 2, 2),
            date(2026, 2, 5),
            "UPI/{ref}/IRCTC/irctc@sbi/Ticket",
            "UPI/REV/{ref}/IRCTC/irctc@sbi/Ticket cancelled",
        ),
    ],
    trips=[
        Trip(
            "trip-rishikesh-2025",
            date(2025, 11, 28),
            date(2025, 11, 30),
            (
                trip_spend("icici", "MAKEMYTRIP", 1, "card", _card, ("8200", "14600")),
                trip_spend("icici", "ZOMATO", 2, "card", "DEHRADUN", ("340", "1250")),
                trip_spend("kotak", "UBER", 2, "upi", _card, ("620", "1480")),
            ),
        ),
        Trip(
            "trip-andaman-2026",
            date(2026, 1, 22),
            date(2026, 1, 26),
            (
                trip_spend("axis", "INDIGO", 2, "card", _card, ("9800", "16400")),
                trip_spend("axis", "MAKEMYTRIP", 1, "card", _card, ("21000", "34000")),
                trip_spend("axis", "ZOMATO", 3, "card", "PORT BLAIR", ("450", "2100")),
            ),
        ),
    ],
    fees=[
        *_fuel_fees,
        *sms_alert_charges("hdfc", date(2025, 10, 8), date(2026, 1, 8)),
        *sms_alert_charges("sbi", date(2025, 12, 12), date(2026, 3, 12)),
        bank_charge(
            "kotak",
            date(2026, 2, 3),
            "199.00",
            "DEBIT CARD ANNUAL FEE",
            "GST ON DEBIT CARD ANNUAL FEE",
        ),
        bank_charge(
            "hdfccc",
            date(2025, 12, 16),
            "1000.00",
            "ANNUAL MEMBERSHIP FEE",
            "IGST @18% ON ANNUAL MEMBERSHIP FEE",
        ),
    ],
    cashbacks=[
        CashbackPlan("hdfccc", on, D(amount), "CASHBACK REDEMPTION")
        for on, amount in (
            (date(2025, 11, 20), "684.15"),
            (date(2026, 1, 20), "912.40"),
            (date(2026, 3, 18), "537.85"),
        )
    ],
    mf_orders=[
        *sip("groww", "120716", "25000", 5, "hdfc"),
        *sip("groww", "122639", "25000", 10, "hdfc"),
        *sip("groww", "118989", "15000", 15, "hdfc"),
        *sip("groww", "125497", "10000", 20, "hdfc"),
        *sip("groww", "120503", "12500", 22, "hdfc", MONTHS[3:]),
        mf_buy("groww", "118989", "500000", trading_day(2025, 10, 10), "idfc"),
        mf_sell(
            "groww",
            "118989",
            trading_day(2026, 3, 13),
            units="600.000",
            payout_to="hdfc",
        ),
    ],
    stock_orders=[
        stock("zerodha", "LT", "buy", 40, trading_day(2025, 10, 9)),
        stock("zerodha", "TCS", "buy", 30, trading_day(2025, 11, 13)),
        stock("zerodha", "RELIANCE", "buy", 100, trading_day(2025, 12, 11)),
        stock("zerodha", "INFY", "buy", 60, trading_day(2026, 1, 15)),
        stock("zerodha", "LT", "sell", 10, trading_day(2026, 3, 12)),
        stock("growwst", "HDFCBANK", "buy", 100, trading_day(2025, 11, 6)),
        stock("growwst", "ITC", "buy", 300, trading_day(2026, 2, 10)),
    ],
)
