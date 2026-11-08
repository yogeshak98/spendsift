"""Opening writes (add, started at zero, correct) over synthetic tmp_path
journals. Every refusal leaves every journal file byte-identical and takes
no snapshot; every write takes exactly one."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from datetime import date
from decimal import Decimal

import pytest
from beancount.core import data
from beancount.core.amount import Amount

from backend.app.core.errors.errors import CodedError
from backend.app.models.api.openings import (
    OpeningCorrectRequest,
    OpeningCreateRequest,
    OpeningExpected,
    OpeningZeroRequest,
)
from backend.app.models.domain.accounts import EQUITY_OPENING
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.models.storage.opening_evidence import (
    FirstRowOpening,
    OpeningEvidence,
    PrintedOpening,
)
from backend.app.models.storage.statement_record import StatementSummary
from backend.app.persistence.journal import Ledger
from backend.app.persistence.review_batches import ReviewStore
from backend.app.persistence.templates import PdfTemplateStore
from backend.app.service.ledger.account_meta import AccountMetaService
from backend.app.service.ledger.openings import read, write
from backend.app.service.ledger.openings.detect import account_statuses
from backend.app.service.ledger.openings.keys import (
    OPENING_BASIS_KEY,
    OPENING_CORRECTED_KEY,
    OPENING_OPEN_KEY,
    OPENING_SOURCE_KEY,
    OPENING_START_KEY,
    OPENING_TXN_KEY,
    OPENING_ZERO,
)
from backend.app.service.ledger.openings.service import OpeningService
from backend.app.service.reports.health.ledger_view import open_meta_from_entries

TODAY = date(2026, 6, 15)

BANK = "Assets:Checking:Acme"  # empty: no opening, no postings
SPENT = "Assets:Checking:Busy"  # missing: postings from 2025-05-10, no opening
OPENED = "Assets:Checking:Opened"  # opening 2025-03-31, first posting 2025-04-03
OLD = "Assets:Checking:Old"  # closed
CARD = "Liabilities:CreditCard:Zeta"
TDS = "Assets:Receivable:TDS"
ASHA = "Liabilities:People:Asha"
BEN = "Liabilities:People:Ben"
CHEN = "Liabilities:People:Chen"
FOOD = "Expenses:Food"

_ACCOUNTS = (BANK, SPENT, OPENED, OLD, CARD, TDS, ASHA, BEN, CHEN, FOOD)

_BASE_2025 = f"""\
2025-03-31 * "Opening balance"
  txn-id: "t-open"
  opening-source: "statement"
  opening-basis: "printed on the statement"
  {OPENED}                5000.00 INR
  {EQUITY_OPENING}       -5000.00 INR

2025-04-03 * "Groceries"
  txn-id: "t-food-1"
  {OPENED}                -120.00 INR
  {FOOD}                   120.00 INR

2025-05-10 * "Groceries"
  txn-id: "t-food-2"
  {SPENT}                  -80.00 INR
  {FOOD}                    80.00 INR

2025-03-31 * "Opening balance"
  txn-id: "t-old"
  {OLD}                     10.00 INR
  {EQUITY_OPENING}         -10.00 INR

2025-04-05 * "Settle old"
  txn-id: "t-old-out"
  {OLD}                    -10.00 INR
  {FOOD}                    10.00 INR
