"""Real Indian merchants, with narrations shaped like real statement rows.

Each merchant has narration variants in two shapes:

- a UPI row as a bank statement prints it (`UPI/<ref>/SWIGGY/...`), always
  starting with `UPI`;
- a card row as a card statement prints it (`SWIGGY BANGALORE IN`).

A variant may hold `{ref}`, which the simulator fills with a fresh reference,
and `{city}`, which the helpers below fill with the persona's city as card
statements print it. Every category is an `Expenses:*` account from the app's
taxonomy: the categorise prompt's list, and the seed dictionary where it knows
the merchant (the catalog tests check the dictionary never disagrees).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from backend.app.models.domain.accounts import expense
from scripts.personas.model import Recurring, SpendPattern, TripSpend

Channel = Literal["upi", "card"]

FOOD = expense("Food")
GROCERIES = expense("Groceries")
SHOPPING = expense("Shopping")
TRAVEL = expense("Travel")
UTILITIES = expense("Utilities")
ENTERTAINMENT = expense("Entertainment")
RENT = expense("Rent")
HEALTH = expense("Health")
# A bank's own charges (SMS alerts, debit-card fees). The seed dictionary has
# no such category yet, so the checker holds this expectation as pending.
BANK_CHARGES = expense("Bank Charges")

CATEGORIES: frozenset[str] = frozenset(
    {
        FOOD,
        GROCERIES,
        SHOPPING,
        TRAVEL,
        UTILITIES,
        ENTERTAINMENT,
        RENT,
        HEALTH,
        BANK_CHARGES,
    }
)


@dataclass(frozen=True)
class MerchantInfo:
    narration_variants: tuple[str, ...]
    category: str
    typical_amount: tuple[Decimal, Decimal]
    recurring: bool = False


def _m(
    category: str,
    lo: str,
    hi: str,
    *variants: str,
    recurring: bool = False,
) -> MerchantInfo:
    return MerchantInfo(
        tuple(variants), category, (Decimal(lo), Decimal(hi)), recurring
    )


MERCHANTS: dict[str, MerchantInfo] = {
    # --- food ---------------------------------------------------------------
    "SWIGGY": _m(
        FOOD,
        "149",
        "780",
        "UPI/{ref}/SWIGGY/swiggy@axisbank/Payment",
        "UPI-SWIGGY LIMITED-swiggyupi@axisbank-{ref}-ORDER",
        "SWIGGY {city} IN",
        "WWW SWIGGY COM {city}",
        "SWIGGY INSTAMART {city}",
    ),
    "ZOMATO": _m(
        FOOD,
        "180",
        "920",
        "UPI/{ref}/ZOMATO/zomato.payu@hdfcbank/Food order",
        "UPI-ZOMATO LIMITED-zomato@icici-{ref}-PAYMENT",
        "ZOMATO {city} IN",
        "ZOMATO ONLINE ORDER GURGAON",
    ),
    "BLINKIT": _m(
        FOOD,
        "120",
        "1150",
        "UPI/{ref}/BLINKIT/blinkit.payu@axisbank/Order",
        "BLINKIT GURGAON IN",
        "BLINKIT COMMERCE PVT LTD GURGAON",
    ),
    "ZEPTO": _m(
        FOOD,
        "99",
        "880",
        "UPI/{ref}/ZEPTO/zeptonow@ybl/Order",
        "UPI-KIRANAKART TECHNOLOGIES ZEPTO-zepto@hdfcbank-{ref}-UPI",
        "ZEPTO MARKETPLACE MUMBAI",
    ),
    "STARBUCKS": _m(
        FOOD,
        "290",
        "940",
        "UPI/{ref}/TATA STARBUCKS/tatastarbucks@hdfcbank/Coffee",
        "TATA STARBUCKS PVT LTD {city}",
        "STARBUCKS COFFEE {city} IN",
    ),
    "CHAAYOS": _m(
        FOOD,
        "140",
        "460",
        "UPI/{ref}/CHAAYOS/chaayos@icici/Payment",
        "CHAAYOS SUNSHINE TEAHOUSE {city}",
    ),
    "DOMINOS": _m(
        FOOD,
        "299",
        "1180",
        "UPI/{ref}/DOMINOS PIZZA/jubilantfood@hdfcbank/Order",
        "DOMINOS PIZZA {city} IN",
    ),
    "CAFE COFFEE DAY": _m(
        FOOD,
        "160",
        "540",
        "UPI/{ref}/CAFE COFFEE DAY/ccd@axisbank/Payment",
        "CAFE COFFEE DAY MADIKERI",
    ),
    # --- groceries ----------------------------------------------------------
    "BIGBASKET": _m(
        GROCERIES,
        "450",
        "3400",
        "UPI/{ref}/BIGBASKET/bigbasket@hdfcbank/Order",
        "UPI-INNOVATIVE RETAIL BIGBASKET-bigbasket.rzp@icici-{ref}-UPI",
        "BIGBASKET {city} IN",
        "WWW BIGBASKET COM BANGALORE",
    ),
    "DMART": _m(
        GROCERIES,
        "640",
        "4200",
        "UPI/{ref}/DMART AVENUE SUPERMARTS/dmart@icici/Payment",
        "DMART AVENUE SUPERMARTS {city}",
    ),
    "NATURES BASKET": _m(
        GROCERIES,
        "520",
        "3900",
        "UPI/{ref}/NATURES BASKET/naturesbasket@hdfcbank/Payment",
        "NATURES BASKET {city} IN",
    ),
    # --- shopping -----------------------------------------------------------
    "AMAZON": _m(
        SHOPPING,
        "249",
        "6800",
        "UPI/{ref}/AMAZON PAY/amazon@apl/Order",
        "AMAZON PAY INDIA PRIVATE BANGALORE",
        "AMAZON IN BANGALORE IN",
        "AMZN MKTP IN BANGALORE",
    ),
    "FLIPKART": _m(
        SHOPPING,
        "299",
        "7400",
        "UPI/{ref}/FLIPKART/flipkart@axisbank/Order",
        "FLIPKART INTERNET PVT LTD BANGALORE",
        "WWW FLIPKART COM BANGALORE IN",
    ),
    "MYNTRA": _m(
        SHOPPING,
        "499",
        "4600",
        "MYNTRA DESIGNS PVT LTD BANGALORE",
        "WWW MYNTRA COM BANGALORE IN",
    ),
    "NYKAA": _m(
        SHOPPING,
        "399",
        "3800",
        "UPI/{ref}/NYKAA E RETAIL/nykaa@icici/Order",
        "NYKAA E RETAIL PVT LTD MUMBAI",
    ),
    "DECATHLON": _m(
        SHOPPING,
        "699",
        "8900",
        "DECATHLON SPORTS INDIA {city}",
        "DECATHLON {city} IN",
    ),
    "CROMA": _m(
        SHOPPING,
        "1299",
        "24000",
        "CROMA INFINITI RETAIL {city}",
        "CROMA {city} IN",
    ),
    # --- travel -------------------------------------------------------------
    "UBER": _m(
        TRAVEL,
        "92",
        "640",
        "UPI/{ref}/UBER INDIA/uber.rzp@axisbank/Trip",
        "UBER INDIA SYSTEMS PVT LTD {city}",
        "UBER TRIP HELP.UBER.COM",
    ),
    "OLA": _m(
        TRAVEL,
        "85",
        "560",
        "UPI/{ref}/ANI TECHNOLOGIES OLA/olacabs@ybl/Ride",
        "OLA CABS ANI TECHNOLOGIES BANGALORE",
    ),
    "RAPIDO": _m(
        TRAVEL,
        "38",
        "190",
        "UPI/{ref}/RAPIDO/rapido@ybl/Ride",
        "UPI-ROPPEN TRANSPORTATION RAPIDO-rapido.rzp@icici-{ref}-UPI",
    ),
    "IRCTC": _m(
        TRAVEL,
        "420",
        "4800",
        "UPI/{ref}/IRCTC/irctc@sbi/Ticket",
        "IRCTC WEB NEW DELHI",
        "IRCTC E TICKETING NEW DELHI IN",
    ),
    "INDIGO": _m(
        TRAVEL,
        "3800",
        "14500",
        "INDIGO INTERGLOBE AVIATION GURGAON",
        "WWW GOINDIGO IN GURGAON",
    ),
    "MAKEMYTRIP": _m(
        TRAVEL,
        "2400",
        "18500",
        "MAKEMYTRIP INDIA PVT LTD GURGAON",
        "MMT HOTELS MAKEMYTRIP GURGAON",
    ),
    "REDBUS": _m(
        TRAVEL,
        "680",
        "2100",
        "UPI/{ref}/REDBUS/redbus@icici/Ticket",
        "REDBUS IN BANGALORE",
    ),
    "OYO": _m(
        TRAVEL,
        "1400",
        "5200",
        "OYO ORAVEL STAYS GURGAON",
        "OYO ROOMS GURGAON IN",
    ),
    "INDIAN OIL": _m(
        TRAVEL,
        "500",
        "3000",
        "INDIAN OIL FUEL STATION {city}",
        "IOCL COCO OUTLET {city}",
        recurring=True,
    ),
    "HPCL": _m(
        TRAVEL,
        "300",
        "2500",
        "HPCL PETROL PUMP {city}",
        recurring=True,
    ),
    # --- utilities ----------------------------------------------------------
    "AIRTEL": _m(
        UTILITIES,
        "349",
        "1499",
        "UPI/{ref}/AIRTEL PAYMENTS/airtel@airtel/Recharge",
        "AIRTEL PAYMENTS BANK GURGAON",
        recurring=True,
    ),
    "JIO": _m(
        UTILITIES,
        "299",
        "999",
        "UPI/{ref}/RELIANCE JIO/jio@sbi/Recharge",
        "RELIANCE JIO INFOCOMM MUMBAI",
        recurring=True,
    ),
    "BESCOM": _m(
        UTILITIES,
        "480",
        "2600",
        "UPI/{ref}/BESCOM/bescom.billdesk@hdfcbank/Electricity bill",
        "BILLDESK BESCOM BANGALORE",
        recurring=True,
    ),
    "MSEDCL": _m(
        UTILITIES,
        "620",
        "3100",
        "UPI/{ref}/MSEDCL/mahadiscom@sbi/Electricity bill",
        "BILLDESK MSEDCL PUNE",
        recurring=True,
    ),
    "TGSPDCL": _m(
        UTILITIES,
        "540",
        "2900",
        "UPI/{ref}/TGSPDCL/tgspdcl.billdesk@hdfcbank/Electricity bill",
        recurring=True,
    ),
    "DHBVN": _m(
        UTILITIES,
        "1800",
        "6400",
        "UPI/{ref}/DHBVN/dhbvn.billdesk@icici/Electricity bill",
        recurring=True,
    ),
    "ADANI ELECTRICITY": _m(
        UTILITIES,
        "2400",
        "9800",
        "UPI/{ref}/ADANI ELECTRICITY MUMBAI/adanielectricity@hdfcbank/Bill",
        "ADANI ELECTRICITY MUMBAI LTD",
        recurring=True,
    ),
    "MAHANAGAR GAS": _m(
        UTILITIES,
        "540",
        "1600",
        "UPI/{ref}/MAHANAGAR GAS LTD/mglbill@icici/Gas bill",
        recurring=True,
    ),
    "ACT FIBERNET": _m(
        UTILITIES,
        "1059",
        "1999",
        "UPI/{ref}/ATRIA CONVERGENCE ACT/actfibernet@hdfcbank/Broadband",
        "ACT FIBERNET BANGALORE IN",
        recurring=True,
    ),
    # --- entertainment ------------------------------------------------------
    "NETFLIX": _m(
        ENTERTAINMENT,
        "199",
        "649",
        "NETFLIX.COM MUMBAI",
        "NETFLIX ENTERTAINMENT SERVICES MUMBAI IN",
        recurring=True,
    ),
    "SPOTIFY": _m(
        ENTERTAINMENT,
        "119",
        "179",
        "SPOTIFY INDIA MUMBAI",
        "SPOTIFY SI MUMBAI IN",
        recurring=True,
    ),
    "BOOKMYSHOW": _m(
        ENTERTAINMENT,
        "260",
        "2400",
        "UPI/{ref}/BOOKMYSHOW/bookmyshow@icici/Tickets",
        "BIGTREE ENTERTAINMENT BOOKMYSHOW MUMBAI",
    ),
    "PVR": _m(
        ENTERTAINMENT,
        "320",
        "1900",
        "PVR INOX LTD {city}",
        "PVR CINEMAS {city} IN",
    ),
    # --- health -------------------------------------------------------------
    "APOLLO PHARMACY": _m(
        HEALTH,
        "118",
        "2400",
        "UPI/{ref}/APOLLO PHARMACY/apollopharmacy@hdfcbank/Medicines",
        "APOLLO PHARMACY {city}",
    ),
    "CULTFIT": _m(
        HEALTH,
        "1499",
        "3999",
        "UPI/{ref}/CULTFIT/curefit@icici/Membership",
        "CULTFIT CUREFIT HEALTHCARE BANGALORE",
        recurring=True,
    ),
    "PHARMEASY": _m(
        HEALTH,
        "210",
        "1800",
        "UPI/{ref}/PHARMEASY/pharmeasy@axisbank/Order",
        "PHARMEASY API HOLDINGS MUMBAI",
    ),
    # --- rent ---------------------------------------------------------------
    "NOBROKER": _m(
        RENT,
        "15000",
        "95000",
        "UPI/{ref}/NOBROKER RENT/nobroker.rentpay@icici/Rent",
        recurring=True,
    ),
    "STANZA LIVING": _m(
        RENT,
        "12000",
        "24000",
        "UPI/{ref}/STANZA LIVING/stanzaliving@icici/Rent",
        recurring=True,
    ),
}


def narrations(merchant: str, channel: Channel, city: str) -> tuple[str, ...]:
    """The merchant's variants for `channel`, with `{city}` filled in."""
    info = MERCHANTS[merchant]
    picked = tuple(
        v.replace("{city}", city.upper())
        for v in info.narration_variants
        if v.startswith("UPI") == (channel == "upi")
    )
    if not picked:
        raise ValueError(f"{merchant} has no {channel} narration")
    return picked


