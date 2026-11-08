"""Building blocks the five personas share: fuel fills with their surcharge,
a bank's own charges, and the dates those land on."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from scripts.personas.instruments import MONTHS
from scripts.personas.merchants import BANK_CHARGES, MERCHANTS, recurring
from scripts.personas.model import FeePlan, Recurring

D = Decimal

# Card issuers add 1% (at least 10 rupees) on a fuel purchase, plus 18% GST on
# that surcharge, each as its own row on the purchase's date.
FUEL_SURCHARGE_RATE = D("0.01")
FUEL_SURCHARGE_MIN = D("10")


def fuel_fills(
    card_key: str, merchant: str, amount: str, day: int, city: str
) -> tuple[Recurring, list[FeePlan]]:
    """A fill-up of `amount` on `day` of every month, and its surcharge rows.
    The surcharge takes the fuel's category, as the GST row beside it does."""
    fill = recurring(card_key, merchant, amount, day, "card", city)
    surcharge = max(
        (D(amount) * FUEL_SURCHARGE_RATE).quantize(D("0.01"), rounding=ROUND_HALF_UP),
        FUEL_SURCHARGE_MIN,
    )
    fees = [
        FeePlan(
            card_key,
            date(y, m, day),
            surcharge,
            "FUEL SURCHARGE",
            MERCHANTS[merchant].category,
            gst_narration="IGST @18% ON FUEL SURCHARGE",
            merchant=merchant,
        )
        for y, m in MONTHS
    ]
    return fill, fees


def bank_charge(
    account_key: str, on: date, amount: str, narration: str, gst_narration: str
) -> FeePlan:
    """A charge the bank or card issuer levies on its own account, with GST."""
    return FeePlan(
        account_key,
        on,
        D(amount),
        narration,
        BANK_CHARGES,
        gst_narration=gst_narration,
    )


def sms_alert_charges(account_key: str, *days: date) -> list[FeePlan]:
    """Quarterly SMS alert charges (15 rupees plus GST)."""
    return [
        bank_charge(
            account_key,
            day,
            "15.00",
            "SMS ALERT CHARGES FOR QTR",
            "GST ON SMS ALERT CHARGES",
        )
        for day in days
    ]
