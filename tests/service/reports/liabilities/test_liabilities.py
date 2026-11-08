from datetime import date
from decimal import Decimal

import pytest

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    TransactionRow,
    all_postings,
    all_transactions,
)
from backend.app.models.domain.candidate import Split
from backend.app.service.reports.liabilities.liabilities import (
    balance_rows,
    balances,
    credit_card_detail,
    credit_card_monthly,
    credit_card_summary,
)
from backend.app.service.reports.liabilities.loan_terms import LoanTerms


def test_liability_balances(ledger, investment_ledger):
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
    out = balances(all_postings(ledger))
    cards = {r["account"]: r for r in out}
    assert cards["Liabilities:CreditCard:HDFC"]["outstanding"] == "4299"


def test_credit_card_summary_outstanding_and_last_activity(ledger, investment_ledger):
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
            CandidateTxn(
                date=date(2026, 4, 10),
                amount=Decimal("2000"),
                raw_narration="Payment",
                normalized_narration="PAYMENT",
                source_account="Liabilities:CreditCard:HDFC",
                direction=Direction.CREDIT,
                suggested_other_leg="Assets:Bank:HDFC",
            ),
        ],
    )
    out = credit_card_summary(all_transactions(ledger), expiry_soon_days=60)
    card = next(r for r in out if r["account"] == "Liabilities:CreditCard:HDFC")
    assert card["outstanding"] == "2299.00"
    assert card["last_activity_date"] == "2026-04-10"


def test_credit_card_detail_flags_payments_vs_purchases(ledger, investment_ledger):
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
            CandidateTxn(
                date=date(2026, 4, 10),
                amount=Decimal("2000"),
                raw_narration="Payment",
                normalized_narration="PAYMENT",
                source_account="Liabilities:CreditCard:HDFC",
                direction=Direction.CREDIT,
                suggested_other_leg="Assets:Bank:HDFC",
            ),
        ],
    )
    rows = credit_card_detail(all_transactions(ledger), "Liabilities:CreditCard:HDFC")
    assert rows[0]["narration"] == "Payment"
    assert rows[0]["is_payment"] is True
    assert rows[1]["narration"] == "Amazon"
    assert rows[1]["is_payment"] is False


def test_balance_rows_splits_drawn_repaid_and_nets_balance(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            # Draw on the card: liability grows (DEBIT spend on the card)
            CandidateTxn(
                date=date(2026, 4, 2),
                amount=Decimal("4299"),
                raw_narration="Amazon",
                normalized_narration="AMAZON",
                source_account="Liabilities:CreditCard:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
            # Repay the card from a bank account
            CandidateTxn(
                date=date(2026, 4, 10),
                amount=Decimal("2000"),
                raw_narration="Payment",
                normalized_narration="PAYMENT",
                source_account="Liabilities:CreditCard:HDFC",
                direction=Direction.CREDIT,
                suggested_other_leg="Assets:Bank:HDFC",
            ),
        ],
    )
    rows = balance_rows(all_postings(ledger), all_transactions(ledger), LoanTerms({}))
    card = next(r for r in rows if r["account"] == "Liabilities:CreditCard:HDFC")
    assert card["drawn"] == "4299"  # magnitude of negative deltas
    assert card["repaid"] == "2000"  # positive deltas
    assert card["balance"] == "2299"  # 4299 - 2000, positive magnitude
    assert card["interest"] == "0"  # no interest postings here
    assert card["apr"] is None  # no loan-terms entry


def test_balance_rows_apr_from_loan_terms_and_interest_sum(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 2),
                amount=Decimal("5000000"),
                raw_narration="Home loan disbursal",
                normalized_narration="HOME LOAN",
                source_account="Liabilities:Loan:HDFCHome",
                direction=Direction.DEBIT,
                suggested_category="Assets:Property:Flat",
            ),
            # An interest charge: principal split + interest sub-account, so the
            # interest report attributes it to the sibling Liabilities posting.
            CandidateTxn(
                date=date(2026, 4, 28),
                amount=Decimal("34000"),
                raw_narration="EMI interest",
                normalized_narration="EMI INTEREST",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                splits=[
                    Split(
                        account="Liabilities:Loan:HDFCHome",
                        amount=Decimal("10000"),
                    ),
                    Split(
                        account="Expenses:Interest:HomeLoan",
                        amount=Decimal("24000"),
                    ),
                ],
            ),
        ],
    )
    terms = LoanTerms(
        {
            "Liabilities:Loan:HDFCHome": {
                "annual_rate_pct": "8.5",
                "term_months": 240,
            }
        }
    )
    rows = balance_rows(all_postings(ledger), all_transactions(ledger), terms)
    loan = next(r for r in rows if r["account"] == "Liabilities:Loan:HDFCHome")
    assert loan["interest"] == "24000"
    assert loan["apr"] == "8.5"


