"""The first-import opening prompt over synthetic tmp_path journals and
synthetic review batches. Figures are kept distinct wherever the code could
read the wrong source: printed vs derived, period_start vs the earliest row,
one account's evidence vs another's."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.app.core.errors.errors import CodedError
from backend.app.models.api.openings import OpeningCreateRequest
from backend.app.models.domain.accounts import EQUITY_OPENING
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.models.storage.opening_evidence import (
    FirstRowOpening,
    OpeningEvidence,
    PrintedOpening,
)
from backend.app.models.storage.statement_record import StatementSummary
from backend.app.service.ingest.pipeline.opening_evidence import NON_FINITE_BALANCE
from backend.app.persistence.journal import Ledger
from backend.app.persistence.review_batches import ReviewStore
from backend.app.persistence.templates import PdfTemplateStore
from backend.app.service.ledger.account_meta import AccountMetaService
from backend.app.service.ledger.openings.keys import OPENING_OPEN_KEY, OPENING_ZERO
from backend.app.service.ledger.openings.service import OpeningService
from backend.app.service.ledger.openings.suggest import pdf_suggestion

TODAY = date(2026, 6, 15)

BANK = "Assets:Checking:Acme"  # empty
BANK2 = "Assets:Checking:Beta"  # empty
CARD = "Liabilities:CreditCard:Zeta"  # empty
SPENT = "Assets:Checking:Busy"  # missing: postings from 2025-05-10, no opening
OPENED = "Assets:Checking:Opened"  # opening 2025-03-31, first posting 2025-04-03
LATE = "Assets:Checking:Late"  # first posting 2025-04-02, opening 2025-12-01
ZEROED = "Assets:Checking:Zeroed"  # started-at-zero mark, no postings
ASHA = "Liabilities:People:Asha"
FOOD = "Expenses:Food"

_ACCOUNTS = (BANK, BANK2, CARD, SPENT, OPENED, LATE, ASHA, FOOD)

_JOURNAL = f"""\
2025-03-31 * "Opening balance"
  {OPENED}                5000.00 INR
  {EQUITY_OPENING}       -5000.00 INR

2025-04-03 * "Groceries"
  {OPENED}                -120.00 INR
  {FOOD}                   120.00 INR

2025-05-10 * "Groceries"
  {SPENT}                  -80.00 INR
  {FOOD}                    80.00 INR

2025-04-02 * "Groceries"
  {LATE}                   -40.00 INR
  {FOOD}                    40.00 INR

2025-12-01 * "Liability Reset"
  {LATE}                   700.00 INR
  {EQUITY_OPENING}        -700.00 INR
"""


def _ledger(tmp_path, extra: str = "") -> Ledger:
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap(seed_accounts=())
    opens = "".join(f"2020-01-01 open {a} INR\n" for a in _ACCOUNTS)
    ledger.main_file.write_text(
        'option "operating_currency" "INR"\n\n'
        "2000-01-01 commodity INR\n"
        f"2020-01-01 open {EQUITY_OPENING} INR\n"
        f"{opens}"
        f"2020-01-01 open {ZEROED} INR\n"
        f'  {OPENING_OPEN_KEY}: "{OPENING_ZERO}"\n'
        'include "2025.beancount"\n',
        encoding="utf-8",
    )
    (ledger.journal_dir / "2025.beancount").write_text(
        _JOURNAL + extra, encoding="utf-8"
    )
    assert ledger.validate() == []
    return ledger


def _service(tmp_path, ledger: Ledger, review: ReviewStore) -> OpeningService:
    return OpeningService(
        ledger=ledger,
        investment_ledger=ledger,
        review_store=review,
        account_meta=AccountMetaService(
            ledger, PdfTemplateStore(tmp_path / "templates" / "pdf")
        ),
        scheme=lambda _symbol: None,
        clock=lambda: TODAY,
    )


def _setup(tmp_path) -> tuple[Ledger, ReviewStore, OpeningService]:
    ledger = _ledger(tmp_path)
    review = ReviewStore(lock=ledger.write_lock())
    return ledger, review, _service(tmp_path, ledger, review)


def _candidate(
    account: str, when: date, *, needs_account: bool = False
) -> CandidateTxn:
    return CandidateTxn(
        date=when,
        amount=Decimal("100.00"),
        raw_narration="ROW",
        normalized_narration="row",
        source_account=account,
        direction=Direction.DEBIT,
        needs_account=needs_account,
    )


def _printed(raw: str, value: str, suffix=None) -> PrintedOpening:
    return PrintedOpening(
        label_text="Opening Balance",
        raw_text=raw,
        value=Decimal(value),
        suffix=suffix,
        row=2,
        column=3,
    )


def _evidence(
    account: str | None,
    *,
    printed: PrintedOpening | None = None,
    first_row: FirstRowOpening | None = None,
    printed_error: str | None = None,
) -> OpeningEvidence:
    return OpeningEvidence(
        account=account,
        printed=printed,
        first_row=first_row,
        printed_error=printed_error,
    )


def _batch(
    review: ReviewStore,
    rows: list[CandidateTxn],
    *,
    evidence: OpeningEvidence | None = None,
    statement: StatementSummary | None = None,
) -> str:
    batch_id = review.add_batch(rows)
    if evidence is not None:
        review.set_opening_evidence(batch_id, evidence)
    if statement is not None:
        review.set_statement(batch_id, statement)
    return batch_id


def _bank_rows(first: date) -> list[CandidateTxn]:
    """BANK rows out of order, earliest `first`; plus an earlier row of
    another (missing) account and an earlier needs_account row, neither of
    which may set BANK's statement_start."""
    return [
        _candidate(BANK, date(2025, 6, 20)),
        _candidate(BANK, first),
        _candidate(BANK, date(2025, 6, 9)),
        _candidate(SPENT, date(2025, 3, 1)),
        _candidate(BANK, date(2025, 3, 2), needs_account=True),
    ]


