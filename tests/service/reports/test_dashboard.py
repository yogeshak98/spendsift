from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.reports.dashboard import account_balances, summary
from backend.app.service.reports.health.ledger_view import all_postings


def test_account_balances(loaded_ledger):
    balances = account_balances(all_postings(loaded_ledger))
    # HDFC: +280000 -35000 -412 -888 -4299 = 239401
    assert balances["Assets:Bank:HDFC"] == "239401"


def test_summary_has_headline_numbers(loaded_ledger):
    s = summary(all_postings(loaded_ledger), 2026, 4)
    assert s["spent_this_month"] == "40599"
    assert "cash_net_worth" in s


def test_dashboard_investments_match_holdings_for_unfunded_purchase(
    ledger, investment_ledger
):
    # A Groww PURCHASE before its funding bank debit is imported leaves the
    # clearing leg (Assets:Investment:MutualFund:Groww:Cash) at -21500 and the
    # holding at +21500 cost basis. The clearing account sits under the
    # Assets:Investment: prefix, so naively summing every Assets:Investment:*
    # balance nets the dashboard investment total to 0 while holdings() (which
    # drops non-positive balances) reports 21500. The two surfaces must agree.
    from backend.app.service.reports.investments.investments import holdings

    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 18),
                amount=Decimal("21500.00"),
                raw_narration="Purchase — Parag Parikh Flexi Cap",
                normalized_narration="Parag Parikh Flexi Cap",
                source_account="Assets:Investment:MutualFund:Groww:Cash",
                direction=Direction.DEBIT,
                units=Decimal("100.0"),
                commodity="MF122639",
                unit_cost=Decimal("215.0"),
            )
        ],
    )
    rows = all_postings(ledger)
    s = summary(rows, 2026, 4)
    holdings_total = holdings(rows)["total"]
    assert holdings_total == "21500.00"
    assert s["investments"] == holdings_total
    # The clearing account is not a held investment kind.
    assert s["investment_kinds"] == 1


def test_dashboard_investments_excludes_any_negative_account_not_just_groww_cash(
    ledger,
    investment_ledger,
):
    # A second, differently-named Investment account (e.g. a Kite clearing leg,
    # or an unswept redemption) can also net negative — dashboard.py must not
    # rely on a hardcoded single-account exclusion (GROWW_CASH) to keep the
    # dashboard total in agreement with holdings()/Allocation, which drop
    # every non-positive Investment balance generically.
    from backend.app.service.reports.investments.investments import holdings

    commit_candidates(
        ledger,
        investment_ledger,
        [
            # A real, positive holding.
            CandidateTxn(
                date=date(2026, 4, 3),
                amount=Decimal("50000"),
                raw_narration="SIP Equity",
                normalized_narration="SIP EQUITY",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Assets:Investment:MutualFund:Equity",
                suggested_other_leg=None,
            ),
            # A redemption recorded before its funding purchase is imported —
            # leaves a NON-Groww Investment account net negative.
            CandidateTxn(
                date=date(2026, 4, 18),
                amount=Decimal("30000"),
                raw_narration="Kite payout",
                normalized_narration="KITE PAYOUT",
                source_account="Assets:Investment:Stocks:Kite:Cash",
                direction=Direction.DEBIT,
                suggested_category="Assets:Checking:HDFC",
                suggested_other_leg=None,
            ),
        ],
    )
    rows = all_postings(ledger)
    s = summary(rows, 2026, 4)
    holdings_total = holdings(rows)["total"]

    assert holdings_total == "50000"
    assert s["investments"] == holdings_total
    assert s["investment_kinds"] == 1


def test_dashboard_flags_negative_investment_accounts_dragging_down_net_worth(
    ledger,
    investment_ledger,
):
    # A negative Investment account is dropped from the "investments" headline
    # (matching holdings()/Allocation) but still reduces cash_net_worth via the
    # raw Assets:* sum. Without a flag, net worth can look inexplicably lower
    # than "Liquid + Investments" implies, with no visible account to blame.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 3),
                amount=Decimal("50000"),
                raw_narration="SIP Equity",
                normalized_narration="SIP EQUITY",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Assets:Investment:MutualFund:Equity",
                suggested_other_leg=None,
            ),
            CandidateTxn(
                date=date(2026, 4, 18),
                amount=Decimal("30000"),
                raw_narration="Kite payout",
                normalized_narration="KITE PAYOUT",
                source_account="Assets:Investment:Stocks:Kite:Cash",
                direction=Direction.DEBIT,
                suggested_category="Assets:Checking:HDFC",
                suggested_other_leg=None,
            ),
        ],
    )
    rows = all_postings(ledger)
    s = summary(rows, 2026, 4)

    assert s["flagged_investment_accounts"] == [
        {
            "account": "Assets:Investment:Stocks:Kite:Cash",
            "account_display": "Assets:Investment:Stocks:Kite:Cash",
            "amount": "-30000",
        }
    ]


