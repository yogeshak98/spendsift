"""Pure pieces of Net Worth · Overview: grouping, cover, benchmarks, store."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.models.storage.benchmarks import BenchmarksConfig
from backend.app.persistence.benchmarks_store import BenchmarksStore
from backend.app.service.reports.health.ledger_view import PostingRow
from backend.app.service.reports.networth import benchmarks as bm
from backend.app.service.reports.networth.account_groups import (
    LABELS,
    group_of,
    vehicle_of,
)
from backend.app.service.reports.networth.liquidity import (
    average_monthly_spend,
    months_of_cover,
)


def _row(d: date, account: str, amount: str) -> PostingRow:
    return PostingRow(d, account, Decimal(amount), "", frozenset())


def test_group_labels_match_the_frontend():
    """The Net Worth cards (backend labels) and the Ledger group filter
    (frontend `GROUP_LABELS`) name each group the same way."""
    ts = (
        Path(__file__).parents[4] / "frontend/src/features/networth/accountGroups.ts"
    ).read_text(encoding="utf-8")
    block = ts.split("export const GROUP_LABELS", 1)[1].split("};", 1)[0]
    assert dict(re.findall(r'(\w+): "([^"]+)"', block)) == LABELS


@pytest.mark.parametrize(
    ("account", "group"),
    [
        ("Assets:Investment:MutualFund:Groww:MF1", "investments"),
        ("Assets:Debt:PPF", "investments"),
        ("Assets:NPS", "investments"),
        ("Assets:House:Home", "property"),
        ("Assets:Investment:RealEstate:Plot", "property"),
        ("Assets:Checking:HDFC", "bank"),
        ("Assets:Cash", "bank"),
        ("Assets:Wallet:Pay", "other"),
        ("Assets:Receivable:TDS", None),
        ("Assets:Receivable:AdvanceTax", None),
        ("Liabilities:Loan:Bike", "loans"),
        ("Liabilities:CreditCard:X1", "cards"),
        ("Liabilities:People:Sam", "people"),
        ("Liabilities:Cash:Misc", "other_liabilities"),
        ("Expenses:Food", None),
    ],
)
def test_group_of(account, group):
    assert group_of(account) == group


def test_vehicle_of():
    assert vehicle_of("Assets:Investment:MutualFund:Groww:MF1") == (
        "mutual_fund",
        "Mutual Fund",
    )
    assert vehicle_of("Assets:Investment:FD:HDFC") == ("fd", "FD")
    assert vehicle_of("Assets:Debt:PPF") == ("ppf", "PPF")
    assert vehicle_of("Assets:NPS") == ("nps", "NPS")
    assert vehicle_of("Assets:Cash") is None


def test_average_monthly_spend_uses_the_three_complete_months_before_as_of():
    rows = [
        _row(date(2026, 7, 10), "Expenses:Food", "99999"),  # outside the window
        _row(date(2026, 8, 10), "Expenses:Food", "3000"),
        _row(date(2026, 9, 10), "Expenses:Food", "6000"),
        _row(date(2026, 10, 10), "Expenses:Food", "9000"),
        _row(date(2026, 10, 11), "Expenses:Investment:Stamp", "500"),  # not spending
        _row(date(2026, 11, 3), "Expenses:Food", "70000"),  # as_of month: excluded
    ]
    assert average_monthly_spend(rows, date(2026, 11, 15)) == Decimal(6000)


def test_average_monthly_spend_spans_a_year_boundary_and_skips_empty_months():
    rows = [_row(date(2025, 12, 5), "Expenses:Food", "4000")]
    assert average_monthly_spend(rows, date(2026, 1, 20)) == Decimal(4000)
    assert average_monthly_spend([], date(2026, 1, 20)) is None


def test_months_of_cover_rounds_half_up_and_guards_edges():
    assert months_of_cover(Decimal(141000), Decimal(20000)) == "7.1"  # 7.05
    assert months_of_cover(Decimal(0), Decimal(20000)) == "0.0"
    assert months_of_cover(Decimal(-5), Decimal(20000)) == "0.0"
    assert months_of_cover(Decimal(1000), None) is None


def test_blended_return_weights_by_value_and_leaves_uncovered_vehicles_out():
    config = BenchmarksConfig()
    values = {
        "mutual_fund": Decimal(600),
        "fd": Decimal(300),
        "gold_coin": Decimal(100),  # not in the blend
    }
    rate, weights = bm.blended_return(config, values, "nifty500")
    # (600 * 12.5 + 300 * 7.0) / 900
    assert abs(rate - Decimal("10.6666667")) < Decimal("0.0001")
    assert [(w["vehicle"], w["weight_pct"]) for w in weights] == [
        ("mutual_fund", "66.67"),
        ("fd", "33.33"),
    ]
    assert bm.blended_return(config, {}, "nifty50") == (None, [])


def test_check_selection_rejects_unknown_keys_and_non_equity_index():
    config = BenchmarksConfig()
    bm.check_selection(config, "blended", "sensex")
    with pytest.raises(CodedError) as caught:
        bm.check_selection(config, "nope", "nifty50")
    assert caught.value.code == "E1096"
    with pytest.raises(CodedError) as caught:
        bm.check_selection(config, "nifty50", "gold")
    assert caught.value.code == "E1096"


def test_store_seeds_a_commented_file_once_and_reads_edits(tmp_path):
    store = BenchmarksStore(tmp_path / "config")
    config = store.load()
    assert config.benchmarks["nifty50"].annual_return_pct == "12.0"
    text = store.path.read_text(encoding="utf-8")
    assert text.startswith("# Benchmarks for Net Worth")
    store.path.write_text(text.replace("'12.0'", "'9.5'", 1), encoding="utf-8")
    assert store.load().benchmarks["nifty50"].annual_return_pct == "9.5"


def test_store_rejects_a_blend_pointing_at_nothing(tmp_path):
    store = BenchmarksStore(tmp_path)
    store.path.write_text("blend: {stock: nonexistent}\n", encoding="utf-8")
    with pytest.raises(CodedError) as caught:
        store.load()
    assert caught.value.code == "E1097"