def test_balance_rows_empty_is_zero_state(ledger, investment_ledger):
    rows = balance_rows(all_postings(ledger), all_transactions(ledger), LoanTerms({}))
    assert rows == []


def test_credit_card_monthly_aggregates_and_filters(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 2),
                amount=Decimal("1000"),
                raw_narration="A",
                normalized_narration="A",
                source_account="Liabilities:CreditCard:Acme",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
            CandidateTxn(
                date=date(2026, 4, 3),
                amount=Decimal("2000"),
                raw_narration="B",
                normalized_narration="B",
                source_account="Liabilities:CreditCard:Globex",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
            CandidateTxn(
                date=date(2026, 4, 10),
                amount=Decimal("500"),
                raw_narration="Pay",
                normalized_narration="PAY",
                source_account="Liabilities:CreditCard:Acme",
                direction=Direction.CREDIT,
                suggested_other_leg="Assets:Bank:HDFC",
            ),
        ],
    )
    txns = all_transactions(ledger)

    allc = credit_card_monthly(txns, 2026)
    assert allc["account"] is None
    assert allc["cards"] == [
        "Liabilities:CreditCard:Acme",
        "Liabilities:CreditCard:Globex",
    ]
    apr = next(m for m in allc["months"] if m["month"] == "2026-04")
    assert apr["purchases"] == "3000"  # 1000 + 2000
    assert apr["payments"] == "500"

    acme = credit_card_monthly(txns, 2026, "Liabilities:CreditCard:Acme")
    assert acme["account"] == "Liabilities:CreditCard:Acme"
    # cards list stays full even when filtered (selector must remain stable)
    assert acme["cards"] == [
        "Liabilities:CreditCard:Acme",
        "Liabilities:CreditCard:Globex",
    ]
    apr_a = next(m for m in acme["months"] if m["month"] == "2026-04")
    assert apr_a["purchases"] == "1000"
    assert apr_a["payments"] == "500"