def test_dashboard_does_not_flag_a_fully_redeemed_zero_balance_investment_account(
    ledger,
    investment_ledger,
):
    # A closed/fully-redeemed lot settling to exactly zero is not an anomaly —
    # only a genuinely negative balance should be flagged for review.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 3),
                amount=Decimal("50000"),
                raw_narration="SIP Equity",
                normalized_narration="SIP EQUITY",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Assets:Investment:MutualFund:Equity",
                suggested_other_leg=None,
            ),
            CandidateTxn(
                date=date(2026, 4, 18),
                amount=Decimal("50000"),
                raw_narration="Redeem Equity",
                normalized_narration="REDEEM EQUITY",
                source_account="Assets:Investment:MutualFund:Equity",
                direction=Direction.DEBIT,
                suggested_category="Assets:Checking:HDFC",
                suggested_other_leg=None,
            ),
        ],
    )
    rows = all_postings(ledger)
    s = summary(rows, 2026, 4)

    assert s["flagged_investment_accounts"] == []


def test_dashboard_excludes_tds_receivable_from_net_worth_and_accounts(
    ledger, investment_ledger
):
    # TDS withheld posts to Assets:Receivable:TDS (a prepaid-tax credit) but is
    # not a real, spendable asset until an actual filing-settlement transaction
    # (refund or additional tax due) occurs — it must not inflate cash_net_worth
    # or appear in the dashboard's Accounts card until then.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 3),
                amount=Decimal("272000"),
                raw_narration="Salary",
                normalized_narration="SALARY",
                source_account="Assets:Checking:HDFC",
                direction=Direction.CREDIT,
                suggested_category="Income:Salary",
                suggested_other_leg=None,
            ),
            CandidateTxn(
                date=date(2026, 4, 3),
                amount=Decimal("8000"),
                raw_narration="TDS withheld",
                normalized_narration="TDS WITHHELD",
                source_account="Income:Salary",
                direction=Direction.DEBIT,
                suggested_category="Assets:Receivable:TDS",
                suggested_other_leg=None,
            ),
        ],
    )
    rows = all_postings(ledger)
    s = summary(rows, 2026, 4)

    assert s["liquid"] == "272000"
    assert s["cash_net_worth"] == "272000"  # TDS receivable (8000) excluded
    accts = {a["account"] for a in s["accounts"]}
    assert "Assets:Receivable:TDS" not in accts
    # the raw ledger balance is still inspectable directly
    assert account_balances(rows)["Assets:Receivable:TDS"] == "8000"


def test_summary_returns_account_splits(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 3),
                amount=Decimal("280000"),
                raw_narration="Salary",
                normalized_narration="SALARY",
                source_account="Assets:Checking:HDFC",
                direction=Direction.CREDIT,
                suggested_category="Income:Salary",
                suggested_other_leg=None,
            ),
            CandidateTxn(
                date=date(2026, 4, 5),
                amount=Decimal("50000"),
                raw_narration="SIP Equity",
                normalized_narration="SIP EQUITY",
                source_account="Assets:Checking:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Assets:Investment:MutualFund:Equity",
                suggested_other_leg=None,
            ),
        ],
    )
    rows = all_postings(ledger)
    s = summary(rows, 2026, 4)

    # liquid = Assets:Checking:* + Assets:Cash
    assert s["liquid"] == "230000"  # 280000 inflow - 50000 SIP outflow
    assert s["investments"] == "50000"  # Assets:Investment:* total
    assert s["checking_count"] == 1  # one distinct Assets:Checking: account
    assert s["investment_kinds"] == 1  # one distinct Assets:Investment: sub-account

    # per-account asset balances, decimal strings, only Assets:* surfaced
    accts = {a["account"]: a["amount"] for a in s["accounts"]}
    assert accts["Assets:Checking:HDFC"] == "230000"
    assert accts["Assets:Investment:MutualFund:Equity"] == "50000"
    # accounts are sorted by balance descending
    assert [a["account"] for a in s["accounts"]][0] == "Assets:Checking:HDFC"


def test_summary_headline_unchanged(loaded_ledger):
    # regression guard: existing headline keys still present and correct
    s = summary(all_postings(loaded_ledger), 2026, 4)
    assert s["spent_this_month"] == "40599"
    assert "cash_net_worth" in s
    assert "liabilities" in s


def test_dashboard_spent_excludes_investment_charges():
    from backend.app.service.reports.health.ledger_view import PostingRow

    def p(account, amount):
        return PostingRow(
            date=date(2026, 8, 5),
            account=account,
            amount=Decimal(amount),
            narration="x",
            tags=frozenset(),
            inr_amount=Decimal(amount),
        )

    food = [p("Expenses:Food", "100"), p("Assets:Checking:Synth", "-100")]
    mf = [
        p("Assets:Investment:MutualFund", "600036.2856"),
        p("Assets:Checking:Synth", "-599970"),
        p("Expenses:Investment:Charges:Synth", "-66.2856"),
    ]
    assert summary(food, 2026, 8)["spent_this_month"] == "100"
    assert summary(food + mf, 2026, 8)["spent_this_month"] == "100"