def _summary(
    previous: str,
    *,
    account: str = CARD,
    start: date = date(2025, 3, 16),
    end: date = date(2025, 4, 15),
) -> StatementSummary:
    return StatementSummary(
        account=account,
        period_start=start,
        period_end=end,
        previous_balance=Decimal(previous),
        payments_credits=Decimal("0"),
        purchases_debits=Decimal("0"),
        total_due=Decimal(previous),
    )


def _hashes(ledger: Ledger) -> dict[str, str]:
    return {
        p.relative_to(ledger.journal_dir).as_posix(): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(ledger.journal_dir.rglob("*"))
        if p.is_file()
    }


# ── CSV/XLS ─────────────────────────────────────────────────────────────────
def test_prompt_csv_printed_april(tmp_path):
    ledger, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        _bank_rows(date(2025, 4, 3)),
        evidence=_evidence(BANK, printed=_printed("10,000.00", "10000.00")),
    )

    out = svc.prompt(batch_id)

    assert out.batch_id == batch_id
    (item,) = out.prompts
    assert item.account == BANK
    assert item.kind == "bank"
    assert item.statement_start == date(2025, 4, 3)
    assert item.suggested_date == date(2025, 3, 31)
    assert item.april is True
    assert item.april_advice is None
    assert item.directions == ["have", "owe"]
    s = item.suggestion
    assert s is not None
    assert s.source == "statement"
    assert (s.amount, s.direction, s.ledger_amount) == ("10000.00", "have", "10000.00")
    assert s.label == "Opening Balance"
    assert s.arithmetic == (
        f"'Opening Balance' printed 10,000.00; recorded as 10000.00 INR on {BANK}"
    )
    assert item.alternatives == []
    assert item.mismatch is False
    assert item.note is None
    assert out.missing == [SPENT]


def test_prompt_csv_derived_not_april(tmp_path):
    _, review, svc = _setup(tmp_path)
    first_row = FirstRowOpening(
        date=date(2025, 5, 10),
        balance=Decimal("10000.00"),
        signed_amount=Decimal("500.00"),
        derived=Decimal("9500.00"),
    )
    batch_id = _batch(
        review,
        _bank_rows(date(2025, 5, 10)),
        evidence=_evidence(BANK, first_row=first_row),
    )

    (item,) = svc.prompt(batch_id).prompts

    assert item.statement_start == date(2025, 5, 10)
    assert item.suggested_date == date(2025, 5, 9)
    assert item.april is False
    assert item.april_advice == (
        "This statement starts on 10 May 2025. For a full FY 2025-26, import the"
        " statement that starts in April 2025 first; otherwise the opening is"
        " dated 09 May 2025."
    )
    s = item.suggestion
    assert s is not None
    assert s.source == "derived"
    assert (s.amount, s.direction, s.ledger_amount) == ("9500.00", "have", "9500.00")
    assert s.label is None
    assert s.arithmetic == (
        "first row 2025-05-10: balance 10000.00 minus credit 500.00 = 9500.00"
    )
    assert item.alternatives == []
    assert item.mismatch is False


def test_prompt_csv_derived_from_a_debit_first_row(tmp_path):
    _, review, svc = _setup(tmp_path)
    first_row = FirstRowOpening(
        date=date(2025, 5, 10),
        balance=Decimal("-200.00"),
        signed_amount=Decimal("-300.00"),
        derived=Decimal("100.00"),
    )
    batch_id = _batch(
        review,
        _bank_rows(date(2025, 5, 10)),
        evidence=_evidence(BANK, first_row=first_row),
    )

    (item,) = svc.prompt(batch_id).prompts

    s = item.suggestion
    assert s is not None
    assert s.arithmetic == (
        "first row 2025-05-10: balance -200.00 plus debit 300.00 = 100.00"
    )
    assert (s.ledger_amount, s.direction) == ("100.00", "have")


