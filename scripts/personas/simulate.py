"""Turn a persona's plans into dated statement events and closing state.

The only source of randomness is `random.Random(persona.seed)`, consumed in a
fixed order (income, recurring, spends, trips, refunds, fees, cashbacks,
transfers, MF orders, stock orders, card bills), so a persona always yields the
same truth. Nothing reads the clock.
"""

from __future__ import annotations

import calendar
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from scripts.personas.instruments import is_trading_day
from scripts.personas.model import (
    PERIOD_END,
    PERIOD_START,
    Account,
    CardBillPlan,
    CardCycle,
    Event,
    EventKind,
    MfOrderPlan,
    NavLookup,
    Persona,
    PriceLookup,
    StockOrderPlan,
    TransferPlan,
    Truth,
)

PAISA = Decimal("0.01")
UNIT_STEP = Decimal("0.001")
NAV_QUANT = Decimal("0.0001")
# 0.005% on the cash of an MF purchase, deducted before units are allotted.
STAMP_DUTY_RATE = Decimal("0.00005")
GST_RATE = Decimal("0.18")
# A redemption leaving this many units or fewer redeems the whole holding.
DUST_UNITS = Decimal("0.005")
# Trading days between an MF redemption and its payout into the bank.
PAYOUT_DAYS = 2
STATEMENT_DAY = 15
DUE_DAY = 5
_MONTHS = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)


def money(value: Decimal) -> Decimal:
    return value.quantize(PAISA, rounding=ROUND_HALF_UP)


def mf_purchase(cash: Decimal, nav: Decimal) -> tuple[Decimal, Decimal]:
    """(stamp duty, units allotted) for an MF purchase of `cash` at `nav`.

    Units are rounded half-up to 3 dp; when that rounding overshoots the cash
    (small orders on a high NAV) one 0.001 step is taken down, as the app's
    exact-NAV booking does, so `units * nav` never exceeds the cash paid.
    """
    stamp = money(cash * STAMP_DUTY_RATE)
    units = ((cash - stamp) / nav).quantize(UNIT_STEP, rounding=ROUND_HALF_UP)
    if units * nav > cash:
        units -= UNIT_STEP
    return stamp, units


