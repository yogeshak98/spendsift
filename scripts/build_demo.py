"""Build a fully-fictional, multi-year demo ledger through the REAL commit pipeline.

Generates ~2 years of synthetic monthly transactions (recurring salary / rent / loan
EMI / utilities / interest, plus varied card spending, cashback, refunds, a receivable
lifecycle and a flat FD), then commits them via the real ``commit_candidates`` ->
``candidate_to_entry`` -> ``Ledger.append`` path, which snapshots, opens accounts, and
**beancount-validates**. A balance bug surfaces as a load error, not a silently-wrong demo.

Deterministic: a fixed RNG seed means every build produces the same ledger. No real PII —
``Jane Doe`` is the only personal name; the rest are merchants and invented amounts.

Usage:
    uv run python scripts/build_demo.py [--data-dir demo/data] [--check]

``--check`` runs only the PII scan over an already-built ledger (no rebuild). Any build
also runs the PII scan at the end and exits non-zero on a hit.
"""

from __future__ import annotations

import argparse
import random
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from backend.app.models.domain.candidate import CandidateTxn, Direction, Split
from backend.app.service.ingest.pipeline.entry_writer import (
    candidate_to_entry,
    commit_candidates,
)
from backend.app.persistence.templates import TemplateStore
from backend.app.models.storage.template import Template
from backend.app.persistence.journal import Ledger
from backend.app.service.prompts import seed_default_prompts

ROOT = Path(__file__).resolve().parents[1]
SEED = 20260625

# --- everything dated here is RELATIVE TO THE BUILD DATE -------------------
#
# The demo's whole job is to look like a ledger someone is actually keeping. A
# hardcoded window stops being that the moment it falls behind: this file used
# to end at 2026-06, so by late 2026 the demo opened on four months of
# emptiness, every price read "stale", and the tax workspace showed a closed
# financial year. It degrades silently and gets worse every month.
#
# So the window is anchored to the build, and `demo/data` is a build artifact
# (gitignored, rebuilt by run_demo.sh). Determinism is now "same output for a
# given build month" rather than "same output forever" -- SEED still fixes
# every amount and category choice, so two builds in the same month are
# byte-identical.
#
# NOTE this is deliberately the opposite of tests/fixtures/statements/*.csv,
# which stay fixed: those are asserted against exact parsed output and encode
# chosen holding periods. Fixed fixture + injected clock is right for a test;
# a moving window is right only for data whose job is to look current.
_BUILD_TODAY = date.today()

# 25 months inclusive, ending in the current month, so "this month" is populated.
_HISTORY_MONTHS = 24


def _shift_month(ym: tuple[int, int], delta: int) -> tuple[int, int]:
    """Move a (year, month) pair by `delta` months, in either direction."""
    y, m = ym
    total = y * 12 + (m - 1) + delta
    return total // 12, total % 12 + 1


END = (_BUILD_TODAY.year, _BUILD_TODAY.month)
START = _shift_month(END, -_HISTORY_MONTHS)

# Opening balances predate the transaction history by a clear gap, so the
# starting position is established before the first month's activity rather
# than landing on top of it.
_OPENING_Y, _OPENING_M = _shift_month(START, -5)
OPENING_DATE = date(_OPENING_Y, _OPENING_M, 1)


def _fy_start(day: date) -> date:
    """First day of the Indian financial year containing `day` (Apr 1 - Mar 31)."""
    return date(day.year if day.month >= 4 else day.year - 1, 4, 1)


def _month_day(ym: tuple[int, int], day: int) -> date:
    return date(ym[0], ym[1], day)


CHECKING_HDFC = "Assets:Checking:HDFC"
CHECKING_ICICI = "Assets:Checking:ICICI"
CARD_HDFC = "Liabilities:CreditCard:HDFC"
CARD_ICICI = "Liabilities:CreditCard:ICICI"