"""


def _ledger(tmp_path, text_2025: str = _BASE_2025, text_2026: str = "") -> Ledger:
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap(seed_accounts=())
    opens = "".join(f"2020-01-01 open {a} INR\n" for a in _ACCOUNTS)
    ledger.main_file.write_text(
        'option "operating_currency" "INR"\n\n'
        "2000-01-01 commodity INR\n"
        f"2020-01-01 open {EQUITY_OPENING} INR\n"
        f"{opens}"
        f"2026-01-01 close {OLD}\n"
        'include "2025.beancount"\n'
        'include "2026.beancount"\n',
        encoding="utf-8",
    )
    (ledger.journal_dir / "2025.beancount").write_text(text_2025, encoding="utf-8")
    (ledger.journal_dir / "2026.beancount").write_text(text_2026, encoding="utf-8")
    assert ledger.validate() == []
    return ledger


def _review(ledger: Ledger) -> ReviewStore:
    # The app shares the journal write lock with the review store.
    return ReviewStore(lock=ledger.write_lock())


def _service(
    tmp_path, ledger: Ledger, review: ReviewStore | None = None
) -> OpeningService:
    return OpeningService(
        ledger=ledger,
        investment_ledger=ledger,
        review_store=review if review is not None else _review(ledger),
        account_meta=AccountMetaService(
            ledger, PdfTemplateStore(tmp_path / "templates" / "pdf")
        ),
        scheme=lambda _symbol: None,
        clock=lambda: TODAY,
    )


def _hashes(ledger: Ledger) -> dict[str, str]:
    return {
        p.relative_to(ledger.journal_dir).as_posix(): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(ledger.journal_dir.rglob("*"))
        if p.is_file()
    }


def _snapshots(ledger: Ledger) -> int:
    return len(list(ledger.snapshots_dir.iterdir()))


def _candidate(account: str, when: date, amount: str = "100.00") -> CandidateTxn:
    return CandidateTxn(
        date=when,
        amount=Decimal(amount),
        raw_narration="ROW",
        normalized_narration="row",
        source_account=account,
        direction=Direction.DEBIT,
    )


_PRINTED = PrintedOpening(
    label_text="Opening Balance",
    raw_text="10,000.00",
    value=Decimal("10000.00"),
    suffix=None,
    row=2,
    column=3,
)
# Distinct from the printed figure, so a source read from the wrong
# suggestion shows.
_FIRST_ROW = FirstRowOpening(
    date=date(2025, 4, 3),
    balance=Decimal("9300.00"),
    signed_amount=Decimal("-500.00"),
    derived=Decimal("9800.00"),
)


def _bank_batch(review: ReviewStore, **evidence) -> str:
    batch_id = review.add_batch(
        [
            _candidate(BANK, date(2025, 4, 9)),
            _candidate(BANK, date(2025, 4, 3)),
            _candidate(BANK, date(2025, 4, 20)),
            # Not this account's rows: they never set its earliest date.
            _candidate(SPENT, date(2025, 3, 1)),
            _candidate(BANK, date(2025, 3, 2)).model_copy(
                update={"needs_account": True}
            ),
        ]
    )
    fields = {"account": BANK, "printed": _PRINTED, "first_row": _FIRST_ROW}
    fields.update(evidence)
    review.set_opening_evidence(batch_id, OpeningEvidence(printed_error=None, **fields))
    return batch_id


def _add(**overrides) -> OpeningCreateRequest:
    body = {
        "account": BANK,
        "amount": "10000.00",
        "direction": "have",
        "date": date(2025, 3, 31),
        **overrides,
    }
    return OpeningCreateRequest(**body)


def _txns(ledger: Ledger, account: str) -> list[data.Transaction]:
    return [
        e
        for e in ledger.load(fresh=True).entries
        if isinstance(e, data.Transaction)
        and any(p.account == account for p in e.postings)
    ]


def _opening_of(ledger: Ledger, account: str) -> data.Transaction:
    [txn] = [
        t
        for t in _txns(ledger, account)
        if any(p.account == EQUITY_OPENING for p in t.postings)
    ]
    return txn


def _amounts(txn: data.Transaction) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for p in txn.postings:
        out[p.account] = out.get(p.account, Decimal(0)) + p.units.number
    return out


def _balances(ledger: Ledger, account: str) -> list[data.Balance]:
    return [
        e
        for e in ledger.load(fresh=True).entries
        if isinstance(e, data.Balance) and e.account == account
    ]


def _status(ledger: Ledger, account: str):
    return next(
        r
        for r in account_statuses(ledger.load(fresh=True).entries, today=TODAY)
        if r.account == account
    )


def _refused(ledger: Ledger, call, code: str) -> CodedError:
    hashes, count = _hashes(ledger), _snapshots(ledger)
    with pytest.raises(CodedError) as caught:
        call()
    assert caught.value.code == code, caught.value.description
    assert _hashes(ledger) == hashes
    assert _snapshots(ledger) == count
    return caught.value


# ── add ──────────────────────────────────────────────────────────────────────
STATEMENT_BASIS = (
    "Opening Balance printed 10,000.00; recorded as 10000.00 INR on " + BANK
)


def test_add_bank_opening_from_batch_statement_source(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = _bank_batch(review)
    count = _snapshots(ledger)

    resp = _service(tmp_path, ledger, review).add(_add(batch_id=batch_id))

    assert resp.source == "statement"
    assert resp.ledger_amount == "10000.00"
    assert resp.date == date(2025, 3, 31)
    assert resp.balance_check is False
    assert re.fullmatch(r"[0-9a-f]{32}", resp.txn_id)
    assert _snapshots(ledger) == count + 1
    assert (ledger.snapshots_dir / resp.snapshot).is_dir()

    txn = _opening_of(ledger, BANK)
    assert txn.date == date(2025, 3, 31)
    assert txn.flag == "*"
    assert txn.narration == "Opening balance"
    assert txn.meta["txn-id"] == resp.txn_id
    assert txn.meta[OPENING_SOURCE_KEY] == "statement"
    # The prompt's arithmetic, its quotes dropped.
    assert txn.meta[OPENING_BASIS_KEY] == STATEMENT_BASIS
    assert OPENING_CORRECTED_KEY not in txn.meta
    assert [(p.account, p.units.number, p.units.currency) for p in txn.postings] == [
        (BANK, Decimal("10000.00"), "INR"),
        (EQUITY_OPENING, Decimal("-10000.00"), "INR"),
    ]
    assert _balances(ledger, BANK) == []
    assert ledger.validate() == []
    status = _status(ledger, BANK)
    assert status.status == "opening"
    assert status.opening is not None and status.opening.source == "statement"


def test_add_edited_amount_is_manual(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = _bank_batch(review)

    resp = _service(tmp_path, ledger, review).add(
        _add(batch_id=batch_id, amount="9999.00")
    )

    assert resp.source == "manual"
    txn = _opening_of(ledger, BANK)
    assert txn.meta[OPENING_SOURCE_KEY] == "manual"
    assert txn.meta[OPENING_BASIS_KEY] == "entered by the user"
    assert _amounts(txn)[BANK] == Decimal("9999.00")


def test_add_matching_the_derived_alternative_is_derived(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = _bank_batch(review)

    resp = _service(tmp_path, ledger, review).add(
        _add(batch_id=batch_id, amount="9800.00")
    )

    assert resp.source == "derived"
    txn = _opening_of(ledger, BANK)
    assert txn.meta[OPENING_SOURCE_KEY] == "derived"
    assert txn.meta[OPENING_BASIS_KEY] == (
        "first row 2025-04-03: balance 9300.00 plus debit 500.00 = 9800.00"
    )


def test_add_matching_a_figure_with_the_other_sign_is_manual(tmp_path):
    # "I owe 10000" is not the printed "I have 10000".
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = _bank_batch(review)

    resp = _service(tmp_path, ledger, review).add(
        _add(batch_id=batch_id, direction="owe")
    )

    assert resp.source == "manual"
    assert resp.ledger_amount == "-10000.00"


def test_add_without_batch_or_with_multi_account_evidence_is_manual(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = _bank_batch(review, account=None)  # several source accounts
    svc = _service(tmp_path, ledger, review)

    assert svc.add(_add(batch_id=batch_id)).source == "manual"
    assert svc.add(_add(account=CARD, direction="owe")).source == "manual"


def test_add_card_from_pdf_previous_balance(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = review.add_batch([_candidate(CARD, date(2025, 3, 18))])
    review.set_statement(
        batch_id,
        StatementSummary(
            account=CARD,
            period_start=date(2025, 3, 16),
            period_end=date(2025, 4, 15),
            previous_balance=Decimal("4210.37"),
            payments_credits=Decimal("0"),
            purchases_debits=Decimal("100.00"),
            total_due=Decimal("4310.37"),
        ),
    )

    resp = _service(tmp_path, ledger, review).add(
        _add(
            account=CARD,
            amount="4210.37",
            direction="owe",
            date=date(2025, 3, 15),
            batch_id=batch_id,
        )
    )

    assert resp.source == "statement"
    assert resp.ledger_amount == "-4210.37"
    txn = _opening_of(ledger, CARD)
    assert txn.meta[OPENING_BASIS_KEY] == (
        "previous balance 4210.37 on the statement for 2025-03-16 to 2025-04-15; "
        f"recorded as -4210.37 INR on {CARD}"
    )
    assert _amounts(txn) == {
        CARD: Decimal("-4210.37"),
        EQUITY_OPENING: Decimal("4210.37"),
    }


def test_add_refusals_in_order(tmp_path):
    """Each case also breaks every later check, so a check run out of order
    shows as the wrong code."""
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = _bank_batch(review)
    svc = _service(tmp_path, ledger, review)
    future = date(2026, 6, 16)
    # Breaks E1093, E1006 and E1089 (after today).
    worst = {"amount": "abc", "batch_id": "no-such-batch", "date": future}

    _refused(ledger, lambda: svc.add(_add(account="Assets:Nope", **worst)), "E1039")
    # OLD is closed and also has an opening (E1088 later).
    _refused(ledger, lambda: svc.add(_add(account=OLD, **worst)), "E1084")
    err = _refused(ledger, lambda: svc.add(_add(account=TDS, **worst)), "E1090")
    assert err.data == {"account": TDS, "reason": "it never takes an opening balance"}
    err = _refused(ledger, lambda: svc.add(_add(account=CARD, **worst)), "E1090")
    assert err.data["reason"] == "the direction have is not allowed for it"

    for amount, reason in [
        ("-5", "must not be negative"),
        ("-0.00", "must not be negative"),
        ("1.234", "has more than 2 decimal places"),
        ("abc", "is not a decimal number"),
        ("1e3", "is not a decimal number"),
        ("NaN", "is not a decimal number"),
        ("1,000", "is not a decimal number"),
        ("", "is not a decimal number"),
    ]:
        # OPENED already has an opening: E1088 would come next.
        err = _refused(
            ledger,
            lambda a=amount: svc.add(_add(**{**worst, "account": OPENED, "amount": a})),
            "E1093",
        )
        assert err.data == {"field": "amount", "value": amount, "reason": reason}

    err = _refused(
        ledger,
        lambda: svc.add(_add(**{**worst, "account": OPENED, "amount": "1.00"})),
        "E1088",
    )
    assert err.data == {
        "account": OPENED,
        "date": "2025-03-31",
        "ledger_amount": "5000.00",
    }

    _refused(ledger, lambda: svc.add(_add(**{**worst, "amount": "1.00"})), "E1006")

    err = _refused(
        ledger,
        lambda: svc.add(_add(batch_id=batch_id, date=date(2025, 4, 3))),
        "E1089",
    )
    assert err.data == {
        "account": BANK,
        "date": "2025-04-03",
        "limit": "2025-04-03",
        "reason": "it is not before the batch's earliest row for the account",
    }

    err = _refused(
        ledger, lambda: svc.add(_add(account=SPENT, date=date(2025, 5, 10))), "E1089"
    )
    assert err.data == {
        "account": SPENT,
        "date": "2025-05-10",
        "limit": "2025-05-10",
        "reason": "it is not before the account's first posting",
    }

    err = _refused(ledger, lambda: svc.add(_add(date=future)), "E1089")
    assert err.data == {
        "account": BANK,
        "date": "2026-06-16",
        "limit": "2026-06-15",
        "reason": "it is after today",
    }


def test_add_date_limit_is_the_earliest_blocking_date(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    # SPENT's first posting is 2025-05-10; this batch's earliest SPENT row
    # is earlier.
    batch_id = review.add_batch([_candidate(SPENT, date(2025, 4, 20))])
    svc = _service(tmp_path, ledger, review)

    err = _refused(
        ledger,
        lambda: svc.add(_add(account=SPENT, batch_id=batch_id, date=date(2025, 6, 1))),
        "E1089",
    )
    assert err.data["limit"] == "2025-04-20"
    assert "batch" in err.data["reason"]


def test_add_dated_today_is_allowed(tmp_path):
    ledger = _ledger(tmp_path)
    resp = _service(tmp_path, ledger).add(_add(date=TODAY, amount="1.50"))
    assert resp.date == TODAY
    assert ledger.validate() == []


def test_add_with_balance_check_writes_paired_assertion(tmp_path):
    ledger = _ledger(tmp_path)
    count = _snapshots(ledger)

    resp = _service(tmp_path, ledger).add(_add(amount="777.25", balance_check=True))

    assert resp.balance_check is True
    assert _snapshots(ledger) == count + 1
    txn = _opening_of(ledger, BANK)
    [balance] = _balances(ledger, BANK)
    assert balance.date == date(2025, 4, 1)
    assert balance.amount.number == Decimal("777.25")
    assert balance.amount.currency == "INR"
    assert balance.meta[OPENING_TXN_KEY] == txn.meta["txn-id"] == resp.txn_id
    assert ledger.validate() == []
    status = _status(ledger, BANK)
    assert status.opening is not None and status.opening.balance_check is True


def test_add_on_zero_marked_account_wins(tmp_path):
    ledger = _ledger(tmp_path)
    svc = _service(tmp_path, ledger)
    svc.mark_zero(OpeningZeroRequest(account=BANK, zero=True))
    assert _status(ledger, BANK).status == "zero"
    count = _snapshots(ledger)

    svc.add(_add(amount="250.00"))

    assert _snapshots(ledger) == count + 1  # one write: the mark is not touched
    assert _status(ledger, BANK).status == "opening"
    meta = open_meta_from_entries(ledger.load(fresh=True).entries)[BANK]
    assert meta[OPENING_OPEN_KEY] == OPENING_ZERO
    # The superseded mark can still be cleared.
    resp = svc.mark_zero(OpeningZeroRequest(account=BANK, zero=False))
    assert resp.status == "opening"
    meta = open_meta_from_entries(ledger.load(fresh=True).entries)[BANK]
    assert OPENING_OPEN_KEY not in meta


def test_add_basis_is_cleaned_and_capped(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    long_raw = '10,000.00 "as of" 31/03\n' + "x" * 300
    batch_id = _bank_batch(
        review,
        printed=_PRINTED.model_copy(
            update={"label_text": 'Bal "B/F"', "raw_text": long_raw}
        ),
        first_row=None,
    )

    _service(tmp_path, ledger, review).add(_add(batch_id=batch_id))

    basis = _opening_of(ledger, BANK).meta[OPENING_BASIS_KEY]
    assert len(basis) == 200
    assert basis.startswith("Bal B/F printed 10,000.00 as of 31/03 xxx")
    assert not set(basis) & set("\"'\\\n\r")
    text = (ledger.journal_dir / "2025.beancount").read_text(encoding="utf-8")
    [line] = [
        ln
        for ln in text.splitlines()
        if ln.strip().startswith("opening-basis:") and "Bal" in ln
    ]
    assert line.strip() == f'opening-basis: "{basis}"'
    assert ledger.validate() == []


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("a \"b\" 'c'", "a b c"),
        ("line\nbreak\r\tand \\slash", "line break and slash"),
        ("  spaced   out  ", "spaced out"),
        ("y" * 250, "y" * 200),
    ],
)
def test_clean_basis(raw, cleaned):
    assert write.clean_basis(raw) == cleaned


def test_concurrent_adds_write_one_opening(tmp_path, monkeypatch):
    """Two adds on one empty account: the first holds the lock from its
    check through its append, so the second sees the opening (E1088)."""
    ledger = _ledger(tmp_path)
    svc = _service(tmp_path, ledger)
    inside = threading.Event()
    real = write._new_txn_id

    def slow_id() -> str:
        # Runs between the checks and the append.
        inside.set()
        time.sleep(0.3)
        return real()

    monkeypatch.setattr(write, "_new_txn_id", slow_id)
    results: list[object] = []

    def run() -> None:
        try:
            results.append(svc.add(_add(amount="321.00")))
        except CodedError as exc:
            results.append(exc.code)

    first = threading.Thread(target=run)
    first.start()
    assert inside.wait(5)
    second = threading.Thread(target=run)
    second.start()
    first.join(10)
    second.join(10)
    assert not first.is_alive() and not second.is_alive()

    assert sorted(r if isinstance(r, str) else "ok" for r in results) == [
        "E1088",
        "ok",
    ]
    assert len(_txns(ledger, BANK)) == 1
    assert ledger.validate() == []


# ── started at zero ──────────────────────────────────────────────────────────
def test_mark_zero_round_trip(tmp_path):
    ledger = _ledger(tmp_path)
    svc = _service(tmp_path, ledger)
    count = _snapshots(ledger)

    resp = svc.mark_zero(OpeningZeroRequest(account=BANK, zero=True))
    assert (resp.account, resp.status) == (BANK, "zero")
    assert resp.account_display
    assert _snapshots(ledger) == count + 1
    meta = open_meta_from_entries(ledger.load(fresh=True).entries)[BANK]
    assert meta[OPENING_OPEN_KEY] == OPENING_ZERO
    assert _status(ledger, BANK).status == "zero"

    resp = svc.mark_zero(OpeningZeroRequest(account=BANK, zero=False))
    assert resp.status == "empty"
    assert _snapshots(ledger) == count + 2
    assert (
        OPENING_OPEN_KEY
        not in open_meta_from_entries(ledger.load(fresh=True).entries)[BANK]
    )
    assert ledger.validate() == []

    # Undoing a mark that is not there writes nothing.
    _hashes_before = _hashes(ledger)
    assert svc.mark_zero(OpeningZeroRequest(account=BANK, zero=False)).status == "empty"
    assert _hashes(ledger) == _hashes_before
    assert _snapshots(ledger) == count + 2


def test_mark_zero_on_a_missing_account_reads_zero(tmp_path):
    ledger = _ledger(tmp_path)
    resp = _service(tmp_path, ledger).mark_zero(
        OpeningZeroRequest(account=SPENT, zero=True)
    )
    assert resp.status == "zero"


def test_mark_zero_refusals(tmp_path):
    ledger = _ledger(tmp_path)
    svc = _service(tmp_path, ledger)

    def mark(account: str):
        return lambda: svc.mark_zero(OpeningZeroRequest(account=account, zero=True))

    _refused(ledger, mark("Assets:Nope"), "E1039")
    _refused(ledger, mark(OLD), "E1084")  # closed, with an opening
    _refused(ledger, mark(TDS), "E1090")
    _refused(ledger, mark(f"{EQUITY_OPENING}"), "E1090")
    err = _refused(ledger, mark(OPENED), "E1088")
    assert err.data == {
        "account": OPENED,
        "date": "2025-03-31",
        "ledger_amount": "5000.00",
    }


def test_set_opening_zero_check_order(tmp_path):
    ledger = _ledger(tmp_path)
    meta = AccountMetaService(ledger, PdfTemplateStore(tmp_path / "templates" / "pdf"))
    with pytest.raises(LookupError):
        meta.set_opening_zero("Assets:Nope", True)
    _refused(ledger, lambda: meta.set_opening_zero(OLD, True), "E1084")
    _refused(ledger, lambda: meta.set_opening_zero(TDS, True), "E1090")


# ── correct ──────────────────────────────────────────────────────────────────
def _fix(**overrides) -> OpeningCorrectRequest:
    body = {
        "account": OPENED,
        "amount": "5200.00",
        "direction": "have",
        "date": date(2025, 3, 31),
        "balance_check": False,
        "expected": OpeningExpected(date=date(2025, 3, 31), ledger_amount="5000.00"),
        **overrides,
    }
    return OpeningCorrectRequest(**body)


def test_correct_single_amount(tmp_path):
    ledger = _ledger(tmp_path)
    count = _snapshots(ledger)

    resp = _service(tmp_path, ledger).correct(_fix())

    assert resp.txn_id == "t-open"
    assert (resp.ledger_amount, resp.source, resp.date) == (
        "5200.00",
        "manual",
        date(2025, 3, 31),
    )
    assert (ledger.snapshots_dir / resp.snapshot).is_dir()
    assert _snapshots(ledger) == count + 1
    txn = _opening_of(ledger, OPENED)
    assert txn.meta["txn-id"] == "t-open"
    assert txn.narration == "Opening balance"
    assert txn.meta[OPENING_SOURCE_KEY] == "manual"
    assert txn.meta[OPENING_BASIS_KEY] == "entered by the user"
    assert txn.meta[OPENING_CORRECTED_KEY] == "2026-06-15"
    assert _amounts(txn) == {
        OPENED: Decimal("5200.00"),
        EQUITY_OPENING: Decimal("-5200.00"),
    }
    assert ledger.validate() == []
    status = _status(ledger, OPENED)
    assert status.opening is not None
    assert (status.opening.ledger_amount, status.opening.source) == (
        "5200.00",
        "manual",
    )


_LEGACY = f"""\
2025-03-31 * "Bank" "Initial Balance" #setup
    {BANK}          2500.00 INR
    {EQUITY_OPENING}

