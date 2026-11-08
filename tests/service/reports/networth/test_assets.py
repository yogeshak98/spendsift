from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.reports.networth.assets import balance_rows
from backend.app.service.reports.health.ledger_view import all_postings


def test_balance_rows_splits_inflow_outflow_and_nets_balance(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            # Salary credited into checking: inflow
            CandidateTxn(
                date=date(2026, 4, 1),
                amount=Decimal("80000"),
                raw_narration="Salary",
                normalized_narration="SALARY",
                source_account="Assets:Checking:HDFC",
                direction=Direction.CREDIT,
                suggested_other_leg="Income:Salary",
            ),
            # Card payment debited from checking: outflow
            CandidateTxn(
                date=date(2026, 4, 10),
                amount=Decimal("20000"),
                raw_narration="CC Payment",
                normalized_narration="CC PAYMENT",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Liabilities:CreditCard:HDFC",
            ),
        ],
    )
    rows = balance_rows(all_postings(ledger))
    acct = next(r for r in rows if r["account"] == "Assets:Checking:HDFC")
    assert acct["inflow"] == "80000"
    assert acct["outflow"] == "20000"
    assert acct["balance"] == "60000"  # 80000 - 20000


def test_balance_rows_only_includes_assets_accounts(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 2),
                amount=Decimal("4299"),
                raw_narration="Amazon",
                normalized_narration="AMAZON",
                source_account="Liabilities:CreditCard:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
        ],
    )
    rows = balance_rows(all_postings(ledger))
    assert all(r["account"].startswith("Assets:") for r in rows)
    assert not any(r["account"].startswith("Liabilities:") for r in rows)
    assert not any(r["account"].startswith("Expenses:") for r in rows)


def test_balance_rows_empty_is_zero_state(ledger, investment_ledger):
    rows = balance_rows(all_postings(ledger))
    assert rows == []
