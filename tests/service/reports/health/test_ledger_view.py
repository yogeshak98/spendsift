import hashlib
from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction, Split
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.reports.health.ledger_view import (
    all_postings,
    all_transactions,
    get_transaction,
    open_accounts,
    transactions_in_fy,
    transactions_in_range,
)


def _commit(ledger, investment_ledger, d, narr, amount="100"):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=d,
                amount=Decimal(amount),
                raw_narration=narr,
                normalized_narration=narr.upper(),
                source_account="Assets:Bank:HDFC",
                direction=Direction.DEBIT,
                suggested_category="Expenses:Rent",
            )
        ],
    )


def test_transactions_in_fy_includes_in_window(ledger, investment_ledger):
    _commit(ledger, investment_ledger, date(2026, 4, 1), "Rent")

    in_fy = transactions_in_fy(ledger, 2026)
    assert len(in_fy) == 1
    assert in_fy[0].txn_id

    assert transactions_in_fy(ledger, 2025) == []


def test_transactions_in_fy_excludes_other_fy_boundaries(ledger, investment_ledger):
    _commit(ledger, investment_ledger, date(2026, 3, 31), "EndOfFY2025")
    _commit(ledger, investment_ledger, date(2027, 3, 31), "EndOfFY2026")

    narrations_2025 = {r.narration for r in transactions_in_fy(ledger, 2025)}
    narrations_2026 = {r.narration for r in transactions_in_fy(ledger, 2026)}

    assert "EndOfFY2025" in narrations_2025
    assert "EndOfFY2025" not in narrations_2026
    assert "EndOfFY2026" in narrations_2026
    assert "EndOfFY2026" not in narrations_2025


def test_transactions_in_range_is_inclusive_on_both_ends(ledger, investment_ledger):
    _commit(ledger, investment_ledger, date(2026, 2, 14), "Before")
    _commit(ledger, investment_ledger, date(2026, 2, 15), "First")
    _commit(ledger, investment_ledger, date(2026, 5, 10), "Last")
    _commit(ledger, investment_ledger, date(2026, 5, 11), "After")

    rows = transactions_in_range(ledger, date(2026, 2, 15), date(2026, 5, 10))

    assert [r.narration for r in rows] == ["First", "Last"]
    assert all(r.txn_id for r in rows)


def test_transactions_in_range_single_day_and_empty(ledger, investment_ledger):
    _commit(ledger, investment_ledger, date(2026, 8, 3), "Day")

    assert [
        r.narration
        for r in transactions_in_range(ledger, date(2026, 8, 3), date(2026, 8, 3))
    ] == ["Day"]
    assert transactions_in_range(ledger, date(2026, 9, 1), date(2026, 9, 30)) == []


def test_all_postings_flattens_transactions(loaded_ledger):
    rows = all_postings(loaded_ledger)
    expense_food = [r for r in rows if r.account == "Expenses:Food"]
    assert len(expense_food) == 2
    assert all(r.narration for r in expense_food)


