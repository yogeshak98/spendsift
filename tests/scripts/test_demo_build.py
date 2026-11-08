"""The demo build must produce a valid, multi-year, canonical, PII-free ledger."""

from __future__ import annotations

from beancount import loader
from beancount.core import getters

from scripts.build_demo import build_demo, check_pii

EXPECTED_ACCOUNTS = {
    "Assets:Checking:HDFC",
    "Assets:Checking:ICICI",
    "Assets:Investment:FD:HDFC",
    "Assets:Investment:MutualFund:Axis:Bluechip",
    "Assets:Investment:RealEstate:Home",
    "Assets:Receivable:JaneDoe",
    "Expenses:Food",
    "Expenses:Rent",
    "Expenses:Shopping",
    "Expenses:Groceries",
    "Expenses:Travel",
    "Expenses:Fuel",
    "Expenses:Entertainment",
    "Expenses:Health",
    "Expenses:Utilities",
    "Expenses:Interest:HomeLoan",
    "Income:Salary",
    "Income:CapitalGains:LTCG",
    "Income:CapitalGains:STCG",
    "Income:Interest:Savings:HDFC",
    "Income:Cashback:CreditCard:HDFC",
    "Income:Cashback:CreditCard:ICICI",
    "Income:Refund:CreditCard:HDFC",
    "Liabilities:CreditCard:HDFC",
    "Liabilities:CreditCard:ICICI",
    "Liabilities:Loan:HDFC:Home",
    "Equity:Opening-Balances",
}


def _load(tmp_path):
    journal = build_demo(tmp_path / "data")
    return loader.load_file(str(journal / "main.beancount"))


def test_demo_ledger_loads_without_errors(tmp_path):
    entries, errors, _ = _load(tmp_path)
    assert errors == [], [e.message for e in errors]


def test_demo_spans_at_least_two_years(tmp_path):
    entries, _, _ = _load(tmp_path)
    dates = [e.date for e in entries if type(e).__name__ == "Transaction"]
    assert len(dates) >= 300
    assert (max(dates) - min(dates)).days >= 730


def test_demo_covers_canonical_taxonomy(tmp_path):
    entries, _, _ = _load(tmp_path)
    accounts = set(getters.get_accounts(entries))
    missing = EXPECTED_ACCOUNTS - accounts
    assert not missing, f"missing canonical accounts: {sorted(missing)}"


def test_demo_has_no_real_pii(tmp_path):
    storage_dir = tmp_path / "data"
    build_demo(storage_dir)
    assert check_pii(storage_dir) == []


def test_demo_realizes_capital_gains(tmp_path):
    """The demo books both a long- and short-term realized gain so the tax
    workspace's capital-gains buckets are populated, not empty."""
    entries, _, _ = _load(tmp_path)
    by_account: dict[str, object] = {}
    for entry in entries:
        if type(entry).__name__ != "Transaction":
            continue
        for posting in entry.postings:
            by_account.setdefault(posting.account, []).append(posting.units.number)
    # Gains are credits to Income:CapitalGains:* (negative amounts).
    assert sum(by_account.get("Income:CapitalGains:LTCG", [])) == -30000
    assert sum(by_account.get("Income:CapitalGains:STCG", [])) == -12000


def test_demo_seeds_fy2026_tax_rules_so_workspace_is_configured(tmp_path):
    """The demo ships config/tax/2026.yaml so the tax workspace produces a
    liability estimate instead of the 'not configured' fallback."""
    from backend.app.persistence.journal import Ledger
    from backend.app.service.tax.rules import load_declared_deductions, load_tax_rules
    from backend.app.service.reports.health.ledger_view import transactions_in_fy
    from backend.app.service.tax.report import tax_workspace

    storage_dir = tmp_path / "data"
    build_demo(storage_dir)
    assert (storage_dir / "config" / "tax" / "2026.yaml").exists()

    rules = load_tax_rules(storage_dir, 2026)
    assert rules is not None and rules.fy == 2026

    ws = tax_workspace(
        transactions_in_fy(Ledger(storage_dir), 2026),
        2026,
        rules,
        load_declared_deductions(storage_dir, 2026),
    )
    assert ws["summary"]["rules_configured"] is True
    assert ws["summary"]["estimate"] is not None