2025-04-03 * "Groceries"
  txn-id: "t-food"
  {BANK}   -50.00 INR
  {FOOD}    50.00 INR
"""


def test_correct_legacy_without_txn_id_gets_one(tmp_path):
    ledger = _ledger(tmp_path, _BASE_2025 + "\n" + _LEGACY)
    status = _status(ledger, BANK)
    assert status.opening is not None and status.opening.txn_id is None

    resp = _service(tmp_path, ledger).correct(
        _fix(
            account=BANK,
            amount="2600.00",
            expected=OpeningExpected(date=date(2025, 3, 31), ledger_amount="2500.00"),
        )
    )

    assert re.fullmatch(r"[0-9a-f]{32}", resp.txn_id)
    txn = _opening_of(ledger, BANK)
    assert txn.meta["txn-id"] == resp.txn_id
    # What the legacy entry said besides the figure is kept.
    assert (txn.payee, txn.narration, set(txn.tags)) == (
        "Bank",
        "Initial Balance",
        {"setup"},
    )
    assert _amounts(txn) == {
        BANK: Decimal("2600.00"),
        EQUITY_OPENING: Decimal("-2600.00"),
    }
    assert _status(ledger, BANK).opening.txn_id == resp.txn_id
    assert ledger.validate() == []


# Three people, three different figures (Ben is owed by the user).
_RESET = f"""\
2025-12-01 * "Liability Reset"
  opening-basis: "family reset sheet"
  {ASHA}       685.00 INR
  {BEN}       -400.00 INR
  {CHEN}       250.00 INR
  {EQUITY_OPENING}      -535.00 INR
