"""Ingest routes a new EMI instalment to its plan sub-account. Synthetic refs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.core.settings import Settings
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.emi_routing import (
    PlanRoute,
    emi_plan_index,
    route_emi_instalments,
)
from backend.app.service.reports.liabilities.emi_plans import compile_patterns

PARENT = "Liabilities:Loan:CardEMI"
CARD = "Liabilities:CreditCard:Acme1234"
OTHER = "Liabilities:CreditCard:Beta5678"
PLAN = f"{PARENT}:Acme1234-20260722"
PATTERNS = compile_patterns(Settings().card_emi_instalment_patterns)
INDEX = {(CARD, "555000111"): PlanRoute(PLAN, owed=True, paid=3)}


def _cand(
    narration: str = "OFFUS EMI,PRIN NB:04,0000555000111",
    *,
    source: str = CARD,
    direction: Direction = Direction.DEBIT,
    category: str | None = PARENT,
    category_source: str = "rule",
) -> CandidateTxn:
    return CandidateTxn(
        date=date(2026, 11, 21),
        amount=Decimal("950"),
        raw_narration=narration,
        normalized_narration=narration.upper(),
        source_account=source,
        direction=direction,
        suggested_category=category,
        category_source=category_source,  # type: ignore[arg-type]
    )


def _route(*cands: CandidateTxn) -> int:
    return route_emi_instalments(list(cands), PARENT, INDEX, PATTERNS)


def test_emi_plan_index_reads_direct_children_with_both_keys():
    meta = {
        PLAN: {"emi-ref": "0000 555000111", "emi-card": CARD, "label": "x"},
        f"{PARENT}:NoRef": {"emi-card": CARD},
        f"{PLAN}:Deeper": {"emi-ref": "1", "emi-card": CARD},
        "Liabilities:Loan:Car": {"emi-ref": "2", "emi-card": CARD},
        f"{PARENT}:Closed": {"emi-ref": "3", "emi-card": CARD},
    }
    got = emi_plan_index(
        meta,
        PARENT,
        closed=frozenset({f"{PARENT}:Closed"}),
        postings={PLAN: [Decimal("-12000"), Decimal("1000"), Decimal("1000")]},
    )
    assert got == {(CARD, "555000111"): PlanRoute(PLAN, owed=True, paid=2)}


def test_routes_instalment_with_known_ref_to_plan_account():
    c = _cand()
    assert _route(c) == 1
    assert c.suggested_category == PLAN
    assert c.category_source == "rule"


def test_routes_dictionary_sourced_rows_too():
    c = _cand(category_source="dictionary")
    assert _route(c) == 1
    assert c.suggested_category == PLAN


def test_unknown_ref_stays_on_parent():
    c = _cand("OFFUS EMI,PRIN NB:01,0000999999")
    assert _route(c) == 0
    assert c.suggested_category == PARENT


def test_same_ref_on_another_card_stays_on_parent():
    c = _cand(source=OTHER)
    assert _route(c) == 0
    assert c.suggested_category == PARENT


@pytest.mark.parametrize("source", ["user", "fee_follows", "taught_batch", "none"])
def test_never_touches_user_or_fee_linked_rows(source):
    c = _cand(category_source=source)
    assert _route(c) == 0
    assert c.suggested_category == PARENT


def test_conversion_row_stays_on_parent():
    c = _cand(direction=Direction.CREDIT)
    assert _route(c) == 0
    assert c.suggested_category == PARENT


def test_row_suggested_elsewhere_is_untouched():
    c = _cand(category="Expenses:Shopping")
    assert _route(c) == 0
    assert c.suggested_category == "Expenses:Shopping"


def test_row_with_other_leg_or_splits_is_untouched():
    c = _cand()
    c.suggested_other_leg = "Assets:Transfer:CreditCard"
    assert _route(c) == 0
    assert c.suggested_category == PARENT


# ── Merchant-name refs: never guess between plans ───────────────────────────
MERCHANT = "Principal Amount Amortization - <{seq}/6>ACME STORES"


def test_emi_plan_index_drops_a_key_shared_by_two_open_plans():
    first, second = f"{PARENT}:Acme1234-20260105", f"{PARENT}:Acme1234-20260905"
    meta = {
        first: {"emi-ref": "ACME STORES", "emi-card": CARD},
        second: {"emi-ref": "ACME STORES", "emi-card": CARD},
        PLAN: {"emi-ref": "555000111", "emi-card": CARD},
    }
    got = emi_plan_index(meta, PARENT)
    assert set(got) == {(CARD, "555000111")}


def test_two_plans_at_the_same_merchant_route_nowhere():
    first, second = f"{PARENT}:Acme1234-20260105", f"{PARENT}:Acme1234-20260905"
    meta = {
        first: {"emi-ref": "ACME STORES", "emi-card": CARD},
        second: {"emi-ref": "ACME STORES", "emi-card": CARD},
    }
    index = emi_plan_index(meta, PARENT, postings={second: [Decimal("-6000")]})
    c = _cand(MERCHANT.format(seq=2))
    assert route_emi_instalments([c], PARENT, index, PATTERNS) == 0
    assert c.suggested_category == PARENT


def test_repaid_plan_still_open_is_not_routed_to():
    meta = {PLAN: {"emi-ref": "ACME STORES", "emi-card": CARD}}
    repaid = [Decimal("-6000")] + [Decimal("1000")] * 6
    index = emi_plan_index(meta, PARENT, postings={PLAN: repaid})
    assert index[(CARD, "ACMESTORES")] == PlanRoute(PLAN, owed=False, paid=6)
    # A new EMI at the same merchant starts again at instalment 1.
    c = _cand(MERCHANT.format(seq=1))
    assert route_emi_instalments([c], PARENT, index, PATTERNS) == 0
    assert c.suggested_category == PARENT


def test_open_plan_with_balance_owed_is_routed():
    meta = {PLAN: {"emi-ref": "ACME STORES", "emi-card": CARD}}
    running = [Decimal("-6000"), Decimal("1000")]
    index = emi_plan_index(meta, PARENT, postings={PLAN: running})
    c = _cand(MERCHANT.format(seq=2))
    assert route_emi_instalments([c], PARENT, index, PATTERNS) == 1
    assert c.suggested_category == PLAN


def test_plan_without_postings_takes_an_instalment_past_its_paid_count():
    # A freshly split plan whose rows are all still to come (net 0, 0 paid).
    route = PlanRoute(PLAN, owed=False, paid=0)
    assert route.accepts(1) and not route.accepts(None)
