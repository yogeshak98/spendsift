"""The rule book and checker, on data dirs built by the app's own import path.

The data dirs come from `scripts.personas.importer.import_persona_files`
(seed, open accounts, detect, openings from the first-import prompt, commit)
over a persona's
generated files, into `tmp_path`, with the app built by
`create_app(storage_dir=tmp)`. Two imports of the Task 1 tiny persona are used:

- **defaults**: every row committed as the app proposes it (what QA does);
- **reviewed**: a reviewing user's pass through the review routes before each
  commit. The user pairs own-bank transfers (picks the other account on the
  first leg, drops the second leg), and categorises income, fees and
  cashback. Spends are left to the app, so the one merchant the seed
  dictionary does not know stays uncategorised.

On the reviewed journal every live rule passes, so each mutation below (an
edit to a copy of its journal) shows its rule can fail. One real persona
(p1cr, which holds Zerodha stocks and has a Groww MF payout) pins the
per-persona pending causes; its tests are opt-in (`tests/demo/optin.py`).
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.settings import get_settings
from backend.app.server.main import create_app
from scripts.personas import check, snapshot
from scripts.personas.build import build
from scripts.personas.catalog import ALL
from scripts.personas.check import AppView, label, run_view, status_of
from scripts.personas.importer import ImportedFile, import_persona_files
from scripts.personas.instruments import SCHEMES, stock, trading_day
from scripts.personas.model import (
    Account,
    CashbackPlan,
    Event,
    MfOrderPlan,
    Persona,
    TransferPlan,
    Truth,
)
from scripts.personas.naming import PLATFORM_CASH as WITH_ZERODHA
from scripts.personas.naming import PLATFORM_ENV as ZERODHA_ENV
from scripts.personas.naming import account_name
from scripts.personas.rules import (
    OPENING_FROM_STATEMENT,
    REDEMPTION_SETTLEMENT,
    RULES,
    TRANSFER_MATCHING,
    UNCATEGORISED_FALLBACK,
    ZERODHA_READER,
    Rule,
    RuleResult,
    payout_pending,
    rule,
    truncated_paise,
    zerodha_accounts,
)
from scripts.personas.simulate import simulate
from tests.demo import optin
from tests.demo.test_simulate import TINY

D = Decimal
CLEARING = get_settings().card_clearing_account


def _demo_tiny() -> Persona:
    """The Task 1 tiny persona, made importable and given the cases its plan
    lacks. Its accounts take p30l's numbers (the committed templates and PDF
    templates know those), its MF orders the AMFI-listed scheme name and
    trading days (the snapshot has no NAV on a weekend). Added: a cashback, a
    Groww stock buy, and a redemption of all but 0.002 units (dust)."""
    numbers = {a.key: a.number for a in ALL["p30l"].accounts}
    accounts = [dataclasses.replace(a, number=numbers[a.key]) for a in TINY.accounts]
    accounts.append(
        Account("growwst", "broker_stocks", "Groww", "Stocks", numbers["growwst"], D(0))
    )
    orders = [
        dataclasses.replace(
            o,
            scheme_name=SCHEMES[o.scheme_code],
            on=trading_day(o.on.year, o.on.month, o.on.day),
        )
        for o in TINY.mf_orders
    ]
    code = TINY.mf_orders[0].scheme_code
    orders.append(
        MfOrderPlan(
            "groww",
            code,
            SCHEMES[code],
            "sell",
            date(2026, 3, 20),
            leave_units=D("0.002"),
        )
    )
    return dataclasses.replace(
        TINY,
        accounts=accounts,
        mf_orders=orders,
        transfers=[
            *TINY.transfers,
            TransferPlan(
                "hdfc", "growwst", D("10000.00"), date(2025, 11, 3), rail="UPI"
            ),
        ],
        stock_orders=[stock("growwst", "HDFCBANK", "buy", 10, date(2025, 11, 3))],
        cashbacks=[
            CashbackPlan(
                "hdfc",
                date(2025, 12, 10),
                D("75.00"),
                "UPI-GOOGLE PAY-goog-payment@okaxis-{ref}-REWARD",
            )
        ],
    )


DEMO = _demo_tiny()


@pytest.fixture(scope="module")
def truth() -> Truth:
    return simulate(DEMO, nav=snapshot.load_nav(), prices=snapshot.load_prices())


@pytest.fixture(scope="module")
def files(tmp_path_factory, truth) -> list[Path]:
    return build(DEMO, tmp_path_factory.mktemp("tiny-files"), truth=truth)


@pytest.fixture(autouse=True)
def default_settings(monkeypatch):
    """Every test starts from the app's default settings."""
    monkeypatch.delenv(ZERODHA_ENV, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- a reviewing user ------------------------------------------------------------


def _signed(row: dict) -> Decimal:
    amount = D(str(row["amount"]))
    return -amount if row["direction"] == "debit" else amount


class Reviewer:
    """What a careful user does in review, through the review routes: on the
    first leg of an own-bank transfer, pick the other account; drop the
    second leg when its own statement comes in; set income, fee and cashback
    categories the app missed. It never touches a spend or a card bill."""

    def __init__(self, persona: Persona, truth: Truth) -> None:
        self.names = {a.key: account_name(a) for a in persona.accounts}
        self.rows: dict[tuple[str, str, Decimal], list[Event]] = {}
        for e in truth.events:
            key = (self.names[e.account_key], e.date.isoformat(), e.amount)
            self.rows.setdefault(key, []).append(e)
        self.recorded: set[str] = set()

    def __call__(self, client, persona: Persona, path: Path, body: dict) -> None:
        batch = body["batch_id"]
        for row in body["candidates"]:
            key = (row["source_account"], row["date"], _signed(row))
            events = self.rows.get(key)
            if not events:
                continue
            e = events.pop(0)
            url = f"/api/review/{batch}/{row['id']}"
            if e.kind in ("transfer_out", "transfer_in"):
                if e.pair_id in self.recorded:
                    assert client.delete(url).status_code == 200
                    continue
                self.recorded.add(e.pair_id or "")
                patch = {"suggested_other_leg": self.names[e.counter_account_key or ""]}
            elif e.kind in ("salary", "interest", "fee", "gst_on_fee"):
                if row["suggested_category"] == e.expected_category:
                    continue
                patch = {"suggested_category": e.expected_category}
            elif e.kind == "cashback":
                patch = {"suggested_category": "Income:Cashback"}
            else:
                continue
            response = client.post(url, json=patch)
            assert response.status_code == 200, response.text


def _import(
    data: Path, persona: Persona, paths: list[Path], review=None
) -> list[ImportedFile]:
    data.mkdir(parents=True)
    snapshot.seed_data_dir(data)
    client = TestClient(create_app(storage_dir=data))
    return import_persona_files(client, persona, paths, review=review)


@pytest.fixture(scope="module")
def defaults_dir(tmp_path_factory, files) -> Path:
    data = tmp_path_factory.mktemp("tiny-defaults") / "data"
    done = _import(data, DEMO, files)
    assert [r.path.name for r in done if r.refused] == [], done
    return data


@pytest.fixture(scope="module")
def reviewed_dir(tmp_path_factory, files, truth) -> Path:
    data = tmp_path_factory.mktemp("tiny-reviewed") / "data"
    done = _import(data, DEMO, files, review=Reviewer(DEMO, truth))
    assert [r.path.name for r in done if r.refused] == [], done
    return data


def results_by_id(view: AppView, truth: Truth, persona: Persona = DEMO, **kw):
    return {r.id: res for r, res in run_view(view, persona, truth, **kw)}


@pytest.fixture(scope="module")
def reviewed(reviewed_dir, truth) -> dict[str, RuleResult]:
    return results_by_id(AppView.from_data_dir(reviewed_dir), truth)


@pytest.fixture(scope="module")
def defaults(defaults_dir, truth) -> dict[str, RuleResult]:
    return results_by_id(AppView.from_data_dir(defaults_dir), truth)


# --- journal mutations on a copy -------------------------------------------------

_BLOCK = re.compile(r"(?m)^(?=\d{4}-\d{2}-\d{2} [*!] )")


def mutate(
    src: Path,
    dst: Path,
    pick: Callable[[str], bool],
    change: Callable[[str], str],
    *,
    ledger: str = "main",
    last: bool = False,
) -> Path:
    """Copy the data dir to `dst` and rewrite the first (or `last`)
    transaction block of the main (or investment) ledger that `pick`
    selects."""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("snapshots"))
    names = (
        ("2025", "2026")
        if ledger == "main"
        else ("investments-2025", "investments-2026")
    )
    hits = []
    for name in names:
        path = dst / "journal" / f"{name}.beancount"
        if not path.exists():
            continue
        blocks = _BLOCK.split(path.read_text(encoding="utf-8"))
        hits += [(path, i) for i, b in enumerate(blocks) if b.strip() and pick(b)]
    if not hits:
        raise AssertionError("no transaction matched the mutation")
    path, i = hits[-1] if last else hits[0]
    blocks = _BLOCK.split(path.read_text(encoding="utf-8"))
    new = change(blocks[i])
    assert new != blocks[i], "the mutation changed nothing"
    blocks[i] = new
    path.write_text("".join(blocks), encoding="utf-8")
    return dst