"""
_ASHA_LATER = f"""\
2026-01-05 * "Asha repaid"
  txn-id: "t-asha"
  {ASHA}      -100.00 INR
  {FOOD}       100.00 INR
"""


def _shared_ledger(tmp_path, reset: str = _RESET) -> Ledger:
    return _ledger(tmp_path, _BASE_2025 + "\n" + reset, _ASHA_LATER)


def _fix_asha(**overrides) -> OpeningCorrectRequest:
    return _fix(
        **{
            "account": ASHA,
            "amount": "700.00",
            "direction": "owed_to_me",
            "date": date(2025, 12, 1),
            "expected": OpeningExpected(date=date(2025, 12, 1), ledger_amount="685.00"),
            **overrides,
        }
    )


def test_correct_shared_same_date_splits_out(tmp_path):
    """A shared opening is never rewritten for one person: the person is
    split out even on the same date, and the others keep their entry."""
    ledger = _shared_ledger(tmp_path)
    count = _snapshots(ledger)

    resp = _service(tmp_path, ledger).correct(_fix_asha())

    assert _snapshots(ledger) == count + 1
    [reset] = [t for t in _txns(ledger, BEN) if t.narration == "Liability Reset"]
    assert _amounts(reset) == {
        BEN: Decimal("-400.00"),
        CHEN: Decimal("250.00"),
        EQUITY_OPENING: Decimal("150.00"),  # -535 moved by exactly +685
    }
    assert [p.account for p in reset.postings] == [BEN, CHEN, EQUITY_OPENING]
    for key in ("txn-id", OPENING_SOURCE_KEY, OPENING_CORRECTED_KEY):
        assert key not in reset.meta
    assert reset.meta[OPENING_BASIS_KEY] == "family reset sheet"

    split = _opening_of(ledger, ASHA)
    assert split is not reset
    assert (split.date, split.narration) == (date(2025, 12, 1), "Opening balance")
    assert split.meta["txn-id"] == resp.txn_id
    assert split.meta[OPENING_SOURCE_KEY] == "manual"
    assert split.meta[OPENING_CORRECTED_KEY] == "2026-06-15"
    assert _amounts(split) == {
        ASHA: Decimal("700.00"),
        EQUITY_OPENING: Decimal("-700.00"),
    }
    assert ledger.validate() == []

    asha = _status(ledger, ASHA).opening
    assert asha is not None
    assert (asha.shared, asha.source, asha.ledger_amount) == (False, "manual", "700.00")
    for person, amount in ((BEN, "-400.00"), (CHEN, "250.00")):
        row = _status(ledger, person).opening
        assert row is not None
        assert (row.source, row.shared, row.ledger_amount) == ("legacy", True, amount)


def test_correct_shared_new_date_splits_out(tmp_path):
    ledger = _shared_ledger(tmp_path)
    count = _snapshots(ledger)

    resp = _service(tmp_path, ledger).correct(_fix_asha(date=date(2025, 11, 30)))

    assert _snapshots(ledger) == count + 1
    [reset] = [t for t in _txns(ledger, BEN) if t.narration == "Liability Reset"]
    assert ASHA not in _amounts(reset)
    assert _amounts(reset) == {
        BEN: Decimal("-400.00"),
        CHEN: Decimal("250.00"),
        EQUITY_OPENING: Decimal("150.00"),  # -535 + 685
    }
    # The shared entry is otherwise untouched.
    assert "txn-id" not in reset.meta
    assert reset.meta[OPENING_BASIS_KEY] == "family reset sheet"
    assert OPENING_SOURCE_KEY not in reset.meta
    split = _opening_of(ledger, ASHA)
    assert split.date == date(2025, 11, 30)
    assert split.narration == "Opening balance"
    assert split.meta["txn-id"] == resp.txn_id
    assert split.meta[OPENING_SOURCE_KEY] == "manual"
    assert split.meta[OPENING_CORRECTED_KEY] == "2026-06-15"
    assert _amounts(split) == {
        ASHA: Decimal("700.00"),
        EQUITY_OPENING: Decimal("-700.00"),
    }
    assert ledger.validate() == []
    asha = _status(ledger, ASHA).opening
    assert asha is not None and not asha.shared and asha.date == date(2025, 11, 30)
    for person, amount in ((BEN, "-400.00"), (CHEN, "250.00")):
        row = _status(ledger, person).opening
        assert row is not None and row.ledger_amount == amount and row.shared


_RESET_WITH_ID = f"""\
2025-12-01 * "Liability Reset"
  txn-id: "t-reset"
  {ASHA}       685.00 INR
  {BEN}       -400.00 INR
  {CHEN}       250.00 INR
  {EQUITY_OPENING}      -535.00 INR