def test_prompt_csv_overdrawn_bank_is_owe(tmp_path):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        _bank_rows(date(2025, 4, 3)),
        evidence=_evidence(BANK, printed=_printed("1,250.00 Dr", "-1250.00", "Dr")),
    )

    (item,) = svc.prompt(batch_id).prompts

    s = item.suggestion
    assert s is not None
    assert (s.amount, s.direction, s.ledger_amount) == ("1250.00", "owe", "-1250.00")


@pytest.mark.parametrize(
    "first_row",
    [
        None,
        # A balance column on a card never derives: derivation is bank only.
        FirstRowOpening(
            date=date(2025, 4, 3),
            balance=Decimal("700.00"),
            signed_amount=Decimal("-100.00"),
            derived=Decimal("800.00"),
        ),
    ],
)
def test_prompt_card_csv_no_balance_asks(tmp_path, first_row):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        [_candidate(CARD, date(2025, 4, 3))],
        evidence=_evidence(CARD, first_row=first_row),
    )

    (item,) = svc.prompt(batch_id).prompts

    assert item.account == CARD
    assert item.kind == "card"
    assert item.suggestion is None
    assert item.alternatives == []
    assert item.mismatch is False
    assert item.directions == ["owe", "owed_to_me"]


@pytest.mark.parametrize(
    ("printed", "amount", "direction", "ledger"),
    [
        (_printed("5,000.00", "5000.00"), "5000.00", "owe", "-5000.00"),
        (_printed("5,000.00 Cr", "5000.00", "Cr"), "5000.00", "owed_to_me", "5000.00"),
        (_printed("5,000.00 Dr", "-5000.00", "Dr"), "5000.00", "owe", "-5000.00"),
    ],
)
def test_prompt_card_csv_printed_sign(tmp_path, printed, amount, direction, ledger):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        [_candidate(CARD, date(2025, 4, 3))],
        evidence=_evidence(CARD, printed=printed),
    )

    (item,) = svc.prompt(batch_id).prompts

    s = item.suggestion
    assert s is not None
    assert (s.amount, s.direction, s.ledger_amount) == (amount, direction, ledger)
    assert s.arithmetic.endswith(f"; recorded as {ledger} INR on {CARD}")


def test_prompt_printed_and_derived_mismatch(tmp_path):
    _, review, svc = _setup(tmp_path)
    first_row = FirstRowOpening(
        date=date(2025, 4, 3),
        balance=Decimal("9300.00"),
        signed_amount=Decimal("-500.00"),
        derived=Decimal("9800.00"),
    )
    batch_id = _batch(
        review,
        _bank_rows(date(2025, 4, 3)),
        evidence=_evidence(
            BANK, printed=_printed("10,000.00", "10000.00"), first_row=first_row
        ),
    )

    (item,) = svc.prompt(batch_id).prompts

    s = item.suggestion
    assert s is not None
    assert (s.source, s.ledger_amount) == ("statement", "10000.00")
    (alt,) = item.alternatives
    assert (alt.source, alt.ledger_amount, alt.label) == ("derived", "9800.00", None)
    assert alt.arithmetic == (
        "first row 2025-04-03: balance 9300.00 plus debit 500.00 = 9800.00"
    )
    assert item.mismatch is True


def test_prompt_printed_and_derived_agree_is_no_mismatch(tmp_path):
    _, review, svc = _setup(tmp_path)
    first_row = FirstRowOpening(
        date=date(2025, 4, 3),
        balance=Decimal("9500.00"),
        signed_amount=Decimal("-500.00"),
        derived=Decimal("10000.00"),
    )
    batch_id = _batch(
        review,
        _bank_rows(date(2025, 4, 3)),
        evidence=_evidence(
            BANK, printed=_printed("10,000.00", "10000.00"), first_row=first_row
        ),
    )

    (item,) = svc.prompt(batch_id).prompts

    assert item.suggestion is not None
    assert item.suggestion.source == "statement"
    assert [a.source for a in item.alternatives] == ["derived"]
    assert item.mismatch is False


def test_prompt_note_is_the_printed_error(tmp_path):
    _, review, svc = _setup(tmp_path)
    error = "opening_balance label matched no cell outside the data rows 4..9"
    batch_id = _batch(
        review,
        _bank_rows(date(2025, 4, 3)),
        evidence=_evidence(BANK, printed_error=error),
    )

    (item,) = svc.prompt(batch_id).prompts

    assert item.note == error
    assert item.suggestion is None