def append(src: Path, dst: Path, text: str, *, ledger: str = "2025") -> Path:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("snapshots"))
    path = dst / "journal" / f"{ledger}.beancount"
    path.write_text(path.read_text(encoding="utf-8") + "\n" + text, encoding="utf-8")
    return dst


def _shift(block: str, delta: Decimal) -> str:
    """Move every INR posting of a two-posting block `delta` further from 0."""

    def bump(m: re.Match) -> str:
        value = D(m.group(1))
        return f"{value + (delta if value > 0 else -delta)} INR"

    return re.sub(r"(?<=\s)(-?\d+(?:\.\d+)?) INR", bump, block)


def _drop(block: str) -> str:
    return ""


def _lot_cost_up(src: Path, dst: Path) -> Path:
    """Raise the first lot's cost by 0.0001 wherever the journal names it
    (its purchase and the redemption that consumes it), taking the
    difference off the purchase's stamp duty so every entry still balances."""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("snapshots"))
    first = dst / "journal" / "investments-2025.beancount"
    text = first.read_text(encoding="utf-8")
    m = re.search(r"(\d+\.\d+) MF\d+ \{(\d+\.\d+) INR, (\d{4}-\d\d-\d\d)\}", text)
    assert m
    units, cost, day = D(m.group(1)), D(m.group(2)), m.group(3)
    s = re.search(r"Charges:StampDuty\s+(\d+\.\d+) INR", text)
    assert s
    stamp = D(s.group(1))
    text = text.replace(f"{stamp} INR", f"{stamp - units * D('0.0001')} INR", 1)
    first.write_text(text, encoding="utf-8")
    for path in (dst / "journal").glob("investments-*.beancount"):
        lot = f"{{{cost} INR, {day}}}"
        new = path.read_text(encoding="utf-8").replace(
            lot, f"{{{cost + D('0.0001')} INR, {day}}}"
        )
        path.write_text(new, encoding="utf-8")
    return dst


def _leave_dust(block: str) -> str:
    """Redeem 0.004 fewer units from the last lot the redemption consumes."""
    lots = re.findall(r"(-\d+\.\d+) (MF\d+) \{", block)
    assert lots
    units, code = lots[-1]
    return block.replace(f"{units} {code}", f"{D(units) + D('0.004')} {code}", 1)


def _fewer_shares(block: str) -> str:
    """Hold one share fewer, at the same cost, against the same wallet cash."""
    m = re.search(r"(\d+) HDFCBANK \{(\d+\.\d+) INR", block)
    assert m
    shares, cost = int(m.group(1)), D(m.group(2))
    block = block.replace(f"{shares} HDFCBANK {{", f"{shares - 1} HDFCBANK {{", 1)
    return block.rstrip("\n") + f"\n  Expenses:Uncategorized  {cost} INR\n\n"


def _twice(block: str) -> str:
    """Book the block a second time, txn-id and all: what a content-derived
    re-import of the same row writes."""
    block = block if block.endswith("\n\n") else block.rstrip("\n") + "\n\n"
    return block + block


def _flip(block: str) -> str:
    """Negate every INR posting: a spend turned into a negative expense."""
    return re.sub(
        r"(?<=\s)(-?\d+(?:\.\d+)?) INR", lambda m: f"{-D(m.group(1))} INR", block
    )


_DOUBLE_IN_LEG = """
2025-10-11 * "IMPS/P2A/DUPLICATE/FICTIVE TESTER/HDFC/XX7462"
  txn-id: "0000000000000000000000000000dup1"
  Assets:Checking:IDFC-5519   25000.00 INR
  Expenses:Uncategorized     -25000.00 INR
"""

MUTATIONS: dict[str, tuple[Callable[[Path, Path], Path], str]] = {
    # rule id -> (corrupt a copy, a fragment of the failure it must print).
    # Each runs on the reviewed journal, except re-import (see BASE).
    "balances_match_statements": (
        lambda s, d: mutate(
            s, d, lambda b: "BIGBASKET" in b, lambda b: _shift(b, D(1))
        ),
        "Assets:Checking:HDFC-7462: journal",
    ),
    "net_worth_matches": (
        lambda s, d: mutate(
            s, d, lambda b: "BIGBASKET" in b, lambda b: _shift(b, D(1))
        ),
        # The rupee taken, plus the redemption's truncated paise, which are
        # excused only when they are the whole difference.
        "off by -1.78",
    ),
    "transfers_pair_once": (
        lambda s, d: mutate(
            s,
            d,
            lambda b: "-25000" in b and "IDFC-5519" in b,
            lambda b: b.replace("Assets:Checking:IDFC-5519", "Expenses:Uncategorized "),
        ),
        "no transaction carries both legs",
    ),
    "refund_pairs_with_purchase": (
        lambda s, d: mutate(
            s,
            d,
            lambda b: "REFUND" in b,
            lambda b: b.replace("Expenses:Shopping", "Income:Salary"),
        ),
        "refund booked as income",
    ),
    "platform_funding_to_platform_cash": (
        lambda s, d: mutate(
            s,
            d,
            lambda b: "ADD FUNDS" in b,
            lambda b: b.replace("Assets:Broker:Groww", "Assets:Transfers:InTransit"),
        ),
        "platform cash Assets:Broker:Groww",
    ),
    "mf_cash_snaps_and_stamp_booked": (
        lambda s, d: mutate(
            s,
            d,
            lambda b: "Purchase" in b,
            lambda b: b.replace("Charges:StampDuty", "Charges:Groww"),
            ledger="investments",
        ),
        "stamp duty 0",
    ),
    "mf_units_from_exact_nav": (
        _lot_cost_up,
        ": lot [(",
    ),
    "mf_dust_redeems_all": (
        lambda s, d: mutate(
            s, d, lambda b: "Redeem" in b, _leave_dust, ledger="investments"
        ),
        "units left after redeeming all but",
    ),
    "broker_stock_holdings_imported": (
        lambda s, d: mutate(s, d, lambda b: "HDFCBANK {" in b, _fewer_shares),
        "HDFCBANK: journal 9, truth 10",
    ),
    "reimport_is_all_duplicates": (
        lambda s, d: mutate(s, d, lambda b: "BIGBASKET" in b, _drop),
        "row(s) stage as new",
    ),
    "expenses_positive": (
        lambda s, d: mutate(s, d, lambda b: "SWIGGY" in b, _flip),
        "Expenses:Food -",
    ),
    "fees_categorised": (
        lambda s, d: mutate(
            s,
            d,
            lambda b: "IGST" in b,
            lambda b: b.replace("Expenses:Interest", "Expenses:Food    "),
        ),
        "its charge to ['Expenses:Interest']",
    ),
    "income_is_income": (
        lambda s, d: mutate(
            s,
            d,
            lambda b: "REWARD" in b,
            lambda b: b.replace("Income:Cashback", "Expenses:Food  "),
        ),
        "a credit booked as an expense",
    ),
    "known_merchants_categorised": (
        lambda s, d: mutate(
            s,
            d,
            lambda b: "SWIGGY" in b,
            lambda b: b.replace("Expenses:Food", "Expenses:Rent"),
        ),
        "truth Expenses:Food",
    ),
    # Live since fix round 1 (R2): only the uncategorised rows wait for the
    # seed, only singly booked bill legs for the card-chart merge.
    "all_merchants_categorised": (
        lambda s, d: mutate(
            s,
            d,
            lambda b: "SWIGGY" in b,
            lambda b: b.replace("Expenses:Food", "Expenses:Rent"),
        ),
        "booked to ['Expenses:Rent'], truth Expenses:Food",
    ),
    "all_income_categorised": (
        lambda s, d: mutate(
            s,
            d,
            lambda b: "SALARY OCT" in b,
            lambda b: b.replace("Income:Salary", "Income:Interest"),
        ),
        "booked to ['Income:Interest'], truth Income:Salary",
    ),
    "card_bill_pairs": (
        lambda s, d: mutate(s, d, lambda b: "PAYMENT RECEIVED" in b, _drop),
        "card leg in 0 transaction(s)",
    ),
    "no_wrong_tags": (
        lambda s, d: mutate(s, d, lambda b: "SWIGGY" in b, lambda b: _tag(b, "gift")),
        "tag(s) ['gift'] the truth does not give",
    ),
}
_WALLET_HALF = """
2025-12-15 * "SCRATCH WALLET CREDIT"
  txn-id: "0000000000000000000000000000hlf1"
  Assets:Broker:Groww        0.50 INR
  Equity:Opening-Balances   -0.50 INR
"""
_CLEARING_HALF = """
2025-12-15 * "SCRATCH CLEARING CREDIT"
  txn-id: "0000000000000000000000000000hlf2"
  Assets:Investment:MutualFund:Groww:Cash   0.50 INR
  Expenses:Investment:Charges:Groww        -0.50 INR
"""
_STRAY_UNCATEGORISED = """
2025-12-15 * "SCRATCH CREDIT NOT ON ANY STATEMENT"
  txn-id: "0000000000000000000000000000unc1"
  Assets:Checking:HDFC-7462   50.00 INR
  Expenses:Uncategorized     -50.00 INR
"""