def _months() -> list[tuple[int, int]]:
    out, (y, m) = [], (PERIOD_START.year, PERIOD_START.month)
    while date(y, m, 1) <= PERIOD_END:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _last_day(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def last_working_day(y: int, m: int) -> date:
    day = _last_day(y, m)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _next_month(day: date, dom: int) -> date:
    y, m = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(y, m, dom)


@dataclass
class _Lot:
    units: Decimal
    cost: Decimal


@dataclass
class _Sim:
    persona: Persona
    rng: random.Random
    events: list[Event] = field(default_factory=list)
    refs: set[str] = field(default_factory=set)
    pairs: int = 0
    lots: dict[str, dict[str, list[_Lot]]] = field(default_factory=dict)

    def acct(self, key: str, *kinds: str) -> Account:
        acct = self.persona.account(key)
        if kinds and acct.kind not in kinds:
            raise ValueError(
                f"{self.persona.slug}: {key} is a {acct.kind}, need {kinds}"
            )
        return acct

    def ref(self) -> str:
        while True:
            ref = str(self.rng.randrange(10**11, 10**12))
            if ref not in self.refs:
                self.refs.add(ref)
                return ref

    def pair(self) -> str:
        self.pairs += 1
        return f"P{self.pairs:04d}"

    def add(
        self,
        day: date,
        key: str,
        amount: Decimal,
        kind: EventKind,
        narration: str,
        **kw,
    ) -> Event:
        if not PERIOD_START <= day <= PERIOD_END:
            raise ValueError(
                f"{self.persona.slug}: {kind} on {day} is outside the period"
            )
        event = Event(
            date=day,
            account_key=key,
            amount=money(amount),
            narration=narration,
            ref=kw.pop("ref", None),
            kind=kind,
            **kw,
        )
        self.events.append(event)
        return event

    def amount_in(self, lo: Decimal, hi: Decimal) -> Decimal:
        paise = self.rng.randrange(int(lo * 100), int(hi * 100) + 1)
        return Decimal(paise) / 100

    def narrate(self, template: str) -> tuple[str, str | None]:
        if "{ref}" not in template:
            return template, None
        ref = self.ref()
        return template.format(ref=ref), ref

    def spend(
        self,
        day: date,
        key: str,
        merchant: str,
        category: str,
        amount: Decimal,
        template: str,
        tags: tuple[str, ...] = (),
        pair_id: str | None = None,
    ) -> None:
        self.acct(key, "bank", "card")
        if not category.startswith("Expenses:"):
            raise ValueError(f"{self.persona.slug}: spend category {category!r}")
        text, ref = self.narrate(template)
        self.add(
            day,
            key,
            -amount,
            "spend",
            text,
            ref=ref,
            merchant=merchant,
            expected_category=category,
            expected_tags=tags,
            pair_id=pair_id,
        )

    # -- plan kinds, in rng-consumption order ---------------------------------

    def income(self) -> None:
        for plan in self.persona.income:
            self.acct(plan.account_key, "bank")
            for y, m in _months():
                if plan.kind == "salary":
                    day = last_working_day(y, m)
                elif m % 3 == 0:
                    day = _last_day(y, m)
                else:
                    continue
                ref = self.ref()
                text = plan.narration.format(month=f"{_MONTHS[m - 1]} {y}", ref=ref)
                self.add(
                    day,
                    plan.account_key,
                    plan.amount,
                    plan.kind,
                    text,
                    ref=ref,
                    expected_category=plan.category,
                )

    def recurring(self) -> None:
        for plan in self.persona.recurring:
            for y, m in _months():
                day = date(y, m, min(plan.day, calendar.monthrange(y, m)[1]))
                self.spend(
                    day,
                    plan.account_key,
                    plan.merchant,
                    plan.category,
                    plan.amount,
                    plan.narration,
                    plan.tags,
                )

    def spends(self) -> None:
        for y, m in _months():
            last = calendar.monthrange(y, m)[1]
            for plan in self.persona.spends:
                for _ in range(plan.per_month):
                    day = date(y, m, self.rng.randint(1, last))
                    self.spend(
                        day,
                        plan.account_key,
                        plan.merchant,
                        plan.category,
                        self.amount_in(*plan.amount_range),
                        self.rng.choice(plan.narrations),
                        plan.tags,
                    )

    def trips(self) -> None:
        for trip in self.persona.trips:
            span = (trip.end - trip.start).days
            for plan in trip.spends:
                for _ in range(plan.count):
                    day = trip.start + timedelta(days=self.rng.randint(0, span))
                    self.spend(
                        day,
                        plan.account_key,
                        plan.merchant,
                        plan.category,
                        self.amount_in(*plan.amount_range),
                        self.rng.choice(plan.narrations),
                        (trip.tag,),
                    )

    def refunds(self) -> None:
        for plan in self.persona.refunds:
            if plan.refund_on <= plan.purchase_on:
                raise ValueError(f"{self.persona.slug}: refund before its purchase")
            pair_id = self.pair()
            self.spend(
                plan.purchase_on,
                plan.account_key,
                plan.merchant,
                plan.category,
                plan.amount,
                plan.purchase_narration,
                pair_id=pair_id,
            )
            text, ref = self.narrate(plan.refund_narration)
            self.add(
                plan.refund_on,
                plan.account_key,
                plan.amount,
                "refund",
                text,
                ref=ref,
                merchant=plan.merchant,
                expected_category=plan.category,
                pair_id=pair_id,
            )

    def fees(self) -> None:
        for plan in self.persona.fees:
            self.acct(plan.account_key, "bank", "card")
            text, ref = self.narrate(plan.narration)
            self.add(
                plan.on,
                plan.account_key,
                -plan.amount,
                "fee",
                text,
                ref=ref,
                merchant=plan.merchant,
                expected_category=plan.category,
            )
            if plan.gst_narration is not None:
                text, ref = self.narrate(plan.gst_narration)
                self.add(
                    plan.on,
                    plan.account_key,
                    -money(plan.amount * GST_RATE),
                    "gst_on_fee",
                    text,
                    ref=ref,
                    merchant=plan.merchant,
                    expected_category=plan.category,
                )

    def cashbacks(self) -> None:
        for plan in self.persona.cashbacks:
            self.acct(plan.account_key, "bank", "card")
            text, ref = self.narrate(plan.narration)
            self.add(
                plan.on,
                plan.account_key,
                plan.amount,
                "cashback",
                text,
                ref=ref,
                expected_category=plan.category,
            )

    def transfer(self, plan: TransferPlan) -> None:
        src = self.acct(plan.from_key, "bank", "broker_mf", "broker_stocks")
        dst = self.acct(plan.to_key, "bank", "broker_mf", "broker_stocks")
        if src.key == dst.key or plan.lag_days not in (0, 1) or plan.amount <= 0:
            raise ValueError(f"{self.persona.slug}: bad transfer {plan}")
        funding = src.kind != "bank" or dst.kind != "bank"
        out_kind: EventKind = "platform_funding" if funding else "transfer_out"
        in_kind: EventKind = "platform_funding" if funding else "transfer_in"
        ref, pair_id = self.ref(), self.pair()
        holder = self.persona.display_name.upper()
        self.add(
            plan.on,
            src.key,
            -plan.amount,
            out_kind,
            f"{plan.rail}/{ref}/{holder}/{dst.institution.upper()} XX{dst.last4}",
            ref=ref,
            counter_account_key=dst.key,
            pair_id=pair_id,
        )
        self.add(
            plan.on + timedelta(days=plan.lag_days),
            dst.key,
            plan.amount,
            in_kind,
            f"{plan.rail}/{ref}/{holder}/{src.institution.upper()} XX{src.last4}",
            ref=ref,
            counter_account_key=src.key,
            pair_id=pair_id,
        )

    def transfers(self) -> None:
        for plan in self.persona.transfers:
            self.transfer(plan)

    def _lots(self, broker: str, commodity: str) -> list[_Lot]:
        return self.lots.setdefault(broker, {}).setdefault(commodity, [])

    def _consume(self, lots: list[_Lot], units: Decimal, what: str) -> None:
        held = sum((lot.units for lot in lots), Decimal(0))
        if units <= 0 or units > held:
            raise ValueError(
                f"{self.persona.slug}: sell {units} of {what}, holding {held}"
            )
        while units > 0:
            take = min(units, lots[0].units)
            lots[0].units -= take
            units -= take
            if lots[0].units == 0:
                lots.pop(0)

    def mf_order(self, plan: MfOrderPlan, price: NavLookup) -> None:
        self.acct(plan.broker_key, "broker_mf")
        nav = price(plan.scheme_code, plan.on).quantize(
            NAV_QUANT, rounding=ROUND_HALF_UP
        )
        lots = self._lots(plan.broker_key, plan.scheme_code)
        if plan.side == "buy":
            if plan.payout_to is not None:
                raise ValueError(
                    f"{self.persona.slug}: payout_to is only for a sell, not {plan}"
                )
            cash = plan.amount
            if cash is None or cash <= 0 or cash % 100 != 0:
                raise ValueError(
                    f"{self.persona.slug}: MF buy {cash} is not a multiple of 100"
                )
            if plan.funded_from is not None:
                self.transfer(
                    TransferPlan(
                        plan.funded_from, plan.broker_key, cash, plan.on, rail="UPI"
                    )
                )
            stamp, units = mf_purchase(cash, nav)
            self.add(
                plan.on,
                plan.broker_key,
                -cash,
                "mf_buy",
                plan.scheme_name,
                commodity=plan.scheme_code,
                units=units,
                price=nav,
                charges=stamp,
            )
            lots.append(_Lot(units, nav))
            return
        if (plan.units is None) == (plan.leave_units is None):
            raise ValueError(
                f"{self.persona.slug}: MF sell needs units or leave_units, not both"
            )
        units = plan.units
        if units is None:
            held = sum((lot.units for lot in lots), Decimal(0))
            units = held - (plan.leave_units or Decimal(0))
        self._consume(lots, units, plan.scheme_code)
        if sum((lot.units for lot in lots), Decimal(0)) <= DUST_UNITS:
            lots.clear()
        pair_id = None if plan.payout_to is None else self.pair()
        sold = self.add(
            plan.on,
            plan.broker_key,
            units * nav,
            "mf_sell",
            plan.scheme_name,
            commodity=plan.scheme_code,
            units=-units,
            price=nav,
            pair_id=pair_id,
        )
        if plan.payout_to is not None:
            self.payout(plan, sold, pair_id)

    def payout(self, plan: MfOrderPlan, sold: Event, pair_id: str | None) -> None:
        """The redemption's cash leaves the broker for the bank, both legs on
        the same day and sharing the sell's pair id, so the payout links to
        the redemption it settles."""
        broker = self.acct(plan.broker_key, "broker_mf")
        bank = self.acct(plan.payout_to or "", "bank")
        day, left = plan.on, PAYOUT_DAYS
        while left:
            day += timedelta(days=1)
            left -= is_trading_day(day)
        if day > PERIOD_END:
            raise ValueError(
                f"{self.persona.slug}: the payout of the {plan.on} redemption"
                f" falls on {day}, after the period"
            )
        ref = self.ref()
        holder = self.persona.display_name.upper()
        self.add(
            day,
            broker.key,
            -sold.amount,
            "mf_redemption_payout",
            f"REDEMPTION PAYOUT {bank.institution.upper()} XX{bank.last4}",
            ref=ref,
            counter_account_key=bank.key,
            pair_id=pair_id,
        )
        self.add(
            day,
            bank.key,
            sold.amount,
            "mf_redemption_payout",
            f"NEFT CR-N{ref}-{broker.institution.upper()} MF REDEMPTION-{holder}",
            ref=ref,
            counter_account_key=broker.key,
            pair_id=pair_id,
        )

    def stock_order(self, plan: StockOrderPlan, price: PriceLookup) -> None:
        self.acct(plan.broker_key, "broker_stocks")
        if plan.quantity <= 0:
            raise ValueError(f"{self.persona.slug}: stock order of {plan.quantity}")
        close = money(price(plan.symbol, plan.on))
        qty = Decimal(plan.quantity)
        lots = self._lots(plan.broker_key, plan.symbol)
        order_id = str(self.rng.randrange(10**15, 2 * 10**15))
        if plan.side == "buy":
            lots.append(_Lot(qty, close))
            amount, kind, units = -(qty * close), "stock_buy", qty
        else:
            self._consume(lots, qty, plan.symbol)
            amount, kind, units = qty * close, "stock_sell", -qty
        self.add(
            plan.on,
            plan.broker_key,
            amount,
            kind,
            plan.name,
            ref=order_id,
            commodity=plan.symbol,
            units=units,
            price=close,
        )

    def card_bills(self) -> list[CardCycle]:
        plans: dict[str, CardBillPlan] = {}
        for plan in self.persona.card_bills:
            self.acct(plan.card_key, "card")
            self.acct(plan.from_key, "bank")
            if plan.card_key in plans or not 0 <= plan.days_early < 20:
                raise ValueError(f"{self.persona.slug}: bad card bill plan {plan}")
            plans[plan.card_key] = plan
        cycles: list[CardCycle] = []
        # The statement just before the period: a card's opening balance.
        before = PERIOD_START.replace(day=1) - timedelta(days=1)
        for card in (a for a in self.persona.accounts if a.kind == "card"):
            stmt, balance = before.replace(day=STATEMENT_DAY), card.opening_balance
            while stmt <= PERIOD_END:
                plan = plans.get(card.key)
                due = _next_month(stmt, DUE_DAY)
                pay_day = due - timedelta(days=plan.days_early if plan else 0)
                if plan is not None and balance < 0 and pay_day <= PERIOD_END:
                    ref, pair_id = self.ref(), self.pair()
                    self.add(
                        pay_day,
                        plan.from_key,
                        balance,
                        "card_bill_payment",
                        f"CC PAYMENT {card.institution.upper()} XX{card.last4} {ref}",
                        ref=ref,
                        counter_account_key=card.key,
                        pair_id=pair_id,
                    )
                    self.add(
                        pay_day,
                        card.key,
                        -balance,
                        "card_payment_received",
                        "PAYMENT RECEIVED - THANK YOU",
                        ref=ref,
                        counter_account_key=plan.from_key,
                        pair_id=pair_id,
                    )
                prev_stmt, prev_balance = stmt, balance
                stmt = _next_month(stmt, STATEMENT_DAY)
                if stmt > PERIOD_END:
                    break
                balance = card.opening_balance + sum(
                    (
                        e.amount
                        for e in self.events
                        if e.account_key == card.key and e.date <= stmt
                    ),
                    Decimal(0),
                )
                cycles.append(
                    CardCycle(
                        card.key,
                        prev_stmt + timedelta(days=1),
                        stmt,
                        _next_month(stmt, DUE_DAY),
                        prev_balance,
                        balance,
                    )
                )
        return cycles


def _in_statement_order(events: list[Event]) -> list[Event]:
    """By date, then in the order the events were made, except that a fee
    charged on a purchase (and its GST) sits right after that purchase: the
    same-day spend on its account at its merchant."""
    order = {id(e): i for i, e in enumerate(events)}
    anchor: dict[int, int] = {}
    for e in events:
        if e.kind not in ("fee", "gst_on_fee") or e.merchant is None:
            continue
        spend = next(
            (
                s
                for s in events
                if s.kind == "spend"
                and s.account_key == e.account_key
                and s.date == e.date
                and s.merchant == e.merchant
            ),
            None,
        )
        if spend is None:
            raise ValueError(f"{e.narration} on {e.date}: no {e.merchant} purchase")
        anchor[id(e)] = order[id(spend)]

    def key(e: Event) -> tuple[date, int, int, int]:
        n = order[id(e)]
        if id(e) in anchor:
            return (e.date, anchor[id(e)], 1, n)
        return (e.date, n, 0, n)

    return sorted(events, key=key)


def simulate(persona: Persona, *, nav: NavLookup, prices: PriceLookup) -> Truth:
    sim = _Sim(persona, random.Random(persona.seed))
    sim.income()
    sim.recurring()
    sim.spends()
    sim.trips()
    sim.refunds()
    sim.fees()
    sim.cashbacks()
    sim.transfers()
    for plan in sorted(persona.mf_orders, key=lambda p: p.on):
        sim.mf_order(plan, nav)
    for plan in sorted(persona.stock_orders, key=lambda p: p.on):
        sim.stock_order(plan, prices)
    cycles = sim.card_bills()

    events = _in_statement_order(sim.events)
    _reject_overdrafts(persona, events)

    closing = {
        a.key: a.opening_balance
        + sum((e.amount for e in events if e.account_key == a.key), Decimal(0))
        for a in persona.accounts
    }
    holdings: dict[str, dict[str, Decimal]] = {}
    cost = Decimal(0)
    for a in persona.accounts:
        if a.kind not in ("broker_mf", "broker_stocks"):
            continue
        held = holdings.setdefault(a.key, {})
        for commodity, lots in sorted(sim.lots.get(a.key, {}).items()):
            if lots:
                held[commodity] = sum((lot.units for lot in lots), Decimal(0))
                cost += sum((lot.units * lot.cost for lot in lots), Decimal(0))
    net_worth = money(sum(closing.values(), Decimal(0)) + cost)
    return Truth(
        events=events,
        closing=closing,
        net_worth=net_worth,
        holdings=holdings,
        cycles=cycles,
    )


def _reject_overdrafts(persona: Persona, events: list[Event]) -> None:
    """A savings or broker cash account never ends a day below zero; a plan
    that would make one is a persona bug, not a statement to write."""
    for acct in persona.accounts:
        if acct.kind == "card":
            continue
        balance, rows = (
            acct.opening_balance,
            [e for e in events if e.account_key == acct.key],
        )
        for i, e in enumerate(rows):
            balance += e.amount
            end_of_day = i + 1 == len(rows) or rows[i + 1].date != e.date
            if end_of_day and balance < 0:
                raise ValueError(
                    f"{persona.slug}: {acct.key} overdrawn on {e.date}: {balance}"
                )
