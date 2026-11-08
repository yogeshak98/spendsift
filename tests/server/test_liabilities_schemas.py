from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.models.api.liabilities import (
    EmiPlanProposal,
    EmiSplitPlan,
    EmiSplitProposalResponse,
    EmiSplitRequest,
    EmiSplitResponse,
    LoanTermsResponse,
    LoanTermsUpdateRequest,
)
from backend.app.models.api.reports import (
    CreditCardSummary,
    CreditCardSummaryResponse,
    LiabilitiesBalanceResponse,
    LiabilityBalanceRow,
    RepaymentLoan,
    RepaymentResponse,
)
from backend.app.models.api.settings import (
    CardProperties,
    CardPropertiesBatchItem,
    CardPropertiesBatchRequest,
    CardPropertiesListResponse,
    CardPropertiesUpdateRequest,
)

BALANCE_ROW = {
    "account": "Liabilities:People:Asha",
    "drawn": "0",
    "repaid": "10000",
    "balance": "-10000",
    "interest": "0",
    "apr": None,
    "apr_source": None,
    "direction": "owed_to_me",
    "amount": "10000",
}


NO_TRANSIT = {
    "amount": "0.00",
    "attributed": "0.00",
    "unattributed": "0.00",
    "payments": [],
}


def test_balance_row_accepts_contract_example():
    body = LiabilitiesBalanceResponse.model_validate(
        {"balances": [BALANCE_ROW], "in_transit": NO_TRANSIT}
    )
    assert body.model_dump() == {
        "balances": [BALANCE_ROW],
        "in_transit": NO_TRANSIT,
        "cards_outstanding": "0.00",
        "cards_net_owed": "0.00",
    }


def test_balance_cards_net_owed_subtracts_every_in_transit_payment():
    card = {
        **BALANCE_ROW,
        "account": "Liabilities:CreditCard:Acme1234",
        "balance": "31872",
        "direction": "i_owe",
        "amount": "31872",
    }
    in_transit = {
        "amount": "31960.00",
        "attributed": "31860.00",
        "unattributed": "100.00",
        "payments": [
            {
                "txn_id": "t1",
                "date": "2026-10-20",
                "amount": "31860.00",
                "account": "Liabilities:CreditCard:Acme1234",
            },
            {"txn_id": "t2", "date": "2026-10-21", "amount": "100.00", "account": None},
        ],
    }
    body = LiabilitiesBalanceResponse.model_validate(
        {"balances": [BALANCE_ROW, card], "in_transit": in_transit}
    ).model_dump()
    # People:Asha is not a card: only the card row counts.
    assert body["cards_outstanding"] == "31872.00"
    assert body["cards_net_owed"] == "-88.00"


@pytest.mark.parametrize("missing", ["direction", "amount", "apr_source", "apr"])
def test_balance_row_fields_are_required(missing):
    row = {k: v for k, v in BALANCE_ROW.items() if k != missing}
    with pytest.raises(ValidationError):
        LiabilityBalanceRow.model_validate(row)


@pytest.mark.parametrize(
    ("field", "value"), [("direction", "owes"), ("apr_source", "guessed")]
)
def test_balance_row_rejects_unknown_enum_values(field, value):
    with pytest.raises(ValidationError):
        LiabilityBalanceRow.model_validate({**BALANCE_ROW, field: value})


# ── Lane B: card properties and card cycle ──────────────────────────────────

CARD_PROPS = {
    "account": "Liabilities:CreditCard:Acme1234",
    "statement_day": 15,
    "due_day": 3,
    "last4": "1234",
    "expiry": "2029-08",
    "suggested": {
        "statement_day": 15,
        "due_day": 3,
        "last4": "1234",
        "statement_count": 2,
        "last4_source": "pdf_template",
    },
}