# ── PDF ─────────────────────────────────────────────────────────────────────
def test_prompt_pdf_card_previous_balance(tmp_path):
    _, review, svc = _setup(tmp_path)
    # The earliest row (20 Mar) is after period_start (16 Mar), so the
    # start's source shows.
    batch_id = _batch(
        review,
        [_candidate(CARD, date(2025, 4, 1)), _candidate(CARD, date(2025, 3, 20))],
        statement=_summary("4210.37"),
    )

    out = svc.prompt(batch_id)

    (item,) = out.prompts
    assert item.account == CARD
    assert item.statement_start == date(2025, 3, 16)
    assert item.suggested_date == date(2025, 3, 15)
    # The 16 Mar - 15 Apr cycle contains 1 April (Q24).
    assert item.april is True
    assert item.april_advice is None
    s = item.suggestion
    assert s is not None
    assert s.source == "statement"
    assert (s.amount, s.direction, s.ledger_amount) == ("4210.37", "owe", "-4210.37")
    assert s.label == "previous balance"
    assert s.arithmetic == (
        "previous balance 4210.37 on the statement for 2025-03-16 to 2025-04-15;"
        f" recorded as -4210.37 INR on {CARD}"
    )
    assert item.alternatives == []
    assert item.mismatch is False
    assert item.note is None
    assert out.missing == []


def test_prompt_pdf_card_credit_previous_balance_is_owed_to_me(tmp_path):
    # A "Cr" previous balance parses negative (the card owes you).
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review, [_candidate(CARD, date(2025, 3, 20))], statement=_summary("-500.00")
    )

    (item,) = svc.prompt(batch_id).prompts

    s = item.suggestion
    assert s is not None
    assert (s.amount, s.direction, s.ledger_amount) == (
        "500.00",
        "owed_to_me",
        "500.00",
    )


def test_prompt_pdf_zero_previous_balance_has_no_sign(tmp_path):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review, [_candidate(CARD, date(2025, 3, 20))], statement=_summary("0.00")
    )

    (item,) = svc.prompt(batch_id).prompts

    s = item.suggestion
    assert s is not None
    assert (s.amount, s.direction, s.ledger_amount) == ("0.00", "owe", "0.00")
    assert s.arithmetic.endswith(f"; recorded as 0.00 INR on {CARD}")


def test_prompt_printed_minus_zero_has_no_sign(tmp_path):
    # parse_balance("-0.00") keeps the minus: Decimal("-0.00").
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        _bank_rows(date(2025, 4, 3)),
        evidence=_evidence(BANK, printed=_printed("-0.00", "-0.00")),
    )

    (item,) = svc.prompt(batch_id).prompts

    s = item.suggestion
    assert s is not None
    assert (s.amount, s.direction, s.ledger_amount) == ("0.00", "have", "0.00")
    assert s.arithmetic == (
        f"'Opening Balance' printed -0.00; recorded as 0.00 INR on {BANK}"
    )


def test_zero_reads_as_the_kind_default_direction():
    # Q22. A person account's default is owed_to_me, where the liability
    # sign rule alone would read zero as owe. No statement template imports
    # a person account, so this is checked on the suggestion builder.
    s = pdf_suggestion(ASHA, _summary("0.00", account=ASHA))
    assert (s.amount, s.direction, s.ledger_amount) == ("0.00", "owed_to_me", "0.00")


@pytest.mark.parametrize(
    ("start", "suggested", "april", "advised"),
    [
        # A card is dated the day before its cycle starts (Q23); April
        # applies to the cycle containing 1 April (Q24).
        (date(2025, 4, 16), date(2025, 4, 15), False, True),
        (date(2025, 4, 10), date(2025, 4, 9), False, True),
        (date(2025, 5, 10), date(2025, 5, 9), False, True),
        (date(2025, 4, 1), date(2025, 3, 31), True, False),
        (date(2025, 3, 2), date(2025, 3, 1), True, False),
    ],
)
def test_prompt_pdf_card_is_dated_the_day_before_its_cycle(
    tmp_path, start, suggested, april, advised
):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        [_candidate(CARD, start + timedelta(days=4))],
        statement=_summary("100.00", start=start, end=start + timedelta(days=29)),
    )

    (item,) = svc.prompt(batch_id).prompts

    assert item.statement_start == start
    assert (item.suggested_date, item.april) == (suggested, april)
    if advised:
        assert item.april_advice == (
            f"This card statement's cycle starts {start:%d %b %Y}. For FY "
            "2025-26, import the earlier statement whose cycle covers 1 April "
            "2025 first, so the opening is as of the start of the year."
        )
    else:
        assert item.april_advice is None


def test_prompt_bank_april_start_is_31_march(tmp_path):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(review, _bank_rows(date(2025, 4, 16)))

    (item,) = svc.prompt(batch_id).prompts

    assert (item.suggested_date, item.april, item.april_advice) == (
        date(2025, 3, 31),
        True,
        None,
    )


def test_prompt_pdf_with_no_rows_still_prompts_its_card(tmp_path):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(review, [], statement=_summary("4210.37"))

    out = svc.prompt(batch_id)

    (item,) = out.prompts
    assert item.account == CARD
    assert item.statement_start == date(2025, 3, 16)
    assert item.suggested_date == date(2025, 3, 15)
    assert item.suggestion is not None
    assert item.suggestion.ledger_amount == "-4210.37"