_CLEARING_LEFT = f"""
2025-12-15 * "SCRATCH MONEY LEFT ON THE CARD CLEARING ACCOUNT"
  txn-id: "0000000000000000000000000000clr1"
  {CLEARING}        500.00 INR
  Equity:Opening-Balances   -500.00 INR
"""
_CLEARING_CHILD = f"""
2025-12-15 * "SCRATCH MONEY ON A CHILD OF THE CARD CLEARING ACCOUNT"
  txn-id: "0000000000000000000000000000clr2"
  {CLEARING}:Stray        500.00 INR
  Equity:Opening-Balances   -500.00 INR
"""


def _tag(block: str, tag: str) -> str:
    """Add `#tag` to a transaction's header line."""
    head, _, rest = block.partition("\n")
    return f"{head} #{tag}\n{rest}"


def _bank_bill_leg(block: str) -> bool:
    return CLEARING in block and "Assets:Checking:" in block


def _card_bill_leg(block: str) -> bool:
    return CLEARING in block and "Liabilities:CreditCard:" in block


def _cash_off_by_a_rupee(block: str) -> str:
    """Book 4,999 cash for a 5,000 purchase, with the rupee off the stamp."""
    s = re.search(r"Charges:StampDuty\s+(\d+\.\d+) INR", block)
    assert s
    stamp = D(s.group(1))
    block = block.replace("-5000.00 INR", "-4999.00 INR", 1)
    return block.replace(f"{stamp} INR", f"{stamp - 1} INR", 1)


# Sub-check cases (review I1): each loosening of one check in rules.py is
# caught by at least one of these. (case id, rule id, corrupt, fragment).
SUBCHECKS: list[tuple[str, str, Callable[[Path, Path], Path], str]] = [
    (
        "wallet_half_rupee",
        "balances_match_statements",
        lambda s, d: append(s, d, _WALLET_HALF),
        "Assets:Broker:Groww: journal",
    ),
    (
        "clearing_half_rupee",
        "balances_match_statements",
        lambda s, d: append(s, d, _CLEARING_HALF, ledger="investments-2025"),
        "Assets:Investment:MutualFund:Groww:Cash: journal",
    ),
    (
        "net_worth_half_rupee",
        "net_worth_matches",
        lambda s, d: append(s, d, _WALLET_HALF),
        # 0.50 against the 0.78 explained residue: neither is the whole gap.
        "off by -0.28",
    ),
    (
        "interest_to_salary",
        "income_is_income",
        lambda s, d: mutate(
            s,
            d,
            lambda b: "INTEREST CREDIT" in b,
            lambda b: b.replace("Income:Interest", "Income:Salary  "),
        ),
        "truth Income:Interest",
    ),
    (
        "uncategorised_not_a_credit",
        "expenses_positive",
        lambda s, d: append(s, d, _STRAY_UNCATEGORISED),
        "Expenses:Uncategorized -50.00 'SCRATCH CREDIT",
    ),
    (
        "salary_as_negative_food",
        "expenses_positive",
        lambda s, d: mutate(
            s,
            d,
            lambda b: "SALARY OCT" in b,
            lambda b: b.replace("Income:Salary", "Expenses:Food"),
        ),
        "Expenses:Food -98,400.00",
    ),
    (
        "transfer_leg_as_salary",
        "transfers_pair_once",
        lambda s, d: mutate(
            s,
            d,
            lambda b: "IMPS/P2A/" in b and "25000" in b,
            lambda b: b.replace("Expenses:Uncategorized", "Income:Salary         "),
        ),
        "in leg booked against ['Income:Salary']",
    ),
    (
        "transfer_leg_to_categorised_expense",
        "transfers_pair_once",
        lambda s, d: mutate(
            s,
            d,
            lambda b: "IDFB-XXXXXXXX5519-TRANSFER" in b and "25000" in b,
            lambda b: b.replace("Expenses:Uncategorized", "Expenses:Shopping     "),
        ),
        "out leg booked against ['Expenses:Shopping']",
    ),
    (
        "mf_cash_4999",
        "mf_cash_snaps_and_stamp_booked",
        lambda s, d: mutate(
            s, d, lambda b: "Purchase" in b, _cash_off_by_a_rupee, ledger="investments"
        ),
        "cash -4,999.00, truth -5,000.00",
    ),
    (
        "redemption_dropped",
        "mf_units_from_exact_nav",
        lambda s, d: mutate(s, d, lambda b: "Redeem" in b, _drop, ledger="investments"),
        "units held {'120716'",
    ),
    (
        "refund_to_other_category",
        "refund_pairs_with_purchase",
        lambda s, d: mutate(
            s,
            d,
            lambda b: "REFUND" in b,
            lambda b: b.replace("Expenses:Shopping", "Expenses:Food    "),
        ),
        "its purchase to ['Expenses:Shopping']",
    ),
    (
        "card_bill_leg_twice",
        "card_bill_pairs",
        lambda s, d: mutate(s, d, _card_bill_leg, _twice),
        "card leg in 2 transaction(s)",
    ),
    (
        "card_bill_bank_leg_twice",
        "card_bill_pairs",
        lambda s, d: mutate(s, d, _bank_bill_leg, _twice),
        "bank leg in 2 transaction(s)",
    ),
    (
        "card_bill_bank_leg_dropped",
        "card_bill_pairs",
        lambda s, d: mutate(s, d, _bank_bill_leg, _drop),
        "bank leg in 0 transaction(s)",
    ),
    (
        "card_bill_card_leg_shifted",
        "card_bill_pairs",
        lambda s, d: mutate(s, d, _card_bill_leg, lambda b: _shift(b, D(1))),
        f"{CLEARING} holds -1.00, truth 0.00",
    ),
    (
        "card_bill_bank_leg_shifted",
        "card_bill_pairs",
        lambda s, d: mutate(s, d, _bank_bill_leg, lambda b: _shift(b, D(1))),
        f"{CLEARING} holds 1.00, truth 0.00",
    ),
    (
        "card_bill_card_leg_not_on_clearing",
        "card_bill_pairs",
        lambda s, d: mutate(
            s,
            d,
            _card_bill_leg,
            lambda b: b.replace(CLEARING, "Expenses:Uncategorized"),
        ),
        f"card leg booked against ['Expenses:Uncategorized'], not {CLEARING}",
    ),
    (
        "card_bill_bank_leg_not_on_clearing",
        "card_bill_pairs",
        lambda s, d: mutate(
            s, d, _bank_bill_leg, lambda b: b.replace(CLEARING, "Expenses:Shopping")
        ),
        f"bank leg booked against ['Expenses:Shopping'], not {CLEARING}",
    ),
    (
        "payment_received_as_negative_expense",
        "expenses_positive",
        lambda s, d: mutate(
            s,
            d,
            _card_bill_leg,
            lambda b: b.replace(CLEARING, "Expenses:Uncategorized"),
        ),
        "Expenses:Uncategorized -",
    ),
    (
        "clearing_left_over",
        "balances_match_statements",
        lambda s, d: append(s, d, _CLEARING_LEFT),
        f"{CLEARING}: 500.00 left on the card clearing account, truth 0.00",
    ),
    (
        "clearing_child_left_over",
        "balances_match_statements",
        lambda s, d: append(s, d, _CLEARING_CHILD),
        f"{CLEARING}:Stray: 500.00 on an account the persona does not have",
    ),
    (
        "trip_tag_off_trip",
        "no_wrong_tags",
        lambda s, d: mutate(
            s, d, lambda b: "BIGBASKET" in b, lambda b: _tag(b, "trip-goa-2026")
        ),
        "tag(s) ['trip-goa-2026'] the truth does not give",
    ),
    (
        "subscription_tag_on_a_spend",
        "no_wrong_tags",
        lambda s, d: mutate(
            s, d, lambda b: "BIGBASKET" in b, lambda b: _tag(b, "subscription")
        ),
        "tag(s) ['subscription'] the truth does not give",
    ),
    (
        "last_known_spend",
        "known_merchants_categorised",
        lambda s, d: mutate(
            s,
            d,
            lambda b: "SWIGGY" in b,
            lambda b: b.replace("Expenses:Food", "Expenses:Rent"),
            last=True,
        ),
        "truth Expenses:Food",
    ),
]

