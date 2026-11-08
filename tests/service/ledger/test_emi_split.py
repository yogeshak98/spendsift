"""EmiSplitService: proposal and the checked, all-or-nothing apply.

Synthetic EMI rows appended to the `ledger` fixture."""

from __future__ import annotations

from datetime import date

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.core.settings import Settings
from backend.app.models.api.liabilities import EmiSplitPlan
from backend.app.persistence.journal import Ledger
from backend.app.service.ledger.emi_split import EmiSplitService
from backend.app.service.reports.health.ledger_view import (
    all_postings,
    closed_accounts,
    open_meta,
)
from tests.conftest import make_txn

PARENT = "Liabilities:Loan:CardEMI"
CARD = "Liabilities:CreditCard:Acme1234"
OTHER = "Liabilities:CreditCard:Beta5678"
PLAN = f"{PARENT}:Acme1234-20260722"
TODAY = date(2026, 11, 1)


def _txn(d, narration, debit, credit, amount, txn_id):
    txn = make_txn(d, narration, debit, credit, amount)
    txn.meta["txn-id"] = txn_id
    return txn


@pytest.fixture
def svc(ledger: Ledger) -> EmiSplitService:
    ledger.open_named_accounts([PARENT, CARD, OTHER])
    ledger.append(
        [
            _txn(date(2026, 7, 22), "EMI CONVERSION", CARD, PARENT, "1200", "t1"),
            _txn(
                date(2026, 8, 21),
                "OFFUS EMI,PRIN NB:01,0000555000111",
                PARENT,
                CARD,
                "600",
                "t2",
            ),
            _txn(
                date(2026, 9, 21),
                "OFFUS EMI,PRIN NB:02,0000555000111",
                PARENT,
                CARD,
                "600",
                "t3",
            ),
        ]
    )
    return EmiSplitService(
        ledger, PARENT, Settings().card_emi_instalment_patterns, clock=lambda: TODAY
    )


def _plan(**over) -> EmiSplitPlan:
    base = {
        "account": PLAN,
        "card": CARD,
        "ref": "0000 555000111",
        "label": "Test plan",
        "txn_ids": ["t1", "t2", "t3"],
        "close": False,
    }
    return EmiSplitPlan(**{**base, **over})


def _bytes(led: Ledger) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(led.journal_dir.glob("*.beancount"))}


def _code(exc: pytest.ExceptionInfo[CodedError]) -> tuple[str, dict | None]:
    return exc.value.code, exc.value.data


def test_proposal_lists_the_plan(svc):
    out = svc.proposal()
    assert out["parent"] == PARENT
    (plan,) = out["plans"]
    assert (plan["account"], plan["ref"], plan["fully_repaid"]) == (
        PLAN,
        "555000111",
        True,
    )


def test_apply_split_moves_and_stamps_meta(svc, ledger):
    out = svc.apply([_plan()])
    assert out == {"moved": 3, "opened": [PLAN], "plans": [PLAN], "closed": []}
    assert open_meta(ledger)[PLAN] == {
        "emi-ref": "555000111",
        "emi-card": CARD,
        "label": "Test plan",
    }
    assert [r for r in all_postings(ledger) if r.account == PARENT] == []
    assert ledger.validate() == []


def test_apply_split_closes_fully_repaid_plan(svc, ledger):
    out = svc.apply([_plan(close=True)])
    assert out["closed"] == [PLAN]
    assert PLAN in closed_accounts(ledger)


def test_apply_split_omits_null_meta(svc, ledger):
    svc.apply([_plan(card=None, ref=None, label=None)])
    assert open_meta(ledger)[PLAN] == {}


@pytest.mark.parametrize(
    "account",
    [
        "Liabilities:Loan:Other:Acme1234",  # outside the parent
        f"{PLAN}:Sub",  # grandchild
        f"{PARENT}:acme",  # invalid name
        PARENT,  # the parent itself
    ],
)
def test_apply_split_rejects_bad_targets(svc, ledger, account):
    before = _bytes(ledger)
    with pytest.raises(CodedError) as raised:
        svc.apply([_plan(account=account)])
    assert _code(raised)[0] == "E1086"
    assert set(_code(raised)[1] or {}) == {"reason"}
    assert _bytes(ledger) == before


def test_apply_split_rejects_duplicate_accounts(svc):
    with pytest.raises(CodedError) as raised:
        svc.apply([_plan(txn_ids=["t1"]), _plan(txn_ids=["t2"])])
    assert raised.value.code == "E1086"


def test_apply_split_rejects_txn_in_two_plans(svc):
    with pytest.raises(CodedError) as raised:
        svc.apply(
            [_plan(txn_ids=["t1", "t2"]), _plan(account=PLAN + "-2", txn_ids=["t2"])]
        )
    assert raised.value.code == "E1086"
    assert "t2" in raised.value.description


