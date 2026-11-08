"""AccountMetaService.set_loan_terms: the in-app loan terms form writes `open`
metadata."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.persistence.journal import Ledger
from backend.app.persistence.templates import PdfTemplateStore
from backend.app.service.ledger.account_meta import AccountMetaService
from backend.app.service.reports.health.ledger_view import open_meta

LOAN = "Liabilities:Loan:Car"
CARD = "Liabilities:CreditCard:Acme1234"


@pytest.fixture
def svc(ledger: Ledger, tmp_path) -> AccountMetaService:
    ledger.open_named_accounts([LOAN, CARD])
    return AccountMetaService(ledger, PdfTemplateStore(tmp_path / "templates" / "pdf"))


def _bytes(led: Ledger) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(led.journal_dir.glob("*.beancount"))}


def _set(svc, **over):
    args = {
        "account": LOAN,
        "annual_rate_pct": "15.5",
        "term_months": 12,
        "original_principal": None,
        "label": None,
    }
    args.update(over)
    return svc.set_loan_terms(**args)


def test_set_loan_terms_writes_and_clears_meta(svc, ledger):
    got = _set(svc, original_principal="12000", label="Car")
    assert got == {
        "account": LOAN,
        "annual_rate_pct": "15.5",
        "term_months": 12,
        "original_principal": "12000",
        "label": "Car",
    }
    assert open_meta(ledger)[LOAN] == {
        "rate-pct": Decimal("15.5"),
        "term-months": 12,
        "principal": 12000,
        "label": "Car",
    }
    got = _set(svc, annual_rate_pct=None, term_months=None, label="Car")
    assert got == {
        "account": LOAN,
        "annual_rate_pct": None,
        "term_months": None,
        "original_principal": None,
        "label": "Car",
    }
    assert open_meta(ledger)[LOAN] == {"label": "Car"}


def test_set_loan_terms_unchanged_takes_no_snapshot(svc, ledger):
    _set(svc)
    snaps = len(list(ledger.snapshots_dir.iterdir()))
    _set(svc)
    assert len(list(ledger.snapshots_dir.iterdir())) == snaps


def test_set_loan_terms_unknown_account(svc):
    with pytest.raises(LookupError):
        _set(svc, account="Liabilities:Loan:Nope")


def test_set_loan_terms_rejects_non_loan(svc):
    with pytest.raises(CodedError) as raised:
        _set(svc, account=CARD)
    assert raised.value.code == "E1083"
    assert raised.value.data == {"account": CARD, "expected": "Liabilities:Loan:*"}


def test_set_loan_terms_rejects_closed(svc, ledger):
    ledger.close_account(LOAN, date(2026, 10, 1))
    with pytest.raises(CodedError) as raised:
        _set(svc)
    assert (raised.value.code, raised.value.data) == ("E1084", {"account": LOAN})


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"annual_rate_pct": "61"}, "annual_rate_pct"),
        ({"annual_rate_pct": "-1"}, "annual_rate_pct"),
        ({"annual_rate_pct": "abc"}, "annual_rate_pct"),
        ({"term_months": 0}, "term_months"),
        ({"term_months": 601}, "term_months"),
        ({"original_principal": "0"}, "original_principal"),
        ({"original_principal": "x"}, "original_principal"),
        ({"label": ""}, "label"),
        ({"label": 'a "b"'}, "label"),
        ({"label": "x" * 61}, "label"),
        ({"annual_rate_pct": "61", "label": ""}, "annual_rate_pct"),
    ],
)
def test_set_loan_terms_rejects_invalid(svc, ledger, over, field):
    before = _bytes(ledger)
    with pytest.raises(CodedError) as raised:
        _set(svc, **over)
    assert raised.value.code == "E1082"
    assert raised.value.data is not None
    assert raised.value.data["field"] == field
    assert set(raised.value.data) == {"field", "value", "reason"}
    assert _bytes(ledger) == before


def test_set_loan_terms_rejects_half_set(svc):
    with pytest.raises(CodedError) as raised:
        _set(svc, term_months=None)
    assert raised.value.code == "E1082"
    assert raised.value.data == {
        "field": "term_months",
        "value": "null",
        "reason": "set both or neither",
    }
    with pytest.raises(CodedError) as raised:
        _set(svc, annual_rate_pct=None)
    assert raised.value.data["field"] == "annual_rate_pct"
