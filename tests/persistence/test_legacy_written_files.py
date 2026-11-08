"""Files on disk outlive a code move: what the pre-restructure stores wrote
must still load, field for field, through the persistence classes.

Every file under `tests/fixtures/persistence/legacy_written/` was written by
the stores at their OLD import paths (commit a8e89cc, before the persistence
move), from fictional data, with every field the models allow set away from
its default, so a field that a later model drops or renames cannot pass by
falling back to that default. The expected values below are literals, not
re-read from the files, so a changed file or a changed loader both fail.

Review batches and ledger-merge batches are not here: both stores are
in-memory and never touch disk.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import yaml
from beancount.core import data

from backend.app.core.paths import REPO_ROOT
from backend.app.persistence import tax_inputs
from backend.app.persistence.journal import Ledger
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.persistence.nav_store import NavStore
from backend.app.persistence.price_cache import PriceCache
from backend.app.persistence.templates import PdfTemplateStore, TemplateStore
from backend.app.persistence.yaml_stores import BudgetStore, MergeBookingStore

_LEGACY = REPO_ROOT / "tests" / "fixtures" / "persistence" / "legacy_written"

# TemplateStore.save wrote `Template.model_dump()` verbatim, so a load must
# give back exactly this dict.
CSV_TEMPLATE = {
    "fingerprint": "f1c7e0a0b1",
    "institution": "Fictive Bank Savings",
    "header_row_index": 3,
    "date_column": "Txn Date",
    "date_format": "%d-%m-%Y",
    "narration_column": "Particulars",
    "delimiter": ";",
    "debit_column": "Withdrawal",
    "credit_column": "Deposit",
    "amount_column": "Amount",
    "sign_column": "Dr/Cr",
    "credit_indicators": ["C", "CRD"],
    "account_marker_pattern": "A/c No: X+(\\d{4})",
    "account_marker_map": {"7788": "Assets:Bank:FictiveBank"},
    "default_account": "Assets:Bank:FictiveBank",
    "stop_pattern": "^Closing Balance",
    "skip_row_patterns": ["^Opening Balance"],
    "skip_exact_narrations": ["B/F"],
    "amount_sign": "signed",
    "balance_column": "Balance",
    "origin": "agent",
}

# PdfTemplateStore.save wrote `PdfTemplate.model_dump()` verbatim.
PDF_TEMPLATE = {
    "institution": "Fictive Card",
    "default_account": "Liabilities:CreditCard:Fictive",
    "date_format": "%d/%m/%Y",
    "row_pattern": "^(?P<date>\\d{2}/\\d{2}/\\d{4})\\s?\\|?\\s*\\d{2}:\\d{2}(?:\\s+(?P<description>.+?))??(?:\\s+\\+\\s(?P<rewards>\\d+))?\\s+(?P<credit>\\+\\s)?C\\s(?P<amount>[\\d,]+\\.\\d{2})(?:\\s+l)?$",
    "section_start": ["^DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI$"],
    "section_end": "^TRANSACTIONS TOTAL AMOUNT",
    "continuation_pattern": "^(?!\\d{2}/\\d{2}/\\d{4}).+$",
    "skip_patterns": ["^(Domestic|International) Transactions$", "^Page \\S+ of \\S+$"],
    "manual_sections": [
        {"name": "EMI", "start": "^EMI / Personal Loan", "end": "^Page \\S+ of \\S+$"}
    ],
    "summary": {
        "previous_balance": {
            "label": "PREVIOUS STATEMENT DUES",
            "line_offset": 4,
            "date_format": None,
        },
        "payments_credits": {
            "label": "PAYMENTS/CREDITS",
            "line_offset": 4,
            "date_format": None,
        },
        "total_due": {
            "label": "TOTAL AMOUNT DUE",
            "line_offset": 1,
            "date_format": None,
        },
        "minimum_due": {"label": "MINIMUM DUE", "line_offset": 1, "date_format": None},
        "due_date": {"label": "DUE DATE", "line_offset": 1, "date_format": "%d %b, %Y"},
        "transactions_total": {
            "label": "^TRANSACTIONS TOTAL AMOUNT",
            "line_offset": 0,
            "date_format": None,
        },
        "period_start": {
            "label": "Billing Period",
            "line_offset": 0,
            "date_format": "%d %b, %Y",
        },
        "period_end": {
            "label": "Billing Period",
            "line_offset": 0,
            "date_format": "%d %b, %Y",
        },
    },
    "summary_rows": [
        {
            "label": "PURCHASES/DEBIT",
            "line_offset": 4,
            "roles": ["purchases_debits", "finance_charges"],
            "labels": ["PURCHASES/DEBIT", "FINANCE CHARGES"],
        }
    ],
    "card_pattern": "Card No: (?:[\\dX*][ -]?){12}(?P<last4>\\d{4})",
    "finance_charges_listed": True,
    "total_due_rounding": "rupee",
    "description_around": True,
    "fingerprint": "a18f442977c4fa07",
    "card_last4": "4242",
    "origin": "user",
}

# BudgetStore / MergeBookingStore wrote `model_dump()` with sorted keys; key
# order is not compared. `version: 1` is the only version either schema has,
# so it is the one field left at its default.
BUDGET = {
    "targets": {"Expenses:Food": "8000", "Expenses:Travel": "2500.50"},
    "version": 1,
}

MERGE_BOOKING = {
    "charge_stamp_duty_account": "Expenses:Investment:Charges:FictiveStamp",
    "charge_stt_account": "Expenses:Investment:Charges:FictiveSTT",
    "charge_tolerance": "25",
    "charge_tolerance_pct": "0.0007",
    "charge_unexplained_account": "Expenses:Investment:Charges:FictiveOther",
    "charges_account": "Expenses:Investment:Charges:Fictive",
    "ltcg_account": "Income:Gains:FictiveLT",
    "ltcg_min_holding_days": 400,
    "stamp_duty_rate": "0.00006",
    "stcg_account": "Income:Gains:FictiveST",
    "stt_rate_sell": "0.001",
    "version": 1,
}

# LearnedRules.add_many upper-cased each pattern on write (the old writer's
# one transform); later rules win on `match`.
LEARNED = [
    {"pattern": "FICTIVEBIKES", "category": "Expenses:Travel:Bike"},
    {"pattern": "KITEWORKS PRINT", "category": "Expenses:Office"},
    {"pattern": "FICTIVE TIFFINS", "category": "Expenses:Food:Lunch"},
]

TAX_INPUTS_2025 = {
    "regime": "old",
    "income": {"gross_salary": "1200000", "tds": "95000", "exempt_hra_lta": "60000"},
    "deductions": {"80c": "150000", "80d": "25000"},
}

NAVALL_BODY = "Scheme Code;Scheme Name;Net Asset Value;Date\n900001;Fictive Flexi Cap Fund;123.4567;24-Jun-2026\n900002;Fictive Liquid Fund;1012.0001;24-Jun-2026\n"

NAVALL_META = {
    "etag": '"etag-fictive"',
    "last_modified": "Wed, 24 Jun 2026 10:00:00 GMT",
    "fetched_at": "2026-10-31T18:20:11.647910+00:00",
    "source": "live",
    "as_of": "2026-06-24",
}

NAV_ROWS = {
    ("900001", date(2026, 6, 23)): Decimal("121.0000"),
    ("900001", date(2026, 6, 24)): Decimal("123.4567"),
    ("900002", date(2026, 6, 24)): Decimal("1012.0001"),
}

# In beancount's load order (on one date, Open sorts before Commodity).
JOURNAL = [
    ("open", date(2000, 1, 1), "Assets:Bank:FictiveBank", ["INR"]),
    ("open", date(2000, 1, 1), "Expenses:Food", ["INR"]),
    ("open", date(2000, 1, 1), "Income:Salary", ["INR"]),
    ("commodity", date(2000, 1, 1), "INR"),
    (
        "txn",
        date(2025, 12, 31),
        "*",
        None,
        "FICTIVE PAYROLL",
        [
            ("Assets:Bank:FictiveBank", Decimal("50000.00"), "INR"),
            ("Income:Salary", Decimal("-50000.00"), "INR"),
        ],
    ),
    (
        "txn",
        date(2026, 6, 21),
        "*",
        None,
        "FICTIVE TIFFINS",
        [
            ("Expenses:Food", Decimal("240.00"), "INR"),
            ("Assets:Bank:FictiveBank", Decimal("-240.00"), "INR"),
        ],
    ),
]


def _yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _entry(entry: object) -> tuple:
    if isinstance(entry, data.Commodity):
        return ("commodity", entry.date, entry.currency)
    if isinstance(entry, data.Open):
        return ("open", entry.date, entry.account, entry.currencies)
    if isinstance(entry, data.Transaction):
        return (
            "txn",
            entry.date,
            entry.flag,
            entry.payee,
            entry.narration,
            [(p.account, p.units.number, p.units.currency) for p in entry.postings],
        )
    raise AssertionError(f"unexpected journal entry {entry!r}")


def test_files_written_before_restructure_still_load(tmp_path: Path) -> None:
    # A copy: the stores create directories, and the fixture must stay as written.
    root = tmp_path / "storage"
    shutil.copytree(_LEGACY, root)
    assert sorted(p.name for p in root.iterdir()) == [
        "config",
        "journal",
        "prices",
        "rules",
        "templates",
    ]

    csv = TemplateStore(root / "templates").list()
    # `opening_balance` was added after the file was written: it loads unset.
    assert [t.model_dump() for t in csv] == [{**CSV_TEMPLATE, "opening_balance": None}]

    pdf = PdfTemplateStore(root / "templates" / "pdf").list()
    assert [t.model_dump() for t in pdf] == [PDF_TEMPLATE]

    assert BudgetStore(root / "config").load().model_dump() == BUDGET
    assert MergeBookingStore(root / "config").load().model_dump() == MERGE_BOOKING

    # LearnedRules exposes no list, so each rule is read back by its exact
    # pattern, and the file is pinned for rules that are not expected.
    rules = LearnedRules(root / "rules" / "learned.yaml")
    assert _yaml(root / "rules" / "learned.yaml") == LEARNED
    for rule in LEARNED:
        assert rules.exact(rule["pattern"]) == rule["category"]
    assert rules.match("UPI KITEWORKS PRINT 7") == "Expenses:Office"

    # `plain` turns ruamel's round-trip nodes into str/dict; the values are
    # already strings on disk.
    declared = tax_inputs.read(root, 2025)
    assert declared.exists and declared.parse_error is None
    assert tax_inputs.plain(declared.document) == TAX_INPUTS_2025
    assert tax_inputs.read_mapping(root, 2025) == TAX_INPUTS_2025
    assert tax_inputs.declared_regime(root, 2025) == "old"

    cache = PriceCache(root / "prices")
    dump = cache.read()
    assert dump is not None
    assert dump.body == NAVALL_BODY
    assert (dump.etag, dump.last_modified) == (
        NAVALL_META["etag"],
        NAVALL_META["last_modified"],
    )
    assert cache.read_outcome() == (NAVALL_META["source"], NAVALL_META["as_of"])
    # `fetched_at` has no reader on PriceCache; pin the whole metadata file.
    meta = json.loads((root / "prices" / "navall.meta.json").read_text("utf-8"))
    assert meta == NAVALL_META

    parquet = root / "prices" / "nav_history.parquet"
    navs = NavStore(parquet)
    assert navs.exact(set(NAV_ROWS)) == NAV_ROWS
    assert pq.read_table(parquet).num_rows == len(NAV_ROWS)
    for code in ("900001", "900002"):
        assert [(r.date, r.nav) for r in navs.history(code)] == sorted(
            (d, nav) for (c, d), nav in NAV_ROWS.items() if c == code
        )
    assert navs.as_of() == date(2026, 6, 24)

    loaded = Ledger(root).load()
    assert loaded.errors == []
    assert loaded.options_map["title"] == "Fictive Journal"
    assert loaded.options_map["operating_currency"] == ["INR"]
    assert [_entry(e) for e in loaded.entries] == JOURNAL
