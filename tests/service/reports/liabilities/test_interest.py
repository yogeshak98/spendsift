from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction, Split
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.reports.liabilities.interest import by_account_by_month
from backend.app.service.reports.health.ledger_view import all_transactions


def test_groups_canonical_subaccount_interest_to_its_loan(ledger, investment_ledger):
    # Canonical taxonomy: a home-loan EMI splits into principal (Liabilities:Loan:*)
    # and interest (Expenses:Interest:HomeLoan, a sub-account). The report must
    # attribute that interest to the loan, not ignore it for not being a flat
    # "Expenses:Interest".
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 5),
                amount=Decimal("15000"),
                raw_narration="HOME LOAN EMI",
                normalized_narration="HOME LOAN EMI",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                splits=[
                    Split(
                        account="Liabilities:Loan:HDFC:Home", amount=Decimal("12000")
                    ),
                    Split(account="Expenses:Interest:HomeLoan", amount=Decimal("3000")),
                ],
            )
        ],
    )
    out = by_account_by_month(all_transactions(ledger))
    assert out == [
        {"account": "Liabilities:Loan:HDFC:Home", "month": "2026-04", "amount": "3000"}
    ]


def test_groups_interest_charges_by_liability_account_and_month(
    ledger, investment_ledger
):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 15),
                amount=Decimal("250"),
                raw_narration="INTEREST CHARGED",
                normalized_narration="INTEREST CHARGED",
                source_account="Liabilities:CreditCard:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Interest",
            ),
            CandidateTxn(
                date=date(2026, 5, 1),
                amount=Decimal("412"),
                raw_narration="Swiggy",
                normalized_narration="SWIGGY",
                source_account="Assets:Bank:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Food",
            ),
        ],
    )
    out = by_account_by_month(all_transactions(ledger))
    assert out == [
        {"account": "Liabilities:CreditCard:HDFC", "month": "2026-04", "amount": "250"}
    ]


def test_no_interest_returns_empty(ledger, investment_ledger):
    out = by_account_by_month(all_transactions(ledger))
    assert out == []
