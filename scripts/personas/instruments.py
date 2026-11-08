"""The public instruments the personas trade, and the dates they may trade on.

`SCHEMES` holds AMFI scheme codes of Direct-Growth plans and `TICKERS` holds
NSE symbols with their ISINs. These are real public identifiers. The NAV and
price snapshot fetches each one once, for every trading day of the period, so
the QA runs offline on real values.

Orders are dated only on days the market and the fund registrars are open. On
a holiday there is no NAV or close price to look up, so `trading_day` rolls a
date forward to the next weekday that is not an exchange holiday.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from scripts.personas.model import (
    PERIOD_END,
    PERIOD_START,
    MfOrderPlan,
    StockOrderPlan,
)

# AMFI scheme code -> the scheme name as the broker's order history prints it.
SCHEMES: dict[str, str] = {
    "120716": "UTI Nifty 50 Index Fund Direct Growth",
    "122639": "Parag Parikh Flexi Cap Fund Direct Growth",
    "118989": "HDFC Mid Cap Fund Direct Growth",
    "120503": "Axis ELSS Tax Saver Fund Direct Growth",
    "125497": "SBI Small Cap Fund Direct Growth",
    "118834": "Mirae Asset Large & Midcap Fund Direct Growth",
}


@dataclass(frozen=True)
class Ticker:
    isin: str
    name: str


# NSE symbol -> ISIN and the company name as order books print it.
TICKERS: dict[str, Ticker] = {
    "RELIANCE": Ticker("INE002A01018", "RELIANCE INDUSTRIES LTD"),
    "TCS": Ticker("INE467B01029", "TATA CONSULTANCY SERV LT"),
    "INFY": Ticker("INE009A01021", "INFOSYS LIMITED"),
    "HDFCBANK": Ticker("INE040A01034", "HDFC BANK LTD"),
    "ICICIBANK": Ticker("INE090A01021", "ICICI BANK LTD."),
    "ITC": Ticker("INE154A01025", "ITC LTD"),
    "LT": Ticker("INE018A01030", "LARSEN & TOUBRO LTD."),
    "SBIN": Ticker("INE062A01020", "STATE BANK OF INDIA"),
}

# NSE trading holidays that fall on a weekday inside the period.
HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2025, 10, 2),  # Gandhi Jayanti / Dussehra
        date(2025, 10, 21),  # Diwali Laxmi Pujan
        date(2025, 10, 22),  # Balipratipada
        date(2025, 11, 5),  # Guru Nanak Jayanti
        date(2025, 12, 25),  # Christmas
        date(2026, 1, 15),  # Maharashtra municipal elections
        date(2026, 1, 26),  # Republic Day
        date(2026, 3, 3),  # Holi
        date(2026, 3, 26),  # Ram Navami
        date(2026, 3, 31),  # Mahavir Jayanti
    }
)

# Every (year, month) of the period, in order.
MONTHS: tuple[tuple[int, int], ...] = (
    (2025, 10),
    (2025, 11),
    (2025, 12),
    (2026, 1),
    (2026, 2),
    (2026, 3),
)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS


def trading_day(y: int, m: int, d: int) -> date:
    """`y-m-d`, or the first trading day after it."""
    day = date(y, m, d)
    while not is_trading_day(day):
        day += timedelta(days=1)
    if not PERIOD_START <= day <= PERIOD_END:
        raise ValueError(f"{day} is outside the period")
    return day


def sip(
    broker: str,
    code: str,
    amount: str,
    day: int,
    funded_from: str,
    months: tuple[tuple[int, int], ...] = MONTHS,
) -> list[MfOrderPlan]:
    """A monthly SIP: one buy of `amount` on `day` (rolled to a trading day) in
    each of `months`, each paid by a same-day transfer from `funded_from`."""
    return [
        mf_buy(broker, code, amount, trading_day(y, m, day), funded_from)
        for y, m in months
    ]


def mf_buy(
    broker: str, code: str, amount: str, on: date, funded_from: str
) -> MfOrderPlan:
    return MfOrderPlan(
        broker,
        code,
        SCHEMES[code],
        "buy",
        on,
        amount=Decimal(amount),
        funded_from=funded_from,
    )


def mf_sell(
    broker: str,
    code: str,
    on: date,
    *,
    units: str | None = None,
    leave_units: str | None = None,
    payout_to: str | None = None,
) -> MfOrderPlan:
    return MfOrderPlan(
        broker,
        code,
        SCHEMES[code],
        "sell",
        on,
        units=None if units is None else Decimal(units),
        leave_units=None if leave_units is None else Decimal(leave_units),
        payout_to=payout_to,
    )


def stock(
    broker: str, symbol: str, side: str, quantity: int, on: date
) -> StockOrderPlan:
    if side not in ("buy", "sell"):
        raise ValueError(f"stock order side {side!r}")
    ticker = TICKERS[symbol]
    return StockOrderPlan(
        broker,
        symbol,
        ticker.isin,
        ticker.name,
        "buy" if side == "buy" else "sell",
        quantity,
        on,
    )
