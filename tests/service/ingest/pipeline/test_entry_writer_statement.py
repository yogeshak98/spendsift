"""The card-statement record rides in the same journal append as its rows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from beancount.parser import printer

from backend.app.service.categorize.categorizer import Categorizer
from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.core.errors.errors import CodedError
from backend.app.models.domain.candidate import CandidateTxn, DedupStatus, Direction
from backend.app.service.ingest.pipeline.entry_writer import candidate_to_entry
from backend.app.persistence.review_batches import ReviewStore
from backend.app.service.ingest.pipeline.statement_record import (
    already_recorded,
    statement_entry,
)
from backend.app.models.storage.statement_record import StatementSummary
from backend.app.persistence.templates import TemplateStore
from backend.app.persistence.journal import Ledger, LedgerValidationError
from backend.app.service.ingest.service import ImportService

CARD = "Liabilities:CreditCard:HDFC"


def _service(ledger: Ledger, investment_ledger: Ledger) -> ImportService:
    categorizer = Categorizer(
        MerchantDictionary.from_seed(),
        LearnedRules(ledger.storage_dir / "rules" / "learned.yaml"),
    )
    return ImportService(
        ledger=ledger,
        investment_ledger=investment_ledger,
        categorizer=categorizer,
        review_store=ReviewStore(),
        template_store=TemplateStore(ledger.storage_dir / "templates"),
    )


@pytest.fixture
def svc(ledger: Ledger, investment_ledger: Ledger) -> ImportService:
    return _service(ledger, investment_ledger)


def _row(day: date, amount: str = "500.00", narration: str = "SHOP") -> CandidateTxn:
    return CandidateTxn(
        date=day,
        amount=Decimal(amount),
        raw_narration=narration,
        normalized_narration=narration,
        source_account=CARD,
        direction=Direction.DEBIT,
        suggested_category="Expenses:Shopping",
    )


def _summary(
    start=date(2026, 9, 21), end=date(2026, 10, 20), purchases: str = "500"
) -> StatementSummary:
    """A statement whose purchases match the rows staged with it: commit
    re-checks the totals (F4)."""
    return StatementSummary(
        account=CARD,
        period_start=start,
        period_end=end,
        previous_balance=Decimal("0"),
        payments_credits=Decimal("0"),
        purchases_debits=Decimal(purchases),
        total_due=Decimal(purchases),
    )


def _stage(svc: ImportService, rows, statement=None) -> str:
    batch_id = svc._review_store.add_batch(rows)
    if statement is not None:
        svc._review_store.set_statement(batch_id, statement)
    return batch_id


def _journal_text(ledger: Ledger) -> str:
    paths = [
        path
        for path in sorted(ledger.storage_dir.rglob("*.beancount"))
        if "snapshots" not in path.parts
    ]
    assert paths, f"no journal files under {ledger.storage_dir}"
    return "\n=====\n".join(
        f"{path.relative_to(ledger.storage_dir)}\n{path.read_text(encoding='utf-8')}"
        for path in paths
    )


def _appended(monkeypatch) -> list[list]:
    calls: list[list] = []
    real = Ledger.append

    def spy(self, entries):
        calls.append(list(entries))
        return real(self, entries)

    monkeypatch.setattr(Ledger, "append", spy)
    return calls


def test_record_written_in_the_same_append_as_rows(svc, ledger, monkeypatch):
    calls = _appended(monkeypatch)
    batch = _stage(
        svc,
        [_row(date(2026, 10, 5)), _row(date(2026, 10, 6), "250.00", "CAFE")],
        _summary(purchases="750"),
    )
    result = svc.commit(batch)

    assert result["committed"] == 2
    assert result["statement_recorded"] is True
    main_calls = [c for c in calls if any(hasattr(e, "postings") for e in c)]
    assert len(main_calls) == 1
    kinds = sorted(type(e).__name__ for e in main_calls[0])
    assert kinds == ["Custom", "Transaction", "Transaction"]
    assert already_recorded(ledger, CARD, date(2026, 10, 20))


def test_refused_write_leaves_neither(svc, ledger, monkeypatch):
    batch = _stage(svc, [_row(date(2026, 10, 5))], _summary())
    before = _journal_text(ledger)

    real = Ledger.validate
    state = {"fired": False}

    def validate_once(self):
        if not state["fired"]:
            state["fired"] = True
            return ["boom"]
        return real(self)

    monkeypatch.setattr(Ledger, "validate", validate_once)
    with pytest.raises(LedgerValidationError):
        svc.commit(batch)

    assert state["fired"]
    assert _journal_text(ledger) == before
    assert not already_recorded(ledger, CARD, date(2026, 10, 20))


def test_second_commit_of_same_statement_writes_no_record(svc, ledger):
    first = svc.commit(_stage(svc, [_row(date(2026, 10, 5))], _summary()))
    assert first["statement_recorded"] is True

    second = svc.commit(
        _stage(
            svc,
            [_row(date(2026, 10, 7), "90.00", "OTHER")],
            _summary(purchases="90"),
        )
    )
    assert second["committed"] == 1
    assert second["statement_recorded"] is False
    assert _journal_text(ledger).count('custom "card-statement"') == 1


def test_record_only_commit_when_all_rows_are_duplicates(svc, ledger):
    row = _row(date(2026, 9, 25))
    svc.commit(_stage(svc, [row], _summary(date(2026, 8, 21), date(2026, 9, 20))))
    before = _journal_text(ledger)

    dup = _row(date(2026, 9, 25))
    dup.dedup_status = DedupStatus.EXACT_DUP
    result = svc.commit(_stage(svc, [dup], _summary()))

    assert result["committed"] == 0
    assert result["statement_recorded"] is True
    after = _journal_text(ledger)
    assert after != before
    assert after.count('custom "card-statement"') == 2
    assert after.count("txn-id") == before.count("txn-id")  # no new transaction
    assert ledger.validate() == []


def test_new_year_statement_rows_go_to_their_own_year_files(svc, ledger):
    summary = _summary(date(2026, 12, 21), date(2027, 1, 20), purchases="1000")
    batch = _stage(
        svc,
        [
            _row(date(2026, 12, 24), narration="DEC"),
            _row(date(2027, 1, 5), narration="JAN"),
        ],
        summary,
    )
    result = svc.commit(batch)

    assert result["statement_recorded"] is True
    y2026 = (ledger.journal_dir / "2026.beancount").read_text(encoding="utf-8")
    y2027 = (ledger.journal_dir / "2027.beancount").read_text(encoding="utf-8")
    assert "DEC" in y2026 and "JAN" not in y2026
    assert "JAN" in y2027 and 'custom "card-statement"' in y2027
    assert 'custom "card-statement"' not in y2026
    assert ledger.validate() == []


def test_fx_meta_written_only_when_set():
    plain = _row(date(2026, 4, 1), "412.00", "UPI/DR/SWIGGY")
    plain.source_account = "Assets:Bank:HDFC"
    plain.suggested_category = "Expenses:Food"
    plain.normalized_narration = "SWIGGY"
    plain = CandidateTxn(**{**plain.model_dump(exclude={"id"})})
    golden = (
        '2026-04-01 * "UPI/DR/SWIGGY"\n'
        '  txn-id: "bd99ddd76f1e5e7c87e50ab19fa93ff4"\n'
        "  Expenses:Food      412.00 INR\n"
        "  Assets:Bank:HDFC  -412.00 INR\n"
    )
    assert printer.format_entry(candidate_to_entry(plain)) == golden

    with_fx = plain.model_copy(update={"fx": "USD 12.00"})
    entry = candidate_to_entry(with_fx)
    assert entry.meta["fx"] == "USD 12.00"
    assert '  fx: "USD 12.00"\n' in printer.format_entry(entry)
    assert "fx" not in candidate_to_entry(plain).meta


def test_csv_commit_unchanged(svc, ledger):
    row = _row(date(2026, 10, 5))
    batch = _stage(svc, [row])
    assert svc.get_review(batch)["statement"] is None
    result = svc.commit(batch)

    assert result == {
        "committed": 1,
        "uncategorized": 0,
        "unexplained": 0,
        "statement_recorded": False,
        # Contract 1.4: what the commit learned, and what it opened.
        "learned": [],
        "opened": ["Expenses:Shopping"],
    }
    assert 'custom "card-statement"' not in _journal_text(ledger)


def test_get_review_exposes_the_statement(svc):
    summary = _summary()
    review = svc.get_review(_stage(svc, [_row(date(2026, 10, 5))], summary))
    assert review["statement"] == summary.model_dump(mode="json")


def test_clear_drops_the_statement():
    store = ReviewStore()
    batch = store.add_batch([])
    store.set_statement(batch, _summary())
    assert store.statement(batch) is not None
    store.clear(batch)
    assert store.statement(batch) is None


def test_statement_entry_is_only_extra_when_no_rows(ledger, investment_ledger):
    # commit_candidates alone: no candidates, one extra entry -> still written.
    from backend.app.service.ingest.pipeline.entry_writer import commit_candidates

    commit_candidates(
        ledger,
        investment_ledger,
        [],
        extra_main_entries=[statement_entry(_summary())],
    )
    assert already_recorded(ledger, CARD, date(2026, 10, 20))
    assert ledger.validate() == []


def test_listed_finance_charges_count_in_the_commit_recheck(svc, ledger):
    """`finance_charges_listed` rides on the batch statement, so the commit
    re-check counts listed charges in the debits exactly as check 14 does."""
    rows = [_row(date(2026, 10, 5)), _row(date(2026, 10, 6), "100.00", "FIN CHG")]
    listed = _summary().model_copy(
        update={
            "finance_charges": Decimal("100"),
            "total_due": Decimal("600"),
            "finance_charges_listed": True,
        }
    )
    assert "finance_charges_listed" not in listed.model_dump()  # not in the API
    unlisted = listed.model_copy(update={"finance_charges_listed": False})
    with pytest.raises(CodedError) as err:
        svc.commit(_stage(svc, rows, unlisted))
    assert err.value.code == "E1070"
    assert svc.commit(_stage(svc, rows, listed))["committed"] == 2


def test_cash_advances_count_in_the_commit_recheck(svc, ledger):
    """A statement with cash advances passes the commit re-check exactly when
    check 14 would: the cash row counts as a debit against purchases plus
    cash advances. The record carries the figure."""
    rows = [_row(date(2026, 10, 5)), _row(date(2026, 10, 6), "300.00", "ATM CASH")]
    with_cash = _summary().model_copy(
        update={"cash_advances": Decimal("300"), "total_due": Decimal("800")}
    )
    without = with_cash.model_copy(update={"cash_advances": Decimal("0")})
    with pytest.raises(CodedError) as err:
        svc.commit(_stage(svc, rows, without))
    assert err.value.code == "E1070"
    assert "purchases_debits" in err.value.description

    result = svc.commit(_stage(svc, rows, with_cash))
    assert result["committed"] == 2 and result["statement_recorded"] is True
    assert "  cash-advances: 300.00 INR\n" in _journal_text(ledger)
    assert "other-charges" not in _journal_text(ledger)