def test_credit_card_monthly_carries_opening_balance(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            # Prior FY (before 1 Apr 2026): purchase 5000 -> opening debt 5000
            CandidateTxn(
                date=date(2026, 3, 20),
                amount=Decimal("5000"),
                raw_narration="Old",
                normalized_narration="OLD",
                source_account="Liabilities:CreditCard:Acme",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
            # In-FY April purchase 1000
            CandidateTxn(
                date=date(2026, 4, 5),
                amount=Decimal("1000"),
                raw_narration="New",
                normalized_narration="NEW",
                source_account="Liabilities:CreditCard:Acme",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
        ],
    )
    out = credit_card_monthly(all_transactions(ledger), 2026)
    assert out["opening"] == "5000"
    assert len(out["months"]) == 12
    assert out["months"][0]["month"] == "2026-04"
    assert out["months"][-1]["month"] == "2027-03"
    apr = out["months"][0]
    assert apr["purchases"] == "1000"
    assert apr["payments"] == "0"
    # outstanding carries the opening balance: 5000 + 1000 = 6000 (NOT 1000)
    assert apr["outstanding"] == "6000"


def test_credit_card_monthly_fy_boundaries(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            # 31 Mar 2027 -> last month of FY2026
            CandidateTxn(
                date=date(2027, 3, 31),
                amount=Decimal("700"),
                raw_narration="Last",
                normalized_narration="LAST",
                source_account="Liabilities:CreditCard:Acme",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
            # 1 Apr 2027 -> next FY, excluded
            CandidateTxn(
                date=date(2027, 4, 1),
                amount=Decimal("900"),
                raw_narration="Next",
                normalized_narration="NEXT",
                source_account="Liabilities:CreditCard:Acme",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
        ],
    )
    out = credit_card_monthly(all_transactions(ledger), 2026)
    mar = next(m for m in out["months"] if m["month"] == "2027-03")
    assert mar["purchases"] == "700"
    total_purchases = sum(Decimal(m["purchases"]) for m in out["months"])
    assert total_purchases == Decimal("700")  # the 1 Apr 2027 txn is excluded


def test_credit_card_monthly_empty_fy(ledger, investment_ledger):
    out = credit_card_monthly(all_transactions(ledger), 2026)
    assert out["opening"] == "0"
    assert out["cards"] == []
    assert len(out["months"]) == 12
    assert all(
        m["purchases"] == "0" and m["payments"] == "0" and m["outstanding"] == "0"
        for m in out["months"]
    )


_FLOW_CARD = "Liabilities:CreditCard:Acme"


def _card_row(
    day: int,
    amount: str,
    narration: str,
    direction: Direction,
    other: str,
    month: int = 7,
) -> CandidateTxn:
    return CandidateTxn(
        date=date(2026, month, day),
        amount=Decimal(amount),
        raw_narration=narration,
        normalized_narration=narration.upper(),
        source_account=_FLOW_CARD,
        direction=direction,
        suggested_other_leg=other,
    )


def _commit_flow_fixture(ledger, investment_ledger) -> None:
    commit_candidates(
        ledger,
        investment_ledger,
        [
            _card_row(2, "1000", "Shop", Direction.DEBIT, "Expenses:Shopping"),
            _card_row(3, "45.26", "Fee", Direction.DEBIT, "Expenses:Fees:CreditCard"),
            _card_row(
                4,
                "85",
                "Cashback",
                Direction.CREDIT,
                "Income:Refund:CreditCard:UNIFIED",
            ),
            _card_row(
                5, "500", "Bill pay", Direction.CREDIT, "Assets:Transfer:CreditCard"
            ),
            _card_row(
                6, "300", "EMI convert", Direction.CREDIT, "Liabilities:Loan:CardEMI"
            ),
            _card_row(
                7, "100", "EMI principal", Direction.DEBIT, "Liabilities:Loan:CardEMI"
            ),
        ],
    )


def test_credit_card_monthly_splits_flows(ledger, investment_ledger):
    _commit_flow_fixture(ledger, investment_ledger)
    out = credit_card_monthly(all_transactions(ledger), 2026)
    jul = next(m for m in out["months"] if m["month"] == "2026-07")
    assert jul["purchases"] == "1000"
    assert jul["charges"] == "45.26"
    assert jul["refunds"] == "85"
    assert jul["payments"] == "500"
    assert jul["emi"] == "-200"
    assert jul["adjustments"] == "0"


def test_credit_card_monthly_identity_holds(ledger, investment_ledger):
    _commit_flow_fixture(ledger, investment_ledger)
    commit_candidates(
        ledger,
        investment_ledger,
        [
            # Pre-FY debt (opening carry-in), an in-FY opening entry
            # (adjustment), and an excess payment returned to the bank
            # (a negative payment).
            CandidateTxn(
                date=date(2026, 3, 1),
                amount=Decimal("700"),
                raw_narration="Old",
                normalized_narration="OLD",
                source_account=_FLOW_CARD,
                direction=Direction.DEBIT,
                suggested_category="Expenses:Shopping",
            ),
            _card_row(
                9,
                "4210.37",
                "Opening",
                Direction.DEBIT,
                "Equity:Opening-Balances",
                month=8,
            ),
            _card_row(
                10,
                "120",
                "Excess refund",
                Direction.DEBIT,
                "Assets:Bank:HDFC",
                month=8,
            ),
        ],
    )
    out = credit_card_monthly(all_transactions(ledger), 2026)
    aug = next(m for m in out["months"] if m["month"] == "2026-08")
    assert aug["adjustments"] == "4210.37"
    assert aug["payments"] == "-120"
    assert aug["purchases"] == "0"

    prev = Decimal(out["opening"])
    assert prev == Decimal("700")
    for m in out["months"]:
        cur = Decimal(m["outstanding"])
        expected = (
            Decimal(m["purchases"])
            + Decimal(m["charges"])
            - Decimal(m["payments"])
            - Decimal(m["refunds"])
            + Decimal(m["emi"])
            + Decimal(m["adjustments"])
        )
        assert cur - prev == expected, m["month"]
        prev = cur


def test_credit_card_detail_kind(ledger, investment_ledger):
    _commit_flow_fixture(ledger, investment_ledger)
    rows = credit_card_detail(all_transactions(ledger), _FLOW_CARD)
    kinds = {r["narration"]: r["kind"] for r in rows}
    assert kinds == {
        "Shop": "purchase",
        "Fee": "charge",
        "Cashback": "refund",
        "Bill pay": "payment",
        "EMI convert": "emi",
        "EMI principal": "emi",
    }
    paying = [r["narration"] for r in rows if r["is_payment"]]
    assert paying == ["Bill pay"]


def _p(account: str, amount: str, day: int = 1) -> PostingRow:
    return PostingRow(
        date=date(2026, 4, day),
        account=account,
        amount=Decimal(amount),
        narration="synthetic",
        tags=frozenset(),
    )


def _by_account(rows: list[dict]) -> dict[str, dict]:
    return {r["account"]: r for r in rows}


def test_balance_rows_person_positive_is_owed_to_me():
    person = "Liabilities:People:Asha"
    postings = [_p(person, "10000"), _p("Assets:Checking:Bank", "-10000")]
    row = _by_account(balance_rows(postings, [], LoanTerms({})))[person]
    assert row["direction"] == "owed_to_me"
    assert row["amount"] == "10000"
    assert row["balance"] == "-10000"


def test_balance_rows_card_debt_is_i_owe():
    card = "Liabilities:CreditCard:Acme"
    postings = [_p(card, "-2299"), _p("Expenses:Shopping", "2299")]
    row = _by_account(balance_rows(postings, [], LoanTerms({})))[card]
    assert row["direction"] == "i_owe"
    assert row["amount"] == "2299"


def test_balance_rows_zero_net_is_settled():
    person = "Liabilities:People:Zed"
    postings = [_p(person, "500", 1), _p(person, "-500", 2)]
    rows = _by_account(balance_rows(postings, [], LoanTerms({})))
    assert person in rows
    assert rows[person]["direction"] == "settled"
    assert rows[person]["amount"] == "0"


def test_balance_rows_omits_closed_accounts():
    gone = "Liabilities:Cash:Misc"
    kept = "Liabilities:CreditCard:Acme"
    postings = [_p(gone, "-300"), _p(kept, "-100")]
    rows = _by_account(
        balance_rows(postings, [], LoanTerms({}), closed=frozenset({gone}))
    )
    assert gone not in rows
    assert kept in rows


def test_balance_rows_apr_source_file_or_null():
    loan, card = "Liabilities:Loan:Home", "Liabilities:CreditCard:Acme"
    terms = LoanTerms({loan: {"annual_rate_pct": "8.5", "term_months": 240}})
    rows = _by_account(balance_rows([_p(loan, "-1000"), _p(card, "-50")], [], terms))
    assert (rows[loan]["apr"], rows[loan]["apr_source"]) == ("8.5", "file")
    assert (rows[card]["apr"], rows[card]["apr_source"]) == (None, None)


def test_balance_rows_apr_override_wins():
    loan = "Liabilities:Loan:Gadget"
    terms = LoanTerms({loan: {"annual_rate_pct": "8.5", "term_months": 12}})
    rows = _by_account(
        balance_rows(
            [_p(loan, "-1200")],
            [],
            terms,
            apr_overrides={loan: (Decimal("16.84"), "inferred")},
        )
    )
    assert rows[loan]["apr"] == "16.84"
    assert rows[loan]["apr_source"] == "inferred"


# ── credit_card_summary: the statement cycle ─────────────────────────────────
_CYCLE_CARD = "Liabilities:CreditCard:Acme1234"


def _card_txn(
    txn_id: str, when: date, counter: str, card_amount: str
) -> TransactionRow:
    amount = Decimal(card_amount)
    return TransactionRow(
        txn_id=txn_id,
        date=when,
        narration=txn_id,
        postings=[
            PostingRow(when, _CYCLE_CARD, amount, txn_id, frozenset()),
            PostingRow(when, counter, -amount, txn_id, frozenset()),
        ],
        year_file_hash="h",
    )


def _cycle_txns() -> list[TransactionRow]:
    return [
        _card_txn("buy", date(2026, 10, 20), "Expenses:Shopping", "-2299"),
        # An opening-balance adjustment after the purchase does not count as
        # the card's latest line.
        _card_txn("adj", date(2026, 10, 25), "Equity:Opening-Balances", "-1"),
    ]


def test_credit_card_summary_adds_cycle_fields_from_properties():
    props = {
        _CYCLE_CARD: {
            "statement_day": 15,
            "due_day": 3,
            "last4": "1234",
            "expiry": "2029-08",
            "source": "account",
        }
    }

    out = credit_card_summary(
        _cycle_txns(), properties=props, today=date(2026, 11, 1), expiry_soon_days=60
    )

    assert out == [
        {
            "account": _CYCLE_CARD,
            "outstanding": "2300.00",
            "last_activity_date": "2026-10-25",
            "last4": "1234",
            "statement_day": 15,
            "due_day": 3,
            "properties_source": "account",
            "last_statement_date": "2026-10-15",
            "next_statement_date": "2026-11-15",
            "current_due_date": "2026-11-03",
            "next_due_date": "2026-12-03",
            "statement_expected": False,
            "expiry": "2029-08",
            "expiry_status": "ok",
        }
    ]


def test_credit_card_summary_flags_statement_expected():
    props = {
        _CYCLE_CARD: {
            "statement_day": 15,
            "due_day": None,
            "last4": None,
            "source": "suggested",
        }
    }

    # The latest non-adjustment line (20 Oct) is more than 7 days before the
    # 15 Nov statement; the 25 Oct adjustment does not count.
    card = credit_card_summary(
        _cycle_txns(), properties=props, today=date(2026, 11, 20), expiry_soon_days=60
    )[0]

    assert card["statement_expected"] is True
    assert card["properties_source"] == "suggested"
    assert (card["current_due_date"], card["next_due_date"]) == (None, None)
    assert card["last_statement_date"] == "2026-11-15"


def test_credit_card_summary_without_properties_has_null_cycle():
    props = {
        _CYCLE_CARD: {
            "statement_day": 15,
            "due_day": 3,
            "last4": "1234",
            "source": "account",
        }
    }
    # Without properties, or without a today, every new field is null/false,
    # so existing callers keep their shape.
    for out in (
        credit_card_summary(_cycle_txns(), expiry_soon_days=60),
        credit_card_summary(
            _cycle_txns(), properties={}, today=date(2026, 11, 1), expiry_soon_days=60
        ),
        credit_card_summary(_cycle_txns(), properties=props, expiry_soon_days=60),
        credit_card_summary(
            _cycle_txns(), today=date(2026, 11, 1), expiry_soon_days=60
        ),
    ):
        card = out[0]
        assert card["outstanding"] == "2300.00"
        for key in (
            "last4",
            "statement_day",
            "due_day",
            "properties_source",
            "last_statement_date",
            "next_statement_date",
            "current_due_date",
            "next_due_date",
            "expiry",
            "expiry_status",
        ):
            assert card[key] is None, key
        assert card["statement_expected"] is False


@pytest.mark.parametrize(
    ("expiry", "today", "status"),
    [
        ("2026-12", date(2026, 10, 31), "ok"),
        ("2026-12", date(2026, 11, 1), "expiring_soon"),
        ("2026-10", date(2026, 11, 1), "expired"),
        (None, date(2026, 11, 1), None),
    ],
)
def test_credit_card_summary_expiry_status(expiry, today, status):
    # Expiry needs no statement day: it shows on a card with no cycle.
    props = {
        _CYCLE_CARD: {
            "statement_day": None,
            "due_day": None,
            "last4": None,
            "expiry": expiry,
            "source": None,
        }
    }
    card = credit_card_summary(
        _cycle_txns(), properties=props, today=today, expiry_soon_days=60
    )[0]
    assert (card["expiry"], card["expiry_status"]) == (expiry, status)
    assert card["last_statement_date"] is None


def test_credit_card_summary_expiry_soon_threshold_is_passed_in():
    props = {
        _CYCLE_CARD: {
            "statement_day": None,
            "due_day": None,
            "last4": None,
            "expiry": "2026-12",
            "source": None,
        }
    }
    card = credit_card_summary(
        _cycle_txns(), properties=props, today=date(2026, 11, 1), expiry_soon_days=30
    )[0]
    assert card["expiry_status"] == "ok"


def test_credit_card_summary_takes_the_threshold_from_its_caller():
    # The read model never reads settings: the route passes the threshold.
    import inspect

    from backend.app.service.reports.liabilities import liabilities as module

    assert "get_settings" not in inspect.getsource(module)
    param = inspect.signature(credit_card_summary).parameters["expiry_soon_days"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_credit_card_summary_omits_closed_cards():
    out = credit_card_summary(
        _cycle_txns(), closed=frozenset({_CYCLE_CARD}), expiry_soon_days=60
    )
    assert out == []


def test_balance_rows_apr_override_wins_on_plan_then_file_fallback():
    acct = "Liabilities:Loan:CardEMI:Acme1234-20260722"
    rows = [
        PostingRow(
            date=date(2026, 7, 22),
            account=acct,
            amount=Decimal("-12000"),
            narration="x",
            tags=frozenset(),
        )
    ]
    terms = LoanTerms({acct: {"annual_rate_pct": "9", "term_months": 6}})
    (row,) = balance_rows(
        rows, [], terms, apr_overrides={acct: (Decimal("16.84"), "inferred")}
    )
    assert (row["apr"], row["apr_source"]) == ("16.84", "inferred")
    (row,) = balance_rows(rows, [], terms)
    assert (row["apr"], row["apr_source"]) == ("9", "file")


def test_credit_card_summary_outstanding_counts_postings_without_a_txn_id():
    # An opening balance has no txn-id, so it is not in `transactions`; given
    # every posting, `outstanding` is the card's ledger balance.
    card = "Liabilities:CreditCard:SynthA1111"
    spend = TransactionRow(
        txn_id="t1",
        date=date(2026, 10, 1),
        narration="Synth Diner",
        postings=[
            PostingRow(
                date(2026, 10, 1), "Expenses:Food", Decimal("300"), "", frozenset()
            ),
            PostingRow(date(2026, 10, 1), card, Decimal("-300"), "", frozenset()),
        ],
        year_file_hash="h",
    )
    opening = [
        PostingRow(
            date(2026, 7, 1),
            "Equity:Opening-Balances",
            Decimal("4000"),
            "",
            frozenset(),
        ),
        PostingRow(date(2026, 7, 1), card, Decimal("-4000"), "", frozenset()),
    ]
    (tile,) = credit_card_summary([spend], expiry_soon_days=60)
    assert tile["outstanding"] == "300.00"
    (tile,) = credit_card_summary(
        [spend], postings=opening + spend.postings, expiry_soon_days=60
    )
    assert (tile["outstanding"], tile["last_activity_date"]) == (
        "4300.00",
        "2026-10-01",
    )
    closed = credit_card_summary(
        [spend],
        postings=opening + spend.postings,
        closed=frozenset({card}),
        expiry_soon_days=60,
    )
    assert closed == []


def test_credit_card_summary_outstanding_is_two_dp_and_never_negative_zero():
    card = "Liabilities:CreditCard:SynthA1111"
    d = date(2026, 10, 1)
    rows = [
        PostingRow(d, "Expenses:Food", Decimal("10.005"), "", frozenset()),
        PostingRow(d, card, Decimal("-10.005"), "", frozenset()),
        PostingRow(date(2026, 10, 2), card, Decimal("10.005"), "", frozenset()),
    ]
    txn = TransactionRow("t1", d, "x", rows[:2], "h")
    assert credit_card_summary([txn], expiry_soon_days=60)[0]["outstanding"] == "10.01"
    assert (
        credit_card_summary([txn], postings=rows, expiry_soon_days=60)[0]["outstanding"]
        == "0.00"
    )