def pattern(
    account_key: str,
    merchant: str,
    per_month: int,
    channel: Channel,
    city: str,
    amount_range: tuple[str, str] | None = None,
    tags: tuple[str, ...] = (),
) -> SpendPattern:
    info = MERCHANTS[merchant]
    lo, hi = (
        info.typical_amount
        if amount_range is None
        else (Decimal(amount_range[0]), Decimal(amount_range[1]))
    )
    return SpendPattern(
        account_key,
        merchant,
        info.category,
        per_month,
        (lo, hi),
        narrations(merchant, channel, city),
        tags,
    )


def recurring(
    account_key: str,
    merchant: str,
    amount: str,
    day: int,
    channel: Channel,
    city: str,
    tags: tuple[str, ...] = (),
) -> Recurring:
    return Recurring(
        account_key,
        merchant,
        Decimal(amount),
        day,
        narrations(merchant, channel, city)[0],
        MERCHANTS[merchant].category,
        tags,
    )


def trip_spend(
    account_key: str,
    merchant: str,
    count: int,
    channel: Channel,
    city: str,
    amount_range: tuple[str, str] | None = None,
) -> TripSpend:
    p = pattern(account_key, merchant, count, channel, city, amount_range)
    return TripSpend(
        account_key, merchant, p.category, count, p.amount_range, p.narrations
    )