# The reviewed journal re-imports with the dropped transfer legs as new rows
# (see `test_a_leg_dropped_in_review_comes_back_on_reimport`), so re-import
# is mutated from the defaults journal, where it passes.
BASE = {
    "reimport_is_all_duplicates": "defaults",
    # Today's own-transfer booking (each leg alone against Uncategorized) is
    # only in the defaults journal; the reviewer paired the transfers.
    "transfer_leg_as_salary": "defaults",
    "transfer_leg_to_categorised_expense": "defaults",
}
# Live rules that cannot be broken by a journal edit here, and why.
NOT_MUTATED = {
    "mf_redemption_payout_clears": (
        "not applicable to the tiny persona (no payout); mutated on p1cr in"
        " test_p1cr_failures_beyond_the_waived_signatures_are_real"
    ),
}


def test_every_live_rule_has_a_mutation():
    live = {r.id for r in RULES if r.pending is None or callable(r.pending)}
    live -= {"tags_appropriate"}  # a --live rule: its own test below
    assert live == set(MUTATIONS) | set(NOT_MUTATED)


CASES = [(rid, rid, *MUTATIONS[rid]) for rid in sorted(MUTATIONS)] + SUBCHECKS


def test_every_live_rule_has_no_real_failure_on_its_base_journal(reviewed, defaults):
    """The mutations start from a journal where their rule has no real
    failure (it may be PENDING on a waived cause, or UNEXPECTED PASS where
    the reviewer did by hand what a pending feature will do)."""
    bases = {"reviewed": reviewed, "defaults": defaults}
    for rid in MUTATIONS:
        result = bases[BASE.get(rid, "reviewed")][rid]
        assert not result.failures, (rid, result.detail, result.failures[:3])
        assert status_of(result) != "FAIL", rid
    failing = {rid for rid, res in reviewed.items() if status_of(res) == "FAIL"}
    assert failing == {"reimport_is_all_duplicates"}


def test_the_reviewer_does_by_hand_what_two_pending_features_will(reviewed):
    """Pairing own-bank transfers and categorising income in review leaves
    those causes with nothing to wait for: the runner says so loudly."""
    assert label(reviewed["transfers_pair_once"]) == (
        f"UNEXPECTED PASS (was waiting for: {TRANSFER_MATCHING})"
    )
    assert label(reviewed["income_is_income"]) == (
        f"UNEXPECTED PASS (was waiting for: {UNCATEGORISED_FALLBACK})"
    )
    # A card's payment received now clears through the card clearing account
    # (upstream 18b71dd), so after review no uncategorised credit is left:
    # the transfer in-legs the fallback waits for were paired by hand.
    assert label(reviewed["expenses_positive"]) == (
        f"UNEXPECTED PASS (was waiting for: {UNCATEGORISED_FALLBACK})"
    )


def test_a_leg_dropped_in_review_comes_back_on_reimport(reviewed, truth):
    """The app proposes nothing for an own-bank transfer, so the reviewer
    recorded each once by dropping its second leg. Re-importing the
    statement stages exactly those dropped legs again: dedup does not
    recognise them as part of the recorded transfer."""
    dropped = [
        e
        for e in truth.events
        if e.kind in ("transfer_out", "transfer_in") and e.account_key == "idfc"
    ]
    # hdfc is imported first; of idfc's legs, the 2 in-legs from hdfc and the
    # 1 out-leg to hdfc (whose in-leg hdfc's statement already carried).
    assert len(dropped) == 3
    assert reviewed["reimport_is_all_duplicates"].failures == [
        "idfc_5519_2026-03-31.xlsx: 3 of 5 row(s) stage as new"
    ]


@pytest.mark.parametrize(
    ("case", "rule_id", "corrupt", "fragment"), CASES, ids=[c[0] for c in CASES]
)
def test_mutation_breaks_its_rule(
    case, rule_id, corrupt, fragment, reviewed_dir, defaults_dir, truth, tmp_path
):
    defaults_base = BASE.get(case, BASE.get(rule_id)) == "defaults"
    base = defaults_dir if defaults_base else reviewed_dir
    data = corrupt(base, tmp_path / "data")
    result = results_by_id(AppView.from_data_dir(data), truth, only={rule_id})[rule_id]
    assert status_of(result) == "FAIL", (result.detail, result.failures)
    text = "\n".join([result.detail, *result.failures])
    assert fragment in text, text


# --- the prescribed tests -------------------------------------------------------


def test_rule_fails_when_balance_off(reviewed_dir, truth, tmp_path):
    data = mutate(
        reviewed_dir,
        tmp_path / "data",
        lambda b: "SWIGGY" in b,
        lambda b: _shift(b, D("0.01")),
    )
    result = results_by_id(
        AppView.from_data_dir(data), truth, only={"balances_match_statements"}
    )["balances_match_statements"]
    assert status_of(result) == "FAIL"
    want = truth.closing["icici"] - D("0.01")
    assert result.failures == [
        f"Liabilities:CreditCard:ICICIAmazonPay-6807: journal {want:,.2f},"
        f" statement {truth.closing['icici']:,.2f}"
    ]


def test_transfers_pair_once_fails_on_orphan_leg(
    reviewed, reviewed_dir, truth, tmp_path
):
    base = reviewed["transfers_pair_once"]
    assert base.failures == [] and base.waived == {}
    corrupt, _ = MUTATIONS["transfers_pair_once"]
    data = corrupt(reviewed_dir, tmp_path / "data")
    result = results_by_id(
        AppView.from_data_dir(data), truth, only={"transfers_pair_once"}
    )["transfers_pair_once"]
    assert result.failures == [
        "P0002 hdfc→idfc 25,000.00 on 2025-10-10: no transaction carries both legs"
        " (out leg in 1, in leg in 0)"
    ]


def test_transfers_pair_once_fails_on_double_booking(reviewed_dir, truth, tmp_path):
    data = append(reviewed_dir, tmp_path / "data", _DOUBLE_IN_LEG)
    result = results_by_id(
        AppView.from_data_dir(data), truth, only={"transfers_pair_once"}
    )["transfers_pair_once"]
    assert result.failures == [
        "P0002 hdfc→idfc 25,000.00 on 2025-10-10: double-booked, legs also in 1 other"
        " transaction(s)"
    ]


def test_transfers_wait_for_own_account_matching_under_app_defaults(defaults):
    """Today the app proposes no other leg for an own-bank transfer, so each
    side commits on its own: exactly the waived signature, nothing real."""
    result = defaults["transfers_pair_once"]
    assert label(result) == f"PENDING (waits for: {TRANSFER_MATCHING})"
    assert result.failures == []
    assert len(result.waived[TRANSFER_MATCHING]) == 3
    assert all(
        "no transaction carries both legs (out leg in 1, in leg in 1)" in f
        for f in result.waived[TRANSFER_MATCHING]
    )