def test_all_transactions_groups_postings_and_includes_hash(ledger, investment_ledger):
    candidate = CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("21500.00"),
        raw_narration="Groww redemption",
        normalized_narration="GROWW REDEMPTION",
        source_account="Assets:Bank:HDFC",
        direction=Direction.CREDIT,
        splits=[
            Split(account="Assets:Investments:PPFCF", amount=Decimal("18000.00")),
            Split(account="Income:CapitalGains:LongTerm", amount=Decimal("3500.00")),
        ],
    )
    commit_candidates(ledger, investment_ledger, [candidate])

    rows = all_transactions(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row.txn_id == candidate.id
    assert len(row.postings) == 3

    year_file = ledger.journal_dir / "2026.beancount"
    assert row.year_file_hash == hashlib.sha256(year_file.read_bytes()).hexdigest()


def test_get_transaction_returns_none_for_unknown_id(ledger, investment_ledger):
    assert get_transaction(ledger, "does-not-exist") is None


def test_open_accounts_lists_seed_accounts(ledger, investment_ledger):
    accounts = open_accounts(ledger)
    assert "Assets:Checking:HDFC" in accounts
    assert "Expenses:Food" in accounts
    assert accounts == sorted(accounts)


def test_open_meta_excludes_filename_and_lineno(tmp_path):
    from backend.app.persistence.journal import Ledger
    from backend.app.service.reports.health.ledger_view import (
        open_meta,
        open_meta_from_entries,
    )

    led = Ledger(tmp_path)
    led.bootstrap(seed_accounts=("Assets:Checking:Bank",))
    main = led.main_file.read_text(encoding="utf-8")
    led.main_file.write_text(
        main
        + '2000-01-01 open Liabilities:CreditCard:Acme INR\n  last4: "0042"\n'
        + "  statement-day: 15\n  rate-pct: 1.25\n",
        encoding="utf-8",
    )

    meta = open_meta(led)
    assert meta["Liabilities:CreditCard:Acme"] == {
        "last4": "0042",
        "statement-day": 15,
        "rate-pct": Decimal("1.25"),
    }
    assert isinstance(meta["Liabilities:CreditCard:Acme"]["statement-day"], int)
    assert meta["Assets:Checking:Bank"] == {}
    assert open_meta_from_entries(led.load().entries) == meta


def test_closed_accounts_lists_close_directives(tmp_path):
    from backend.app.persistence.journal import Ledger
    from backend.app.service.reports.health.ledger_view import (
        closed_accounts,
        closed_accounts_from_entries,
    )

    a, b = "Liabilities:Cash:A", "Liabilities:Cash:B"
    led = Ledger(tmp_path)
    led.bootstrap(seed_accounts=(a, b))
    led.close_account(a, date(2026, 1, 1))

    assert closed_accounts(led) == frozenset({a})
    assert closed_accounts_from_entries(led.load().entries) == frozenset({a})


def test_flow_transactions_in_range_keeps_untracked_entries(ledger, investment_ledger):
    from backend.app.service.reports.health.ledger_view import (
        UNTRACKED_TXN_PREFIX,
        flow_transactions_in_range,
    )
    from tests.conftest import make_txn

    _commit(ledger, investment_ledger, date(2026, 8, 3), "Tracked")
    ledger.open_named_accounts(["Income:Refund"])
    ledger.append(
        [
            make_txn(
                date(2026, 8, 4), "Hand A", "Assets:Bank:HDFC", "Income:Refund", "5"
            ),
            make_txn(
                date(2026, 8, 5), "Hand B", "Assets:Bank:HDFC", "Income:Refund", "7"
            ),
            make_txn(
                date(2026, 9, 1), "Later", "Assets:Bank:HDFC", "Income:Refund", "9"
            ),
        ]
    )
    start, end = date(2026, 8, 1), date(2026, 8, 31)
    assert [r.narration for r in transactions_in_range(ledger, start, end)] == [
        "Tracked"
    ]
    rows = flow_transactions_in_range(ledger, start, end)
    assert [r.narration for r in rows] == ["Tracked", "Hand A", "Hand B"]
    untracked = [r.txn_id for r in rows[1:]]
    assert all(t.startswith(UNTRACKED_TXN_PREFIX) for t in untracked)
    assert len(set(untracked)) == 2


def test_untracked_ids_are_stable_across_unrelated_writes(ledger, investment_ledger):
    import re

    from backend.app.service.reports.health.ledger_view import (
        flow_transactions_in_range,
    )
    from tests.conftest import make_txn

    ledger.open_named_accounts(["Income:Refund"])
    ledger.append(
        [make_txn(date(2026, 8, 4), "Hand A", "Assets:Bank:HDFC", "Income:Refund", "5")]
    )
    start, end = date(2026, 8, 1), date(2026, 8, 31)
    (before,) = flow_transactions_in_range(ledger, start, end)
    assert re.fullmatch(r"untracked-[0-9a-f]{12}", before.txn_id)
    assert before.tracked is False

    # Unrelated writes: an earlier-dated txn, another year, and a new account.
    _commit(ledger, investment_ledger, date(2026, 7, 1), "Earlier")
    _commit(ledger, investment_ledger, date(2025, 8, 1), "Other year")
    ledger.open_named_accounts(["Expenses:Synth"])
    _commit(ledger, investment_ledger, date(2026, 8, 20), "Tracked later")

    rows = flow_transactions_in_range(ledger, start, end)
    hand = next(r for r in rows if r.narration == "Hand A")
    assert hand.txn_id == before.txn_id
    tracked = next(r for r in rows if r.narration == "Tracked later")
    assert tracked.tracked is True
    assert not tracked.txn_id.startswith("untracked-")


def test_price_annotated_posting_is_valued_in_inr(ledger):
    from backend.app.service.reports.health.ledger_view import (
        flow_transactions_in_range,
    )

    ledger.open_named_accounts(["Assets:Bank:HDFC"])
    # Hand-written: an account with no currency constraint and an `@` price.
    text, digest = ledger.read_year_text(2026)
    text += (
        "\n2026-01-01 open Expenses:TravelAbroad\n"
        '\n2026-08-04 * "Synth hotel abroad"\n'
        "  Expenses:TravelAbroad  10 USD @ 83 INR\n"
        "  Assets:Bank:HDFC  -830 INR\n"
    )
    ledger.write_year_text(2026, text, digest)
    travel = [r for r in all_postings(ledger) if r.account == "Expenses:TravelAbroad"]
    assert [(r.amount, r.inr_amount) for r in travel] == [(Decimal(10), Decimal(830))]
    (row,) = flow_transactions_in_range(ledger, date(2026, 8, 1), date(2026, 8, 31))
    assert row.postings[0].inr_amount == Decimal(830)