2025-12-02 balance {ASHA}   685.00 INR
  opening-txn: "t-reset"

2025-12-02 balance {BEN}   -400.00 INR
  opening-txn: "t-reset"
"""


def test_correct_shared_new_date_moves_the_paired_balance(tmp_path):
    ledger = _shared_ledger(tmp_path, _RESET_WITH_ID)
    assert _status(ledger, ASHA).opening.balance_check is True

    resp = _service(tmp_path, ledger).correct(
        _fix_asha(date=date(2025, 11, 30), balance_check=True)
    )

    [balance] = _balances(ledger, ASHA)
    assert balance.date == date(2025, 12, 1)
    assert balance.amount.number == Decimal("700.00")
    assert balance.meta[OPENING_TXN_KEY] == resp.txn_id != "t-reset"
    assert ledger.validate() == []


def test_correct_shared_same_date_splits_the_paired_balance_out(tmp_path):
    ledger = _shared_ledger(tmp_path, _RESET_WITH_ID)
    ben_balance = [
        "2025-12-02 balance Liabilities:People:Ben   -400.00 INR",
        '  opening-txn: "t-reset"',
    ]
    text = (ledger.journal_dir / "2025.beancount").read_text(encoding="utf-8")
    assert "\n".join(ben_balance) in text

    resp = _service(tmp_path, ledger).correct(_fix_asha(balance_check=True))

    assert resp.txn_id != "t-reset"
    [balance] = _balances(ledger, ASHA)
    assert (balance.date, balance.amount.number) == (
        date(2025, 12, 2),
        Decimal("700.00"),
    )
    assert balance.meta[OPENING_TXN_KEY] == resp.txn_id
    [reset] = [t for t in _txns(ledger, BEN) if t.narration == "Liability Reset"]
    assert reset.meta["txn-id"] == "t-reset"
    assert OPENING_CORRECTED_KEY not in reset.meta
    # Ben's own paired balance is kept byte for byte.
    text = (ledger.journal_dir / "2025.beancount").read_text(encoding="utf-8")
    assert "\n".join(ben_balance) in text
    assert _status(ledger, BEN).opening.balance_check is True
    assert ledger.validate() == []


_OPEN_WITH_BALANCE = _BASE_2025.replace(
    '  opening-basis: "printed on the statement"\n'
    f"  {OPENED}                5000.00 INR\n"
    f"  {EQUITY_OPENING}       -5000.00 INR\n",
    '  opening-basis: "printed on the statement"\n'
    f"  {OPENED}                5000.00 INR\n"
    f"  {EQUITY_OPENING}       -5000.00 INR\n"
    "\n"
    f"2025-04-01 balance {OPENED}   5000.00 INR\n"
    '  opening-txn: "t-open"\n',
)


def test_correct_moves_paired_balance(tmp_path):
    ledger = _ledger(tmp_path, _OPEN_WITH_BALANCE)
    svc = _service(tmp_path, ledger)
    count = _snapshots(ledger)

    svc.correct(_fix(date=date(2025, 3, 30), amount="5300.00", balance_check=True))

    assert _snapshots(ledger) == count + 1
    [balance] = _balances(ledger, OPENED)
    assert balance.date == date(2025, 3, 31)
    assert balance.amount.number == Decimal("5300.00")
    assert balance.meta[OPENING_TXN_KEY] == "t-open"
    assert _opening_of(ledger, OPENED).date == date(2025, 3, 30)
    assert ledger.validate() == []

    svc.correct(
        _fix(
            date=date(2025, 3, 30),
            amount="5300.00",
            balance_check=False,
            expected=OpeningExpected(date=date(2025, 3, 30), ledger_amount="5300.00"),
        )
    )

    assert _balances(ledger, OPENED) == []
    assert _status(ledger, OPENED).opening.balance_check is False
    assert ledger.validate() == []


def test_correct_adds_a_paired_balance(tmp_path):
    ledger = _ledger(tmp_path)
    resp = _service(tmp_path, ledger).correct(_fix(balance_check=True))
    [balance] = _balances(ledger, OPENED)
    assert (balance.date, balance.amount.number) == (
        date(2025, 4, 1),
        Decimal("5200.00"),
    )
    assert balance.meta[OPENING_TXN_KEY] == resp.txn_id == "t-open"
    assert ledger.validate() == []


def test_correct_stale_expected_is_e1091(tmp_path):
    # On file 5000.00; the correction says 5200.00; it was made against 4800.00.
    ledger = _ledger(tmp_path)
    svc = _service(tmp_path, ledger)
    for expected in (
        OpeningExpected(date=date(2025, 3, 31), ledger_amount="4800.00"),
        OpeningExpected(date=date(2025, 3, 30), ledger_amount="5000.00"),
        OpeningExpected(date=date(2025, 3, 31), ledger_amount="-5000.00"),
    ):
        err = _refused(
            ledger, lambda e=expected: svc.correct(_fix(expected=e)), "E1091"
        )
        assert err.data == {"account": OPENED}
    # The same figure written another way is not stale.
    svc.correct(
        _fix(expected=OpeningExpected(date=date(2025, 3, 31), ledger_amount="5000"))
    )


def test_correct_without_opening_is_e1091(tmp_path):
    ledger = _ledger(tmp_path)
    _refused(
        ledger,
        lambda: _service(tmp_path, ledger).correct(
            _fix(account=BANK, date=date(2025, 3, 31))
        ),
        "E1091",
    )


def test_correct_malformed_expected_amount_is_e1093(tmp_path):
    ledger = _ledger(tmp_path)
    svc = _service(tmp_path, ledger)
    for bad in ("abc", "5000.001", "--5", ""):
        err = _refused(
            ledger,
            lambda b=bad: svc.correct(
                _fix(expected=OpeningExpected(date=date(2025, 3, 31), ledger_amount=b))
            ),
            "E1093",
        )
        assert err.data["field"] == "expected.ledger_amount"


def test_correct_refusals_in_order(tmp_path):
    ledger = _ledger(tmp_path)
    svc = _service(tmp_path, ledger)
    stale = OpeningExpected(date=date(2024, 1, 1), ledger_amount="1.00")
    worst = {"amount": "abc", "date": date(2026, 6, 16), "expected": stale}

    _refused(ledger, lambda: svc.correct(_fix(account="Assets:Nope", **worst)), "E1039")
    _refused(ledger, lambda: svc.correct(_fix(account=OLD, **worst)), "E1084")
    _refused(ledger, lambda: svc.correct(_fix(account=TDS, **worst)), "E1090")
    _refused(ledger, lambda: svc.correct(_fix(account=CARD, **worst)), "E1090")
    err = _refused(ledger, lambda: svc.correct(_fix(**worst)), "E1093")
    assert err.data["field"] == "amount"
    _refused(ledger, lambda: svc.correct(_fix(**{**worst, "amount": "1.00"})), "E1091")
    err = _refused(ledger, lambda: svc.correct(_fix(date=date(2026, 6, 16))), "E1089")
    assert err.data["limit"] == "2026-06-15"


def test_correct_date_not_before_first_posting_is_e1089(tmp_path):
    ledger = _ledger(tmp_path)
    err = _refused(
        ledger,
        lambda: _service(tmp_path, ledger).correct(_fix(date=date(2025, 4, 3))),
        "E1089",
    )
    assert err.data == {
        "account": OPENED,
        "date": "2025-04-03",
        "limit": "2025-04-03",
        "reason": "it is not before the account's first posting",
    }


_LATE = f"""\
2025-04-03 * "Groceries"
  txn-id: "t-late-food"
  {BANK}   -50.00 INR
  {FOOD}    50.00 INR