def test_only_uncategorised_credits_wait_for_the_fallback(defaults):
    """R2: under app defaults every negative expense is an uncategorised
    credit row, so the expense and income rules are PENDING with no real
    failure."""
    for rid in ("expenses_positive", "income_is_income"):
        result = defaults[rid]
        assert label(result) == f"PENDING (waits for: {UNCATEGORISED_FALLBACK})", rid
        assert result.failures == [], rid
        waived = result.waived[UNCATEGORISED_FALLBACK]
        assert waived and all("Expenses:Uncategorized" in f for f in waived), rid


def test_a_negative_categorised_expense_fails_beside_pending_credits(
    defaults_dir, truth, tmp_path
):
    """R2: the pending cause does not swallow a real failure."""
    data = mutate(defaults_dir, tmp_path / "data", lambda b: "SWIGGY" in b, _flip)
    result = results_by_id(
        AppView.from_data_dir(data), truth, only={"expenses_positive"}
    )["expenses_positive"]
    assert status_of(result) == "FAIL"
    assert any("Expenses:Food -197.67" in f for f in result.failures)
    assert result.waived[UNCATEGORISED_FALLBACK]


def test_a_waived_cause_the_truth_does_not_expect_is_a_fail(defaults):
    """Failures waived under a cause the truth does not expect for the
    persona are real: the same result, marked with nothing expected, FAILs."""
    result = defaults["transfers_pair_once"]
    assert result.expected == frozenset({TRANSFER_MATCHING})
    assert status_of(result.mark(pending=None, expected=frozenset())) == "FAIL"


def test_income_with_no_seed_known_rows_reports_no_applicable_rows(
    reviewed_dir, reviewed, truth
):
    """R3: when the seed names none of the salary and interest rows, the live
    income rule passes saying "no applicable rows", not silently."""
    view = AppView.from_data_dir(reviewed_dir)
    # Seed-known is judged on the truth's canonical narration (M-C).
    renamed = dataclasses.replace(
        truth,
        events=[
            dataclasses.replace(e, narration="INT.PD:000000:DEC 2025")
            if e.narration.startswith("INTEREST CREDIT")
            else e
            for e in truth.events
        ],
    )
    result = rule("income_is_income").check(view, DEMO, renamed)
    assert result.passed
    assert result.detail == (
        "no applicable rows: the seed dictionary recognises none of the 8"
        " salary/interest narration(s); 1 cashback(s)"
    )
    # With the seed-known interest rows in place, they are checked exactly.
    assert reviewed["income_is_income"].detail.startswith(
        "2 of 8 salary/interest row(s) the seed dictionary"
    )


def test_miscategorised_transfer_legs_fail_the_run(defaults_dir, truth, tmp_path):
    """Re-review I-A: an own transfer booked as ₹25k of shopping out and ₹25k
    of salary in is a real fault, not a wait for transfer matching."""
    corrupt_out = dict((c[0], c[2]) for c in SUBCHECKS)[
        "transfer_leg_to_categorised_expense"
    ]
    corrupt_in = dict((c[0], c[2]) for c in SUBCHECKS)["transfer_leg_as_salary"]
    data = corrupt_in(corrupt_out(defaults_dir, tmp_path / "a"), tmp_path / "b")
    results = run_view(
        AppView.from_data_dir(data), DEMO, truth, only={"transfers_pair_once"}
    )
    ((_, result),) = results
    assert status_of(result) == "FAIL"
    assert result.failures == [
        "P0002 hdfc→idfc 25,000.00 on 2025-10-10: no transaction carries both legs"
        " (out leg in 1, in leg in 1); out leg booked against ['Expenses:Shopping'],"
        " in leg booked against ['Income:Salary']"
    ]
    assert len(result.waived[TRANSFER_MATCHING]) == 2
    assert check.exit_code(results) == 1


def _only(view: AppView, truth: Truth, kinds: set[str]) -> tuple[AppView, Truth]:
    """A constructed persona truth holding only `kinds` of row, and the view
    of just the journal transactions those rows matched."""
    from scripts.personas.rules import Match

    match = Match(view, DEMO, truth)
    keep = {
        match.txn[n].txn_id
        for n, e in enumerate(truth.events)
        if e.kind in kinds and n in match.txn
    }
    events = [e for e in truth.events if e.kind in kinds]
    return (
        dataclasses.replace(
            view, transactions=[t for t in view.transactions if t.txn_id in keep]
        ),
        dataclasses.replace(truth, events=events),
    )


@pytest.mark.parametrize(
    ("kinds", "want"),
    [
        ({"salary"}, "PENDING"),
        ({"cashback"}, "PENDING"),
        ({"transfer_in"}, "PENDING"),
        ({"card_payment_received"}, "PASS"),
        ({"interest"}, "PASS"),
        ({"spend"}, "PASS"),
    ],
    ids=[
        "salary",
        "cashback",
        "transfer_in",
        "payment_received",
        "seed_known_interest",
        "spends",
    ],
)
def test_expenses_expect_the_fallback_for_each_kind_it_waives(
    defaults_dir, truth, kinds, want
):
    """M-A: with only some credit kinds present, the expected cause follows
    exactly the kinds the truth holds: no wrong FAIL, no false UNEXPECTED
    PASS. A card's payment received is no longer one of them: it books
    against the card clearing account (upstream 18b71dd, dedup.py), not as
    a negative `Expenses:Uncategorized`."""
    view, part = _only(AppView.from_data_dir(defaults_dir), truth, kinds)
    ((_, result),) = run_view(view, DEMO, part, only={"expenses_positive"})
    assert status_of(result) == want, (result.failures, result.waived)


def test_a_categorised_cashback_with_seed_known_income_passes(
    reviewed_dir, truth, monkeypatch
):
    """M-B: the income cause is expected per row. With every salary, interest
    and cashback row one the seed names (here a stand-in seed), and all of
    them categorised, the rule is a PASS, not an UNEXPECTED PASS."""
    from scripts.personas import rules as rules_module

    view, part = _only(
        AppView.from_data_dir(reviewed_dir), truth, {"salary", "interest", "cashback"}
    )
    ((_, before),) = run_view(view, DEMO, part, only={"income_is_income"})
    # The real seed names no salary or cashback here, so the cause is expected
    # and the reviewer's categorising shows as a flip.
    assert status_of(before) == "UNEXPECTED PASS"
    named = {e.narration for e in part.events}
    monkeypatch.setattr(rules_module, "_seed_knows", lambda: named.__contains__)
    ((_, after),) = run_view(view, DEMO, part, only={"income_is_income"})
    assert status_of(after) == "PASS", (after.detail, after.failures, after.waived)
    assert after.detail.startswith("8 of 8 salary/interest row(s)")


def test_pending_rule_reported_as_pending_not_pass(defaults):
    result = defaults["transfers_pair_once"]
    assert status_of(result) == "PENDING"
    assert not result.passed
    assert label(result) == f"PENDING (waits for: {TRANSFER_MATCHING})"
    assert check.is_ok(result)
    for rid in (
        "all_merchants_categorised",
        "all_income_categorised",
        "new_accounts_opened_by_name",
        "one_approval_per_persona",
    ):
        assert status_of(defaults[rid]) == "PENDING", rid
    assert status_of(defaults["tags_appropriate"]) == "SKIPPED"
    # Both legs of every card bill clear through the clearing account today.
    assert status_of(defaults["card_bill_pairs"]) == "PASS"
    assert defaults["card_bill_pairs"].detail.endswith(f"{CLEARING} holds 0.00")


def test_pending_rule_that_passes_is_reported_loudly(
    reviewed_dir, truth, tmp_path, capsys
):
    """The reviewer left one spend (an unknown merchant) uncategorised, so
    the pending seed-growth rule fails. Categorise it and the rule passes:
    the run must say UNEXPECTED PASS and exit non-zero."""
    data = mutate(
        reviewed_dir,
        tmp_path / "data",
        lambda b: "MAKEMYTRIP" in b,
        lambda b: b.replace("Expenses:Uncategorized", "Expenses:Travel        "),
    )
    results = run_view(
        AppView.from_data_dir(data), DEMO, truth, only={"all_merchants_categorised"}
    )
    ((_, result),) = results
    assert status_of(result) == "UNEXPECTED PASS"
    assert label(result) == "UNEXPECTED PASS (was waiting for: seed dictionary growth)"
    assert check.exit_code(results) == 1

    code = check.main(
        ["--persona", "tiny", "--data-dir", str(data)], personas={"tiny": DEMO}
    )
    out = capsys.readouterr().out
    assert code == 1
    assert (
        "UNEXPECTED PASS (was waiting for: seed dictionary growth)  all_merchants_categorised"
        in out
    )