# Card spend categories: (narration, expense account, (min, max)). Picked at random
# each month to give the expense breakdown/tags dashboards variety.
CARD_CATEGORIES = [
    ("SWIGGY BANGALORE", "Expenses:Food", (220, 900)),
    ("ZOMATO ORDER", "Expenses:Food", (180, 750)),
    ("AMAZON SHOPPING", "Expenses:Shopping", (400, 4200)),
    ("FLIPKART ORDER", "Expenses:Shopping", (300, 3500)),
    ("BIGBASKET GROCERIES", "Expenses:Groceries", (600, 2600)),
    ("UBER INDIA TRIP", "Expenses:Travel", (90, 620)),
    ("INDIAN OIL FUEL", "Expenses:Fuel", (800, 2400)),
    ("NETFLIX SUBSCRIPTION", "Expenses:Entertainment", (199, 649)),
    ("BOOKMYSHOW TICKETS", "Expenses:Entertainment", (250, 1300)),
    ("APOLLO PHARMACY", "Expenses:Health", (150, 1800)),
]

# Real PII tokens that must never appear in the generated ledger; any hit fails the
# build. The literal tokens are NOT stored in source (that would itself leak PII) —
# they live in a gitignored local denylist file. Absent file → the scan is a structural
# no-op (the demo is fictional by construction, so it has nothing real to catch).
# One token per line; blank lines and '#' comments ignored.
_PII_DENYLIST_FILE = Path(__file__).with_name(".pii-denylist.txt")


