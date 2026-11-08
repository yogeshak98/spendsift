"""Renaming an account reaches the imports still waiting for review, and
every learned.yaml writer shares the journal write lock. Synthetic data only."""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.models.domain.candidate import CandidateTxn, Direction, Split
from backend.app.models.domain.ledger_cli import ParsedLedger
from backend.app.models.storage.ledger_merge import AccountMapping, MergeBatch
from backend.app.models.storage.statement_record import StatementSummary
from backend.app.persistence import learned_rules as learned_rules_module
from backend.app.persistence.journal import Ledger, LedgerValidationError
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.persistence.ledger_merge_store import LedgerMergeStore
from backend.app.persistence.review_batches import ReviewStore
from backend.app.service.ledger.accounts import AccountAdminService

OLD = "Expenses:Food"
NEW = "Expenses:Dining"
BANK = "Assets:Checking:Synth"


def _svc(tmp_path, review=None, merge=None):
    led = Ledger(tmp_path)
    led.bootstrap()
    led.open_named_accounts(
        [a for a in (OLD, BANK) if a not in AccountAdminService(led).list_accounts()]
    )
    svc = AccountAdminService(led, review_store=review, ledger_merge_store=merge)
    return led, svc


def _candidate(narration: str, **update) -> CandidateTxn:
    base = dict(
        date=date(2026, 1, 5),
        amount=Decimal("120.00"),
        raw_narration=narration,
        normalized_narration=narration,
        source_account=BANK,
        direction=Direction.DEBIT,
    )
    base.update(update)
    return CandidateTxn(**base)


def _batch() -> tuple[ReviewStore, str, list[CandidateTxn]]:
    rows = [
        _candidate("SYNTH CAFE", suggested_category=OLD),
        _candidate(
            "SYNTH SPLIT",
            suggested_category="Expenses:Travel",
            splits=[
                Split(account=OLD, amount=Decimal("20")),
                Split(account="Expenses:Food:Snacks", amount=Decimal("100")),
            ],
        ),
        _candidate("SYNTH OTHER", suggested_category="Expenses:FoodCourt"),
    ]
    store = ReviewStore()
    batch_id = store.add_batch(rows)
    return store, batch_id, rows


def test_pending_review_batch_rewritten_keeping_ids(tmp_path):
    store, batch_id, rows = _batch()
    ids = [c.id for c in rows]
    _, svc = _svc(tmp_path, review=store)

    result = svc.rename(OLD, NEW)

    assert result["references_updated"]["review_batches"] == 1  # one batch
    got = store.get_batch(batch_id)
    assert [c.id for c in got] == ids  # dedup identity unchanged
    assert got[0].suggested_category == NEW
    assert [s.account for s in got[1].splits] == [NEW, "Expenses:Food:Snacks"]
    assert got[2].suggested_category == "Expenses:FoodCourt"


def test_pending_batch_source_account_and_statement_rewritten(tmp_path):
    card = "Liabilities:CreditCard:Synth"
    store = ReviewStore()
    batch_id = store.add_batch(
        [_candidate("SYNTH SHOP", source_account=card, suggested_other_leg=card)]
    )
    store.set_statement(
        batch_id,
        StatementSummary(
            account=card,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            previous_balance=Decimal("0"),
            payments_credits=Decimal("0"),
            purchases_debits=Decimal("120"),
            total_due=Decimal("120"),
        ),
    )
    led, svc = _svc(tmp_path, review=store)
    led.open_named_accounts([card])

    result = svc.rename(card, "Liabilities:CreditCard:Synth-Gold")

    # three fields (source, other leg, statement) in one batch
    assert result["references_updated"]["review_batches"] == 1
    [row] = store.get_batch(batch_id)
    assert row.source_account == "Liabilities:CreditCard:Synth-Gold"
    assert row.suggested_other_leg == "Liabilities:CreditCard:Synth-Gold"
    statement = store.statement(batch_id)
    assert statement is not None
    assert statement.account == "Liabilities:CreditCard:Synth-Gold"


def test_journal_failure_reverts_pending_batches(tmp_path, monkeypatch):
    store, batch_id, _ = _batch()
    led, svc = _svc(tmp_path, review=store)

    def boom(old, new):
        raise LedgerValidationError(["synthetic failure"])

    monkeypatch.setattr(led, "rename_account", boom)
    with pytest.raises(LedgerValidationError):
        svc.rename(OLD, NEW)

    got = store.get_batch(batch_id)
    assert got[0].suggested_category == OLD
    assert got[1].splits[0].account == OLD


def _merge_batch(suggested: str, entries=()) -> MergeBatch:
    return MergeBatch(
        entries=list(entries),
        accounts=[
            AccountMapping(raw="expenses:food", suggested=suggested, status="existing")
        ],
        commodities=[],
        unparsed=[],
        ignored_lines=0,
        duplicate_ids=set(),
        unbookable_ids=set(),
        parsed=ParsedLedger(transactions=[], unparsed=[], ignored_lines=0),
        filename="synth.ledger",
    )


def test_pending_ledger_merge_naming_old_refuses_rename(tmp_path):
    merge = LedgerMergeStore()
    merge.add_batch(_merge_batch(OLD))
    review, batch_id, _ = _batch()
    led, svc = _svc(tmp_path, review=review, merge=merge)
    main_before = led.main_file.read_text(encoding="utf-8")

    with pytest.raises(CodedError) as ei:
        svc.rename(OLD, NEW)

    assert ei.value.code == "E1095"
    assert ei.value.status == 409
    [blocking] = ei.value.data["batches"]
    assert blocking["filename"] == "synth.ledger"
    assert merge.get_batch(blocking["batch_id"]) is not None
    assert "synth.ledger" in ei.value.description
    assert led.main_file.read_text(encoding="utf-8") == main_before
    assert review.get_batch(batch_id)[0].suggested_category == OLD