def test_cli_exit_code_nonzero_on_failure(defaults_dir, reviewed_dir, tmp_path, capsys):
    tiny = {"tiny": DEMO}
    # Under app defaults every failure is waived under an expected cause.
    assert (
        check.main(
            ["--persona", "tiny", "--data-dir", str(defaults_dir)], personas=tiny
        )
        == 0
    )
    clean = capsys.readouterr().out
    assert "FAIL" not in clean and "UNEXPECTED" not in clean
    corrupt, _ = MUTATIONS["balances_match_statements"]
    data = corrupt(reviewed_dir, tmp_path / "data")
    assert (
        check.main(["--persona", "tiny", "--data-dir", str(data)], personas=tiny) == 1
    )
    out = capsys.readouterr().out
    assert "FAIL  balances_match_statements" in out
    assert (
        len([line for line in out.splitlines() if not line.startswith(" ")])
        == len(RULES) + 2
    )


def test_tags_are_checked_only_with_live(reviewed_dir, truth):
    """Suggested tags need a live agent; a wrong tag is checked in QA too."""
    view = AppView.from_data_dir(reviewed_dir)
    ((_, skipped),) = run_view(view, DEMO, truth, only={"tags_appropriate"})
    assert status_of(skipped) == "SKIPPED"
    ((_, wrong),) = run_view(view, DEMO, truth, only={"no_wrong_tags"})
    assert status_of(wrong) == "PASS", wrong.failures
    ((_, live),) = run_view(view, DEMO, truth, only={"tags_appropriate"}, live=True)
    # The app sets no tags on import: the trip and subscription rows lack theirs.
    assert status_of(live) == "FAIL"
    assert any("truth ('trip-goa-2026',)" in f for f in live.failures)


def test_a_constructed_pending_rule_flips_loudly(truth, reviewed_dir):
    view = AppView.from_data_dir(reviewed_dir)
    passes = Rule(
        "always", "passes", lambda v, p, t: RuleResult(True, "ok"), "a feature"
    )
    fails = Rule(
        "never", "fails", lambda v, p, t: RuleResult(False, "no", ["x"]), "a feature"
    )
    results = run_view(view, DEMO, truth, rules=(passes, fails))
    assert [status_of(r) for _, r in results] == ["UNEXPECTED PASS", "PENDING"]
    assert check.exit_code(results) == 1
    assert check.exit_code(results[1:]) == 0


# --- live-demo opening balances (R3; Phase 6: openings from the prompt) ---------

_OPENING_ENTRY = re.compile(r'^\d{4}-\d{2}-\d{2} \* "Opening balance"')


def _openings_of(persona: Persona) -> dict[str, Decimal]:
    return {
        account_name(a): a.opening_balance
        for a in persona.accounts
        if a.kind in ("bank", "card") and a.opening_balance
    }


def _without_openings(src: Path, dst: Path, only: str | None = None) -> Path:
    """A copy of the data dir with every opening-balance entry (or only
    `only`'s) removed: what a live run leaves when the agent skips the
    first-import opening prompt."""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("snapshots"))
    dropped = 0
    for path in (dst / "journal").glob("*.beancount"):
        blocks = _BLOCK.split(path.read_text(encoding="utf-8"))
        kept = [
            b
            for b in blocks
            if not (_OPENING_ENTRY.match(b) and (only is None or f" {only} " in b))
        ]
        dropped += len(blocks) - len(kept)
        path.write_text("".join(kept), encoding="utf-8")
    assert dropped == (1 if only else len(_openings_of(DEMO))), dropped
    return dst


_BALANCE_RULES = {"balances_match_statements", "net_worth_matches"}


def test_a_live_run_without_opening_balances_waits_for_them(
    defaults_dir, truth, tmp_path
):
    """--live, the agent skipped every opening prompt: each account short by
    exactly its opening balance, and net worth short by exactly their sum,
    wait for "opening balance from statement". Without --live (QA always
    answers the prompt) the same journal FAILs both rules."""
    data = _without_openings(defaults_dir, tmp_path / "data")
    view = AppView.from_data_dir(data)
    live = results_by_id(view, truth, live=True, only=_BALANCE_RULES)
    openings = _openings_of(DEMO)
    balances = live["balances_match_statements"]
    assert label(balances) == f"PENDING (waits for: {OPENING_FROM_STATEMENT})"
    assert balances.failures == []
    assert sorted(
        w.split(":")[0:3] for w in balances.waived[OPENING_FROM_STATEMENT]
    ) == sorted(name.split(":") for name in openings)
    worth = live["net_worth_matches"]
    assert label(worth) == f"PENDING (waits for: {OPENING_FROM_STATEMENT})"
    unbooked = -sum(openings.values(), D(0))
    assert worth.waived[OPENING_FROM_STATEMENT] == [
        f"{unbooked:,.2f}: the opening balances never booked on"
        f" {', '.join(sorted(openings))}"
    ]
    for rid, result in results_by_id(view, truth, only=_BALANCE_RULES).items():
        assert status_of(result) == "FAIL", rid


def test_a_live_run_off_its_opening_balance_by_a_rupee_fails(
    defaults_dir, truth, tmp_path
):
    """Only the exact signature is waived: an account short by its opening
    balance and one rupee more FAILs, and so does net worth."""
    bare = _without_openings(defaults_dir, tmp_path / "bare")
    data = mutate(
        bare, tmp_path / "data", lambda b: "BIGBASKET" in b, lambda b: _shift(b, D(1))
    )
    live = results_by_id(
        AppView.from_data_dir(data), truth, live=True, only=_BALANCE_RULES
    )
    balances = live["balances_match_statements"]
    assert status_of(balances) == "FAIL"
    assert [f.split(":")[:3] for f in balances.failures] == [
        ["Assets", "Checking", "HDFC-7462"]
    ], balances.failures
    assert status_of(live["net_worth_matches"]) == "FAIL"


def test_openings_come_from_the_first_import_prompt(defaults_dir):
    """QA books every bank and card opening from its first import's prompt:
    each one is the suggestion as shown (statement or derived source, never
    typed in) and its amount is the persona's own opening."""
    from scripts.personas.check import client_for

    rows = {
        r["account"]: r
        for r in client_for(defaults_dir).get("/api/openings").json()["accounts"]
    }
    for name, opening in _openings_of(DEMO).items():
        row = rows[name]
        assert row["status"] == "opening", name
        assert row["opening"]["source"] in ("statement", "derived"), (name, row)
        assert D(row["opening"]["ledger_amount"]) == opening, name


def test_a_live_run_that_books_its_openings_from_the_prompt_passes(
    defaults, defaults_dir, truth
):
    """A live run whose openings came from the first-import prompt (`POST
    /api/openings`, as QA books them) expects nothing of the cause: PASS,
    not UNEXPECTED PASS."""
    live = results_by_id(
        AppView.from_data_dir(defaults_dir), truth, live=True, only=_BALANCE_RULES
    )
    for rid in sorted(_BALANCE_RULES):
        assert label(live[rid]) == "PASS", (rid, live[rid].failures, live[rid].waived)
        assert status_of(defaults[rid]) == "PASS", rid


def test_a_live_run_that_skipped_one_prompt_waits_for_that_account_only(
    defaults_dir, truth, tmp_path
):
    """Only the account whose prompt was skipped waits; the others were
    booked and are checked exactly."""
    skipped = next(n for n in _openings_of(DEMO) if ":IDFC-" in n)
    data = _without_openings(defaults_dir, tmp_path / "data", only=skipped)
    live = results_by_id(
        AppView.from_data_dir(data), truth, live=True, only=_BALANCE_RULES
    )
    balances = live["balances_match_statements"]
    assert label(balances) == f"PENDING (waits for: {OPENING_FROM_STATEMENT})"
    assert balances.failures == []
    assert [w.split(":")[:3] for w in balances.waived[OPENING_FROM_STATEMENT]] == [
        skipped.split(":")
    ]
    worth = live["net_worth_matches"]
    assert label(worth) == f"PENDING (waits for: {OPENING_FROM_STATEMENT})"
    assert worth.waived[OPENING_FROM_STATEMENT] == [
        f"{-_openings_of(DEMO)[skipped]:,.2f}: the opening balances never booked"
        f" on {skipped}"
    ]


