"""The five personas: shape, money, realism and the cases each one exercises.

Every persona is simulated with the `stub_lookups` NAV/price fixture. Net
worth is valued at cost, and an MF buy converts cash into units at about the
cash paid (less stamp duty), so the calibration below barely moves when the
stub gives way to real snapshot values.
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from decimal import Decimal
from itertools import combinations

import pytest

from backend.app.service.categorize.dictionary import MerchantDictionary
from scripts.personas.catalog import ALL
from scripts.personas.instruments import HOLIDAYS, SCHEMES, TICKERS
from scripts.personas.merchants import BANK_CHARGES, CATEGORIES, MERCHANTS
from scripts.personas.model import PERIOD_END, PERIOD_START, Persona
from scripts.personas.simulate import DUST_UNITS, simulate
from tests.demo.pii import count_hits, denylist_tokens

D = Decimal
SLUGS = ("p10l", "p30l", "p50l", "p1cr", "p3cr")
TARGETS = {
    "p10l": D("1000000"),
    "p30l": D("3000000"),
    "p50l": D("5000000"),
    "p1cr": D("10000000"),
    "p3cr": D("30000000"),
}
# The app snaps an MF order to its round cash only up to this amount.
MF_BUY_LIMIT = D("1980000")

# slug -> (bank institutions, card institutions, broker (kind, institution)s)
SHAPES: dict[str, tuple[tuple[str, ...], tuple[str, ...], set[tuple[str, str]]]] = {
    "p10l": (("HDFC",), ("ICICI",), {("broker_mf", "Groww")}),
    "p30l": (
        ("HDFC", "IDFC"),
        ("ICICI", "Axis"),
        {("broker_mf", "Groww"), ("broker_stocks", "Groww")},
    ),
    "p50l": (
        ("HDFC", "IDFC", "SBI"),
        ("ICICI", "Axis"),
        {("broker_mf", "Groww"), ("broker_stocks", "Groww")},
    ),
    "p1cr": (
        ("HDFC", "IDFC", "SBI", "Kotak"),
        ("ICICI", "Axis", "HDFC"),
        {
            ("broker_mf", "Groww"),
            ("broker_stocks", "Groww"),
            ("broker_stocks", "Zerodha"),
        },
    ),
    "p3cr": (
        ("HDFC", "IDFC", "SBI", "Kotak"),
        ("ICICI", "Axis", "HDFC"),
        {
            ("broker_mf", "Groww"),
            ("broker_stocks", "Groww"),
            ("broker_stocks", "Zerodha"),
        },
    ),
}


@pytest.fixture(scope="module")
def truths(stub_lookups):
    return {
        slug: simulate(p, nav=stub_lookups.nav, prices=stub_lookups.prices)
        for slug, p in ALL.items()
    }


def kind(truth, *kinds):
    return [e for e in truth.events if e.kind in kinds]


def test_five_personas_exist_in_order():
    assert tuple(ALL) == SLUGS
    assert all(p.slug == slug for slug, p in ALL.items())
    assert {p.net_worth_target for p in ALL.values()} == set(TARGETS.values())
    assert all(ALL[s].net_worth_target == TARGETS[s] for s in SLUGS)
    assert len({p.seed for p in ALL.values()}) == len(ALL)


@pytest.mark.parametrize("slug", SLUGS)
def test_account_shape_matches_the_persona_table(slug):
    banks, cards, brokers = SHAPES[slug]
    accounts = ALL[slug].accounts
    assert tuple(a.institution for a in accounts if a.kind == "bank") == banks
    assert tuple(a.institution for a in accounts if a.kind == "card") == cards
    assert {
        (a.kind, a.institution)
        for a in accounts
        if a.kind in ("broker_mf", "broker_stocks")
    } == brokers
    # Statements and transfer narrations name an account by its last 4.
    assert len({a.last4 for a in accounts}) == len(accounts)
    assert len({a.key for a in accounts}) == len(accounts)


@pytest.mark.parametrize("slug", SLUGS)
def test_net_worth_within_2pct_of_target(slug, truths):
    target = TARGETS[slug]
    assert abs(truths[slug].net_worth - target) <= target * D("0.02"), truths[
        slug
    ].net_worth


@pytest.mark.parametrize("scale", [D("0.85"), D("1.15")])
@pytest.mark.parametrize("slug", SLUGS)
def test_personas_hold_up_when_real_prices_differ_from_the_stub(
    slug, scale, stub_lookups
):
    """Real snapshot values will not match the stub. With every NAV and price
    15% off, no broker or bank account overdraws (stock funding has headroom),
    no redemption sells more than is held, and net worth stays in band."""
    truth = simulate(
        ALL[slug],
        nav=lambda code, on: stub_lookups.nav(code, on) * scale,
        prices=lambda symbol, on: stub_lookups.prices(symbol, on) * scale,
    )
    target = TARGETS[slug]
    assert abs(truth.net_worth - target) <= target * D("0.02")


@pytest.mark.parametrize("slug", SLUGS)
def test_every_transfer_pair_is_between_distinct_banks_and_covers_each_bank_pair(
    slug, truths
):
    persona = ALL[slug]
    banks = [a.key for a in persona.accounts if a.kind == "bank"]
    outs = kind(truths[slug], "transfer_out")
    for e in outs:
        assert e.counter_account_key is not None
        src, dst = (
            persona.account(e.account_key),
            persona.account(e.counter_account_key),
        )
        assert src.kind == dst.kind == "bank"
        assert src.institution != dst.institution
    pairs = {frozenset((e.account_key, e.counter_account_key)) for e in outs}
    assert pairs == {frozenset(p) for p in combinations(banks, 2)}


def test_p50l_has_a_transfer_collision(truths):
    """Two equal transfers between the same two banks on adjacent days, the
    first arriving a day late, so both credits land on the same day for the
    same amount and only the reference tells them apart."""
    truth = truths["p50l"]
    ins = {e.pair_id: e for e in kind(truth, "transfer_in")}
    outs = kind(truth, "transfer_out")
    found = [
        (a, b)
        for a, b in combinations(outs, 2)
        if (a.account_key, a.counter_account_key, a.amount)
        == (b.account_key, b.counter_account_key, b.amount)
        and abs((b.date - a.date).days) == 1
    ]
    assert found
    for a, b in found:
        if ins[a.pair_id].date == ins[b.pair_id].date:
            assert a.ref != b.ref and a.pair_id != b.pair_id
            break
    else:
        pytest.fail("no collision whose two credits land on the same day")


def test_p3cr_has_dust_redemption(truths):
    truth, persona = truths["p3cr"], ALL["p3cr"]
    dusted = []
    for sell in kind(truth, "mf_sell"):
        moved = sum(
            (
                e.units
                for e in kind(truth, "mf_buy", "mf_sell")
                if e.commodity == sell.commodity and e.units is not None
            ),
            D(0),
        )
        if 0 < moved <= DUST_UNITS:
            dusted.append(sell)
            assert sell.commodity not in truth.holdings[sell.account_key]
    assert dusted
    assert any(p.leave_units is not None for p in persona.mf_orders)


def test_p1cr_has_a_partial_mf_redemption(truths):
    truth = truths["p1cr"]
    sells = kind(truth, "mf_sell")
    assert sells
    assert any(truth.holdings[s.account_key].get(s.commodity, 0) > 0 for s in sells)


@pytest.mark.parametrize("slug", SLUGS)
def test_no_mf_buy_exceeds_the_apps_snap_limit(slug, truths):
    buys = kind(truths[slug], "mf_buy")
    assert buys
    assert all(0 < -e.amount <= MF_BUY_LIMIT for e in buys)
    assert all(
        p.amount is None or p.amount <= MF_BUY_LIMIT for p in ALL[slug].mf_orders
    )


def test_p3cr_has_a_large_sip_book_and_an_fd_like_balance(truths):
    persona, truth = ALL["p3cr"], truths["p3cr"]
    buys = kind(truth, "mf_buy")
    assert len({e.commodity for e in buys}) >= 5
    assert len(buys) >= 30
    biggest = max(truth.closing[a.key] for a in persona.accounts if a.kind == "bank")
    assert biggest >= D("10000000")


@pytest.mark.parametrize("slug", SLUGS)
def test_spends_name_a_known_merchant_and_its_category(slug, truths):
    for e in kind(truths[slug], "spend", "refund"):
        assert e.merchant in MERCHANTS, e.merchant
        assert e.expected_category == MERCHANTS[e.merchant].category
    for e in kind(truths[slug], "spend", "refund", "fee", "gst_on_fee"):
        assert e.expected_category in CATEGORIES, e


@pytest.mark.parametrize("slug", SLUGS)
def test_seed_dictionary_never_contradicts_the_expected_category(slug, truths):
    """Where the app's shipped dictionary recognises a row, it must agree with
    the truth; rows that are not spending must not look like a merchant."""
    persona = ALL[slug]
    seed = MerchantDictionary.from_seed()
    for e in truths[slug].events:
        if persona.account(e.account_key).kind not in ("bank", "card"):
            continue
        hit = seed.lookup(e.narration.upper())
        assert hit is None or hit == e.expected_category, (e.narration, hit)


@pytest.mark.parametrize("slug", SLUGS)
def test_amounts_are_mostly_not_round(slug, truths):
    spends = kind(truths[slug], "spend")
    round_ = [e for e in spends if e.amount % 10 == 0]
    assert len(round_) < len(spends) * D("0.25")
    assert len({e.merchant for e in spends}) >= 10


@pytest.mark.parametrize("slug", SLUGS)
def test_fees_cashback_refunds_and_trips_are_present(slug, truths):
    persona, truth = ALL[slug], truths[slug]
    acct_kind = {a.key: a.kind for a in persona.accounts}
    fees = kind(truth, "fee")
    gsts = kind(truth, "gst_on_fee")
    # A card fee charged on a purchase (fuel surcharge) sits beside it and
    # takes its category; the card's own charges (annual fee) are bank charges.
    card_fees = [
        e
        for e in fees
        if acct_kind[e.account_key] == "card" and e.expected_category != BANK_CHARGES
    ]
    bank_fees = [e for e in fees if acct_kind[e.account_key] == "bank"]
    assert card_fees and bank_fees
    for fee in fees:
        assert any(
            g.account_key == fee.account_key
            and g.date == fee.date
            and g.expected_category == fee.expected_category
            for g in gsts
        ), fee
    for fee in card_fees:
        assert any(
            e.account_key == fee.account_key
            and e.date == fee.date
            and e.expected_category == fee.expected_category
            for e in kind(truth, "spend")
        ), fee
    assert all(e.expected_category == BANK_CHARGES for e in bank_fees)
    assert kind(truth, "cashback")
    assert all(e.amount > 0 for e in kind(truth, "cashback", "refund"))
    assert kind(truth, "refund")
    assert persona.trips
    for trip in persona.trips:
        tagged = [e for e in truth.events if trip.tag in e.expected_tags]
        assert len(tagged) >= 3
        assert all(trip.start <= e.date <= trip.end for e in tagged)


def test_p30l_pays_rent_and_takes_two_trips(truths):
    persona, truth = ALL["p30l"], truths["p30l"]
    assert len(persona.trips) == 2
    rent = [e for e in kind(truth, "spend") if e.expected_category == "Expenses:Rent"]
    assert len(rent) == 6


def test_p10l_is_a_first_jobber_with_sips_one_refund_and_one_trip(truths):
    persona, truth = ALL["p10l"], truths["p10l"]
    assert len(persona.trips) == 1 and len(persona.refunds) == 1
    buys = kind(truth, "mf_buy")
    assert len(buys) >= 12 and all(-e.amount <= D("10000") for e in buys)
    assert not kind(truth, "stock_buy", "stock_sell")


def test_instruments_are_listed_and_all_used():
    codes = {p.scheme_code for s in ALL.values() for p in s.mf_orders}
    symbols = {p.symbol for s in ALL.values() for p in s.stock_orders}
    assert 4 <= len(SCHEMES) <= 8 and codes == set(SCHEMES)
    assert 6 <= len(TICKERS) <= 10 and symbols == set(TICKERS)
    for persona in ALL.values():
        for mf in persona.mf_orders:
            assert mf.scheme_name == SCHEMES[mf.scheme_code]
        for st in persona.stock_orders:
            assert st.isin == TICKERS[st.symbol].isin
            assert re.fullmatch(r"INE[0-9A-Z]{6}\d{2}\d", st.isin)


@pytest.mark.parametrize("slug", SLUGS)
def test_orders_fall_on_trading_days(slug):
    persona = ALL[slug]
    for order in [*persona.mf_orders, *persona.stock_orders]:
        assert order.on.weekday() < 5 and order.on not in HOLIDAYS, order


def test_stub_lookups_are_plausible(stub_lookups):
    days = [PERIOD_START + (PERIOD_END - PERIOD_START) * i // 10 for i in range(11)]
    for day in days:
        for code in SCHEMES:
            assert D(10) <= stub_lookups.nav(code, day) <= D(1000)
        for symbol in TICKERS:
            assert D(100) <= stub_lookups.prices(symbol, day) <= D(5000)


# --- no real people ---------------------------------------------------------

PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
MOBILE = re.compile(r"(?<!\d)(?:\+?91[ -]?)?[6-9]\d{9}(?!\d)")


def _strings(value) -> list[str]:
    """Every string inside a persona's plans, recursively."""
    if isinstance(value, str):
        return [value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return [
            s
            for f in dataclasses.fields(value)
            for s in _strings(getattr(value, f.name))
        ]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [s for v in value for s in _strings(v)]
    return []


def _persona_text(persona: Persona, truth) -> list[str]:
    # A reference is RNG output, like an amount: it is removed before
    # matching, so a short denylist token cannot collide with it by chance.
    narrations = [
        e.narration.replace(e.ref, " ") if e.ref else e.narration for e in truth.events
    ]
    return [*_strings(persona), *narrations]


def _luhn_ok(number: str) -> bool:
    digits = [int(c) for c in reversed(number)]
    total = sum(
        d if i % 2 == 0 else (d * 2 - 9 if d > 4 else d * 2)
        for i, d in enumerate(digits)
    )
    return total % 10 == 0


def _all_text(truths) -> dict[str, list[str]]:
    merchant_text = [v for m in MERCHANTS.values() for v in m.narration_variants]
    return {
        slug: [*_persona_text(persona, truths[slug]), *merchant_text]
        for slug, persona in ALL.items()
    }


def test_no_real_person_strings(truths):
    tokens = denylist_tokens()
    # Counted, never shown: a failure must not print a real token, so no
    # assertion here carries the token or the string it was found in. Both
    # sides are lower-cased: narrations are upper-case, tokens may not be.
    hits = Counter(
        {
            slug: n
            for slug, text in _all_text(truths).items()
            if (n := count_hits(text, tokens))
        }
    )
    if hits:
        pytest.fail(f"denylist tokens found in persona strings: {dict(hits)}")


def test_no_pii_shaped_strings(truths):
    for slug, text in _all_text(truths).items():
        for s in text:
            assert not PAN.search(s), f"{slug}: PAN-like {s!r}"
            assert not IFSC.search(s), f"{slug}: IFSC-like {s!r}"
            assert not MOBILE.search(s), f"{slug}: phone-like {s!r}"
        for acct in ALL[slug].accounts:
            if acct.kind == "card":
                # Failing the Luhn check, the number cannot be a real card.
                assert len(acct.number) == 16 and not _luhn_ok(acct.number)
    names = Counter(p.display_name for p in ALL.values())
    employers = Counter(p.employer for p in ALL.values())
    assert max(names.values()) == 1 and max(employers.values()) == 1


@pytest.mark.parametrize("slug", SLUGS)
def test_every_redemption_is_paid_out_once_to_a_bank(slug, truths):
    """Groww pays redemption proceeds into the linked bank account, so the MF
    account ends with no cash: every sell has exactly one payout pair, for
    the same amount, broker to bank, two trading days later."""
    persona, truth = ALL[slug], truths[slug]
    payouts = kind(truth, "mf_redemption_payout")
    sells = kind(truth, "mf_sell")
    assert len(payouts) == 2 * len(sells)
    for sell in sells:
        assert sell.pair_id is not None
        legs = [e for e in payouts if e.pair_id == sell.pair_id]
        assert len(legs) == 2, sell
        [out] = [e for e in legs if e.amount < 0]
        [inn] = [e for e in legs if e.amount > 0]
        assert out.account_key == sell.account_key and out.amount == -sell.amount
        assert persona.account(inn.account_key).kind == "bank"
        assert inn.amount == sell.amount and inn.ref == out.ref
        assert out.counter_account_key == inn.account_key
        assert inn.counter_account_key == out.account_key
        assert out.date == inn.date > sell.date
        days = [
            sell.date.fromordinal(o)
            for o in range(sell.date.toordinal() + 1, inn.date.toordinal() + 1)
        ]
        trading = [d for d in days if d.weekday() < 5 and d not in HOLIDAYS]
        assert len(trading) == 2 and trading[-1] == inn.date
    for acct in persona.accounts:
        if acct.kind == "broker_mf":
            assert truth.closing[acct.key] == 0, acct.key
