"""Doctor's additive `openings` block."""

from __future__ import annotations

from datetime import date

from backend.app.persistence.journal import Ledger
from backend.app.service.reports.health import doctor

_JOURNAL = """option "operating_currency" "INR"

2000-01-01 commodity INR
2020-01-01 open Assets:Checking:Acme INR
2020-01-01 open Liabilities:People:Ravi INR
2020-01-01 open Liabilities:Loan:Home INR
2020-01-01 open Expenses:Food INR
2020-01-01 open Equity:Opening-Balances INR

2025-03-31 * "Initial Balance"
  Assets:Checking:Acme        5000.00 INR
  Equity:Opening-Balances    -5000.00 INR

2025-05-10 * "Paid for Ravi"
  Liabilities:People:Ravi      250.00 INR
  Assets:Checking:Acme        -250.00 INR

2025-08-02 * "Dinner"
  Liabilities:People:Ravi      -40.00 INR
  Expenses:Food                 40.00 INR
"""


def test_doctor_lists_missing_openings(tmp_path):
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap(seed_accounts=())
    ledger.main_file.write_text(_JOURNAL, encoding="utf-8")

    result = doctor.checks(ledger, today=date(2028, 1, 15))
    # Only the account with activity and no opening; the empty loan and the
    # opened bank are not listed.
    assert result["openings"] == {
        "missing_count": 1,
        "missing": [
            {
                "account": "Liabilities:People:Ravi",
                "account_display": "Liabilities:People:Ravi",
                "first_posting": "2025-05-10",
            }
        ],
    }
    # The existing keys are all still there, unchanged in kind.
    assert {
        "journal_valid",
        "errors",
        "transaction_count",
        "uncategorized_count",
        "uncategorized",
        "negative_assets",
    } <= set(result)
    assert result["journal_valid"] is True
    assert result["transaction_count"] == 3


def test_doctor_openings_block_without_today_uses_the_same_rows(tmp_path):
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap(seed_accounts=())
    ledger.main_file.write_text(_JOURNAL, encoding="utf-8")
    assert doctor.checks(ledger)["openings"]["missing_count"] == 1


def test_doctor_lists_an_account_whose_opening_is_late(tmp_path):
    """Ravi's only opening-shaped entry is dated after his first activity:
    the activity before it has no opening, so Doctor lists him."""
    late = (
        _JOURNAL
        + """
2025-12-01 * "Liability Reset"
  Liabilities:People:Ravi      100.00 INR
  Equity:Opening-Balances     -100.00 INR
"""
    )
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap(seed_accounts=())
    ledger.main_file.write_text(late, encoding="utf-8")

    assert doctor.checks(ledger, today=date(2028, 1, 15))["openings"] == {
        "missing_count": 1,
        "missing": [
            {
                "account": "Liabilities:People:Ravi",
                "account_display": "Liabilities:People:Ravi",
                "first_posting": "2025-05-10",
            }
        ],
    }
