from __future__ import annotations

from backend.app.models.storage.budget import BudgetConfig
from backend.app.service.reports.cashflow import budget
from backend.app.service.reports.health.ledger_view import all_postings


def test_zero_state(loaded_ledger):
    """Empty BudgetConfig → zero-state payload."""
    rows = all_postings(loaded_ledger)
    result = budget.report(rows, BudgetConfig(), 2026, 4)

    assert result["targets"] == []
    assert result["total_budgeted"] == "0"
    assert result["available_for_spending"] == "0"
    # available_for_budgeting == checking_balance (no budgeted amount to subtract)
    assert result["available_for_budgeting"] == result["checking_balance"]
    # projected_month_end == checking_balance (nothing expected to be spent)
    assert result["projected_month_end"] == result["checking_balance"]


def test_populated(loaded_ledger):
    """Populated BudgetConfig → correct card data and sort order."""
    rows = all_postings(loaded_ledger)
    config = BudgetConfig(targets={"Expenses:Rent": "35000", "Expenses:Food": "1000"})
    result = budget.report(rows, config, 2026, 4)

    cards_by_cat = {c["category"]: c for c in result["targets"]}

    # Rent: spent == budgeted, not over budget
    rent = cards_by_cat["Expenses:Rent"]
    assert rent["spent"] == "35000"
    assert rent["budgeted"] == "35000"
    assert rent["over_budget"] is False

    # Food: spent (412+888=1300) > budgeted (1000), over budget
    food = cards_by_cat["Expenses:Food"]
    assert food["spent"] == "1300"
    assert food["budgeted"] == "1000"
    assert food["over_budget"] is True

    # Food sorts before Rent (over-budget first)
    assert result["targets"][0]["category"] == "Expenses:Food"
    assert result["targets"][1]["category"] == "Expenses:Rent"

    # totals
    assert result["total_budgeted"] == "36000"
    assert result["total_spent"] == "36300"
    assert result["available_for_spending"] == "-300"