def _load_pii_tokens() -> list[str]:
    if not _PII_DENYLIST_FILE.exists():
        return []
    return [
        line.strip()
        for line in _PII_DENYLIST_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


PII_TOKENS = _load_pii_tokens()


def _months(start: tuple[int, int], end: tuple[int, int]):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def _d(amount) -> Decimal:
    return Decimal(str(amount)).quantize(Decimal("0.01"))


def build_transactions() -> list[CandidateTxn]:
    rng = random.Random(SEED)
    txns: list[CandidateTxn] = []
    n = 0

    def add(day_date, amount, narr, source, direction, other=None, splits=None):
        nonlocal n
        txns.append(
            CandidateTxn(
                id=f"demo-{n}",
                date=day_date,
                amount=_d(amount),
                raw_narration=narr,
                normalized_narration=narr,
                source_account=source,
                direction=direction,
                suggested_other_leg=other,
                splits=splits or [],
            )
        )
        n += 1

    # Opening balances (via Equity:Opening-Balances) so the starting position is
    # realistic: a financed house, a home-loan liability, and cash. DEBIT from equity
    # books an asset (+); CREDIT from equity books the loan (-).
    OPENING = "Equity:Opening-Balances"
    add(
        OPENING_DATE,
        2500000,
        "OPENING BALANCE HOME VALUE",
        OPENING,
        Direction.DEBIT,
        other="Assets:Investment:RealEstate:Home",
    )
    add(
        OPENING_DATE,
        600000,
        "OPENING BALANCE HDFC CHECKING",
        OPENING,
        Direction.DEBIT,
        other=CHECKING_HDFC,
    )
    add(
        OPENING_DATE,
        250000,
        "OPENING BALANCE ICICI CHECKING",
        OPENING,
        Direction.DEBIT,
        other=CHECKING_ICICI,
    )
    add(
        OPENING_DATE,
        1800000,
        "OPENING HOME LOAN OUTSTANDING",
        OPENING,
        Direction.CREDIT,
        other="Liabilities:Loan:HDFC:Home",
    )

    prev_card_spend = Decimal("0")
    for idx, (y, m) in enumerate(_months(START, END)):
        # Recurring income/expenses.
        salary = (
            280000 + (idx // 12) * 15000 + rng.randint(0, 2000)
        )  # annual raise + jitter
        add(
            date(y, m, 1),
            salary,
            "SALARY ACME PAYROLL",
            CHECKING_HDFC,
            Direction.CREDIT,
            other="Income:Salary",
        )

        rent = 18000 if idx < 12 else 19500
        add(
            date(y, m, 2),
            rent,
            "RENT MONTHLY",
            CHECKING_HDFC,
            Direction.DEBIT,
            other="Expenses:Rent",
        )

        interest = max(2000, 3200 - idx * 35)  # amortizing: interest falls,
        principal = 15000 - interest  # principal rises; sum == EMI
        add(
            date(y, m, 5),
            15000,
            "HOME LOAN EMI",
            CHECKING_HDFC,
            Direction.DEBIT,
            splits=[
                Split(account="Liabilities:Loan:HDFC:Home", amount=_d(principal)),
                Split(account="Expenses:Interest:HomeLoan", amount=_d(interest)),
            ],
        )

        # Monthly equity-fund SIP, stepped up each year. This is where most of the
        # salary surplus goes, so investments grow into the dominant asset over time
        # instead of cash piling up idle in checking.
        sip = 120000 + (idx // 12) * 30000 + rng.randint(0, 5000)
        add(
            date(y, m, 3),
            sip,
            "SIP AXIS BLUECHIP FUND",
            CHECKING_HDFC,
            Direction.DEBIT,
            other="Assets:Investment:MutualFund:Axis:Bluechip",
        )

        add(
            date(y, m, 8),
            rng.randint(1100, 1800),
            "BESCOM ELECTRICITY BILL",
            CHECKING_ICICI,
            Direction.DEBIT,
            other="Expenses:Utilities",
        )
        add(
            date(y, m, 9),
            799,
            "AIRTEL BROADBAND",
            CHECKING_ICICI,
            Direction.DEBIT,
            other="Expenses:Utilities",
        )

        # Quarterly savings interest.
        if m in (3, 6, 9, 12):
            add(
                date(y, m, 28),
                rng.randint(1300, 1750),
                "INTEREST PAID SAVINGS",
                CHECKING_HDFC,
                Direction.CREDIT,
                other="Income:Interest:Savings:HDFC",
            )

        # Varied HDFC card spending (4-7 txns/month).
        card_spend = Decimal("0")
        for k in range(rng.randint(4, 7)):
            narr, acct, (lo, hi) = rng.choice(CARD_CATEGORIES)
            amt = _d(rng.randint(lo, hi))
            card_spend += amt
            add(date(y, m, 10 + k), amt, narr, CARD_HDFC, Direction.DEBIT, other=acct)

        # A monthly ICICI card dining txn + quarterly ICICI cashback (keeps both cards live).
        add(
            date(y, m, 16),
            _d(rng.randint(300, 1400)),
            "DINING TABLE RESTAURANT",
            CARD_ICICI,
            Direction.DEBIT,
            other="Expenses:Food",
        )
        if m in (3, 6, 9, 12):
            add(
                date(y, m, 17),
                _d(rng.randint(40, 90)),
                "REWARD CASHBACK",
                CARD_ICICI,
                Direction.CREDIT,
                other="Income:Cashback:CreditCard:ICICI",
            )

        # Bi-monthly HDFC cashback; occasional refund.
        if idx % 2 == 0:
            add(
                date(y, m, 19),
                _d(rng.randint(50, 130)),
                "CASHBACK CREDIT",
                CARD_HDFC,
                Direction.CREDIT,
                other="Income:Cashback:CreditCard:HDFC",
            )
        if idx % 4 == 2:
            add(
                date(y, m, 21),
                _d(rng.randint(300, 1200)),
                "SWIGGY REFUND",
                CARD_HDFC,
                Direction.CREDIT,
                other="Income:Refund:CreditCard:HDFC",
            )

        # Pay last month's HDFC card balance from checking.
        if prev_card_spend > 0:
            add(
                date(y, m, 18),
                prev_card_spend,
                "CARD PAYMENT TO HDFC CREDIT CARD",
                CHECKING_HDFC,
                Direction.DEBIT,
                other=CARD_HDFC,
            )
        prev_card_spend = card_spend

        # One-off events: FD early, a receivable lifecycle mid-history.
        if idx == 1:
            add(
                date(y, m, 24),
                100000,
                "FD BOOKED 12M",
                CHECKING_HDFC,
                Direction.DEBIT,
                other="Assets:Investment:FD:HDFC",
            )
        if idx == 6:
            add(
                date(y, m, 12),
                5000,
                "UPI-JANE DOE TRANSFER",
                CHECKING_ICICI,
                Direction.DEBIT,
                other="Assets:Receivable:JaneDoe",
            )
        if idx == 9:
            add(
                date(y, m, 14),
                5000,
                "UPI-JANE DOE REPAYMENT",
                CHECKING_ICICI,
                Direction.CREDIT,
                other="Assets:Receivable:JaneDoe",
            )

    # Two mutual-fund redemptions in the CURRENT financial year so the tax
    # workspace's capital-gains buckets are populated by default — one
    # long-term gain and one short-term. Proceeds (the checking credit) = cost
    # basis returned to the holding + the realized gain booked to
    # Income:CapitalGains:*.
    #
    # "Near the end of history" is NOT the same as "in the current FY", and
    # that distinction is the whole point of the clamp below. The Indian FY
    # starts on 1 April, so in April, May and June the month before END falls
    # into the PREVIOUS year — placing a redemption there would empty the tax
    # screen for a third of every year, which is exactly the kind of silent
    # seasonal breakage this rewrite exists to remove.
    MF = "Assets:Investment:MutualFund:Axis:Bluechip"
    _fy_begins = _fy_start(_month_day(END, 1))
    _redemption_late = _month_day(END, 10)
    _redemption_early = max(_month_day(_shift_month(END, -1), 20), _fy_begins)
    add(
        _redemption_early,
        180000,
        "REDEMPTION AXIS BLUECHIP FUND",
        CHECKING_HDFC,
        Direction.CREDIT,
        splits=[
            Split(account=MF, amount=_d(150000)),  # cost basis returned
            Split(
                account="Income:CapitalGains:LTCG", amount=_d(30000)
            ),  # long-term gain
        ],
    )
    add(
        _redemption_late,
        62000,
        "REDEMPTION AXIS BLUECHIP FUND",
        CHECKING_HDFC,
        Direction.CREDIT,
        splits=[
            Split(account=MF, amount=_d(50000)),  # cost basis returned
            Split(
                account="Income:CapitalGains:STCG", amount=_d(12000)
            ),  # short-term gain
        ],
    )

    return txns


# Fictional tagged spend for Jane Doe so the "Spend by tag" dashboard demos itself.
# Each row: (date, amount, narration, expense account, frozenset of tag labels).
# Tags are invented, non-PII, and mirror the spec's suggestions (#trip-goa,
# #reimbursable). by_tag reads transaction-level beancount tags, so these are attached
# to the committed Transaction's ``tags`` field (not posting metadata).
# Offsets are (months before END, day-of-month) rather than calendar dates, so
# the trip stays a three-day trip and the reimbursables stay recent however
# long from now the demo is rebuilt. All sit well inside the 25-month window.
_TAGGED_SPEND_OFFSETS = [
    (7, 14, 6800, "INDIGO FLIGHT BLR-GOI", "Expenses:Travel", {"trip-goa"}),
    (7, 15, 9200, "TAJ RESORT GOA STAY", "Expenses:Travel", {"trip-goa"}),
    (7, 16, 2400, "BEACH SHACK DINING GOA", "Expenses:Food", {"trip-goa"}),
    (4, 9, 3100, "OFFSITE CAB FARE", "Expenses:Travel", {"reimbursable"}),
    (3, 22, 1750, "CLIENT LUNCH MEETING", "Expenses:Food", {"reimbursable"}),
    (
        2,
        5,
        5400,
        "CONFERENCE TICKET",
        "Expenses:Entertainment",
        {"reimbursable", "trip-goa"},
    ),
]

TAGGED_SPEND = [
    (_month_day(_shift_month(END, -back), day), amount, narr, acct, tags)
    for back, day, amount, narr, acct, tags in _TAGGED_SPEND_OFFSETS
]


def _tagged_entries() -> list:
    """Build committed-entry objects with transaction-level tags attached.

    ``CandidateTxn`` carries only posting-level metadata; the ``by_tag`` read model
    reads ``entry.tags``. So we convert each fictional candidate to an entry and replace
    its ``tags`` field with the fictional labels before appending."""
    entries = []
    for i, (day, amount, narr, acct, tags) in enumerate(TAGGED_SPEND):
        candidate = CandidateTxn(
            id=f"demo-tag-{i}",
            date=day,
            amount=_d(amount),
            raw_narration=narr,
            normalized_narration=narr,
            source_account=CARD_HDFC,
            direction=Direction.DEBIT,
            suggested_other_leg=acct,
        )
        entry = candidate_to_entry(candidate)
        entries.append(entry._replace(tags=frozenset(tags)))
    return entries


_DEMO_BUDGET_YAML = """\
# Monthly budget targets for Jane Doe (fictional demo persona).
# Amounts are positive INR strings.  One target applies to every month.
version: 1
targets:
  "Expenses:Rent": "28000"
  "Expenses:Food": "8000"
  "Expenses:Groceries": "6000"
  "Expenses:Shopping": "5000"
  "Expenses:Travel": "3000"
  "Expenses:Entertainment": "2000"
  "Expenses:Health": "2500"
  "Expenses:Fuel": "3500"
  "Expenses:Utilities": "4000"
"""


# Fictional-demo tax rules so the tax workspace shows a liability estimate
# instead of the "not configured" fallback. Values are carried forward from the
# FY2025-26 slabs the tax engine is tested against; the demo persona is
# fictional, so these are illustrative only — a real user supplies their own
# config/tax/<fy>.yaml verified against the Finance Act.
# The FY the demo's own history ends in. The rules file must be named and
# labelled for THIS year, or the tax workspace opens on a year it has no rules
# for and shows nothing -- the same seasonal breakage the redemption clamp
# above avoids, one layer down.
DEMO_FY = _fy_start(_month_day(END, 1)).year

_DEMO_TAX_YAML = """\
# Illustrative income-tax rules for the fictional demo (FY__FY__-__AY_SHORT__ /
# AY__AY__-__AY_NEXT_SHORT__). Carried forward unchanged from FY2025-26 and NOT
# authoritative — these are the same slabs relabelled for whichever year the
# demo was built in, so verify against the Finance Act before relying on any
# real estimate. The demo exists to exercise the screens, not to be correct
# tax.
fy: __FY__
new_regime:
  slabs:
    - { upto: "400000", rate: "0.0" }
    - { upto: "800000", rate: "0.05" }
    - { upto: "1200000", rate: "0.10" }
    - { upto: "1600000", rate: "0.15" }
    - { upto: "2000000", rate: "0.20" }
    - { upto: "2400000", rate: "0.25" }
    - { upto: null, rate: "0.30" }
  standard_deduction: "75000"
  rebate_ceiling: "1200000"
  rebate_max: "60000"
  allowed_deductions: [80ccd_2]
old_regime:
  slabs:
    - { upto: "250000", rate: "0.0" }
    - { upto: "500000", rate: "0.05" }
    - { upto: "1000000", rate: "0.20" }
    - { upto: null, rate: "0.30" }
  standard_deduction: "50000"
  rebate_ceiling: "500000"
  rebate_max: "12500"
  allowed_deductions: [80c, 80ccd_1b, 80ccd_2, 80d]
deductions:
  - { section: 80c, cap: "150000" }
  - { section: 80ccd_1b, cap: "50000" }
  - { section: 80ccd_2, cap: null }
  - { section: 80d, cap: "25000" }
surcharge:
  marginal_relief: true
  bands:
    - { over: "5000000", rate: "0.10" }
    - { over: "10000000", rate: "0.15" }
    - { over: "20000000", rate: "0.25" }
cess_rate: "0.04"
capital_gains:
  ltcg_rate: "0.125"
  ltcg_exemption: "125000"
  stcg_rate: "0.20"
  crypto_rate: "0.30"
"""


_DEMO_TEMPLATES: list[dict] = [
    {
        "fingerprint": "22f2cefc23b97332",
        "institution": "HDFC Bank",
        "header_row_index": 0,
        "date_column": "Date",
        "date_format": "%d/%m/%Y",
        "narration_column": "Narration",
        "debit_column": "Withdrawal Amt.",
        "credit_column": "Deposit Amt.",
        "default_account": "Assets:Checking:HDFC",
    },
    {
        "fingerprint": "4e5e03932d233af9",
        "institution": "ICICI Bank",
        "header_row_index": 0,
        "date_column": "Txn Date",
        "date_format": "%d/%m/%Y",
        "narration_column": "Particulars",
        "debit_column": "Debit",
        "credit_column": "Credit",
        "default_account": "Assets:Checking:ICICI",
    },
    {
        "fingerprint": "b7753c888622bddc",
        "institution": "IDFC FIRST Bank",
        "header_row_index": 0,
        "date_column": "Date",
        "date_format": "%d/%m/%Y",
        "narration_column": "Description",
        "debit_column": "Dr",
        "credit_column": "Cr",
        "default_account": "Assets:Checking:IDFC",
    },
    {
        "fingerprint": "f79ef538a2ca173e",
        "institution": "HDFC Credit Card",
        "header_row_index": 25,
        "date_column": "DATE",
        "date_format": "%d/%m/%Y %H:%M:%S",
        "narration_column": "Description",
        "delimiter": "~|~",
        "amount_column": "AMT",
        "sign_column": "Debit /Credit",
        "account_marker_pattern": r"Card No: \d{4} \d{2}XX XXXX (\d{4})",
        "account_marker_map": {"0001": "Liabilities:CreditCard:HDFC"},
        "default_account": "Liabilities:CreditCard:HDFC",
    },
    {
        "fingerprint": "1ea1154145acc8bd",
        "institution": "ICICI Credit Card",
        "header_row_index": 7,
        "date_column": "Date",
        "date_format": "%d/%m/%Y",
        "narration_column": "Transaction Details",
        "amount_column": "Amount(in Rs)",
        "sign_column": "BillingAmountSign",
        "credit_indicators": ["CR", "Cr", "Credit"],
        "account_marker_pattern": r"(\d{4})XXXXXXXX(\d{4})",
        "account_marker_map": {"43990001": "Liabilities:CreditCard:ICICI"},
        "default_account": "Liabilities:CreditCard:ICICI",
    },
    {
        "fingerprint": "0ac7255ecb0fd9de",
        "institution": "IDFC FIRST Credit Card",
        "header_row_index": 0,
        "date_column": "Transaction Date",
        "date_format": "%d/%m/%Y",
        "narration_column": "Description",
        "debit_column": "Debit",
        "credit_column": "Credit",
        "default_account": "Liabilities:CreditCard:IDFC",
    },
]


def _seed_templates(storage_dir: Path) -> None:
    store = TemplateStore(storage_dir / "templates")
    for raw in _DEMO_TEMPLATES:
        store.save(Template(**raw))


def build_demo(storage_dir: Path) -> Path:
    """Wipe and rebuild the demo ledger at ``storage_dir``. Returns the journal dir."""
    import shutil

    if storage_dir.exists():
        shutil.rmtree(storage_dir)
    ledger = Ledger(storage_dir)
    ledger.bootstrap()
    seed_default_prompts(ledger.storage_dir)
    investment_ledger = Ledger(
        storage_dir,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    investment_ledger.bootstrap(seed_accounts=())
    commit_candidates(ledger, investment_ledger, build_transactions())
    # Fictional tagged spend so the "Spend by tag" dashboard has data to rank.
    ledger.append(_tagged_entries())

    # Seed a fictional budget config for the demo (Jane Doe categories only).
    config_dir = ledger.storage_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "budget.yaml").write_text(_DEMO_BUDGET_YAML, encoding="utf-8")

    # Seed FY2026-27 tax rules so the demo tax workspace shows a liability
    # estimate rather than the "not configured" fallback.
    tax_dir = config_dir / "tax"
    tax_dir.mkdir(parents=True, exist_ok=True)
    # Plain substitution, not str.format: the YAML body is full of literal
    # `{ upto: ... }` flow mappings that format() would read as fields.
    tax_yaml = (
        _DEMO_TAX_YAML.replace("__FY__", str(DEMO_FY))
        .replace("__AY_SHORT__", f"{(DEMO_FY + 1) % 100:02d}")
        .replace("__AY_NEXT_SHORT__", f"{(DEMO_FY + 2) % 100:02d}")
        .replace("__AY__", str(DEMO_FY + 1))
    )
    (tax_dir / f"{DEMO_FY}.yaml").write_text(tax_yaml, encoding="utf-8")

    # Seed import templates for all day-one institutions so the Settings →
    # Import Templates screen has cards to show without requiring a manual import.
    _seed_templates(storage_dir)

    return ledger.storage_dir / "journal"


# A posted amount: digits with a decimal part, followed by a currency or
# commodity symbol -- "280032.00 INR", "215.00 INR" inside a cost block.
_POSTED_AMOUNT_RE = re.compile(r"-?\d[\d,]*\.\d+\s+[A-Z][A-Z0-9._-]*")


def _scannable_text(text: str) -> str:
    """The journal with posted amounts removed, for PII matching.

    Amounts here are RNG output. A real PII token cannot reach the demo
    through one -- it can only arrive in text the generator copies from
    source: a narration, an account name, a template field. Matching against
    the amounts therefore finds nothing real and collides constantly: a
    four-digit denylist token like "8003" is a substring of the perfectly
    innocent salary "280032.00", and the build fails on a coincidence.

    That is not hypothetical tidying. The failure is seasonal now that the
    demo window moves with the build date: the same SEED lands on different
    amounts each month, so a month that happens to generate a colliding
    figure would break `run_demo.sh` for everyone, for that whole month, with
    a "PII CHECK FAILED" message pointing at nothing.

    Digits are preserved everywhere else, so a card fragment or account number
    embedded in a narration ("XXXX8003") is still caught.
    """
    return _POSTED_AMOUNT_RE.sub(" ", text)


def check_pii(storage_dir: Path) -> list[str]:
    """Return human-readable hits of any real PII token in the generated ledger."""
    hits: list[str] = []
    journal = storage_dir / "journal"
    for path in sorted(journal.glob("*.beancount")) if journal.exists() else []:
        text = _scannable_text(path.read_text(encoding="utf-8", errors="replace"))
        for token in PII_TOKENS:
            if token in text:
                hits.append(f"{path.name}: {token!r}")
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # dest= is load-bearing: main() reads args.storage_dir, and without this
    # argparse derives args.data_dir from the flag name, so every CLI run died
    # with AttributeError before reaching the build. The flag name is the
    # documented one (RUNBOOK, run_demo.sh), so the dest moves, not the flag.
    parser.add_argument(
        "--data-dir",
        dest="storage_dir",
        type=Path,
        default=ROOT / "demo" / "data",
    )
    parser.add_argument(
        "--check", action="store_true", help="PII scan only; do not rebuild"
    )
    args = parser.parse_args()

    if not args.check:
        journal = build_demo(args.storage_dir)
        n = len(list((journal).glob("*.beancount")))
        print(f"built demo ledger -> {journal} ({n} year file(s))")

    hits = check_pii(args.storage_dir)
    if hits:
        print("PII CHECK FAILED — real tokens found:")
        for hit in hits:
            print(f"  {hit}")
        return 1
    print("PII check passed (0 real tokens)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