2025-06-30 * "Opening balance"
  txn-id: "t-late"
  {BANK}   900.00 INR
  {EQUITY_OPENING}  -900.00 INR
"""


def test_correct_late_opening_must_move_before_first_posting(tmp_path):
    ledger = _ledger(tmp_path, _BASE_2025 + "\n" + _LATE)
    svc = _service(tmp_path, ledger)
    assert _status(ledger, BANK).warn is True
    expected = OpeningExpected(date=date(2025, 6, 30), ledger_amount="900.00")

    # A new date must still come before the first posting.
    _refused(
        ledger,
        lambda: svc.correct(
            _fix(
                account=BANK, amount="950.00", date=date(2025, 6, 29), expected=expected
            )
        ),
        "E1089",
    )
    svc.correct(
        _fix(account=BANK, amount="950.00", date=date(2025, 4, 2), expected=expected)
    )
    status = _status(ledger, BANK)
    assert (status.status, status.warn) == ("opening", False)
    assert ledger.validate() == []


def test_correct_late_opening_at_its_suggested_date_clears_the_warning(tmp_path):
    """The status row's suggested_date for a late opening is accepted as
    shown, with the row's own expected."""
    ledger = _ledger(tmp_path, _BASE_2025 + "\n" + _LATE)
    row = _status(ledger, BANK)
    assert (row.status, row.warn, row.first_posting) == (
        "opening",
        True,
        date(2025, 4, 3),
    )
    assert row.suggested_date < row.first_posting
    assert row.opening is not None

    _service(tmp_path, ledger).correct(
        _fix(
            account=BANK,
            amount=row.opening.amount,
            direction=row.opening.direction,
            date=row.suggested_date,
            expected=OpeningExpected(
                date=row.opening.date, ledger_amount=row.opening.ledger_amount
            ),
        )
    )

    after = _status(ledger, BANK)
    assert (after.status, after.warn) == ("opening", False)
    assert after.opening is not None and after.opening.date == row.suggested_date
    assert ledger.validate() == []


@pytest.mark.parametrize(
    ("extra", "opening_date", "warn_after"),
    [
        # Same-day legacy opening: not late, but not before its first posting.
        (
            f"""\
2025-06-30 * "Opening balance"
  txn-id: "t-same"
  {BANK}   900.00 INR
  {EQUITY_OPENING}  -900.00 INR

2025-06-30 * "Groceries"
  txn-id: "t-same-food"
  {BANK}   -50.00 INR
  {FOOD}    50.00 INR
""",
            date(2025, 6, 30),
            False,
        ),
        # Late opening: the amount can be fixed in place; it stays late.
        (_LATE, date(2025, 6, 30), True),
    ],
    ids=["same-day", "late"],
)
def test_correct_accepts_an_unchanged_date_past_the_first_posting(
    tmp_path, extra, opening_date, warn_after
):
    ledger = _ledger(tmp_path, _BASE_2025 + "\n" + extra)
    _service(tmp_path, ledger).correct(
        _fix(
            account=BANK,
            amount="950.00",
            date=opening_date,
            expected=OpeningExpected(date=opening_date, ledger_amount="900.00"),
        )
    )
    after = _status(ledger, BANK)
    assert after.opening is not None
    assert (after.opening.date, after.opening.ledger_amount) == (
        opening_date,
        "950.00",
    )
    assert after.warn is warn_after
    assert ledger.validate() == []


def test_correct_breaking_a_hand_written_balance_is_e1089(tmp_path):
    """A hand-written assertion the app does not own pins the old figure:
    the write is restored and the refusal names the assertion's date."""
    ledger = _ledger(
        tmp_path,
        _BASE_2025
        + f"\n2025-04-02 balance {OPENED}   5000.00 INR\n"
        + f"\n2025-06-01 balance {OPENED}   4880.00 INR\n",
    )
    hashes, count = _hashes(ledger), _snapshots(ledger)

    with pytest.raises(CodedError) as caught:
        _service(tmp_path, ledger).correct(_fix())

    assert caught.value.code == "E1089"
    assert caught.value.data == {
        "account": OPENED,
        "date": "2025-03-31",
        "limit": "2025-04-02",
        "reason": (
            f"a balance assertion on {OPENED} dated 2025-04-02 no longer holds; "
            "fix or remove it first"
        ),
    }
    assert _hashes(ledger) == hashes
    assert _snapshots(ledger) == count + 1  # the restore point it rolled back to