def test_pending_ledger_merge_not_naming_old_does_not_block(tmp_path):
    merge = LedgerMergeStore()
    merge.add_batch(_merge_batch("Expenses:Travel"))
    _, svc = _svc(tmp_path, merge=merge)
    assert svc.rename(OLD, NEW)["to"] == NEW


def test_learned_rules_write_atomically(tmp_path, monkeypatch):
    calls = []
    real = learned_rules_module.write_text_atomic

    def spy(path, text):
        calls.append(path)
        real(path, text)

    monkeypatch.setattr(learned_rules_module, "write_text_atomic", spy)
    rules = LearnedRules(tmp_path / "rules" / "learned.yaml")
    rules.add("SYNTHMART", OLD)
    assert calls == [rules.path]


def test_learned_rules_write_waits_for_the_shared_lock(tmp_path):
    led = Ledger(tmp_path)
    led.bootstrap()
    rules = LearnedRules(tmp_path / "rules" / "learned.yaml", lock=led.write_lock())
    done = threading.Event()

    def writer():
        rules.add("SYNTHMART", OLD)
        done.set()

    with led.write_lock():
        thread = threading.Thread(target=writer)
        thread.start()
        assert not done.wait(0.2)  # blocked while the journal lock is held
    thread.join(timeout=5)
    assert done.is_set()
    assert rules.exact("SYNTHMART") == OLD


def test_app_learned_rules_share_the_journal_lock(tmp_path):
    from backend.app.service.wiring import build_services

    services = build_services(tmp_path)
    assert services.categorizer.rules.lock is services.ledger.write_lock()


def test_pending_ledger_merge_entry_posting_naming_old_refuses_rename(tmp_path):
    from beancount.core import data as bdata
    from beancount.core.amount import Amount

    txn = bdata.Transaction(
        meta={"filename": "synth.ledger", "lineno": 1},
        date=date(2026, 1, 5),
        flag="*",
        payee=None,
        narration="SYNTH",
        tags=frozenset(),
        links=frozenset(),
        postings=[
            bdata.Posting(OLD, Amount(Decimal("5"), "INR"), None, None, None, None),
            bdata.Posting(BANK, Amount(Decimal("-5"), "INR"), None, None, None, None),
        ],
    )
    merge = LedgerMergeStore()
    # the mapping suggests something else; only a converted entry names OLD
    merge.add_batch(_merge_batch("Expenses:Travel", entries=[txn]))
    _, svc = _svc(tmp_path, merge=merge)
    with pytest.raises(CodedError) as ei:
        svc.rename(OLD, NEW)
    assert ei.value.code == "E1095"


def test_discarded_ledger_merge_no_longer_blocks(tmp_path):
    merge = LedgerMergeStore()
    batch_id = merge.add_batch(_merge_batch(OLD))
    _, svc = _svc(tmp_path, merge=merge)
    assert merge.discard(batch_id) is True
    assert merge.discard(batch_id) is False
    assert svc.rename(OLD, NEW)["to"] == NEW


def _blocked_until_released(lock, call) -> None:
    """`call` run on another thread must wait while `lock` is held here."""
    done = threading.Event()
    errors: list[BaseException] = []

    def run():
        try:
            call()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            done.set()

    with lock:
        thread = threading.Thread(target=run)
        thread.start()
        assert not done.wait(0.2)
    thread.join(timeout=10)
    assert done.is_set()
    assert not errors, errors


def test_classify_merchant_holds_the_journal_lock(tmp_path, monkeypatch):
    from backend.app.service.categorize.service import CategorizeService

    led = Ledger(tmp_path)
    led.bootstrap()
    rules = LearnedRules(tmp_path / "rules" / "learned.yaml")
    service = CategorizeService(led, rules)
    held: list[bool] = []
    real = led.recategorize

    def spy(*args, **kwargs):
        held.append(led.write_lock()._is_owned())  # type: ignore[attr-defined]
        return real(*args, **kwargs)

    monkeypatch.setattr(led, "recategorize", spy)
    _blocked_until_released(
        led.write_lock(), lambda: service.classify_merchant("SYNTHMART", OLD)
    )
    assert held == [True]
    assert rules.exact("SYNTHMART") == OLD


def test_classify_merchants_holds_the_journal_lock(tmp_path, monkeypatch):
    from backend.app.service.categorize.service import CategorizeService

    led = Ledger(tmp_path)
    led.bootstrap()
    rules = LearnedRules(tmp_path / "rules" / "learned.yaml")
    service = CategorizeService(led, rules)
    held: list[bool] = []
    real = led.recategorize_many

    def spy(*args, **kwargs):
        held.append(led.write_lock()._is_owned())  # type: ignore[attr-defined]
        return real(*args, **kwargs)

    monkeypatch.setattr(led, "recategorize_many", spy)
    _blocked_until_released(
        led.write_lock(),
        lambda: service.classify_merchants("1\tExpenses:Food", {"1": "SYNTHMART"}),
    )
    assert held == [True]


def test_app_stores_share_the_journal_lock(tmp_path):
    from backend.app.service.wiring import build_services

    services = build_services(tmp_path)
    lock = services.ledger.write_lock()
    assert services.categorizer.aliases is not None
    for store in (
        services.categorizer.aliases,
        services.review_store,
        services.template_store,
        services.pdf_template_store,
        services.budget_store,
        services.booking_store,
    ):
        assert store.lock is lock