# ── which accounts ──────────────────────────────────────────────────────────
def test_prompt_skips_accounts_with_records(tmp_path):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        [
            _candidate(OPENED, date(2025, 6, 1)),
            _candidate(SPENT, date(2025, 6, 1)),
            _candidate(ZEROED, date(2025, 6, 1)),
            _candidate(BANK, date(2025, 6, 1)),
        ],
    )

    out = svc.prompt(batch_id)

    assert [i.account for i in out.prompts] == [BANK]
    assert out.missing == [SPENT]


def test_prompt_late_opening_is_missing_not_prompted(tmp_path):
    # Q21: a late opening warns, so it is listed in `missing`.
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(review, [_candidate(LATE, date(2025, 6, 1))])

    out = svc.prompt(batch_id)

    assert out.prompts == []
    assert out.missing == [LATE]


def test_prompt_multi_account_batch_no_evidence(tmp_path):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        [
            _candidate(BANK2, date(2025, 5, 20)),
            _candidate(BANK, date(2025, 4, 3)),
            _candidate(BANK2, date(2025, 5, 12)),
        ],
    )

    out = svc.prompt(batch_id)

    by_account = {i.account: i for i in out.prompts}
    assert set(by_account) == {BANK, BANK2}
    assert all(i.suggestion is None and i.alternatives == [] for i in out.prompts)
    assert all(i.note is None for i in out.prompts)
    assert by_account[BANK].statement_start == date(2025, 4, 3)
    assert by_account[BANK2].statement_start == date(2025, 5, 12)


def test_prompt_multi_account_evidence_without_an_account_suggests_nothing(tmp_path):
    # Staging records account None for a batch spanning several accounts.
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        [_candidate(BANK, date(2025, 4, 3)), _candidate(BANK2, date(2025, 5, 12))],
        evidence=_evidence(
            None, printed=_printed("10,000.00", "10000.00"), printed_error="x"
        ),
    )

    out = svc.prompt(batch_id)

    assert len(out.prompts) == 2
    assert all(i.suggestion is None and i.note is None for i in out.prompts)


def test_prompt_evidence_applies_only_to_its_account(tmp_path):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        [_candidate(BANK, date(2025, 4, 3)), _candidate(BANK2, date(2025, 5, 12))],
        evidence=_evidence(
            BANK2,
            printed=_printed("7,000.00", "7000.00"),
            printed_error="row 3 column 4 is not a balance",
        ),
    )

    by_account = {i.account: i for i in svc.prompt(batch_id).prompts}

    assert by_account[BANK].suggestion is None
    assert by_account[BANK].note is None
    other = by_account[BANK2].suggestion
    assert other is not None
    assert other.ledger_amount == "7000.00"
    assert by_account[BANK2].note == "row 3 column 4 is not a balance"


def test_prompt_pdf_statement_applies_only_to_its_account(tmp_path):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        [_candidate(BANK, date(2025, 4, 3)), _candidate(CARD, date(2025, 3, 20))],
        statement=_summary("4210.37"),
    )

    by_account = {i.account: i for i in svc.prompt(batch_id).prompts}

    assert by_account[BANK].suggestion is None
    assert by_account[BANK].statement_start == date(2025, 4, 3)
    assert by_account[CARD].suggestion is not None
    assert by_account[CARD].statement_start == date(2025, 3, 16)


def test_prompt_needs_account_rows_ignored(tmp_path):
    _, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        [
            _candidate(BANK, date(2025, 5, 12)),
            # CARD is empty, but its only row is waiting for an account.
            _candidate(CARD, date(2025, 4, 1), needs_account=True),
            # An earlier BANK row waiting for an account never sets its start.
            _candidate(BANK, date(2025, 4, 1), needs_account=True),
            # SPENT is missing, but only through a row waiting for an account.
            _candidate(SPENT, date(2025, 4, 1), needs_account=True),
        ],
    )

    out = svc.prompt(batch_id)

    (item,) = out.prompts
    assert item.account == BANK
    assert item.statement_start == date(2025, 5, 12)
    assert out.missing == []


def test_prompt_unknown_batch_is_e1006(tmp_path):
    _, _, svc = _setup(tmp_path)
    with pytest.raises(CodedError) as err:
        svc.prompt("no-such-batch")
    assert err.value.code == "E1006"


def test_prompt_reads_only(tmp_path):
    ledger, review, svc = _setup(tmp_path)
    batch_id = _batch(
        review,
        _bank_rows(date(2025, 4, 3)),
        evidence=_evidence(BANK, printed=_printed("10,000.00", "10000.00")),
    )
    before = _hashes(ledger)

    svc.prompt(batch_id)

    assert _hashes(ledger) == before
    assert not ledger.snapshots_dir.exists() or not any(ledger.snapshots_dir.iterdir())


