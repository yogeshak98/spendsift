from __future__ import annotations

from datetime import date
from decimal import Decimal

from beancount import loader
from beancount.core import data
from beancount.core.amount import Amount
from beancount.parser import parser, printer

from backend.app.service.ingest.pipeline.statement_record import (
    already_recorded,
    statement_entry,
)
from backend.app.models.storage.statement_record import STATEMENT_TYPE, StatementSummary

ACCOUNT = "Liabilities:CreditCard:HDFC"


def _summary(**update) -> StatementSummary:
    values = dict(
        account=ACCOUNT,
        period_start=date(2026, 9, 21),
        period_end=date(2026, 10, 20),
        previous_balance=Decimal("8200"),
        payments_credits=Decimal("8200.00"),
        purchases_debits=Decimal("15345"),
        total_due=Decimal("15345"),
        minimum_due=Decimal("767.25"),
        due_date=date(2026, 11, 10),
    )
    values.update(update)
    return StatementSummary(**values)


def test_statement_entry_prints_and_reloads():
    entry = statement_entry(_summary())
    assert isinstance(entry, data.Custom)
    assert entry.type == STATEMENT_TYPE
    assert entry.date == date(2026, 10, 20)
    assert entry.values[0].value == ACCOUNT

    text = printer.format_entry(entry)
    entries, errors, _ = loader.load_string(f"2020-01-01 open {ACCOUNT}\n\n{text}")
    assert errors == []
    (reloaded,) = [e for e in entries if isinstance(e, data.Custom)]
    assert reloaded.meta["total-due"] == Amount(Decimal("15345.00"), "INR")
    assert str(reloaded.meta["total-due"]) == "15345.00 INR"
    assert reloaded.meta["previous-balance"] == Amount(Decimal("8200.00"), "INR")
    assert reloaded.meta["finance-charges"] == Amount(Decimal("0.00"), "INR")
    assert reloaded.meta["period-start"] == date(2026, 9, 21)
    assert reloaded.meta["due-date"] == date(2026, 11, 10)
    assert reloaded.meta["minimum-due"] == Amount(Decimal("767.25"), "INR")
    assert reloaded.meta["source"] == "pdf"
    assert "credit-limit" not in reloaded.meta


def test_statement_entry_omits_unset_optionals_and_prints_credit_limit():
    entry = statement_entry(
        _summary(minimum_due=None, due_date=None, credit_limit=Decimal("100000"))
    )
    assert "minimum-due" not in entry.meta
    assert "due-date" not in entry.meta
    assert entry.meta["credit-limit"] == Amount(Decimal("100000.00"), "INR")


def test_already_recorded_matches_account_and_period_end_only(ledger):
    assert not already_recorded(ledger, ACCOUNT, date(2026, 10, 20))
    # The year-file check in Ledger.validate inspects Transactions only, so a
    # Custom dated period_end goes through the ordinary append.
    ledger.append([statement_entry(_summary())])
    # Another custom type on the same card and a date of its own: never a match.
    (other,), _, _ = parser.parse_string(f'2026-09-20 custom "card-note" {ACCOUNT}\n')
    ledger.append([other])
    assert ledger.validate() == []

    assert already_recorded(ledger, ACCOUNT, date(2026, 10, 20))
    # Same account, another period end; same period end, another card.
    assert not already_recorded(ledger, ACCOUNT, date(2026, 9, 20))
    assert not already_recorded(ledger, ACCOUNT, date(2026, 9, 21))
    assert not already_recorded(
        ledger, "Liabilities:CreditCard:ICICI", date(2026, 10, 20)
    )


# Captured from statement_entry before cash advances and other charges
# existed (A.1): an HDFC record must stay byte-identical.
HDFC_GOLDEN = (
    '2026-10-20 custom "card-statement" Liabilities:CreditCard:HDFC\n'
    "  period-start: 2026-09-21\n"
    "  previous-balance: 8200.00 INR\n"
    "  payments: 8200.00 INR\n"
    "  purchases: 15345.00 INR\n"
    "  finance-charges: 120.00 INR\n"
    "  total-due: 15345.00 INR\n"
    "  minimum-due: 767.25 INR\n"
    "  due-date: 2026-11-10\n"
    "  credit-limit: 100000.00 INR\n"
    '  source: "pdf"\n'
)


def test_hdfc_record_text_unchanged():
    summary = _summary(finance_charges=Decimal("120"), credit_limit=Decimal("100000"))
    assert summary.cash_advances == 0 and summary.other_charges == 0
    assert printer.format_entry(statement_entry(summary)) == HDFC_GOLDEN


def test_axis_record_carries_other_charges_only_when_non_zero():
    axis = _summary(
        account="Liabilities:CreditCard:Axis",
        purchases_debits=Decimal("15246"),
        other_charges=Decimal("99"),
    )
    text = printer.format_entry(statement_entry(axis))
    assert "  other-charges: 99.00 INR\n" in text
    assert "cash-advances" not in text
    entries, errors, _ = loader.load_string(f"2020-01-01 open {axis.account}\n\n{text}")
    assert errors == []
    (reloaded,) = [e for e in entries if isinstance(e, data.Custom)]
    assert reloaded.meta["other-charges"] == Amount(Decimal("99.00"), "INR")

    with_cash = statement_entry(
        axis.model_copy(update={"cash_advances": Decimal("500")})
    )
    assert with_cash.meta["cash-advances"] == Amount(Decimal("500.00"), "INR")
    zero = statement_entry(axis.model_copy(update={"other_charges": Decimal("0")}))
    assert "other-charges" not in zero.meta and "cash-advances" not in zero.meta