def test_correct_other_validation_failures_are_not_relabelled(tmp_path, monkeypatch):
    from backend.app.core.errors.ledger import LedgerValidationError

    ledger = _ledger(tmp_path)

    def broken(*_args, **_kwargs):
        raise LedgerValidationError(["Transaction does not balance: (1.00 INR)"])

    monkeypatch.setattr(ledger, "replace_entries", broken)
    with pytest.raises(LedgerValidationError):
        _service(tmp_path, ledger).correct(_fix())


# An opening, then a later equity-funded payout on the same account.
_TWO_OPENINGS = f"""\
2025-01-10 * "Opening balance"
  txn-id: "e1"
  {BANK}   100.00 INR
  {EQUITY_OPENING}  -100.00 INR

2025-02-10 * "Payout"
  txn-id: "e2"
  {BANK}    50.00 INR
  {EQUITY_OPENING}   -50.00 INR
"""


def _fix_bank(when: date) -> OpeningCorrectRequest:
    return _fix(
        account=BANK,
        amount="120.00",
        date=when,
        expected=OpeningExpected(date=date(2025, 1, 10), ledger_amount="100.00"),
    )


@pytest.mark.parametrize("when", [date(2025, 3, 1), date(2025, 2, 10)])
def test_correct_on_or_after_another_opening_entry_is_e1089(tmp_path, when):
    ledger = _ledger(tmp_path, _BASE_2025 + "\n" + _TWO_OPENINGS)
    err = _refused(
        ledger, lambda: _service(tmp_path, ledger).correct(_fix_bank(when)), "E1089"
    )
    assert err.data == {
        "account": BANK,
        "date": when.isoformat(),
        "limit": "2025-02-10",
        "reason": "it is not before another opening entry on the account, "
        "dated 2025-02-10",
    }


def test_correct_before_another_opening_entry_is_accepted(tmp_path):
    ledger = _ledger(tmp_path, _BASE_2025 + "\n" + _TWO_OPENINGS)

    resp = _service(tmp_path, ledger).correct(_fix_bank(date(2025, 2, 9)))

    opening = _status(ledger, BANK).opening
    assert opening is not None
    assert (opening.txn_id, opening.date, opening.ledger_amount) == (
        "e1",
        date(2025, 2, 9),
        "120.00",
    )
    assert resp.txn_id == "e1"
    assert ledger.validate() == []


def test_correct_shared_after_another_opening_entry_is_e1089(tmp_path):
    later = f"""
2025-12-15 * "Asha top-up"
  {ASHA}    20.00 INR
  {EQUITY_OPENING}  -20.00 INR
"""
    ledger = _shared_ledger(tmp_path, _RESET + later)
    err = _refused(
        ledger,
        lambda: _service(tmp_path, ledger).correct(_fix_asha(date=date(2025, 12, 20))),
        "E1089",
    )
    assert err.data["limit"] == "2025-12-15"


def test_add_on_31_dec_balance_lands_in_next_year_file(tmp_path):
    ledger = _ledger(tmp_path)

    resp = _service(tmp_path, ledger).add(
        _add(date=date(2025, 12, 31), amount="64.00", balance_check=True)
    )

    [balance] = _balances(ledger, BANK)
    assert balance.date == date(2026, 1, 1)
    assert balance.meta[OPENING_TXN_KEY] == resp.txn_id
    assert balance.meta["filename"].endswith("2026.beancount")
    assert _opening_of(ledger, BANK).meta["filename"].endswith("2025.beancount")
    assert ledger.validate() == []


def test_correct_across_the_year_moves_the_balance_file(tmp_path):
    food = f"""\
2025-04-03 * "Groceries"
  txn-id: "t-food-1"
  {OPENED}                -120.00 INR
  {FOOD}                   120.00 INR
"""
    assert food in _OPEN_WITH_BALANCE
    # OPENED's first posting moves to 2026-02-03, so 31 Dec 2025 is allowed.
    ledger = _ledger(
        tmp_path,
        _OPEN_WITH_BALANCE.replace(food, ""),
        food.replace("2025-04-03", "2026-02-03"),
    )
    _service(tmp_path, ledger).correct(
        _fix(date=date(2025, 12, 31), balance_check=True)
    )
    [balance] = _balances(ledger, OPENED)
    assert balance.date == date(2026, 1, 1)
    assert balance.meta["filename"].endswith("2026.beancount")
    assert ledger.validate() == []


_LOAN = "Liabilities:Loan:Car"
_PERSON_ASSET = "Assets:Receivable:Ravi"


@pytest.mark.parametrize(
    ("account", "direction", "sign"),
    [
        (BANK, "have", 1),
        (BANK, "owe", -1),
        (CARD, "owe", -1),
        (CARD, "owed_to_me", 1),
        (_LOAN, "owe", -1),
        (ASHA, "owed_to_me", 1),
        (ASHA, "owe", -1),
        (_PERSON_ASSET, "owed_to_me", 1),
        ("Assets:Cash", "have", 1),
    ],
)
def test_add_signs_by_kind_and_direction(tmp_path, account, direction, sign):
    ledger = _ledger(tmp_path)
    ledger.open_named_accounts([_LOAN, _PERSON_ASSET, "Assets:Cash"])

    resp = _service(tmp_path, ledger).add(
        _add(account=account, direction=direction, amount="42.10")
    )

    expected = Decimal("42.10") * sign
    assert resp.ledger_amount == str(expected)
    assert _amounts(_opening_of(ledger, account)) == {
        account: expected,
        EQUITY_OPENING: -expected,
    }
    opening = _status(ledger, account).opening
    assert opening is not None and opening.direction == direction


def _journal_text(ledger: Ledger) -> str:
    return "".join(
        p.read_text(encoding="utf-8")
        for p in sorted(ledger.journal_dir.rglob("*.beancount"))
    ) + ledger.main_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("account", "direction", "amount"),
    [(BANK, "have", "0"), (CARD, "owe", "0.00"), (ASHA, "owe", "0.00")],
)
def test_add_zero_opening_is_recorded(tmp_path, account, direction, amount):
    """Q25: a zero opening (a card paid in full prints 0.00) is a fact to
    record: both legs at 0.00, never -0.00, with the paired assertion at 0."""
    ledger = _ledger(tmp_path)
    count = _snapshots(ledger)

    resp = _service(tmp_path, ledger).add(
        _add(account=account, amount=amount, direction=direction, balance_check=True)
    )

    assert resp.ledger_amount == "0.00"
    assert _snapshots(ledger) == count + 1
    txn = _opening_of(ledger, account)
    assert _amounts(txn) == {account: Decimal("0.00"), EQUITY_OPENING: Decimal("0.00")}
    assert all(not p.units.number.is_signed() for p in txn.postings)
    [balance] = _balances(ledger, account)
    assert balance.amount.number == Decimal("0.00")
    assert not balance.amount.number.is_signed()
    assert "-0.00" not in _journal_text(ledger)
    assert ledger.validate() == []
    status = _status(ledger, account)
    assert status.status == "opening"
    assert status.opening is not None and status.opening.ledger_amount == "0.00"