# ── end to end: a generated card PDF staged by the agent, read by the route ──
def test_prompt_route_reads_a_staged_card_pdf(tmp_path):
    from backend.app.llm.flows.prove_template.service import AgentIngestService
    from backend.app.server.main import create_app
    from tests.llm.flows.prove_template.test_service_pdf import (
        CARD as PDF_CARD,
    )
    from tests.llm.flows.prove_template.test_service_pdf import ROWS, _proven
    from tests.llm.flows.prove_template.test_service_pdf import TODAY as PDF_TODAY

    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: PDF_TODAY)
    client = TestClient(app)
    agent = AgentIngestService(
        import_service=app.state.import_service,
        template_store=app.state.template_store,
        pdf_template_store=app.state.pdf_template_store,
        review_store=app.state.review_store,
        ledger=app.state.ledger,
        clock=lambda: PDF_TODAY,
    )
    # Period 21 Sep - 20 Oct 2026; the rows start 23 Sep, so statement_start
    # can only come from period_start. 4,975.56 - 0 + 5,345.00 = 10,320.56,
    # printed in whole rupees as this layout does.
    summary = {
        "previous_balance": "C 4,975.56",
        "payments_credits": "C 0.00",
        "purchases_debits": "C 5,345.00",
        "total_due": "C 10,320.00",
        "transactions_total": "C 5,345.00",
    }
    run, _ = _proven(agent, tmp_path, domestic=ROWS[2:], summary=summary)
    staged = agent.stage(run["prove_id"], "proof")
    assert min(c["date"] for c in staged["candidates"]) > "2026-09-21"

    resp = client.get(f"/api/review/{staged['batch_id']}/opening")

    assert resp.status_code == 200
    body = resp.json()
    (item,) = body["prompts"]
    assert item["account"] == PDF_CARD
    assert item["statement_start"] == "2026-09-21"
    assert item["suggested_date"] == "2026-09-20"
    assert item["april"] is False
    assert item["suggestion"] == {
        "source": "statement",
        "amount": "4975.56",
        "direction": "owe",
        "ledger_amount": "-4975.56",
        "arithmetic": (
            "previous balance 4975.56 on the statement for 2026-09-21 to"
            f" 2026-10-20; recorded as -4975.56 INR on {PDF_CARD}"
        ),
        "label": "previous balance",
    }
    assert body["missing"] == []


@pytest.mark.parametrize("figure", ["Infinity", "-Infinity", "inf", "sNaN", "-NaN"])
def test_prompt_route_survives_a_non_finite_printed_figure(tmp_path, figure):
    """A saved template's opening cell printing a non-finite "number" is
    refused at capture as printed_error: the rest of the evidence (the
    running-balance derivation) is kept, and the route answers 200 with the
    reason as its note rather than failing on the figure."""
    from backend.app.server.main import create_app
    from tests.llm.flows.prove_template.test_stage_opening_evidence import (
        ACCOUNT,
        FINGERPRINT,
        FIELDS,
        STATEMENT,
    )
    from tests.llm.flows.prove_template.test_stage_opening_evidence import (
        TODAY as CSV_TODAY,
    )

    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: CSV_TODAY)
    client = TestClient(app)
    app.state.template_store.save(FIELDS.to_template(FINGERPRINT, origin="agent"))
    statement = STATEMENT.replace(
        b"Opening Balance,,,10000.00", f"Opening Balance,,,{figure}".encode()
    )
    out = app.state.import_service.detect("s.csv", statement)
    assert out["matched"] is True
    ev = app.state.review_store.opening_evidence(out["batch_id"])
    assert ev is not None and ev.printed is None and ev.printed_error

    resp = client.get(f"/api/review/{out['batch_id']}/opening")

    assert resp.status_code == 200
    (item,) = resp.json()["prompts"]
    assert item["account"] == ACCOUNT
    assert item["note"] == ev.printed_error
    assert figure not in item["note"]
    # The running balance still derives the opening.
    assert item["suggestion"]["source"] == "derived"
    assert item["suggestion"]["ledger_amount"] == "10000.00"


# ── the card row before its period (Q26) ─────────────────────────────────────
def test_prompt_card_row_before_the_period_dates_the_opening_before_it(tmp_path):
    _, review, svc = _setup(tmp_path)
    # Period 16 Mar - 15 Apr; one row printed 14 Mar (the period check only
    # warns), another inside the period.
    batch_id = _batch(
        review,
        [_candidate(CARD, date(2025, 3, 20)), _candidate(CARD, date(2025, 3, 14))],
        statement=_summary("4210.37"),
    )

    (item,) = svc.prompt(batch_id).prompts

    assert item.statement_start == date(2025, 3, 16)
    assert item.suggested_date == date(2025, 3, 13)
    # The cycle still contains 1 April: april and the advice follow it.
    assert (item.april, item.april_advice) == (True, None)