def test_a_live_run_that_booked_a_zero_for_its_opening_fails(
    defaults_dir, truth, tmp_path
):
    """The cause covers a skipped prompt only: an account whose opening was
    booked as zero is short by exactly its opening too, but it has an
    opening recorded, so the shortfall is a real FAIL, live or not."""
    name = next(n for n in _openings_of(DEMO) if ":IDFC-" in n)
    data = mutate(
        defaults_dir,
        tmp_path / "data",
        lambda b: bool(_OPENING_ENTRY.match(b)) and f" {name} " in b,
        lambda b: re.sub(r"-?\d+\.\d\d INR", "0.00 INR", b),
    )
    view = AppView.from_data_dir(data)
    assert view.has_opening(name)
    for live in (True, False):
        results = results_by_id(view, truth, live=live, only=_BALANCE_RULES)
        balances = results["balances_match_statements"]
        assert status_of(balances) == "FAIL", live
        assert [f.split(":")[:3] for f in balances.failures] == [name.split(":")]
        assert balances.waived == {}, live
        assert status_of(results["net_worth_matches"]) == "FAIL", live


# --- card bills through the clearing account (R1) -----------------------------------


def test_a_bill_whose_card_side_falls_outside_the_period_is_named(
    defaults_dir, truth, tmp_path
):
    """A bill paid from the bank on the period's last bill date whose
    payment received the card prints after the period: its bank leg alone
    sits on the clearing account, and the rule names it from the truth.
    Against a truth that has both legs, the same journal FAILs."""
    last = max(
        (e for e in truth.events if e.kind == "card_payment_received"),
        key=lambda e: e.date,
    )
    data = mutate(
        defaults_dir,
        tmp_path / "data",
        lambda b: _card_bill_leg(b) and b.startswith(last.date.isoformat()),
        _drop,
    )
    part = dataclasses.replace(truth, events=[e for e in truth.events if e is not last])
    view = AppView.from_data_dir(data)
    result = results_by_id(view, part, only={"card_bill_pairs"})["card_bill_pairs"]
    assert status_of(result) == "PASS", result.failures
    amount = f"{last.amount:,.2f}"
    assert (
        f"open at the period's edge: {last.pair_id} hdfc→icici {amount}"
        f" on {last.date} (bank leg only)"
    ) in result.detail
    assert f"{CLEARING} holds {amount}" in result.detail
    full = results_by_id(view, truth, only={"card_bill_pairs"})["card_bill_pairs"]
    assert status_of(full) == "FAIL"
    assert any("card leg in 0 transaction(s)" in f for f in full.failures)


def test_a_bill_booked_bank_to_card_directly_fails(defaults_dir, truth, tmp_path):
    """The shape this rule used to expect (one transaction carrying both
    legs, bank against card) is not the clearing design: FAIL."""
    from scripts.personas.rules import Match

    view = AppView.from_data_dir(defaults_dir)
    match = Match(view, DEMO, truth)
    bill = next(n for n, e in enumerate(truth.events) if e.kind == "card_bill_payment")
    paid = truth.events[bill]
    card = account_name(DEMO.account(paid.counter_account_key or ""))
    bank = account_name(DEMO.account(paid.account_key))
    received = next(
        n
        for n, e in enumerate(truth.events)
        if e.kind == "card_payment_received" and e.pair_id == paid.pair_id
    )
    gone = {match.txn[bill].txn_id, match.txn[received].txn_id}
    direct = f"""
{paid.date} * "SCRATCH BILL BOOKED BANK TO CARD"
  txn-id: "0000000000000000000000000000drc1"
  {bank}   {paid.amount} INR
  {card}   {-paid.amount} INR
"""
    data = append(defaults_dir, tmp_path / "data", direct, ledger=str(paid.date.year))
    for name in ("2025", "2026"):
        path = data / "journal" / f"{name}.beancount"
        blocks = _BLOCK.split(path.read_text(encoding="utf-8"))
        path.write_text(
            "".join(b for b in blocks if not any(t in b for t in gone)),
            encoding="utf-8",
        )
    result = results_by_id(
        AppView.from_data_dir(data), truth, only={"card_bill_pairs"}
    )["card_bill_pairs"]
    assert status_of(result) == "FAIL"
    assert any(
        f"bank leg booked against ['{card}'], not {CLEARING}" in f
        for f in result.failures
    ), result.failures


# --- per-persona pending causes ----------------------------------------------------


@pytest.fixture(scope="module")
def catalog_truths() -> dict[str, Truth]:
    nav, prices = snapshot.load_nav(), snapshot.load_prices()
    return {slug: simulate(p, nav=nav, prices=prices) for slug, p in ALL.items()}


def test_pending_causes_follow_the_truth_not_the_slug(catalog_truths):
    zerodha = {s for s, t in catalog_truths.items() if zerodha_accounts(ALL[s], t)}
    payout = {s for s, t in catalog_truths.items() if payout_pending(ALL[s], t)}
    assert zerodha == {"p1cr", "p3cr"}
    assert payout == {"p1cr", "p3cr"}
    for slug, t in catalog_truths.items():
        reader = {ZERODHA_READER} if slug in zerodha else set()
        for rid in ("net_worth_matches", "broker_stock_holdings_imported"):
            # Fix round 1 (R2): no longer pending as a whole; only the
            # Zerodha part of each waits for the reader.
            assert rule(rid).pending_for(ALL[slug], t) is None, (slug, rid)
        stocks = rule("broker_stock_holdings_imported").attributed_for(ALL[slug], t)
        assert stocks == frozenset(reader), slug
        want = frozenset({REDEMPTION_SETTLEMENT} if slug in payout else ())
        assert rule("mf_redemption_payout_clears").attributed_for(ALL[slug], t) == want
        got = rule("balances_match_statements").attributed_for(ALL[slug], t)
        assert got == want, slug
        got = rule("net_worth_matches").attributed_for(ALL[slug], t)
        assert got == frozenset(set(want) | reader), slug
        # With nothing read of the app, a live run is taken to have skipped
        # every opening prompt; every persona has an opening.
        for rid in ("balances_match_statements", "net_worth_matches"):
            live = rule(rid).attributed_for(ALL[slug], t, live=True)
            assert live - rule(rid).attributed_for(ALL[slug], t) == {
                OPENING_FROM_STATEMENT
            }, (slug, rid)
        assert rule("card_bill_pairs").attributed_for(ALL[slug], t) == set(), slug
        banks = sum(1 for a in ALL[slug].accounts if a.kind == "bank")
        own = {TRANSFER_MATCHING} if banks > 1 else set()
        assert rule("transfers_pair_once").attributed_for(ALL[slug], t) == own, slug
    # Groww-only stock holders: the stock rule is live for them.
    assert {
        s for s, p in ALL.items() if any(a.kind == "broker_stocks" for a in p.accounts)
    } - zerodha == {
        "p30l",
        "p50l",
    }


@pytest.fixture(scope="module")
def p1cr(tmp_path_factory, catalog_truths):
    """p1cr imported with Zerodha configured as a platform, and the rules
    its pending causes touch run on it."""
    optin.require()
    persona, truth = ALL["p1cr"], catalog_truths["p1cr"]
    root = tmp_path_factory.mktemp("p1cr")
    (root / "files").mkdir()
    paths = build(persona, root / "files", truth=truth)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(ZERODHA_ENV, json.dumps(WITH_ZERODHA))
        get_settings.cache_clear()
        try:
            done = _import(root / "data", persona, paths)
            view = AppView.from_data_dir(root / "data")
        finally:
            get_settings.cache_clear()
    only = {
        "balances_match_statements",
        "net_worth_matches",
        "platform_funding_to_platform_cash",
        "mf_redemption_payout_clears",
        "broker_stock_holdings_imported",
    }
    results = results_by_id(view, truth, persona=persona, only=only)
    return done, view, truth, results, root / "data"


def test_zerodha_holder_waits_for_the_tradebook_reader(p1cr):
    done, _, _, results, _ = p1cr
    assert [(r.path.name, r.refused) for r in done if r.refused] == [
        ("zerodha_0829_2026-03-31.csv", "E1002")
    ]
    for rid in ("net_worth_matches", "broker_stock_holdings_imported"):
        assert results[rid].failures == [], rid
        assert ZERODHA_READER in results[rid].waived, rid
    stocks = results["broker_stock_holdings_imported"]
    assert label(stocks) == f"PENDING (waits for: {ZERODHA_READER})"
    # Groww's stocks did import; only Zerodha's are missing.
    assert not any(
        s in f for f in stocks.waived[ZERODHA_READER] for s in ("HDFCBANK", "ITC")
    )
    assert status_of(results["platform_funding_to_platform_cash"]) == "PASS"