def test_add_zero_from_a_batch_keeps_its_statement_source(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = _bank_batch(
        review,
        printed=_PRINTED.model_copy(update={"raw_text": "0.00", "value": Decimal("0")}),
    )

    resp = _service(tmp_path, ledger, review).add(
        _add(amount="0.00", batch_id=batch_id)
    )

    assert resp.source == "statement"
    txn = _opening_of(ledger, BANK)
    assert txn.meta[OPENING_SOURCE_KEY] == "statement"
    assert txn.meta[OPENING_BASIS_KEY] == (
        f"Opening Balance printed 0.00; recorded as 0.00 INR on {BANK}"
    )
    assert ledger.validate() == []


def test_correct_to_zero(tmp_path):
    ledger = _ledger(tmp_path)

    resp = _service(tmp_path, ledger).correct(_fix(amount="0.00", balance_check=True))

    assert resp.ledger_amount == "0.00"
    txn = _opening_of(ledger, OPENED)
    assert _amounts(txn) == {OPENED: Decimal("0.00"), EQUITY_OPENING: Decimal("0.00")}
    [balance] = _balances(ledger, OPENED)
    assert balance.amount.number == Decimal("0.00")
    assert "-0.00" not in _journal_text(ledger)
    assert ledger.validate() == []


# ── Q29: opening-start and the gap warning ───────────────────────────────────
def _mid_april_batch(review: ReviewStore) -> str:
    """A bank CSV export whose rows start on 20 Apr 2025: the April rule
    still dates its opening 31 Mar."""
    batch_id = review.add_batch(
        [_candidate(BANK, date(2025, 4, 20)), _candidate(BANK, date(2025, 4, 26))]
    )
    review.set_opening_evidence(
        batch_id,
        OpeningEvidence(
            account=BANK, printed=_PRINTED, first_row=None, printed_error=None
        ),
    )
    return batch_id


def _spend(account: str, when: date, amount: str = "40.00") -> data.Transaction:
    return data.Transaction(
        data.new_metadata("<test>", 0, {"txn-id": f"t-gap-{when.isoformat()}"}),
        when,
        "*",
        None,
        "Groceries",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(
                account, -Amount(Decimal(amount), "INR"), None, None, None, None
            ),
            data.Posting(FOOD, Amount(Decimal(amount), "INR"), None, None, None, None),
        ],
    )


def test_statement_opening_carries_opening_start(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = _mid_april_batch(review)

    resp = _service(tmp_path, ledger, review).add(_add(batch_id=batch_id))

    assert (resp.source, resp.date) == ("statement", date(2025, 3, 31))
    assert _opening_of(ledger, BANK).meta[OPENING_START_KEY] == "2025-04-20"
    assert _status(ledger, BANK).warn is False


def test_manual_opening_has_no_opening_start(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = _mid_april_batch(review)

    _service(tmp_path, ledger, review).add(_add(batch_id=batch_id, amount="9999.00"))

    assert OPENING_START_KEY not in _opening_of(ledger, BANK).meta


def test_earlier_rows_imported_after_an_april_rule_opening_warn(tmp_path):
    """20 Apr export -> opening 31 Mar (opening-start 2025-04-20); rows for
    1-19 Apr imported afterwards fall between the two and are not covered by
    the printed figure: the row warns and is counted like a late opening."""
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    svc = _service(tmp_path, ledger, review)
    svc.add(_add(batch_id=_mid_april_batch(review)))
    # The export's own rows (from 20 Apr) never warn.
    ledger.append([_spend(BANK, date(2025, 4, 20)), _spend(BANK, date(2025, 4, 26))])
    assert _status(ledger, BANK).warn is False

    ledger.append([_spend(BANK, date(2025, 4, 1)), _spend(BANK, date(2025, 4, 19))])

    row = _status(ledger, BANK)
    assert (row.status, row.warn) == ("opening", True)
    status = svc.statuses()
    assert BANK in {r.account for r in status.accounts if r.warn}
    assert status.missing_count == sum(1 for r in status.accounts if r.warn)
    block = read.doctor_block(ledger.load(fresh=True).entries, today=TODAY, names={})
    assert BANK in {m["account"] for m in block["missing"]}


def test_the_same_rows_without_opening_start_do_not_warn(tmp_path):
    """A manual or legacy opening (no opening-start) keeps the old rule."""
    ledger = _ledger(tmp_path)
    svc = _service(tmp_path, ledger)
    svc.add(_add())  # no batch: manual
    ledger.append([_spend(BANK, date(2025, 4, 1)), _spend(BANK, date(2025, 4, 19))])

    assert _status(ledger, BANK).warn is False


def test_a_posting_on_the_opening_date_is_a_gap(tmp_path):
    """The figure is the balance as of opening-start (20 Apr), not as of the
    opening's date (31 Mar): a posting dated 31 Mar is not in it either, so
    it is counted twice unless flagged."""
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    _service(tmp_path, ledger, review).add(_add(batch_id=_mid_april_batch(review)))
    ledger.append([_spend(BANK, date(2025, 3, 31))])
    assert _status(ledger, BANK).warn is True


def test_a_same_day_posting_without_opening_start_does_not_warn(tmp_path):
    """Without opening-start (manual, legacy) a same-day posting is not late
    and there is no gap: behaviour is unchanged."""
    ledger = _ledger(tmp_path)
    _service(tmp_path, ledger).add(_add())  # no batch: manual
    ledger.append([_spend(BANK, date(2025, 3, 31))])
    assert _status(ledger, BANK).warn is False


def test_correcting_a_gapped_opening_drops_opening_start(tmp_path):
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    svc = _service(tmp_path, ledger, review)
    svc.add(_add(batch_id=_mid_april_batch(review)))
    ledger.append([_spend(BANK, date(2025, 4, 5))])
    row = _status(ledger, BANK)
    assert row.warn is True and row.opening is not None
    # The gap row's suggested date is the opening's own (already valid).
    assert row.suggested_date == row.opening.date

    svc.correct(
        _fix(
            account=BANK,
            amount="10040.00",
            date=row.suggested_date,
            expected=OpeningExpected(
                date=row.opening.date, ledger_amount=row.opening.ledger_amount
            ),
        )
    )

    assert OPENING_START_KEY not in _opening_of(ledger, BANK).meta
    assert _status(ledger, BANK).warn is False


def test_card_row_before_its_period_is_not_a_gap(tmp_path):
    """Q26: a card statement row dated before the period moves the opening
    to the day before that row; opening-start is that row's date, so the
    statement's own row never reads as a gap."""
    ledger = _ledger(tmp_path)
    review = _review(ledger)
    batch_id = review.add_batch([_candidate(CARD, date(2025, 3, 14))])
    review.set_statement(
        batch_id,
        StatementSummary(
            account=CARD,
            period_start=date(2025, 3, 16),
            period_end=date(2025, 4, 15),
            previous_balance=Decimal("4210.37"),
            payments_credits=Decimal("0"),
            purchases_debits=Decimal("100.00"),
            total_due=Decimal("4310.37"),
        ),
    )
    _service(tmp_path, ledger, review).add(
        _add(
            account=CARD,
            amount="4210.37",
            direction="owe",
            date=date(2025, 3, 13),
            batch_id=batch_id,
        )
    )
    assert _opening_of(ledger, CARD).meta[OPENING_START_KEY] == "2025-03-14"
    ledger.append([_spend(CARD, date(2025, 3, 14))])
    assert _status(ledger, CARD).warn is False