CARD_TILE = {
    "account": "Liabilities:CreditCard:Acme1234",
    "outstanding": "2299",
    "last_activity_date": "2026-10-20",
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


def test_card_properties_accepts_contract_example():
    body = CardPropertiesListResponse.model_validate({"cards": [CARD_PROPS]})
    assert body.model_dump() == {"cards": [CARD_PROPS]}
    assert CardProperties.model_validate(CARD_PROPS).model_dump() == CARD_PROPS


def test_card_properties_suggestion_can_be_empty():
    empty = {
        "statement_day": None,
        "due_day": None,
        "last4": None,
        "statement_count": 0,
        "last4_source": None,
    }
    card = {
        **CARD_PROPS,
        "statement_day": None,
        "due_day": None,
        "last4": None,
        "expiry": None,
    }
    assert CardProperties.model_validate({**card, "suggested": empty})


def test_card_properties_update_accepts_nulls():
    req = CardPropertiesUpdateRequest.model_validate(
        {
            "account": "Liabilities:CreditCard:Acme1234",
            "statement_day": 15,
            "due_day": None,
            "last4": None,
        }
    )
    assert (req.statement_day, req.due_day, req.last4) == (15, None, None)


def test_card_properties_update_leaves_range_checks_to_the_service():
    # Out-of-range values must reach the service, which answers with the coded
    # E1082 naming the field, not a generic validation error.
    req = CardPropertiesUpdateRequest.model_validate(
        {"account": "A", "statement_day": 32, "due_day": 0, "last4": "12a4"}
    )
    assert (req.statement_day, req.due_day, req.last4) == (32, 0, "12a4")


def test_card_properties_update_forbids_extra_keys():
    with pytest.raises(ValidationError):
        CardPropertiesUpdateRequest.model_validate(
            {
                "account": "Liabilities:CreditCard:Acme1234",
                "statement_day": 15,
                "due_day": 3,
                "last4": "1234",
                "colour": "red",
            }
        )


@pytest.mark.parametrize("missing", ["statement_day", "due_day", "last4"])
def test_card_properties_update_requires_every_key(missing):
    body = {"account": "A", "statement_day": 1, "due_day": 2, "last4": "1234"}
    del body[missing]
    with pytest.raises(ValidationError):
        CardPropertiesUpdateRequest.model_validate(body)


def test_card_properties_update_expiry_is_optional_and_unchanged_when_omitted():
    body = {"account": "A", "statement_day": 1, "due_day": 2, "last4": "1234"}
    req = CardPropertiesUpdateRequest.model_validate(body)
    assert "expiry" not in req.model_fields_set
    sent_null = CardPropertiesUpdateRequest.model_validate({**body, "expiry": None})
    assert "expiry" in sent_null.model_fields_set
    assert sent_null.expiry is None
    # The format is checked by the service (E1082), not here.
    assert (
        CardPropertiesUpdateRequest.model_validate({**body, "expiry": "abc"}).expiry
        == "abc"
    )


BATCH_ITEM = {
    "account": "Liabilities:CreditCard:Acme1234",
    "statement_day": 15,
    "due_day": 3,
    "last4": "1234",
    "expiry": "2029-08",
}


def test_card_properties_batch_accepts_contract_example():
    req = CardPropertiesBatchRequest.model_validate({"cards": [BATCH_ITEM]})
    assert req.model_dump() == {"cards": [BATCH_ITEM]}


@pytest.mark.parametrize(
    "missing", ["account", "statement_day", "due_day", "last4", "expiry"]
)
def test_card_properties_batch_item_requires_every_key(missing):
    body = {k: v for k, v in BATCH_ITEM.items() if k != missing}
    with pytest.raises(ValidationError):
        CardPropertiesBatchItem.model_validate(body)


def test_card_properties_batch_forbids_extra_keys():
    with pytest.raises(ValidationError):
        CardPropertiesBatchItem.model_validate({**BATCH_ITEM, "colour": "red"})
    with pytest.raises(ValidationError):
        CardPropertiesBatchRequest.model_validate({"cards": [], "x": 1})


def test_credit_card_summary_rejects_an_unknown_expiry_status():
    with pytest.raises(ValidationError):
        CreditCardSummary.model_validate({**CARD_TILE, "expiry_status": "soon"})


def test_credit_card_summary_accepts_contract_example():
    body = CreditCardSummaryResponse.model_validate(
        {"cards": [{**CARD_TILE, "in_transit": None}], "in_transit": NO_TRANSIT}
    )
    assert body.model_dump() == {
        "cards": [{**CARD_TILE, "in_transit": None, "net_owed": "2299"}],
        "in_transit": NO_TRANSIT,
        "outstanding": "2299.00",
        "net_owed": "2299.00",
    }


def test_credit_card_net_owed_subtracts_in_transit():
    tile = {
        **CARD_TILE,
        "outstanding": "31872",
        "in_transit": {"amount": "31860.00", "date": "2026-10-20", "count": 1},
    }
    body = CreditCardSummaryResponse.model_validate(
        {
            "cards": [tile],
            "in_transit": {
                "amount": "31860.00",
                "attributed": "31860.00",
                "unattributed": "0.00",
                "payments": [
                    {
                        "txn_id": "t1",
                        "date": "2026-10-20",
                        "amount": "31860.00",
                        "account": CARD_TILE["account"],
                    }
                ],
            },
        }
    ).model_dump()
    assert body["cards"][0]["net_owed"] == "12.00"
    assert (body["outstanding"], body["net_owed"]) == ("31872.00", "12.00")


def test_credit_card_summary_without_properties():
    bare = {
        **CARD_TILE,
        "last4": None,
        "statement_day": None,
        "due_day": None,
        "properties_source": None,
        "last_statement_date": None,
        "next_statement_date": None,
        "current_due_date": None,
        "next_due_date": None,
        "statement_expected": False,
        "expiry": None,
        "expiry_status": None,
    }
    assert CreditCardSummary.model_validate(
        {**bare, "in_transit": None}
    ).model_dump() == {
        **bare,
        "in_transit": None,
        "net_owed": "2299",
    }


def test_in_transit_has_no_default_so_a_missed_wire_up_fails():
    with pytest.raises(ValidationError):
        LiabilitiesBalanceResponse.model_validate({"balances": [BALANCE_ROW]})
    with pytest.raises(ValidationError):
        CreditCardSummaryResponse.model_validate(
            {"cards": [{**CARD_TILE, "in_transit": None}]}
        )
    with pytest.raises(ValidationError):
        CreditCardSummary.model_validate(CARD_TILE)


# ── Lane C: EMI plans, loan terms, repayment ────────────────────────────────
PLAN = "Liabilities:Loan:CardEMI:Acme1234-20260722"

REPAYMENT = {
    "loans": [
        {
            "account": PLAN,
            "label": "Washing machine",
            "card": "Liabilities:CreditCard:Acme1234",
            "terms_set": True,
            "terms_source": "inferred",
            "outstanding": "9000",
            "original_principal": "12000",
            "annual_rate_pct": "15.00",
            "term_months": 12,
            "paid_instalments": 3,
            "start_month": "2026-08",
            "emi": "1083",
            "total_interest": "996",
            "payoff_month": "2027-07",
            "schedule": [
                {
                    "month": 1,
                    "emi": "1083",
                    "principal": "933",
                    "interest": "150",
                    "balance": "11067",
                }
            ],
        },
        {
            "account": "Liabilities:Loan:Car",
            "label": None,
            "card": None,
            "terms_set": False,
            "terms_source": None,
            "outstanding": "100000",
            "original_principal": "100000",
            "annual_rate_pct": None,
            "term_months": None,
            "paid_instalments": None,
            "start_month": None,
            "emi": None,
            "total_interest": None,
            "payoff_month": None,
            "schedule": [],
        },
    ],
    "emi_pool": {
        "account": "Liabilities:Loan:CardEMI",
        "unassigned_count": 0,
        "unassigned_amount": "0",
    },
}

PROPOSAL = {
    "parent": "Liabilities:Loan:CardEMI",
    "plans": [
        {
            "account": PLAN,
            "card": "Liabilities:CreditCard:Acme1234",
            "ref": "555000111",
            "conversion": {"txn_id": "t1", "date": "2026-07-22", "amount": "12000"},
            "instalments": [
                {"txn_id": "t2", "date": "2026-08-21", "amount": "933", "seq": 1}
            ],
            "principal": "12000",
            "repaid": "933",
            "outstanding": "11067",
            "fully_repaid": False,
            "label_hint": "ACME STORE",
            "confident": True,
            "reasons": [],
        }
    ],
    "unassigned": [
        {
            "txn_id": "t9",
            "date": "2026-09-01",
            "narration": "EMI ADJ",
            "amount": "50",
            "reason": "no instalment pattern matched",
        }
    ],
}

SPLIT_PLAN = {
    "account": PLAN,
    "card": "Liabilities:CreditCard:Acme1234",
    "ref": "555000111",
    "label": "Washing machine",
    "txn_ids": ["t1", "t2"],
    "close": False,
}

TERMS = {
    "account": "Liabilities:Loan:Car",
    "annual_rate_pct": "15.5",
    "term_months": 12,
    "original_principal": "12000",
    "label": "Car",
}


def test_repayment_accepts_contract_example():
    body = RepaymentResponse.model_validate(REPAYMENT)
    assert body.model_dump() == REPAYMENT


def test_repayment_pool_may_be_null():
    body = RepaymentResponse.model_validate({"loans": [], "emi_pool": None})
    assert body.emi_pool is None


@pytest.mark.parametrize("missing", ["terms_source", "paid_instalments", "card"])
def test_repayment_loan_fields_are_required(missing):
    loan = {k: v for k, v in REPAYMENT["loans"][0].items() if k != missing}
    with pytest.raises(ValidationError):
        RepaymentLoan.model_validate(loan)


def test_proposal_accepts_contract_example():
    body = EmiSplitProposalResponse.model_validate(PROPOSAL)
    assert body.model_dump() == PROPOSAL


def test_proposal_plan_without_conversion():
    plan = {
        **PROPOSAL["plans"][0],
        "conversion": None,
        "principal": None,
        "outstanding": None,
        "label_hint": None,
        "confident": False,
        "reasons": ["no conversion found"],
    }
    assert EmiPlanProposal.model_validate(plan).conversion is None


def test_split_request_accepts_contract_example():
    req = EmiSplitRequest.model_validate({"plans": [SPLIT_PLAN]})
    assert req.model_dump() == {"plans": [SPLIT_PLAN]}


def test_split_plan_close_defaults_false_and_nullables():
    plan = {
        "account": PLAN,
        "card": None,
        "ref": None,
        "label": None,
        "txn_ids": ["t1"],
    }
    assert EmiSplitPlan.model_validate(plan).close is False


@pytest.mark.parametrize(
    "body",
    [
        {"plans": [{**SPLIT_PLAN, "colour": "red"}]},
        {"plans": [SPLIT_PLAN], "dry_run": True},
        {"plans": []},
        {"plans": [{**SPLIT_PLAN, "txn_ids": []}]},
    ],
)
def test_split_request_rejects_extra_keys_and_empty_lists(body):
    with pytest.raises(ValidationError):
        EmiSplitRequest.model_validate(body)


def test_split_response_shape():
    body = {"moved": 2, "opened": [PLAN], "plans": [PLAN], "closed": []}
    assert EmiSplitResponse.model_validate(body).model_dump() == body


def test_loan_terms_request_and_response_accept_contract_example():
    assert LoanTermsUpdateRequest.model_validate(TERMS).model_dump() == TERMS
    assert LoanTermsResponse.model_validate(TERMS).model_dump() == TERMS
    nulls = {k: None for k in TERMS} | {"account": TERMS["account"]}
    assert LoanTermsUpdateRequest.model_validate(nulls).model_dump() == nulls


def test_loan_terms_request_leaves_range_checks_to_the_service():
    body = {**TERMS, "annual_rate_pct": "abc", "term_months": 0}
    req = LoanTermsUpdateRequest.model_validate(body)
    assert (req.annual_rate_pct, req.term_months) == ("abc", 0)


def test_loan_terms_request_forbids_extra_keys():
    with pytest.raises(ValidationError):
        LoanTermsUpdateRequest.model_validate({**TERMS, "emi": "1000"})
