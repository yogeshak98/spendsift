"""The income-kind partition and its tax treatments.

Every amount in these fixtures is invented. The defect being pinned is that a
single `unclassified_income` arm aggregated statutory-exempt terminal benefits,
a tax refund and peer transfers into one row inside a table whose total
excluded all of it. A prefix
landing in the wrong group is a figure in the wrong column, so the mapping is
asserted prefix by prefix rather than through a total that several wrong
partitions would also produce.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.service.tax import report as tax
from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow

# (account, kind, treatment) for every arm of the prefix table, in the
# precedence order the production tuple states it. `Income:Tax:Refund` and
# `Income:Refund` are both present because the first does not match the
# second's prefix -- that miss is what classified a tax refund as income.
_PREFIX_TABLE = [
    ("Income:Gratuity:Acme", "exempt_gratuity", "exempt"),
    ("Income:LeaveEncashment:Acme", "exempt_leave_encashment", "exempt"),
    ("Income:Tax:Refund", "refund", "not_income"),
    ("Income:Refund:CreditCard:HDFC", "refund", "not_income"),
    ("Income:Cashback:CreditCard", "refund", "not_income"),
    ("Income:People:Friend", "transfers_from_people", "not_income"),
    ("Income:Salary:Acme", "salary", "taxed"),
    ("Income:Interest:Savings:HDFC", "interest", "taxed"),
    ("Income:CapitalGains:LTCG", "capital_gain_ltcg", "taxed"),
    ("Income:CapitalGains:STCG", "capital_gain_stcg", "taxed"),
    ("Income:CapitalGains:Crypto", "capital_gain_crypto", "taxed"),
    ("Income:CapitalGains:Debt", "capital_gain_debt", "taxed"),
    ("Income:Bonus", "unclassified_income", "unknown"),
]


@pytest.mark.parametrize(("account", "kind", "treatment"), _PREFIX_TABLE)
def test_every_income_prefix_maps_to_its_kind_and_treatment(
    account: str, kind: str, treatment: str
) -> None:
    assert tax._income_kind(account) == kind, f"{account} landed in the wrong kind"
    assert (
        tax.income_treatment(kind) == treatment
    ), f"{account} ({kind}) landed in the wrong treatment group"


def test_tax_refund_is_not_income() -> None:
    """`Income:Tax:Refund` must be named a refund, not swallowed by the
    catch-all.

    Asserting only that it is excluded from the taxable base would pass either
    way -- `unclassified_income` is excluded too -- so this asserts the kind
    and the treatment, which is what puts the row under "Not income" rather
    than under an unexplained bucket."""
    assert tax._income_kind("Income:Tax:Refund") == "refund"
    assert tax.income_treatment("refund") == "not_income"


def test_card_cashback_is_not_income() -> None:
    """`Income:Cashback:CreditCard` (card cashback and bill-payment rewards)
    is a refund, not `unclassified_income`: the account was renamed from
    `Income:Refund:CreditCard:*` and must keep its non-taxable treatment."""
    assert tax._income_kind("Income:Cashback:CreditCard") == "refund"
    assert tax._income_kind("Income:Cashback") == "refund"
    assert tax.income_treatment("refund") == "not_income"


def test_unclassified_income_is_unknown_not_not_income() -> None:
    """An unrecognised `Income:*` account is `unknown`, never `not_income`.

    A hypothetical `Income:Bonus` would be fully taxable and is silently
    untaxed today; calling it "not income" would assert something false on
    screen. `unknown` means excluded from the base and we cannot say it should
    be."""
    assert tax.income_treatment("unclassified_income") == "unknown"
    assert tax.income_treatment(tax._income_kind("Income:Bonus")) == "unknown"


def _txn(txn_id: str, when: date, *legs: tuple[str, str]) -> TransactionRow:
    postings = [
        PostingRow(
            date=when,
            account=account,
            amount=Decimal(amount),
            narration="x",
            tags=frozenset(),
            meta={},
        )
        for account, amount in legs
    ]
    return TransactionRow(
        txn_id=txn_id,
        date=when,
        narration="x",
        postings=postings,
        year_file_hash="h",
    )


def _split_journal() -> list[TransactionRow]:
    """One transaction per treatment: exempt, not-income and taxed together."""
    when = date(2026, 6, 1)
    return [
        _txn(
            "g1",
            when,
            ("Income:Gratuity:Acme", "-700000"),
            ("Assets:Checking:HDFC", "700000"),
        ),
        _txn(
            "le1",
            when,
            ("Income:LeaveEncashment:Acme", "-310000"),
            ("Assets:Checking:HDFC", "310000"),
        ),
        _txn(
            "r1",
            when,
            ("Income:Tax:Refund", "-21000"),
            ("Assets:Checking:HDFC", "21000"),
        ),
        _txn(
            "p1",
            when,
            ("Income:People:Friend", "-4300"),
            ("Assets:Checking:HDFC", "4300"),
        ),
        _txn(
            "s1",
            when,
            ("Income:Salary:Acme", "-820000"),
            ("Assets:Checking:HDFC", "820000"),
        ),
        _txn(
            "i1",
            when,
            ("Income:Interest:Savings:HDFC", "-6100"),
            ("Assets:Checking:HDFC", "6100"),
        ),
    ]


def test_gross_ordinary_income_is_salary_plus_interest_only() -> None:
    """The split is classification and disclosure: it must move no rupee into
    or out of the taxable base. Over a journal carrying all four treatments,
    `gross_ordinary_income` is exactly salary + interest."""
    items, summary = tax.classify_items(_split_journal())

    assert Decimal(summary["gross_ordinary_income"]) == Decimal("820000") + Decimal(
        "6100"
    )
    # The excluded rupees are still each NAMED -- the point of the split is
    # that nothing is dropped silently.
    by_kind = {item["kind"]: item for item in items}
    assert Decimal(by_kind["exempt_gratuity"]["amount"]) == Decimal("700000")
    assert Decimal(by_kind["exempt_leave_encashment"]["amount"]) == Decimal("310000")
    assert Decimal(by_kind["refund"]["amount"]) == Decimal("21000")
    assert Decimal(by_kind["transfers_from_people"]["amount"]) == Decimal("4300")
    # ... and every emitted income item carries the treatment the screen groups on.
    assert {k: v["treatment"] for k, v in by_kind.items()} == {
        "exempt_gratuity": "exempt",
        "exempt_leave_encashment": "exempt",
        "refund": "not_income",
        "transfers_from_people": "not_income",
        "salary": "taxed",
        "interest": "taxed",
    }
