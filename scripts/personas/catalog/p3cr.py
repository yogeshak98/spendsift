"""₹3Cr: a partner at an advisory firm in Mumbai. Four banks, one of them a
high-interest savings account holding most of her cash like a fixed deposit;
three premium cards; a large SIP book on Groww, stocks on Groww and Zerodha.

She exits a large-cap fund in February, redeeming all but 0.004 units, so the
holding's dust must clear with it."""

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
CITY = "Mumbai"
_card = "MUMBAI"
_fuel, _fuel_fees = fuel_fills("icici", "INDIAN OIL", "5000", 8, _card)

PERSONA = Persona(
    slug="p3cr",
    display_name="Nandini Bhattacharjee",
    city=CITY,
    employer="Halcyra Capital Advisors LLP",
    net_worth_target=D("30000000"),
    seed=300030,
    accounts=[
        Account("hdfc", "bank", "HDFC", "Savings", "50100000110942", D("3418562.40")),
        Account("idfc", "bank", "IDFC", "Savings", "10000000120834", D("18830000.00")),
        Account("sbi", "bank", "SBI", "Savings", "30000000130726", D("1186904.18")),
        Account("kotak", "bank", "Kotak", "Savings", "1300001743", D("2240617.93")),
        Account(
            "icici", "card", "ICICI", "Emeralde", "4000001900002286", D("-84317.62")
        ),
        Account("axis", "card", "Axis", "Magnus", "4000002700006648", D("-52906.11")),
        Account(
            "hdfccc", "card", "HDFC", "Infinia", "4000003300004185", D("-71248.80")
        ),
        Account("groww", "broker_mf", "Groww", "Mutual Funds", "G100009682", D("0")),
        Account("growwst", "broker_stocks", "Groww", "Stocks", "G100010931", D("0")),
        Account("zerodha", "broker_stocks", "Zerodha", "Kite", "DM20417", D("0")),
    ],
    income=[
        Income(
            "hdfc",
            "salary",
            D("782400.00"),
            "NEFT CR-N{ref}-HALCYRA CAPITAL ADVISORS LLP-SALARY {month}",
            "Income:Salary",
        ),
        Income(
            "idfc",
            "interest",
            D("338620.00"),
            "INTEREST CREDIT FOR {month}",
            "Income:Interest",
        ),
        Income(
            "sbi",
            "interest",
            D("8342.00"),
            "CREDIT INTEREST {month}",
            "Income:Interest",
        ),
        Income(
            "kotak",
            "interest",
            D("16804.00"),
            "INT.PD:{ref}:{month}",
            "Income:Interest",
        ),
    ],
    recurring=[
        recurring("hdfc", "AIRTEL", "1766.82", 17, "upi", _card),
        recurring("hdfc", "ACT FIBERNET", "2359.00", 6, "upi", _card),
        recurring("axis", "NETFLIX", "649", 12, "card", _card, ("subscription",)),
        recurring("axis", "SPOTIFY", "179", 25, "card", _card, ("subscription",)),
        recurring("hdfccc", "CULTFIT", "3999", 1, "card", _card),
        _fuel,
    ],
    spends=[
        pattern("hdfccc", "SWIGGY", 12, "card", _card),
        pattern("hdfccc", "ZOMATO", 6, "card", _card, ("420", "2400")),
        pattern("hdfccc", "AMAZON", 4, "card", _card, ("499", "12800")),
        pattern("hdfccc", "CROMA", 1, "card", _card, ("2499", "18000")),
        pattern("icici", "UBER", 10, "card", _card, ("180", "980")),
        pattern("icici", "NATURES BASKET", 3, "card", _card),
        pattern("icici", "STARBUCKS", 4, "card", _card),
        pattern("icici", "PVR", 2, "card", _card, ("640", "2800")),
        pattern("axis", "FLIPKART", 2, "card", _card),
        pattern("axis", "NYKAA", 2, "card", _card),
        pattern("axis", "DECATHLON", 1, "card", _card),
        pattern("hdfc", "BIGBASKET", 4, "upi", _card),
        pattern("hdfc", "BLINKIT", 4, "upi", _card),
        pattern("hdfc", "ADANI ELECTRICITY", 1, "upi", _card),
        pattern("hdfc", "MAHANAGAR GAS", 1, "upi", _card),
        pattern("kotak", "APOLLO PHARMACY", 2, "upi", _card),
        pattern("kotak", "DMART", 1, "upi", _card),
        pattern("sbi", "IRCTC", 1, "upi", _card),
    ],
    transfers=[
        *(
            TransferPlan("hdfc", "idfc", D("250000.00"), date(y, m, 4), lag_days=1)
            for y, m in MONTHS
        ),
        TransferPlan("hdfc", "kotak", D("400000.00"), date(2025, 10, 6), rail="NEFT"),
        TransferPlan("hdfc", "sbi", D("75000.00"), date(2025, 12, 1), rail="UPI"),
        TransferPlan("idfc", "kotak", D("1000000.00"), date(2025, 10, 9)),
        TransferPlan(
            "idfc", "sbi", D("120000.00"), date(2026, 2, 16), lag_days=1, rail="NEFT"
        ),
        TransferPlan("sbi", "kotak", D("60000.00"), date(2026, 1, 21)),
        TransferPlan("kotak", "zerodha", D("1800000.00"), date(2025, 10, 13)),
        TransferPlan("idfc", "growwst", D("750000.00"), date(2025, 11, 3), rail="UPI"),
    ],
    card_bills=[
        CardBillPlan("icici", "hdfc", days_early=3),
        CardBillPlan("axis", "kotak", days_early=1),
        CardBillPlan("hdfccc", "hdfc", days_early=4),
    ],
    refunds=[
        RefundPlan(
            "hdfccc",
            "CROMA",
            MERCHANTS["CROMA"].category,
            D("18990.00"),
            date(2025, 11, 9),
            date(2025, 11, 21),
            narrations("CROMA", "card", _card)[0],
            "REFUND CROMA INFINITI RETAIL MUMBAI",
        ),
        RefundPlan(
            "icici",
            "AMAZON",
            MERCHANTS["AMAZON"].category,
            D("2749.00"),
            date(2026, 2, 6),
            date(2026, 2, 13),
            narrations("AMAZON", "card", _card)[2],
            "REFUND AMAZON IN BANGALORE IN",
        ),
    ],
    trips=[
        Trip(
            "trip-udaipur-2025",
            date(2025, 11, 14),
            date(2025, 11, 17),
            (
                trip_spend("hdfccc", "INDIGO", 2, "card", _card, ("8600", "14800")),
                trip_spend(
                    "hdfccc", "MAKEMYTRIP", 1, "card", _card, ("38000", "62000")
                ),
                trip_spend("axis", "ZOMATO", 3, "card", "UDAIPUR", ("650", "3200")),
            ),
        ),
        Trip(
            "trip-sikkim-2026",
            date(2026, 3, 6),
            date(2026, 3, 11),
            (
                trip_spend("hdfccc", "INDIGO", 2, "card", _card, ("11200", "19800")),
                trip_spend(
                    "hdfccc", "MAKEMYTRIP", 1, "card", _card, ("42000", "71000")
                ),
                trip_spend("axis", "ZOMATO", 3, "card", "GANGTOK", ("520", "2600")),
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
            "299.00",
            "DEBIT CARD ANNUAL FEE",
            "GST ON DEBIT CARD ANNUAL FEE",
        ),
        bank_charge(
            "hdfccc",
            date(2026, 1, 16),
            "12500.00",
            "ANNUAL MEMBERSHIP FEE",
            "IGST @18% ON ANNUAL MEMBERSHIP FEE",
        ),
    ],
    cashbacks=[
        CashbackPlan("axis", on, D(amount), "MERCHANT CASHBACK CREDIT")
        for on, amount in (
            (date(2025, 10, 28), "1250.00"),
            (date(2026, 1, 9), "842.30"),
        )
    ],
    mf_orders=[
        *sip("groww", "120716", "100000", 5, "hdfc"),
        *sip("groww", "122639", "100000", 7, "hdfc"),
        *sip("groww", "118989", "75000", 10, "hdfc"),
        *sip("groww", "125497", "50000", 12, "kotak"),
        *sip("groww", "120503", "12500", 15, "hdfc"),
        mf_buy("groww", "122639", "1980000", trading_day(2025, 10, 16), "idfc"),
        mf_buy("groww", "120716", "1500000", trading_day(2026, 1, 8), "idfc"),
        mf_buy("groww", "118834", "400000", trading_day(2025, 10, 9), "kotak"),
        mf_buy("groww", "118834", "200000", trading_day(2025, 11, 12), "kotak"),
        mf_sell(
            "groww",
            "118834",
            trading_day(2026, 2, 11),
            leave_units="0.004",
            payout_to="kotak",
        ),
    ],
    stock_orders=[
        stock("zerodha", "RELIANCE", "buy", 300, trading_day(2025, 10, 14)),
        stock("zerodha", "TCS", "buy", 120, trading_day(2025, 11, 11)),
        stock("zerodha", "LT", "buy", 100, trading_day(2025, 12, 9)),
        stock("zerodha", "INFY", "buy", 200, trading_day(2026, 1, 13)),
        stock("zerodha", "TCS", "sell", 40, trading_day(2026, 3, 10)),
        stock("growwst", "HDFCBANK", "buy", 250, trading_day(2025, 11, 5)),
        stock("growwst", "ICICIBANK", "buy", 150, trading_day(2025, 12, 15)),
        stock("growwst", "SBIN", "buy", 200, trading_day(2026, 2, 9)),
    ],
)