# ── round trip: the prompt's figure, accepted as shown, is recorded ─────────
def _pdf_case(previous: str, rows: list[date], **period):
    def build(review: ReviewStore) -> str:
        return _batch(
            review,
            [_candidate(CARD, d) for d in rows],
            statement=_summary(previous, **period),
        )

    return CARD, build


def _csv_case(account: str, rows: list[CandidateTxn], **evidence):
    def build(review: ReviewStore) -> str:
        return _batch(review, rows, evidence=_evidence(account, **evidence))

    return account, build


_DERIVED = FirstRowOpening(
    date=date(2025, 4, 3),
    balance=Decimal("9300.00"),
    signed_amount=Decimal("-500.00"),
    derived=Decimal("9800.00"),
)

_ROUND_TRIPS = {
    "pdf card previous balance": (
        *_pdf_case("4210.37", [date(2025, 3, 20)]),
        False,
        "statement",
        "-4210.37",
    ),
    "pdf card Cr previous balance": (
        *_pdf_case("-500.00", [date(2025, 3, 20)]),
        False,
        "statement",
        "500.00",
    ),
    "pdf card 0.00": (
        *_pdf_case("0.00", [date(2025, 3, 20)]),
        False,
        "statement",
        "0.00",
    ),
    "pdf card row before the period": (
        *_pdf_case("4210.37", [date(2025, 3, 20), date(2025, 3, 14)]),
        False,
        "statement",
        "-4210.37",
    ),
    "pdf card cycle starting mid-April": (
        *_pdf_case(
            "1200.00",
            [date(2025, 4, 20)],
            start=date(2025, 4, 16),
            end=date(2025, 5, 15),
        ),
        False,
        "statement",
        "-1200.00",
    ),
    "csv printed bank": (
        *_csv_case(
            BANK,
            _bank_rows(date(2025, 4, 3)),
            printed=_printed("10,000.00", "10000.00"),
        ),
        False,
        "statement",
        "10000.00",
    ),
    "csv printed card": (
        *_csv_case(
            CARD,
            [_candidate(CARD, date(2025, 4, 3))],
            printed=_printed("5,000.00", "5000.00"),
        ),
        False,
        "statement",
        "-5000.00",
    ),
    "csv derived bank": (
        *_csv_case(BANK, _bank_rows(date(2025, 4, 3)), first_row=_DERIVED),
        False,
        "derived",
        "9800.00",
    ),
    "csv derived bank picked from the alternatives": (
        *_csv_case(
            BANK,
            _bank_rows(date(2025, 4, 3)),
            printed=_printed("10,000.00", "10000.00"),
            first_row=_DERIVED,
        ),
        True,
        "derived",
        "9800.00",
    ),
    "csv printed 0.00": (
        *_csv_case(
            BANK, _bank_rows(date(2025, 4, 3)), printed=_printed("0.00", "0.00")
        ),
        False,
        "statement",
        "0.00",
    ),
}


@pytest.mark.parametrize(
    ("account", "build", "pick_alternative", "source", "ledger_amount"),
    list(_ROUND_TRIPS.values()),
    ids=list(_ROUND_TRIPS),
)
def test_prompt_suggestion_accepted_as_shown_is_recorded(
    tmp_path, account, build, pick_alternative, source, ledger_amount
):
    ledger, review, svc = _setup(tmp_path)
    batch_id = build(review)
    by_account = {i.account: i for i in svc.prompt(batch_id).prompts}
    item = by_account[account]
    shown = item.alternatives[0] if pick_alternative else item.suggestion
    assert shown is not None
    assert shown.ledger_amount == ledger_amount

    resp = svc.add(
        OpeningCreateRequest(
            account=account,
            amount=shown.amount,
            direction=shown.direction,
            date=item.suggested_date,
            batch_id=batch_id,
        )
    )

    assert resp.source == source
    assert resp.ledger_amount == ledger_amount
    assert resp.date == item.suggested_date
    assert ledger.validate() == []
    (status,) = [r for r in svc.statuses().accounts if r.account == account]
    assert status.status == "opening"
    assert status.opening is not None
    assert (status.opening.ledger_amount, status.opening.source) == (
        ledger_amount,
        source,
    )


@pytest.mark.parametrize("figure", ["Infinity", "sNaN"])
def test_prompt_non_finite_running_balance_keeps_the_printed_opening(tmp_path, figure):
    """A running-balance cell that is not a finite number cannot be walked:
    the derivation is absent and says why in `note`, and the printed
    opening is kept."""
    from backend.app.server.main import create_app
    from tests.llm.flows.prove_template.test_stage_opening_evidence import (
        FIELDS,
        FINGERPRINT,
        STATEMENT,
    )
    from tests.llm.flows.prove_template.test_stage_opening_evidence import (
        TODAY as CSV_TODAY,
    )

    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: CSV_TODAY)
    client = TestClient(app)
    app.state.template_store.save(FIELDS.to_template(FINGERPRINT, origin="agent"))
    statement = STATEMENT.replace(b"-500.00,9500.00", f"-500.00,{figure}".encode())
    out = app.state.import_service.detect("s.csv", statement)
    assert out["matched"] is True

    resp = client.get(f"/api/review/{out['batch_id']}/opening")

    assert resp.status_code == 200
    (item,) = resp.json()["prompts"]
    assert item["suggestion"]["source"] == "statement"
    assert item["suggestion"]["ledger_amount"] == "10000.00"
    assert item["alternatives"] == []
    assert item["note"] == NON_FINITE_BALANCE
    assert figure not in item["note"]