def test_balances_skip_zerodha_and_report_the_payout_accounts_as_pending(p1cr):
    _, view, truth, results, _ = p1cr
    result = results["balances_match_statements"]
    assert (
        "skipped zerodha (Assets:Broker:Zerodha): its tradebook is not importable"
        f" (waits for: {ZERODHA_READER})" in result.detail
    )
    residue, n = truncated_paise(truth)
    assert (residue, n) == (D("0.20"), 1)
    assert result.failures == []
    assert label(result) == f"PENDING (waits for: {REDEMPTION_SETTLEMENT})"
    assert result.waived[REDEMPTION_SETTLEMENT] == [
        "Assets:Broker:Groww is off -0.20 after the payout: the measured wallet"
        " residue equals the explained truncation residue -0.20 exactly",
        f"{view.mf_clearing} (investment ledger) keeps the redemption proceeds, as"
        " the export truncates them: off -0.20, the explained truncation residue"
        " -0.20 exactly; no payout is booked there",
    ]
    # The HDFC card's bills no longer land on the seed placeholder
    # `Liabilities:CreditCard:HDFC` (lifted: upstream 18b71dd books every
    # bank-side bill against the card clearing account).
    assert set(result.waived) == {REDEMPTION_SETTLEMENT}
    assert view.balance("Liabilities:CreditCard:HDFC") == 0


def test_p1cr_failures_beyond_the_waived_signatures_are_real(p1cr, tmp_path):
    """R2 on balances and the payout: a wallet residue that is not exactly
    the truncated paise is a FAIL, on both rules."""
    _, _, truth, _, root = p1cr
    data = mutate(
        root,
        tmp_path / "b",
        lambda b: "Assets:Broker:Groww" in b and "123598 INR" in b,
        lambda b: b.replace("123598 INR", "123597 INR").replace(
            "Income:CapitalGains:STCG         6804.200000 INR",
            "Income:CapitalGains:STCG         6805.200000 INR",
        ),
    )
    only = {"balances_match_statements", "mf_redemption_payout_clears"}
    results = results_by_id(
        AppView.from_data_dir(data), truth, persona=ALL["p1cr"], only=only
    )
    balances = results["balances_match_statements"]
    assert status_of(balances) == "FAIL"
    assert any(
        f.startswith("Assets:Broker:Groww: journal ") for f in balances.failures
    ), balances.failures
    assert REDEMPTION_SETTLEMENT not in balances.waived or not any(
        "Assets:Broker:Groww is off" in w
        for w in balances.waived[REDEMPTION_SETTLEMENT]
    )
    payout = results["mf_redemption_payout_clears"]
    assert status_of(payout) == "FAIL"
    assert "(left -1.20)" in payout.failures[0]


def test_payout_waits_for_clearing_and_pins_todays_booking(p1cr):
    """Today: the bank-side `GROWW MF REDEMPTION` credit is booked against the
    Groww wallet, which the redemption itself credited with the export's
    truncated proceeds. The wallet is left short by the truncated paise, not
    by the payout; the investment ledger's clearing account keeps the
    proceeds, since no payout is ever booked there."""
    _, view, truth, results, _ = p1cr
    result = results["mf_redemption_payout_clears"]
    assert label(result) == f"PENDING (waits for: {REDEMPTION_SETTLEMENT})"
    (line,) = result.waived[REDEMPTION_SETTLEMENT]
    assert line.endswith(
        "(left -0.20): the measured wallet residue -0.20 equals the explained"
        f" truncation residue -0.20 exactly, and {view.mf_clearing} (investment"
        " ledger) keeps the 123,598.00 proceeds"
    )
    (payout,) = [
        e
        for e in truth.events
        if e.kind == "mf_redemption_payout" and e.account_key == "hdfc"
    ]
    (sold,) = [e for e in truth.events if e.kind == "mf_sell"]
    bank = [
        t
        for t in view.transactions
        if t.date == payout.date and any(p.amount == payout.amount for p in t.postings)
    ]
    assert len(bank) == 1
    assert sorted(p.account for p in bank[0].postings) == [
        "Assets:Broker:Groww",
        "Assets:Checking:HDFC-1157",
    ]
    wallet = {
        p.amount
        for t in view.transactions
        for p in t.postings
        if p.account == "Assets:Broker:Groww"
        and t.date in (sold.date, payout.date)
        and abs(p.amount) > 100000
    }
    assert wallet == {D("123598"), -payout.amount}
    want_wallet = truth.closing["groww"] + truth.closing["growwst"]
    assert view.balance("Assets:Broker:Groww") == want_wallet - D("0.20")
    buys = sum((e.amount for e in truth.events if e.kind == "mf_buy"), D(0))
    assert view.investment_balance(view.mf_clearing) == buys + D("123598")


# --- the Zerodha platform setting -------------------------------------------------


@pytest.fixture(scope="module")
def p1cr_kotak(tmp_path_factory, catalog_truths) -> tuple[Path, Event]:
    optin.require()
    out = tmp_path_factory.mktemp("p1cr-kotak")
    paths = build(ALL["p1cr"], out, truth=catalog_truths["p1cr"])
    (kotak,) = [p for p in paths if p.name.startswith("kotak_")]
    (funding,) = [
        e
        for e in catalog_truths["p1cr"].events
        if e.kind == "platform_funding"
        and e.account_key == "kotak"
        and e.counter_account_key == "zerodha"
    ][:1]
    return kotak, funding


def _zerodha_funding_postings(data: Path, kotak: Path, funding: Event) -> list[str]:
    _import(data, ALL["p1cr"], [kotak])
    view = AppView.from_data_dir(data)
    (txn,) = [
        t
        for t in view.transactions
        if t.date == funding.date
        and any(
            p.account == "Assets:Checking:Kotak-1481" and p.amount == funding.amount
            for p in t.postings
        )
    ]
    return sorted(p.account for p in txn.postings)


def test_zerodha_funding_misses_its_wallet_without_the_setting(
    p1cr_kotak, catalog_truths, tmp_path
):
    kotak, funding = p1cr_kotak
    assert "zerodha" not in get_settings().platform_cash_accounts
    accounts = _zerodha_funding_postings(tmp_path / "data", kotak, funding)
    assert "Assets:Broker:Zerodha" not in accounts
    assert accounts == ["Assets:Checking:Kotak-1481", "Expenses:Uncategorized"]
    result = results_by_id(
        AppView.from_data_dir(tmp_path / "data"),
        catalog_truths["p1cr"],
        persona=ALL["p1cr"],
        only={"platform_funding_to_platform_cash"},
    )["platform_funding_to_platform_cash"]
    assert status_of(result) == "FAIL"
    assert any(
        "'zerodha' is not a configured platform (configured: ['groww'])" in f
        for f in result.failures
    )


def test_zerodha_funding_posts_to_its_wallet_with_the_setting(
    p1cr_kotak, tmp_path, monkeypatch
):
    kotak, funding = p1cr_kotak
    monkeypatch.setenv(ZERODHA_ENV, json.dumps(WITH_ZERODHA))
    get_settings.cache_clear()
    accounts = _zerodha_funding_postings(tmp_path / "data", kotak, funding)
    assert accounts == ["Assets:Broker:Zerodha", "Assets:Checking:Kotak-1481"]


# --- snapshot anchors (Task 4 minor) ----------------------------------------------

# One exact value per instrument on 3 Nov 2025, read from the committed CSVs:
# a series-wide scale error (a NAV in paise, a close ×10) fails here.
ANCHORS: dict[str, Decimal] = {
    "118834": D("177.2930"),
    "118989": D("223.3470"),
    "120503": D("111.7992"),
    "120716": D("179.9324"),
    "122639": D("94.4577"),
    "125497": D("200.9709"),
    "HDFCBANK": D("992.65"),
    "ICICIBANK": D("1346.40"),
    "INFY": D("1485.50"),
    "ITC": D("413.95"),
    "LT": D("3980.50"),
    "RELIANCE": D("1484.70"),
    "SBIN": D("949.70"),
    "TCS": D("3016.80"),
}


def test_each_instrument_matches_its_anchor_value():
    nav, prices = snapshot.load_nav(), snapshot.load_prices()
    on = date(2025, 11, 3)
    got = {k: (nav(k, on) if k.isdigit() else prices(k, on)) for k in ANCHORS}
    assert got == ANCHORS