@pytest.mark.parametrize(
    "over",
    [
        {"card": "Liabilities:CreditCard:Nope9999"},
        {"card": "Assets:Checking:Bank"},
        {"label": ""},
        {"label": 'say "hi"'},
        {"label": "x" * 61},
        {"label": "two\nlines"},
        {"ref": "   "},
    ],
)
def test_apply_split_rejects_bad_card_label_ref(svc, over):
    with pytest.raises(CodedError) as raised:
        svc.apply([_plan(**over)])
    assert raised.value.code == "E1086"


def test_apply_split_rejects_open_target_with_postings(svc, ledger):
    ledger.append([_txn(date(2026, 10, 1), "x", PLAN, CARD, "1", "z1")])
    with pytest.raises(CodedError) as raised:
        svc.apply([_plan()])
    assert raised.value.code == "E1086"


def test_apply_split_rejects_open_target_for_another_card(svc, ledger):
    ledger.open_named_accounts([PLAN])
    ledger.set_open_meta(PLAN, {"emi-card": OTHER})
    with pytest.raises(CodedError) as raised:
        svc.apply([_plan()])
    assert raised.value.code == "E1086"


def test_apply_split_into_empty_open_target_is_allowed(svc, ledger):
    ledger.open_named_accounts([PLAN])
    ledger.set_open_meta(PLAN, {"emi-card": CARD})
    out = svc.apply([_plan()])
    assert out["opened"] == [] and out["plans"] == [PLAN]


def test_apply_split_rejects_closed_target(svc, ledger):
    ledger.open_named_accounts([PLAN])
    ledger.close_account(PLAN, date(2026, 10, 1))
    with pytest.raises(CodedError) as raised:
        svc.apply([_plan(card=None)])
    assert raised.value.code == "E1086"


def test_apply_split_stale_txn_writes_nothing(svc, ledger):
    before = _bytes(ledger)
    snaps = len(list(ledger.snapshots_dir.iterdir()))
    with pytest.raises(CodedError) as raised:
        svc.apply([_plan(txn_ids=["t1", "gone", "also-gone"])])
    assert _code(raised) == ("E1085", {"txn_id": "gone"})
    assert len(list(ledger.snapshots_dir.iterdir())) == snaps
    assert _bytes(ledger) == before


def test_apply_split_close_requires_zero_sum(svc, ledger):
    before = _bytes(ledger)
    with pytest.raises(CodedError) as raised:
        svc.apply([_plan(txn_ids=["t1", "t2"], close=True)])
    assert raised.value.code == "E1086"
    assert _bytes(ledger) == before


# ── (card, ref) names one plan; already-open targets get their metadata ─────
def test_apply_split_rejects_two_plans_sharing_card_and_ref(svc, ledger):
    before = _bytes(ledger)
    with pytest.raises(CodedError) as raised:
        svc.apply(
            [
                _plan(txn_ids=["t1", "t2"]),
                _plan(account=PLAN + "-2", ref="555000111", txn_ids=["t3"]),
            ]
        )
    assert raised.value.code == "E1086"
    assert _bytes(ledger) == before


def test_apply_split_rejects_ref_of_another_open_plan(svc, ledger):
    older = f"{PARENT}:Acme1234-20250101"
    ledger.open_named_accounts([older])
    ledger.set_open_meta(older, {"emi-card": CARD, "emi-ref": "555000111"})
    before = _bytes(ledger)
    with pytest.raises(CodedError) as raised:
        svc.apply([_plan()])
    assert raised.value.code == "E1086"
    assert older in raised.value.description
    assert _bytes(ledger) == before


def test_apply_split_same_ref_as_a_closed_plan_or_another_card_is_allowed(svc, ledger):
    closed_plan = f"{PARENT}:Acme1234-20250101"
    other_card = f"{PARENT}:Beta5678-20250101"
    ledger.open_named_accounts([closed_plan, other_card])
    ledger.set_open_meta(closed_plan, {"emi-card": CARD, "emi-ref": "555000111"})
    ledger.set_open_meta(other_card, {"emi-card": OTHER, "emi-ref": "555000111"})
    ledger.close_account(closed_plan, date(2026, 1, 1))
    assert svc.apply([_plan()])["moved"] == 3


def test_apply_split_stamps_meta_on_an_already_open_bare_target(svc, ledger):
    ledger.open_named_accounts([PLAN])
    out = svc.apply([_plan()])
    assert out["opened"] == []
    assert open_meta(ledger)[PLAN] == {
        "emi-ref": "555000111",
        "emi-card": CARD,
        "label": "Test plan",
    }
    assert ledger.validate() == []