# ── an account first touched by a transfer (Q27) ────────────────────────────
def _transfer(account: str, when: date, amount: str = "500.00") -> str:
    """A transfer paired onto `account` before its own first import: the
    account gets a posting, so its status is "missing"."""
    return f"""
{when.isoformat()} * "Transfer"
  {account}  {amount} INR
  {OPENED}  -{amount} INR
"""


def _setup_with(tmp_path, extra: str) -> tuple[Ledger, ReviewStore, OpeningService]:
    ledger = _ledger(tmp_path, extra)
    review = ReviewStore(lock=ledger.write_lock())
    return ledger, review, _service(tmp_path, ledger, review)


def _own_statement_batch(review: ReviewStore) -> str:
    return _batch(
        review,
        [
            _candidate(BANK, date(2025, 4, 20)),
            _candidate(BANK, date(2025, 4, 1)),
            _candidate(BANK, date(2025, 4, 10)),
        ],
        evidence=_evidence(BANK, printed=_printed("10,000.00", "10000.00")),
    )


def test_prompt_missing_account_with_its_own_statement_is_offered(tmp_path):
    ledger, review, svc = _setup_with(tmp_path, _transfer(BANK, date(2025, 4, 10)))
    batch_id = _own_statement_batch(review)

    out = svc.prompt(batch_id)

    assert out.missing == [BANK]
    (item,) = out.prompts
    assert item.account == BANK
    assert item.statement_start == date(2025, 4, 1)
    assert item.suggested_date == date(2025, 3, 31)
    s = item.suggestion
    assert s is not None
    assert (s.source, s.ledger_amount, s.direction) == ("statement", "10000.00", "have")

    resp = svc.add(
        OpeningCreateRequest(
            account=BANK,
            amount=s.amount,
            direction=s.direction,
            date=item.suggested_date,
            batch_id=batch_id,
        )
    )

    assert (resp.source, resp.ledger_amount) == ("statement", "10000.00")
    assert ledger.validate() == []
    (status,) = [r for r in svc.statuses().accounts if r.account == BANK]
    assert (status.status, status.warn) == ("opening", False)


def test_prompt_missing_account_with_records_before_the_statement_is_not_offered(
    tmp_path,
):
    # The transfer (20 Mar) precedes the opening the statement implies
    # (31 Mar): an opening there would come after existing records.
    _, review, svc = _setup_with(tmp_path, _transfer(BANK, date(2025, 3, 20)))
    batch_id = _own_statement_batch(review)

    out = svc.prompt(batch_id)

    assert out.prompts == []
    assert out.missing == [BANK]


def test_prompt_missing_account_without_its_own_evidence_is_not_offered(tmp_path):
    _, review, svc = _setup_with(tmp_path, _transfer(BANK, date(2025, 4, 10)))
    batch_id = _batch(
        review,
        [_candidate(BANK, date(2025, 4, 1)), _candidate(BANK2, date(2025, 4, 2))],
        evidence=_evidence(BANK2, printed=_printed("7,000.00", "7000.00")),
    )

    out = svc.prompt(batch_id)

    assert [i.account for i in out.prompts] == [BANK2]
    assert out.missing == [BANK]


def test_prompt_missing_card_with_its_own_pdf_is_offered(tmp_path):
    _, review, svc = _setup_with(
        tmp_path, _transfer(CARD, date(2025, 4, 1), amount="200.00")
    )
    batch_id = _batch(
        review, [_candidate(CARD, date(2025, 3, 20))], statement=_summary("4210.37")
    )

    out = svc.prompt(batch_id)

    assert out.missing == [CARD]
    (item,) = out.prompts
    assert item.suggested_date == date(2025, 3, 15)
    assert item.suggestion is not None
    assert item.suggestion.ledger_amount == "-4210.37"


def test_prompt_late_opening_with_its_own_statement_is_not_offered(tmp_path):
    _, review, svc = _setup_with(tmp_path, "")
    batch_id = _batch(
        review,
        [_candidate(LATE, date(2025, 3, 1))],
        evidence=_evidence(LATE, printed=_printed("10,000.00", "10000.00")),
    )

    out = svc.prompt(batch_id)

    assert out.prompts == []
    assert out.missing == [LATE]
